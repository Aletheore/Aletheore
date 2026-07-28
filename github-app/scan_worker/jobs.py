import asyncio
import inspect
import json
import logging
import os
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from rq import get_current_job

from app_server.audit_signing import content_hash, sign_report
from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.code_graph_diff import diff_endpoints, diff_modules
from aletheore.evidence import write_evidence
from aletheore.git_intel.analyzer import analyze_git, compute_hotspots
from aletheore.evidence_resolution import (
    empty_resolution,
    merge_resolution,
    normalize_resolution,
    resolve_code_evidence,
)
from aletheore.history import compute_diff
from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment
from aletheore.healthcheck import run_healthcheck
from aletheore.signature_diff import find_regression_fence_violations
from app_server.config import get_settings
from app_server.db import MAX_SCANNED_REPOS_PER_MONTH
from app_server.github_auth import generate_app_jwt, get_installation_token
from app_server.llm_cost import base_cap_for_plan, cost_for_usage, monthly_cap_for_installation
from app_server.logging_config import log_job
from app_server.rate_limit import cooldown_seconds_for_loc, total_loc_from_evidence
from app_server.url_validation import UnsafeURLError, validate_external_https_url
from scan_worker import live_wiki
from scan_worker.db import (
    check_and_reserve_flash_review_attempt,
    check_and_reserve_managed_audit,
    check_and_reserve_monthly_repo_scan_slot,
    delete_expired_sessions,
    delete_wiki_subsystems_not_in,
    get_extra_seats,
    get_flash_review_count_this_month,
    get_installation as get_installation_row,
    get_last_endpoint_health,
    get_last_reviewed_sha,
    get_latest_evidence,
    get_llm_spend_this_month,
    increment_flash_review_count,
    insert_audit_report,
    insert_endpoint_health,
    insert_repo_history,
    installation_spend_lock,
    list_health_check_targets_all,
    list_recent_endpoint_incidents,
    list_repos_for_installation,
    list_wiki_subsystems,
    record_llm_spend,
    set_last_reviewed_sha,
    set_wiki_build_status,
    upsert_wiki_overview,
    upsert_wiki_subsystem,
)
from scan_worker.flash_review import (
    build_code_evidence_context,
    build_referenced_symbol_context,
    fetch_changed_file_contents,
    gather_file_context,
    is_non_substantive_diff,
    review_diff,
)
from scan_worker.flash_review_cache import (
    lookup_cached_result as lookup_cached_flash_review_result,
    store_result as store_flash_review_result,
)
from scan_worker.github_api import (
    create_check_run,
    fetch_default_branch_head_sha,
    fetch_file_content,
    fetch_pr_changed_files,
    fetch_pr_diff,
    upsert_pr_comment,
)
from scan_worker.managed_audit import run_managed_audit
from scan_worker.model_tiers import PRO_MODEL, model_for_plan, writing_adapter_for_plan
from scan_worker.packet_cache import lookup_cached_result, store_result
from scan_worker.code_graph_store import CodeGraphStore
from scan_worker.postgres_graph_store import PostgresRepoGraphStore
from scan_worker.slack import (
    format_latency_alert,
    format_reachability_alert,
    format_runtime_error_alert,
    format_shape_change_alert,
    send_health_alert,
    send_slack_alert,
)

JOBS_ROOT = Path("/tmp/aletheore-jobs")
AUDIT_COMMENT_MARKER = "<!-- aletheore-audit -->"
FLASH_REVIEW_MARKER = "<!-- aletheore-flash-review -->"
# Generous: the one-time full build calls a strong model once per
# subsystem plus the overview, deliberately the most expensive step in
# the whole Live Wiki pipeline - see scan_worker/live_wiki.py.
LIVE_WIKI_FULL_BUILD_JOB_TIMEOUT_SECONDS = 1800
LIVE_WIKI_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS = 300
HEALTH_CHECK_DOWN_RETRY_ATTEMPTS = 2
HEALTH_CHECK_DOWN_RETRY_DELAY_SECONDS = 2.0
# PR scans clone via `git checkout <sha>` (detached HEAD, not a named
# branch) - the persisted git graph tracks one repo's mainline history
# across scans, not each individual PR's ephemeral branch, so every hosted
# sync uses this one fixed key rather than whatever branch name (or lack
# of one) a given clone happens to be on.
GRAPH_BRANCH = "default"

# Bounds the very first (cold) sync of a repo's history to its most recent
# N commits, rather than walking the entire history in one pass. Needed
# independent of the fold()-level memory caps (incremental.py's
# MAX_CO_CHANGE_PARTNERS_TRACKED): reproduced directly against
# torvalds/linux (1.46M commits, ~174K files) in a container capped at the
# same 1GB limit as this worker - even with zero co-change/recent-commit
# tracking, just the base per-file bookkeeping for that many distinct
# files was already at the memory limit. A depth cap keeps a cold sync's
# file count proportional to a bounded recent window instead of a repo's
# entire lifetime, whatever that repo's total size turns out to be. Later
# scans extend coverage incrementally (each only processes commits landed
# since last sync) but never backfill older history beyond the original
# cap - an accepted trade-off, surfaced via `history_depth_limited` in the
# git evidence.
GRAPH_COLD_SYNC_DEPTH_CAP = 50_000

# `git log -p` (full unified diffs, used by the secrets-in-history scan)
# costs far more per commit than the graph engine's `--name-only` walk:
# measured directly at ~2s / ~1.4MB of diff text per 1000 commits, so
# torvalds/linux's full history would take git itself ~50 minutes and
# stream over 2GB of diff text on every hosted scan, independent of
# whether it also OOMs. Capped separately and more conservatively than
# GRAPH_COLD_SYNC_DEPTH_CAP for that reason.
SECRETS_HISTORY_DEPTH_CAP = 20_000

# The real, customer-facing promise for Pro ($34.99/mo): up to 300 PR
# reviews/month. Worst-case cost at 300 reviews hitting the max context
# caps each is well under $3 (deepseek-v4-flash, ~$0.14/M input +
# $0.28/M output) - this is a usage ceiling for the promise itself, not a
# cost-protection measure; the existing dollar-based monthly_cap_for_installation
# check stays in place as a separate defense against a pathological
# per-review cost blowing past what 300 reviews should ever cost.
MAX_FLASH_REVIEWS_PER_MONTH = 300


def _job_temp_dir() -> Path:
    path = JOBS_ROOT / str(uuid.uuid4())
    path.mkdir(parents=True, exist_ok=False)
    return path


def _clone_url(repo_full_name: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"


def _clone_ref(url: str, ref: str, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(["git", "checkout", "-q", ref], cwd=dest, check=True)


# Root for persistent, reused-across-scans checkouts (see
# _ensure_persistent_checkout) - overridable so a real deployment can point
# it at a mounted volume and tests aren't stuck with a hardcoded path.
_REPO_CHECKOUT_ROOT_ENV = "ALETHEORE_REPO_CHECKOUT_ROOT"
_DEFAULT_REPO_CHECKOUT_ROOT = "/data/aletheore-repo-checkouts"


def _persistent_checkout_dir(installation_id: int, repo_full_name: str) -> Path:
    root = Path(os.environ.get(_REPO_CHECKOUT_ROOT_ENV, _DEFAULT_REPO_CHECKOUT_ROOT))
    safe_name = repo_full_name.replace("/", "__")
    return root / str(installation_id) / safe_name


def _ensure_persistent_checkout(url: str, checkout_sha: str, checkout_dir: Path) -> None:
    """Keeps one real checkout per repo, reused across scans, instead of
    the clone-fresh-and-delete pattern _clone_ref/_clone_pr_head use for
    the ephemeral per-job checkouts above. This is what gives a later
    scan a real "last time" to `git diff` against locally, and is a
    prerequisite for the incremental scan cache
    (aletheore.evidence._load_unchanged_scan_cache) actually helping -
    without a persistent checkout, every scan starts from nothing to
    diff against, same as a fresh clone.

    Mirrors _clone_ref's exact proven-working shape: a plain `git fetch`
    (no explicit refspec) followed by `git checkout <sha>`, rather than
    fetching the SHA directly - GitHub does not reliably allow fetching a
    bare SHA unless it happens to be reachable from an advertised ref,
    the same reason _clone_ref itself relies on a full clone's implicit
    ref fetching rather than fetching head_sha directly.

    `git remote set-url` runs on every reuse so a rotated access token
    (see _clone_url - `url` always carries a fresh one) doesn't leave
    this checkout stuck fetching against a stale URL baked in at clone
    time.
    """
    if (checkout_dir / ".git").exists():
        subprocess.run(["git", "remote", "set-url", "origin", url], cwd=checkout_dir, check=True)
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=checkout_dir, check=True)
        subprocess.run(["git", "checkout", "-q", "-f", checkout_sha], cwd=checkout_dir, check=True)
        subprocess.run(["git", "clean", "-q", "-fdx"], cwd=checkout_dir, check=True)
    else:
        checkout_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(checkout_dir)], check=True)
        subprocess.run(["git", "checkout", "-q", checkout_sha], cwd=checkout_dir, check=True)


def _prepare_head_checkout(
    clone_url: str, head_sha: str, installation_id: int, repo_full_name: str, fallback_dir: Path
) -> Path:
    """Uses a persistent, reused-across-scans checkout when one is
    available (see _ensure_persistent_checkout), falling back to the
    original ephemeral clone-and-delete pattern (_clone_ref) if
    persistent storage isn't mounted, isn't writable, or fails for any
    other reason - this must never be the reason a PR scan fails
    outright, it only ever gates whether the upcoming scan can be
    incremental.
    """
    try:
        checkout_dir = _persistent_checkout_dir(installation_id, repo_full_name)
        _ensure_persistent_checkout(clone_url, head_sha, checkout_dir)
        return checkout_dir
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "persistent checkout unavailable (%s); falling back to an ephemeral clone", type(exc).__name__
        )
        _clone_ref(clone_url, head_sha, fallback_dir)
        return fallback_dir


def _build_unchanged_scan_cache(
    installation_id: int,
    repo_full_name: str,
    checkout_dir: Path,
    previous_sha: str | None,
    current_sha: str,
    cache_path: Path,
) -> Path | None:
    """Writes a JSON cache file (see
    aletheore.evidence._load_unchanged_scan_cache) listing every
    currently-tracked file NOT touched between previous_sha and
    current_sha, with its previously-persisted module/endpoint data, so
    the upcoming `aletheore scan` can skip re-parsing it. Returns None
    (no cache - a full scan, matching today's behavior exactly) whenever
    there's no solid basis for a diff: no previous sync yet, `git diff`
    itself failing (e.g. previous_sha isn't reachable in this checkout -
    expected on a fallback ephemeral clone), or the graph database being
    unreachable. This only ever narrows what gets scanned; any failure
    here just means "scan everything," never "silently skip something
    that might have changed."
    """
    if previous_sha is None:
        return None
    diff_result = subprocess.run(
        ["git", "diff", "--name-only", previous_sha, current_sha],
        cwd=checkout_dir, capture_output=True, text=True,
    )
    if diff_result.returncode != 0:
        return None
    changed_files = set(diff_result.stdout.splitlines())

    try:
        settings = get_settings()
        store = CodeGraphStore(settings.database_url, installation_id, repo_full_name)
        all_modules = store.load_all_modules(GRAPH_BRANCH)
        all_endpoints = store.load_all_endpoints(GRAPH_BRANCH)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "could not build unchanged-scan cache (%s); falling back to a full scan", type(exc).__name__
        )
        return None

    unchanged_modules = {path: m for path, m in all_modules.items() if path not in changed_files}
    unchanged_endpoints = {path: e for path, e in all_endpoints.items() if path not in changed_files}
    if not unchanged_modules and not unchanged_endpoints:
        return None

    cache_path.write_text(json.dumps({"modules": unchanged_modules, "endpoints": unchanged_endpoints}))
    return cache_path


def _run_scan(repo_dir: Path, unchanged_scan_cache_path: Path | None = None) -> Path:
    # See GRAPH_COLD_SYNC_DEPTH_CAP and SECRETS_HISTORY_DEPTH_CAP - the
    # CLI's own analyze_git and find_secrets_in_history calls (inside this
    # subprocess) hit the same cold-sync cost/memory ceilings as the
    # Postgres sync below, and run first, so they need the same caps. Both
    # left unset for a developer running `aletheore scan` directly on their
    # own machine (see evidence.py's handling of both env vars).
    env = {
        **os.environ,
        "ALETHEORE_GIT_HISTORY_DEPTH_CAP": str(GRAPH_COLD_SYNC_DEPTH_CAP),
        "ALETHEORE_SECRETS_HISTORY_DEPTH_CAP": str(SECRETS_HISTORY_DEPTH_CAP),
    }
    if unchanged_scan_cache_path is not None:
        env["ALETHEORE_UNCHANGED_SCAN_CACHE"] = str(unchanged_scan_cache_path)
    subprocess.run(["aletheore", "scan", str(repo_dir)], check=True, env=env)
    return repo_dir / ".aletheore" / "air.json"


def _sync_persistent_git_graph(installation_id: int, repo_full_name: str, repo_dir: Path, evidence: dict) -> dict:
    # `aletheore scan` (the subprocess above) already computed a `git` key
    # using its own local, throwaway .aletheore/graph.db inside repo_dir -
    # safe and memory-bounded now, but every hosted scan clones a fresh
    # repo copy that gets deleted afterward, so that local cache never
    # persists between scans on its own. This overrides it with a real,
    # cross-scan incremental sync backed by Postgres, so a repeat scan of
    # the same installation's repo only processes commits since last time,
    # and the resulting ownership/recent-commits data survives to answer
    # later queries (e.g. runtime-failure correlation) without a fresh
    # git walk. Never allowed to fail the scan itself: any error here
    # just leaves the subprocess's own (correct, just non-persistent) git
    # data in place.
    if not evidence.get("git", {}).get("available"):
        return evidence
    try:
        settings = get_settings()
        store = PostgresRepoGraphStore(settings.database_url, installation_id, repo_full_name)
        modules = evidence.get("repository", {}).get("modules", [])
        git_data = analyze_git(repo_dir, store=store, depth_cap=GRAPH_COLD_SYNC_DEPTH_CAP, branch=GRAPH_BRANCH)
        if git_data.get("available"):
            git_data["hotspots"] = compute_hotspots(
                repo_dir, modules, store=store, depth_cap=GRAPH_COLD_SYNC_DEPTH_CAP, branch=GRAPH_BRANCH
            )
            evidence["git"] = git_data
    except Exception as exc:  # noqa: BLE001
        # Broad by design: a GitAnalysisError (bad history state) and a
        # Postgres connection failure are both real possibilities in
        # production, and neither is allowed to break the PR scan itself -
        # this whole step is a persistence enhancement, not the source of
        # truth for this scan's own result.
        logging.getLogger("scan_worker.jobs").warning(
            "persistent git graph sync failed (%s); keeping this scan's own git data", type(exc).__name__
        )
    return evidence


def _sync_code_graph(installation_id: int, repo_full_name: str, head_sha: str, evidence: dict) -> None:
    """Updates the durable, incrementally-queryable code graph
    (code_graph_files/symbols/dependency_edges/endpoints) from this
    scan's fresh evidence - the counterpart to _sync_persistent_git_graph
    above, for the code model rather than git history. repo_history's
    evidence JSONB blob is a whole-repo snapshot rewritten on every single
    scan; this only touches the rows for files whose extracted content
    actually changed (see aletheore.code_graph_diff), so the durable
    graph is addressable and queryable at file/symbol/edge/endpoint
    granularity instead of "re-parse the latest blob every time you need
    one fact from it." Never allowed to fail the scan itself: any error
    here just leaves the durable graph stale until the next successful
    scan, same discipline as the git graph sync above.
    """
    try:
        settings = get_settings()
        store = CodeGraphStore(settings.database_url, installation_id, repo_full_name)

        modules = evidence.get("repository", {}).get("modules", [])
        previous_hashes = store.load_content_hashes(GRAPH_BRANCH)
        changed_modules, deleted_paths = diff_modules(previous_hashes, modules)
        store.apply_module_deltas(
            GRAPH_BRANCH,
            changed_modules,
            deleted_paths,
            new_sync_sha=head_sha,
            new_sync_at=datetime.now(timezone.utc),
        )

        endpoints = evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", [])
        previous_endpoints = store.load_endpoint_keys(GRAPH_BRANCH)
        changed_endpoints, deleted_keys = diff_endpoints(previous_endpoints, endpoints)
        store.apply_endpoint_deltas(GRAPH_BRANCH, changed_endpoints, deleted_keys)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "code graph sync failed (%s); durable graph left stale until next successful scan",
            type(exc).__name__,
        )


def _insert_history(installation_id: int, repo_full_name: str, evidence: dict) -> None:
    settings = get_settings()
    insert_repo_history(
        settings.database_url,
        installation_id,
        repo_full_name,
        datetime.now(timezone.utc),
        evidence,
    )


def _maybe_send_slack_alert(
    installation_id: int, repo_full_name: str, pr_number: int, diff: dict
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return
    webhook_url = installation.get("webhook_url")
    if not webhook_url:
        return
    send_slack_alert(webhook_url, diff, repo_full_name, pr_number)


def _real_new_secrets(diff: dict) -> list[dict]:
    return [
        finding
        for finding in diff.get("secrets", {}).get("new", [])
        if not finding.get("likely_placeholder", False) and not finding.get("accepted", False)
    ]


REGRESSION_FENCE_WINDOW_DAYS = 7


def find_touched_incident_endpoints(
    changed_files: list[str],
    evidence: dict,
    incidents: list[dict],
) -> list[dict]:
    incident_by_key = {(i["endpoint_method"], i["endpoint_path"]): i for i in incidents}
    endpoints = evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", [])
    changed = set(changed_files)
    touched = []
    for endpoint in endpoints:
        if endpoint.get("file") not in changed:
            continue
        key = (endpoint.get("method"), endpoint.get("path"))
        incident = incident_by_key.get(key)
        if incident is None:
            continue
        touched.append(
            {
                "method": endpoint.get("method"),
                "path": endpoint.get("path"),
                "file": endpoint.get("file"),
                "line": endpoint.get("line"),
                "incident_count": incident["incident_count"],
                "last_incident_at": incident["last_incident_at"],
            }
        )
    return touched


def _maybe_create_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    diff: dict,
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    new_secrets = _real_new_secrets(diff)
    if new_secrets:
        summary = "\n".join(
            f"- `{finding.get('path')}:{finding.get('line')}` ({finding.get('pattern')})"
            for finding in new_secrets
        )
        create_check_run(client, token, repo_full_name, head_sha, "failure", summary)
    else:
        create_check_run(client, token, repo_full_name, head_sha, "success", "No new secrets found.")


def _maybe_create_regression_risk_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    evidence: dict,
    changed_files: list[str],
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    since = datetime.now(timezone.utc) - timedelta(days=REGRESSION_FENCE_WINDOW_DAYS)
    incidents = list_recent_endpoint_incidents(
        settings.database_url,
        installation_id,
        repo_full_name,
        since,
    )
    if not incidents:
        return

    touched = find_touched_incident_endpoints(changed_files, evidence, incidents)
    if not touched:
        return

    lines = []
    for item in touched:
        location = (
            f" - handled by {item['file']}:{item['line']}"
            if item.get("file") and item.get("line") is not None
            else ""
        )
        lines.append(
            f"- `{item['method']} {item['path']}`{location}: "
            f"{item['incident_count']} reachability incident(s) in the last "
            f"{REGRESSION_FENCE_WINDOW_DAYS} days"
        )
    summary = (
        "This PR touches a handler with recent production reachability incidents:\n"
        + "\n".join(lines)
    )
    create_check_run(
        client,
        token,
        repo_full_name,
        head_sha,
        "neutral",
        summary,
        name="Aletheore regression risk",
    )


def _maybe_create_regression_fence_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    old_evidence: dict,
    new_evidence: dict,
    changed_files: list[str],
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    violations = find_regression_fence_violations(old_evidence, new_evidence, changed_files)
    if not violations:
        return

    lines = []
    for v in violations:
        callers = ", ".join(f"`{c}`" for c in v["untouched_callers"])
        lines.append(
            f"- `{v['function']}` in `{v['file']}`: `{v['old_params']}` -> `{v['new_params']}`, "
            f"but these importers weren't updated in this PR: {callers}"
        )
    summary = (
        "This PR changes a function signature without updating all known importers:\n"
        + "\n".join(lines)
    )
    create_check_run(
        client,
        token,
        repo_full_name,
        head_sha,
        "neutral",
        summary,
        name="Aletheore Regression Fence",
    )


async def _resolve_token(installation_id: int, app_jwt: str) -> str:
    result = get_installation_token(installation_id, app_jwt)
    if inspect.isawaitable(result):
        return await result
    return result


def _token_sync(installation_id: int, app_jwt: str) -> str:
    return asyncio.run(_resolve_token(installation_id, app_jwt))


def _failure_body(error: Exception) -> str:
    return f"{COMMENT_MARKER}\nAletheore couldn't complete this scan: {error}"


def _post_failure_comment(
    settings,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    error: Exception,
) -> None:
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = _token_sync(installation_id, app_jwt)
    client = httpx.Client(base_url="https://api.github.com")
    upsert_pr_comment(client, token, repo_full_name, pr_number, _failure_body(error))


def _post_flash_review_failure_comment(
    settings,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    error: Exception,
) -> None:
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = _token_sync(installation_id, app_jwt)
    client = httpx.Client(base_url="https://api.github.com")
    body = (
        f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n\n"
        f"Aletheore couldn't complete this flash review: {error}"
    )
    upsert_pr_comment(client, token, repo_full_name, pr_number, body, marker=FLASH_REVIEW_MARKER)


@log_job
def run_pr_scan_job(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    settings = get_settings()

    # Pro plan: unlimited repos may be connected, but only
    # MAX_SCANNED_REPOS_PER_MONTH distinct repos actually get scanned per
    # calendar month - free plan is not subject to this cap.
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is not None and installation["plan"] != "free":
        if not check_and_reserve_monthly_repo_scan_slot(
            settings.database_url, installation_id, repo_full_name, MAX_SCANNED_REPOS_PER_MONTH
        ):
            return

    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)

        clone_url = _clone_url(repo_full_name, token)
        base_dir = job_dir / "base"
        _clone_ref(clone_url, base_sha, base_dir)
        head_dir = _prepare_head_checkout(clone_url, head_sha, installation_id, repo_full_name, job_dir / "head")

        try:
            previous_sha = CodeGraphStore(
                settings.database_url, installation_id, repo_full_name
            ).load_last_synced_sha(GRAPH_BRANCH)
        except Exception:  # noqa: BLE001
            previous_sha = None
        unchanged_scan_cache_path = _build_unchanged_scan_cache(
            installation_id, repo_full_name, head_dir, previous_sha, head_sha,
            job_dir / "unchanged-scan-cache.json",
        )

        base_evidence_path = _run_scan(base_dir)
        head_evidence_path = _run_scan(head_dir, unchanged_scan_cache_path=unchanged_scan_cache_path)
        old = json.loads(base_evidence_path.read_text())
        new = json.loads(head_evidence_path.read_text())
        diff = compute_diff(old, new, full=False)

        client = httpx.Client(base_url="https://api.github.com")
        upsert_pr_comment(client, token, repo_full_name, pr_number, format_diff_comment(diff))
        new = _sync_persistent_git_graph(installation_id, repo_full_name, head_dir, new)
        _sync_code_graph(installation_id, repo_full_name, head_sha, new)
        _insert_history(installation_id, repo_full_name, new)

        # These are side effects, not the primary deliverable above - a failure in
        # either (e.g. a missing Slack webhook or missing Checks permission) must
        # not fall through to the outer except, which would overwrite the diff
        # comment we already posted with a generic failure message.
        try:
            _maybe_send_slack_alert(installation_id, repo_full_name, pr_number, diff)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("scan_worker.jobs").warning(
                "alert webhook send failed for installation=%s repo=%s (%s)",
                installation_id, repo_full_name, exc,
            )
        try:
            _maybe_create_check_run(client, token, repo_full_name, head_sha, installation_id, diff)
        except Exception:  # noqa: BLE001
            pass
        try:
            changed_files = fetch_pr_changed_files(client, token, repo_full_name, base_sha, head_sha)
        except Exception:  # noqa: BLE001
            changed_files = None
        if changed_files is not None:
            try:
                _maybe_update_live_wiki(installation_id, repo_full_name, new, changed_files, head_sha)
            except Exception as exc:  # noqa: BLE001
                # _maybe_update_live_wiki already records failures to
                # wiki_build_status itself; this only catches something
                # failing before that handling could run (e.g. get_settings).
                logging.getLogger("scan_worker.jobs").warning(
                    "live wiki incremental update failed for installation=%s repo=%s (%s)",
                    installation_id, repo_full_name, exc,
                )
            try:
                _maybe_create_regression_risk_check_run(
                    client,
                    token,
                    repo_full_name,
                    head_sha,
                    installation_id,
                    new,
                    changed_files,
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                _maybe_create_regression_fence_check_run(
                    client,
                    token,
                    repo_full_name,
                    head_sha,
                    installation_id,
                    old,
                    new,
                    changed_files,
                )
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@log_job
def run_initial_scan_job(installation_id: int, repo_full_name: str) -> None:
    """Scans a repo's default branch once, right after it's connected (a
    brand-new installation, or a repo added to an existing one) - see
    webhooks/installation.py. Without this, a repo with no open pull
    requests never gets scanned at all: run_pr_scan_job is the only other
    thing that ever writes a repo_history row, and it only fires on a PR
    event. A repo could otherwise sit "Initialization required" on the
    dashboard forever with no feedback or path forward.

    Best-effort and silent on failure - there's no PR to comment a
    failure on, and the dashboard's existing "Initialization required"
    state is already a truthful (if unhelpful) signal rather than one
    this job needs to actively correct.
    """
    settings = get_settings()

    installation = get_installation_row(settings.database_url, installation_id)
    if installation is not None and installation["plan"] != "free":
        if not check_and_reserve_monthly_repo_scan_slot(
            settings.database_url, installation_id, repo_full_name, MAX_SCANNED_REPOS_PER_MONTH
        ):
            return

    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        client = httpx.Client(base_url="https://api.github.com")

        head_sha = fetch_default_branch_head_sha(client, token, repo_full_name)
        clone_url = _clone_url(repo_full_name, token)
        repo_dir = job_dir / "repo"
        _clone_ref(clone_url, head_sha, repo_dir)

        evidence_path = _run_scan(repo_dir)
        evidence = json.loads(evidence_path.read_text())
        evidence = _sync_persistent_git_graph(installation_id, repo_full_name, repo_dir, evidence)
        _sync_code_graph(installation_id, repo_full_name, head_sha, evidence)
        _insert_history(installation_id, repo_full_name, evidence)

        # A repo added to an already-paid installation should get its
        # AIRview build right away too, rather than waiting for enough
        # incremental pushes to slowly build clusters one at a time - the
        # same gap the Paddle subscription.created wiki-build trigger
        # closed for brand-new upgrades.
        if installation is not None and installation["plan"] != "free":
            try:
                run_live_wiki_full_build_job(installation_id, repo_full_name)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@log_job
def run_push_scan_job(
    installation_id: int, repo_full_name: str, head_sha: str, changed_files: list[str]
) -> None:
    """Re-scans a repo's default branch after a direct push or a PR merge,
    and reconciles AIRview against that scan.

    Before this job existed, AIRview only ever updated off pull_request
    events using the PR's *head* SHA - proposed, possibly-unmerged code -
    via _maybe_update_live_wiki inside run_pr_scan_job. Nothing ever
    re-scanned the actual default branch after the fact, so a merge could
    leave the wiki describing the PR's pre-merge state indefinitely, and a
    PR closed without merging left the wiki describing abandoned branch
    content forever. Routing every push to main through the same
    incremental update path used for PRs means merges (and direct pushes)
    become the recurring correction against real merged code.
    """
    settings = get_settings()

    installation = get_installation_row(settings.database_url, installation_id)
    if installation is not None and installation["plan"] != "free":
        if not check_and_reserve_monthly_repo_scan_slot(
            settings.database_url, installation_id, repo_full_name, MAX_SCANNED_REPOS_PER_MONTH
        ):
            return

    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        clone_url = _clone_url(repo_full_name, token)
        repo_dir = _prepare_head_checkout(clone_url, head_sha, installation_id, repo_full_name, job_dir / "repo")

        evidence_path = _run_scan(repo_dir)
        evidence = json.loads(evidence_path.read_text())
        evidence = _sync_persistent_git_graph(installation_id, repo_full_name, repo_dir, evidence)
        _sync_code_graph(installation_id, repo_full_name, head_sha, evidence)
        _insert_history(installation_id, repo_full_name, evidence)

        if installation is not None and installation["plan"] != "free":
            try:
                _maybe_update_live_wiki(installation_id, repo_full_name, evidence, changed_files, head_sha)
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("scan_worker.jobs").warning(
                    "live wiki reconciliation after push failed for installation=%s repo=%s (%s)",
                    installation_id, repo_full_name, exc,
                )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "push scan job failed for installation=%s repo=%s (%s)", installation_id, repo_full_name, exc,
        )
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _clone_pr_head(url: str, pr_number: int, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(
        ["git", "fetch", "-q", "origin", f"refs/pull/{pr_number}/head"],
        cwd=dest,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, check=True)


def _git_rev_parse_head(repo_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _sign_and_persist_audit_report(
    settings,
    installation_id: int,
    repo_full_name: str,
    report_text: str,
) -> str | None:
    try:
        verification_token = secrets.token_hex(32)
        report_hash = content_hash(report_text)
        signature = sign_report(report_text, settings.audit_signing_private_key)
        insert_audit_report(
            settings.database_url,
            installation_id,
            repo_full_name,
            verification_token,
            report_text,
            report_hash,
            signature,
        )
        return verification_token
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "audit report signing/persistence failed (%s); report still returned unsigned",
            type(exc).__name__,
        )
        return None


def _maybe_create_audit_certificate_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str | None,
    verify_url: str,
) -> None:
    # Best-effort, like every other Check Run this codebase posts - a
    # customer's actual audit result must never be blocked on GitHub's
    # check-runs API being reachable. head_sha can be None if `git
    # rev-parse` itself failed; there's nothing to attach a check run to
    # in that case.
    if head_sha is None:
        return
    try:
        create_check_run(
            client,
            token,
            repo_full_name,
            head_sha,
            "success",
            "A cryptographically signed (Ed25519) record of this audit is available for "
            f"independent verification: {verify_url}\n\n"
            "Require this check in branch protection to block merges without a valid, "
            "freshly-signed Aletheore audit certificate.",
            name="Aletheore Audit Certificate",
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "audit certificate check run failed for repo=%s (%s)", repo_full_name, exc,
        )


@log_job
def run_managed_audit_pr_job(installation_id: int, repo_full_name: str, pr_number: int) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    plan = installation["plan"] if installation is not None else "air"

    if plan != "free" and not check_and_reserve_monthly_repo_scan_slot(
        settings.database_url, installation_id, repo_full_name, MAX_SCANNED_REPOS_PER_MONTH
    ):
        return

    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        repo_dir = job_dir / "head"
        _clone_pr_head(_clone_url(repo_full_name, token), pr_number, repo_dir)
        evidence_path = _run_scan(repo_dir)

        evidence = json.loads(evidence_path.read_text())
        cooldown_seconds = cooldown_seconds_for_loc(total_loc_from_evidence(evidence))
        client = httpx.Client(base_url="https://api.github.com")
        if not check_and_reserve_managed_audit(
            settings.database_url, installation_id, repo_full_name, cooldown_seconds
        ):
            body = (
                f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                f"Rate limited: this repo can run one managed audit every "
                f"{cooldown_seconds // 3600} hours. Try again later."
            )
        else:
            with installation_spend_lock(settings.database_url, installation_id):
                extra_seats = get_extra_seats(settings.database_url, installation_id)
                monthly_cap = monthly_cap_for_installation(base_cap_for_plan(plan), extra_seats)
                current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
                if current_spend >= monthly_cap:
                    body = (
                        f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                        f"Monthly spend cap reached for this installation (${monthly_cap:.2f}). "
                        "Try again next month, or contact support to increase your limit."
                    )
                else:
                    spend_accumulator = {"total": 0.0, "model": model_for_plan(plan)}

                    def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                        spend_accumulator["total"] += cost_for_usage(
                            spend_accumulator["model"], prompt_tokens, completion_tokens
                        )

                    report_text = run_managed_audit(repo_dir, on_usage=_on_usage, plan=plan)
                    record_llm_spend(
                        settings.database_url, installation_id, spend_accumulator["total"]
                    )
                    verification_token = _sign_and_persist_audit_report(
                        settings,
                        installation_id,
                        repo_full_name,
                        report_text,
                    )
                    if verification_token is not None:
                        verify_url = f"{settings.public_base_url}/v1/audit/{verification_token}/verify"
                        body = (
                            f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                            f"{report_text}\n\n[Verify this report]({verify_url})"
                        )
                        _maybe_create_audit_certificate_check_run(
                            client,
                            token,
                            repo_full_name,
                            _git_rev_parse_head(repo_dir),
                            verify_url,
                        )
                    else:
                        body = f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n{report_text}"
        upsert_pr_comment(
            client,
            token,
            repo_full_name,
            pr_number,
            body,
            marker=AUDIT_COMMENT_MARKER,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@log_job
def run_managed_audit_api_job(
    installation_id: int,
    evidence: dict | str,
    repo_full_name: str,
) -> str:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    plan = installation["plan"] if installation is not None else "air"
    with installation_spend_lock(settings.database_url, installation_id):
        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(base_cap_for_plan(plan), extra_seats)
        current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
        if current_spend >= monthly_cap:
            raise RuntimeError(
                f"monthly spend cap reached for this installation (${monthly_cap:.2f})"
            )

        job_dir = _job_temp_dir()
        try:
            if isinstance(evidence, dict):
                write_evidence(evidence, job_dir)
            else:
                aletheore_dir = job_dir / ".aletheore"
                aletheore_dir.mkdir(parents=True, exist_ok=True)
                (aletheore_dir / "air.toon").write_text(evidence)
                (aletheore_dir / "air.json").write_text(json.dumps({"managed_evidence": True}))
            spend_accumulator = {"total": 0.0, "model": model_for_plan(plan)}

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    spend_accumulator["model"], prompt_tokens, completion_tokens
                )

            result = run_managed_audit(job_dir, on_usage=_on_usage, plan=plan)
            record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
            verification_token = _sign_and_persist_audit_report(
                settings,
                installation_id,
                repo_full_name,
                result,
            )
            job = get_current_job()
            if job is not None:
                job.meta["verification_token"] = verification_token
                job.save_meta()
            return result
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


@log_job
def run_flash_review_job(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    if not check_and_reserve_monthly_repo_scan_slot(
        settings.database_url, installation_id, repo_full_name, MAX_SCANNED_REPOS_PER_MONTH
    ):
        return

    if not check_and_reserve_flash_review_attempt(
        settings.database_url, installation_id, repo_full_name, pr_number
    ):
        return

    with installation_spend_lock(settings.database_url, installation_id):
        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(base_cap_for_plan(installation["plan"]), extra_seats)
        current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
        if current_spend >= monthly_cap:
            return
        if get_flash_review_count_this_month(settings.database_url, installation_id) >= MAX_FLASH_REVIEWS_PER_MONTH:
            return

        try:
            _run_flash_review(
                settings, installation_id, repo_full_name, pr_number, base_sha, head_sha
            )
        except Exception as exc:  # noqa: BLE001
            try:
                _post_flash_review_failure_comment(
                    settings, installation_id, repo_full_name, pr_number, exc
                )
            except Exception:  # noqa: BLE001
                pass


def _run_flash_review(
    settings,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = _token_sync(installation_id, app_jwt)
    client = httpx.Client(base_url="https://api.github.com")

    last_reviewed_sha = get_last_reviewed_sha(
        settings.database_url, installation_id, repo_full_name, pr_number
    )
    diff_base = last_reviewed_sha or base_sha
    diff_text = fetch_pr_diff(client, token, repo_full_name, diff_base, head_sha)
    changed_files = fetch_pr_changed_files(client, token, repo_full_name, diff_base, head_sha)

    spend_accumulator = {"total": 0.0}

    if is_non_substantive_diff(changed_files):
        findings: list[dict] = []
    else:
        file_context = gather_file_context(client, token, repo_full_name, changed_files, head_sha)
        file_contents = fetch_changed_file_contents(client, token, repo_full_name, changed_files, head_sha)
        evidence = _latest_evidence_or_none(settings.database_url, installation_id, repo_full_name)
        code_evidence_context = build_code_evidence_context(evidence, changed_files)

        def _fetch_symbol_source(file_path: str, start_line: int, end_line: int) -> str | None:
            content = fetch_file_content(client, token, repo_full_name, file_path, head_sha)
            if content is None:
                return None
            return "\n".join(content.splitlines()[start_line - 1 : end_line])

        referenced_symbol_context = build_referenced_symbol_context(
            evidence, changed_files, diff_text, _fetch_symbol_source
        )
        dsn = settings.database_url

        def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
            spend_accumulator["total"] += cost_for_usage(
                "deepseek-v4-flash", prompt_tokens, completion_tokens
            )

        def _cache_lookup(diff: str) -> list[dict] | None:
            return lookup_cached_flash_review_result(dsn, installation_id, repo_full_name, diff)

        def _cache_write(diff: str, found: list[dict], used: str) -> None:
            store_flash_review_result(dsn, installation_id, repo_full_name, diff, found, used)

        if code_evidence_context:
            findings = review_diff(
                diff_text,
                file_context=file_context,
                code_evidence_context=code_evidence_context,
                on_usage=_on_usage,
                referenced_symbol_context=referenced_symbol_context,
                cache_lookup=_cache_lookup,
                cache_write=_cache_write,
                model_used="deepseek-v4-flash",
                file_contents=file_contents,
            )
        else:
            findings = review_diff(
                diff_text,
                file_context=file_context,
                on_usage=_on_usage,
                referenced_symbol_context=referenced_symbol_context,
                cache_lookup=_cache_lookup,
                cache_write=_cache_write,
                model_used="deepseek-v4-flash",
                file_contents=file_contents,
            )
    record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
    increment_flash_review_count(settings.database_url, installation_id)

    if findings:
        lines = [f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n"]
        for finding in findings:
            lines.append(f"- `{finding['file']}:{finding['line']}` — {finding['issue']}")
            suggestion = finding.get("suggestion")
            if suggestion:
                lines.append(f"  ```\n  {suggestion}\n  ```")
        body = "\n".join(lines)
    else:
        body = (
            f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n\nNo issues found in this diff."
        )

    upsert_pr_comment(client, token, repo_full_name, pr_number, body, marker=FLASH_REVIEW_MARKER)
    set_last_reviewed_sha(
        settings.database_url, installation_id, repo_full_name, pr_number, head_sha
    )


def _send_if_webhook_configured(installation: dict, message: dict) -> None:
    webhook_url = installation.get("webhook_url")
    if webhook_url:
        send_health_alert(webhook_url, message)


def _endpoint_results(evidence: dict, base_url: str) -> list[dict]:
    endpoints = evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", [])
    if not endpoints:
        return []
    results = run_healthcheck(endpoints, base_url).get("results", [])
    for endpoint, result in zip(endpoints, results, strict=False):
        if endpoint.get("file") is not None:
            result["file"] = endpoint["file"]
        if endpoint.get("line") is not None:
            result["line"] = endpoint["line"]
        result["evidence_resolution"] = resolve_code_evidence(
            evidence,
            kind="endpoint",
            method=str(endpoint.get("method") or result.get("method") or ""),
            path=str(endpoint.get("path") or result.get("path") or ""),
        )
    return results


def _latest_evidence_or_none(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    try:
        return get_latest_evidence(dsn, installation_id, repo_full_name)
    except Exception:  # noqa: BLE001
        return None


def _latency_flipped(
    prior: dict | None,
    reachable: bool,
    latency_ms: float | None,
    threshold_ms: int | None,
) -> bool:
    if threshold_ms is None or not reachable or latency_ms is None:
        return False
    prior_has_latency = (
        prior is not None
        and prior.get("reachable") is True
        and prior.get("latency_ms") is not None
    )
    now_over = latency_ms > threshold_ms
    if not prior_has_latency:
        return now_over
    return (prior["latency_ms"] > threshold_ms) != now_over


def _recheck_single_endpoint(entry: dict, base_url: str) -> dict:
    minimal_endpoint = {"method": entry.get("method"), "path": entry["path"]}
    results = run_healthcheck([minimal_endpoint], base_url).get("results", [])
    if not results:
        return {
            "reachable": False,
            "status_code": None,
            "latency_ms": None,
            "response_shape": None,
        }
    return results[0]


def _commit_attachment_from_graph(installation_id: int, repo_full_name: str, source_file: str) -> dict | None:
    # Reads the same persisted, incrementally-synced graph
    # _owner_attachment_from_graph (below) already uses, instead of a live
    # GitHub API call (fetch_recent_commits_for_path) - evidence_git_file_churn
    # already has this exact data cached from the last scan, including the
    # commit subject (git_intel/incremental.py's stream_commit_touches
    # captures %s alongside sha/author/date). Degrades to None (no commit
    # attachment, not a broken alert) if this repo has no graph data yet
    # or the database is unreachable - same discipline as every other
    # attachment in this correlation chain.
    try:
        settings = get_settings()
        store = PostgresRepoGraphStore(settings.database_url, installation_id, repo_full_name)
        snapshot = store.load("unused", GRAPH_BRANCH)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "commit correlation from graph failed (%s); alerting without it", type(exc).__name__
        )
        return None
    churn = snapshot.file_churn.get(source_file)
    if churn is None or not churn.recent_commits:
        return None
    latest = churn.recent_commits[0]
    return normalize_resolution(
        kind="commit",
        commit={
            "sha": latest.sha,
            "author_name": latest.author_name,
            "author_email": latest.author_email,
            "subject": latest.subject,
        },
        confidence="weak",
    )


def _owner_attachment_from_graph(installation_id: int, repo_full_name: str, source_file: str) -> dict | None:
    # Prefers the persisted graph over a live API call: no extra GitHub
    # round-trip, and it still answers if GitHub itself is degraded.
    try:
        settings = get_settings()
        store = PostgresRepoGraphStore(settings.database_url, installation_id, repo_full_name)
        snapshot = store.load("unused", GRAPH_BRANCH)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "owner correlation from graph failed (%s); alerting without it", type(exc).__name__
        )
        return None
    churn = snapshot.file_churn.get(source_file)
    if churn is None or not churn.recent_commits:
        return None
    top_author_email = churn.recent_commits[0].author_email.lower()
    owner = snapshot.ownership.get(top_author_email)
    owner_name = sorted(owner.names)[0] if owner and owner.names else churn.recent_commits[0].author_name
    return normalize_resolution(kind="owner", owner=owner_name, confidence="inferred")


def _dependency_context_attachment(evidence: dict | None, source_file: str) -> dict | None:
    # Upstream/downstream modules for the failing file - already computed
    # by the scan itself (module.imports / module.imported_by), so this is
    # a lookup against data already in hand, not a new analysis pass.
    if not evidence:
        return None
    modules_by_path = {m["path"]: m for m in evidence.get("repository", {}).get("modules", [])}
    module = modules_by_path.get(source_file)
    if module is None:
        return None
    upstream = sorted(module.get("imports", []))[:5]
    downstream = sorted(module.get("imported_by", []))[:5]
    if not upstream and not downstream:
        return None
    return normalize_resolution(
        kind="dependency",
        dependency={"upstream": upstream, "downstream": downstream},
        confidence="exact",
    )


FIX_SUGGESTION_SYSTEM_PROMPT = """You are diagnosing why an API endpoint stopped responding. You are given
the endpoint, its status, the exact file/line/symbol implicated by static analysis, and the surrounding
source code. Respond with ONLY a concise, specific, actionable fix suggestion (2-3 sentences, plain text, no
markdown fences) - name the actual likely cause and what to change, never a vague "check your code" answer.
If you cannot identify a plausible concrete cause from what's given, respond with exactly: unknown."""


def _health_fix_suggestion_adapter() -> OpenAICompatibleAdapter:
    # Always Pro, never tier-routed through model_tiers.writing_adapter_for_plan -
    # this feature is part of every Pro subscription at one fixed cost, not
    # something that varies by a tier that no longer exists.
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=PRO_MODEL,
        supports_tool_choice=False,
    )


def _find_enclosing_symbol(evidence: dict | None, source_file: str, source_line: int | None) -> str | None:
    if not evidence or source_line is None:
        return None
    modules = evidence.get("repository", {}).get("modules", [])
    module = next((m for m in modules if m.get("path") == source_file), None)
    if module is None:
        return None
    symbols = module.get("symbols", {})
    for group in ("functions", "classes"):
        for entry in symbols.get(group, []):
            start, end = entry.get("start_line"), entry.get("end_line")
            if start is not None and end is not None and start <= source_line <= end:
                return entry.get("name")
    return None


def _fix_suggestion_attachment(
    installation_id: int,
    repo_full_name: str,
    source_file: str,
    source_line: int | None,
    method: str,
    path: str,
    status_code: int | None,
    evidence: dict | None,
) -> dict | None:
    # Grounded in the file/line/symbol already pinpointed deterministically
    # by the owner/dependency attachments - the one LLM call in this whole
    # correlation chain, and it only ever supplements what's already found.
    # Same degrade-on-any-failure discipline as every other attachment
    # here: missing code context, a DeepSeek outage, or a low-confidence
    # model response just means the alert goes out without a suggestion,
    # never blocks it.
    try:
        settings = get_settings()
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        with httpx.Client(base_url="https://api.github.com") as client:
            file_content = fetch_file_content(client, token, repo_full_name, source_file)
        if not file_content:
            return None

        lines = file_content.splitlines()
        anchor = (source_line or 1) - 1
        snippet = "\n".join(lines[max(0, anchor - 15) : min(len(lines), anchor + 15)])
        user_prompt = json.dumps(
            {
                "endpoint": f"{method} {path}",
                "status_code": status_code,
                "file": source_file,
                "line": source_line,
                "symbol": _find_enclosing_symbol(evidence, source_file, source_line),
                "code_context": snippet,
            }
        )
        raw = _health_fix_suggestion_adapter().simple_completion(
            FIX_SUGGESTION_SYSTEM_PROMPT, user_prompt, cwd="."
        )
        suggestion = raw.strip()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "fix-suggestion generation failed (%s); alerting without it", type(exc).__name__
        )
        return None
    if not suggestion or suggestion.lower() == "unknown":
        return None
    return normalize_resolution(kind="suggestion", suggestion=suggestion, confidence="inferred")


def _attach_recent_commit_for_failure(
    installation_id: int,
    repo_full_name: str,
    source_file: str,
    evidence_resolution: dict | None,
    evidence: dict | None = None,
    method: str = "",
    path: str = "",
    status_code: int | None = None,
    source_line: int | None = None,
) -> dict | None:
    attachments = []
    commit_attachment = _commit_attachment_from_graph(installation_id, repo_full_name, source_file)
    if commit_attachment is not None:
        attachments.append(commit_attachment)
    owner_attachment = _owner_attachment_from_graph(installation_id, repo_full_name, source_file)
    if owner_attachment is not None:
        attachments.append(owner_attachment)
    dependency_attachment = _dependency_context_attachment(evidence, source_file)
    if dependency_attachment is not None:
        attachments.append(dependency_attachment)
    suggestion_attachment = _fix_suggestion_attachment(
        installation_id,
        repo_full_name,
        source_file,
        source_line,
        method,
        path,
        status_code,
        evidence,
    )
    if suggestion_attachment is not None:
        attachments.append(suggestion_attachment)

    if not attachments:
        return evidence_resolution
    base = evidence_resolution or empty_resolution("endpoint")
    return merge_resolution(base, *attachments)


@log_job
def run_runtime_event_job(
    installation_id: int,
    repo_full_name: str,
    exception_type: str,
    exception_value: str,
    source_file: str,
    source_line: int,
    method: str = "",
    path: str = "",
) -> None:
    """Phase 3 - runtime-to-code evidence: resolves an inbound
    Sentry-compatible error event through the SAME correlation chain
    already proven for HTTP health-check failures
    (_attach_recent_commit_for_failure: handler symbol, dependent
    modules, recent commit, likely owner, optional fix suggestion) -
    "zero-hop debugging" for a second real trigger, not a second
    implementation. See app_server/runtime_events.py for the inbound
    webhook this is enqueued from.
    """
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    webhook_url = installation.get("webhook_url")
    if not webhook_url:
        return

    evidence = _latest_evidence_or_none(settings.database_url, installation_id, repo_full_name)
    evidence_resolution = _attach_recent_commit_for_failure(
        installation_id,
        repo_full_name,
        source_file,
        None,
        evidence,
        method=method,
        path=path,
        source_line=source_line,
    )

    message = format_runtime_error_alert(
        repo_full_name,
        exception_type,
        exception_value,
        source_file,
        source_line,
        method=method,
        path=path,
        evidence_resolution=evidence_resolution,
    )
    send_health_alert(webhook_url, message)


@log_job
def run_health_check_sweep_job() -> None:
    settings = get_settings()
    dsn = settings.database_url

    for target in list_health_check_targets_all(dsn):
        installation_id = target["installation_id"]
        repo_full_name = target["repo_full_name"]
        target_id = target["target_id"]
        base_url = target["base_url"]
        threshold_ms = target["latency_threshold_ms"]

        try:
            _run_health_check_sweep_for_target(
                dsn, target, installation_id, repo_full_name, target_id, base_url, threshold_ms
            )
        except Exception as exc:  # noqa: BLE001
            # One customer's dead webhook URL, an unreachable target, or any
            # other failure here must not take down the sweep for every
            # other installation - this loop runs every
            # HEALTH_SWEEP_INTERVAL_SECONDS for the whole paying customer
            # base, so one bad target skipping its own cycle is far
            # preferable to all of them silently going stale.
            logging.getLogger("scan_worker.jobs").warning(
                "health check sweep failed for installation=%s repo=%s target=%s (%s)",
                installation_id,
                repo_full_name,
                target_id,
                type(exc).__name__,
                exc_info=True,
            )


def _run_health_check_sweep_for_target(
    dsn: str,
    target: dict,
    installation_id: int,
    repo_full_name: str,
    target_id: int,
    base_url: str,
    threshold_ms: int | None,
) -> None:
    # validate_external_https_url only ever ran once, when the target was
    # saved (admin.py) - re-checking here, immediately before every fetch,
    # closes the DNS-rebinding window down to the gap between this call and
    # the actual request instead of "until someone edits the target again."
    # A customer could otherwise register a domain that resolves to a public
    # IP at save time, pass validation, then repoint DNS at an internal
    # service or cloud metadata endpoint before the next sweep - whose
    # response would then get echoed back to that customer's own dashboard
    # via response_shape.
    try:
        validate_external_https_url(base_url)
    except UnsafeURLError as exc:
        logging.getLogger("scan_worker.jobs").warning(
            "skipping health check for installation=%s repo=%s target=%s - %s",
            installation_id,
            repo_full_name,
            target_id,
            exc,
        )
        return

    evidence = get_latest_evidence(dsn, installation_id, repo_full_name)
    if evidence is None:
        return

    for entry in _endpoint_results(evidence, base_url):
        if entry.get("skipped"):
            continue
        method = entry["method"]
        path = entry["path"]
        source_file = entry.get("file")
        source_line = entry.get("line")
        evidence_resolution = entry.get("evidence_resolution")
        reachable = entry["reachable"]
        status_code = entry.get("status_code")
        latency_ms = entry.get("latency_ms")
        response_shape = entry.get("response_shape")
        prior = get_last_endpoint_health(
            dsn,
            installation_id,
            repo_full_name,
            method,
            path,
            target_id=target_id,
        )

        reachability_flipped = (prior is None and not reachable) or (
            prior is not None and prior.get("reachable") != reachable
        )

        if reachability_flipped and not reachable:
            for _ in range(HEALTH_CHECK_DOWN_RETRY_ATTEMPTS):
                time.sleep(HEALTH_CHECK_DOWN_RETRY_DELAY_SECONDS)
                retry_result = _recheck_single_endpoint(entry, base_url)
                if retry_result.get("reachable"):
                    reachable = True
                    status_code = retry_result.get("status_code")
                    latency_ms = retry_result.get("latency_ms")
                    response_shape = retry_result.get("response_shape")
                    break
            reachability_flipped = (prior is None and not reachable) or (
                prior is not None and prior.get("reachable") != reachable
            )

        if reachability_flipped:
            if not reachable and source_file:
                evidence_resolution = _attach_recent_commit_for_failure(
                    installation_id,
                    repo_full_name,
                    source_file,
                    evidence_resolution,
                    evidence,
                    method=method,
                    path=path,
                    status_code=status_code,
                    source_line=source_line,
                )
            _send_if_webhook_configured(
                target,
                format_reachability_alert(
                    repo_full_name,
                    method,
                    path,
                    source_file,
                    source_line,
                    reachable,
                    evidence_resolution=evidence_resolution,
                ),
            )

        if _latency_flipped(prior, reachable, latency_ms, threshold_ms):
            _send_if_webhook_configured(
                target,
                format_latency_alert(
                    repo_full_name,
                    method,
                    path,
                    source_file,
                    source_line,
                    latency_ms,
                    threshold_ms,
                    latency_ms > threshold_ms,
                    evidence_resolution=evidence_resolution,
                ),
            )

        shape_changed = (
            reachable
            and not reachability_flipped
            and prior is not None
            and prior.get("reachable") is True
            and prior.get("response_shape") is not None
            and response_shape is not None
            and prior["response_shape"] != response_shape
        )
        if shape_changed:
            _send_if_webhook_configured(
                target,
                format_shape_change_alert(
                    repo_full_name,
                    method,
                    path,
                    source_file,
                    source_line,
                    prior["response_shape"],
                    response_shape,
                    evidence_resolution=evidence_resolution,
                ),
            )

        insert_endpoint_health(
            dsn,
            installation_id,
            repo_full_name,
            method,
            path,
            reachable,
            status_code,
            latency_ms,
            response_shape=response_shape,
            target_id=target_id,
        )


@log_job
def run_session_cleanup_job() -> None:
    dsn = get_settings().database_url
    deleted = delete_expired_sessions(dsn)
    logging.getLogger("scan_worker.jobs").info(
        "session cleanup completed", extra={"deleted_count": deleted}
    )


def _live_wiki_naming_adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=live_wiki.FLASH_MODEL,
    )


def _live_wiki_full_build_writing_adapter(plan: str) -> OpenAICompatibleAdapter | AnthropicAdapter:
    # The one-time full build uses the same model as managed audits (see
    # model_tiers.py) - always DeepSeek Pro, for every plan.
    return writing_adapter_for_plan(plan)


def _live_wiki_update_writing_adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=live_wiki.UPDATE_MODEL,
    )


def _real_line_count_fetcher(
    installation_id: int, repo_full_name: str, ref: str | None
) -> Callable[[str], int | None] | None:
    """Backs generate_subsystems/generate_overview's fetch_line_count
    param with a real GitHub Contents API lookup, closing the same
    documented citation-verification gap fixed in flash_review.py and
    citation_verifier.py: without this, a citation naming a real file but
    a fabricated line number is reported as verified. Degrades to None
    (falls back to file-existence-only verification) on any setup
    failure - this is a verification enhancement, never allowed to break
    wiki generation itself.
    """
    try:
        settings = get_settings()
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        client = httpx.Client(base_url="https://api.github.com")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "could not set up line-count fetcher (%s); citations checked for file existence only",
            type(exc).__name__,
        )
        return None

    def _fetch_line_count(path: str) -> int | None:
        try:
            content = fetch_file_content(client, token, repo_full_name, path, ref)
        except Exception:  # noqa: BLE001
            return None
        if content is None:
            return None
        return len(content.splitlines())

    return _fetch_line_count


def _store_wiki_generation(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    evidence: dict,
    fresh_records: list[dict],
    writing_adapter,
    source_commit: str | None,
    fetch_line_count: Callable[[str], int | None] | None = None,
) -> None:
    """Upserts freshly-generated subsystem records, prunes any subsystem
    whose cluster no longer exists in the current evidence at all, then
    regenerates the overview from the full current set (fresh records
    merged with whatever was already stored for subsystems untouched by
    this run).
    """
    for record in fresh_records:
        upsert_wiki_subsystem(
            dsn,
            installation_id,
            repo_full_name,
            record["subsystem_id"],
            record["name"],
            record["description"],
            record["files"],
            record["diagram_mermaid"],
            source_commit,
        )

    current_cluster_ids = [str(c["id"]) for c in evidence.get("architecture", {}).get("clusters", [])]
    delete_wiki_subsystems_not_in(dsn, installation_id, repo_full_name, current_cluster_ids)

    all_records = {r["subsystem_id"]: r for r in list_wiki_subsystems(dsn, installation_id, repo_full_name)}
    for record in fresh_records:
        all_records[record["subsystem_id"]] = record
    if not all_records:
        return

    overview = live_wiki.generate_overview(
        evidence, list(all_records.values()), writing_adapter, fetch_line_count=fetch_line_count
    )
    upsert_wiki_overview(
        dsn, installation_id, repo_full_name, overview["description"], overview["diagram_mermaid"], source_commit
    )


@log_job
def run_live_wiki_full_build_job(installation_id: int, repo_full_name: str) -> None:
    dsn = get_settings().database_url
    evidence = get_latest_evidence(dsn, installation_id, repo_full_name)
    if evidence is None:
        return  # nothing scanned for this repo yet - nothing to build from

    installation = get_installation_row(dsn, installation_id)
    plan = installation["plan"] if installation is not None else "air"
    model_used = model_for_plan(plan)

    try:
        naming_adapter = _live_wiki_naming_adapter()
        writing_adapter = _live_wiki_full_build_writing_adapter(plan)
        fetch_line_count = _real_line_count_fetcher(installation_id, repo_full_name, None)
        records = live_wiki.generate_subsystems(
            evidence,
            naming_adapter,
            writing_adapter,
            cache_lookup=lambda packet: lookup_cached_result(dsn, installation_id, repo_full_name, packet),
            cache_write=lambda packet, output, used: store_result(
                dsn, installation_id, repo_full_name, packet, output, used
            ),
            model_used=model_used,
            fetch_line_count=fetch_line_count,
        )
        _store_wiki_generation(
            dsn, installation_id, repo_full_name, evidence, records, writing_adapter, None,
            fetch_line_count=fetch_line_count,
        )
    except Exception as exc:  # noqa: BLE001
        # Without this, a failed build (LLM error, DB error) just leaves the
        # AIRview page permanently blank with no way for the customer to
        # tell "still building" apart from "broke and is never coming back".
        logging.getLogger("scan_worker.jobs").warning(
            "live wiki full build failed for installation=%s repo=%s (%s)",
            installation_id, repo_full_name, exc,
        )
        set_wiki_build_status(dsn, installation_id, repo_full_name, "failed", str(exc))
        return
    set_wiki_build_status(dsn, installation_id, repo_full_name, "ready")


@log_job
def _scans_queue(redis_url: str):
    from redis import Redis
    from rq import Queue

    return Queue("scans", connection=Redis.from_url(redis_url))


def run_live_wiki_full_build_for_installation_job(installation_id: int) -> None:
    """Fans out one full-build job per repo, rather than looping in
    process, so one slow or failing repo can't consume the whole
    installation's build budget or block the others.
    """
    settings = get_settings()
    queue = _scans_queue(settings.redis_url)
    for repo_full_name in list_repos_for_installation(settings.database_url, installation_id):
        queue.enqueue(
            "scan_worker.jobs.run_live_wiki_full_build_job",
            job_timeout=LIVE_WIKI_FULL_BUILD_JOB_TIMEOUT_SECONDS,
            installation_id=installation_id,
            repo_full_name=repo_full_name,
        )


def _maybe_update_live_wiki(
    installation_id: int, repo_full_name: str, evidence: dict, changed_files: list[str], head_sha: str
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    cluster_ids = live_wiki.affected_cluster_ids(evidence, changed_files)
    if not cluster_ids:
        return

    dsn = settings.database_url
    try:
        naming_adapter = _live_wiki_naming_adapter()
        writing_adapter = _live_wiki_update_writing_adapter()
        fetch_line_count = _real_line_count_fetcher(installation_id, repo_full_name, head_sha)
        records = live_wiki.generate_subsystems(
            evidence,
            naming_adapter,
            writing_adapter,
            cluster_ids=cluster_ids,
            cache_lookup=lambda packet: lookup_cached_result(dsn, installation_id, repo_full_name, packet),
            cache_write=lambda packet, output, used: store_result(
                dsn, installation_id, repo_full_name, packet, output, used
            ),
            model_used=live_wiki.UPDATE_MODEL,
            fetch_line_count=fetch_line_count,
        )
        _store_wiki_generation(
            dsn, installation_id, repo_full_name, evidence, records, writing_adapter, head_sha,
            fetch_line_count=fetch_line_count,
        )
    except Exception as exc:  # noqa: BLE001
        # Without this, a failed incremental update just leaves stale
        # content in place with zero signal - the customer (and
        # wiki_build_status) has no way to tell "stale" apart from
        # "current", especially once a first full build has already
        # succeeded and populated an overview.
        logging.getLogger("scan_worker.jobs").warning(
            "live wiki incremental update failed for installation=%s repo=%s (%s)",
            installation_id, repo_full_name, exc,
        )
        set_wiki_build_status(dsn, installation_id, repo_full_name, "failed", str(exc))
        return
    set_wiki_build_status(dsn, installation_id, repo_full_name, "ready")
