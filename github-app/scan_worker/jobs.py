import asyncio
import inspect
import json
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.evidence import write_evidence
from aletheore.evidence_resolution import resolve_code_evidence
from aletheore.history import compute_diff
from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment
from aletheore.healthcheck import run_healthcheck
from app_server.config import get_settings
from app_server.github_auth import generate_app_jwt, get_installation_token
from app_server.llm_cost import cost_for_usage, monthly_cap_for_installation
from app_server.logging_config import log_job
from app_server.rate_limit import cooldown_seconds_for_loc, total_loc_from_evidence
from scan_worker import live_wiki
from scan_worker.db import (
    check_and_reserve_flash_review_attempt,
    check_and_reserve_managed_audit,
    delete_expired_sessions,
    delete_wiki_subsystems_not_in,
    get_extra_seats,
    get_installation as get_installation_row,
    get_last_endpoint_health,
    get_last_reviewed_sha,
    get_latest_evidence,
    get_llm_spend_this_month,
    insert_endpoint_health,
    insert_repo_history,
    installation_spend_lock,
    list_health_check_targets_all,
    list_repos_for_installation,
    list_wiki_subsystems,
    record_llm_spend,
    set_last_reviewed_sha,
    upsert_wiki_overview,
    upsert_wiki_subsystem,
)
from scan_worker.flash_review import build_code_evidence_context, gather_file_context, review_diff
from scan_worker.github_api import create_check_run, fetch_pr_changed_files, fetch_pr_diff, upsert_pr_comment
from scan_worker.managed_audit import run_managed_audit
from scan_worker.model_tiers import model_for_plan, writing_adapter_for_plan
from scan_worker.packet_cache import lookup_cached_result, store_result
from scan_worker.slack import (
    format_latency_alert,
    format_reachability_alert,
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


def _job_temp_dir() -> Path:
    path = JOBS_ROOT / str(uuid.uuid4())
    path.mkdir(parents=True, exist_ok=False)
    return path


def _clone_url(repo_full_name: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"


def _clone_ref(url: str, ref: str, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(["git", "checkout", "-q", ref], cwd=dest, check=True)


def _run_scan(repo_dir: Path) -> Path:
    subprocess.run(["aletheore", "scan", str(repo_dir)], check=True)
    return repo_dir / ".aletheore" / "air.json"


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


@log_job
def run_pr_scan_job(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)

        clone_url = _clone_url(repo_full_name, token)
        base_dir = job_dir / "base"
        head_dir = job_dir / "head"
        _clone_ref(clone_url, base_sha, base_dir)
        _clone_ref(clone_url, head_sha, head_dir)

        base_evidence_path = _run_scan(base_dir)
        head_evidence_path = _run_scan(head_dir)
        old = json.loads(base_evidence_path.read_text())
        new = json.loads(head_evidence_path.read_text())
        diff = compute_diff(old, new, full=False)

        client = httpx.Client(base_url="https://api.github.com")
        upsert_pr_comment(client, token, repo_full_name, pr_number, format_diff_comment(diff))
        _insert_history(installation_id, repo_full_name, new)

        # These are side effects, not the primary deliverable above - a failure in
        # either (e.g. a missing Slack webhook or missing Checks permission) must
        # not fall through to the outer except, which would overwrite the diff
        # comment we already posted with a generic failure message.
        try:
            _maybe_send_slack_alert(installation_id, repo_full_name, pr_number, diff)
        except Exception:  # noqa: BLE001
            pass
        try:
            _maybe_create_check_run(client, token, repo_full_name, head_sha, installation_id, diff)
        except Exception:  # noqa: BLE001
            pass
        try:
            changed_files = fetch_pr_changed_files(client, token, repo_full_name, base_sha, head_sha)
            _maybe_update_live_wiki(installation_id, repo_full_name, new, changed_files, head_sha)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
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


@log_job
def run_managed_audit_pr_job(installation_id: int, repo_full_name: str, pr_number: int) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    installation = get_installation_row(settings.database_url, installation_id)
    plan = installation["plan"] if installation is not None else "indie"
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
                monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
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
def run_managed_audit_api_job(installation_id: int, evidence: dict | str) -> str:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    plan = installation["plan"] if installation is not None else "indie"
    with installation_spend_lock(settings.database_url, installation_id):
        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
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

    if not check_and_reserve_flash_review_attempt(
        settings.database_url, installation_id, repo_full_name, pr_number
    ):
        return

    with installation_spend_lock(settings.database_url, installation_id):
        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
        current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
        if current_spend >= monthly_cap:
            return

        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        client = httpx.Client(base_url="https://api.github.com")

        last_reviewed_sha = get_last_reviewed_sha(
            settings.database_url, installation_id, repo_full_name, pr_number
        )
        diff_base = last_reviewed_sha or base_sha
        diff_text = fetch_pr_diff(client, token, repo_full_name, diff_base, head_sha)
        changed_files = fetch_pr_changed_files(client, token, repo_full_name, diff_base, head_sha)
        file_context = gather_file_context(client, token, repo_full_name, changed_files, head_sha)
        evidence = _latest_evidence_or_none(settings.database_url, installation_id, repo_full_name)
        code_evidence_context = build_code_evidence_context(evidence, changed_files)

        spend_accumulator = {"total": 0.0}

        def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
            spend_accumulator["total"] += cost_for_usage(
                "deepseek-v4-flash", prompt_tokens, completion_tokens
            )

        if code_evidence_context:
            findings = review_diff(
                diff_text,
                file_context=file_context,
                code_evidence_context=code_evidence_context,
                on_usage=_on_usage,
            )
        else:
            findings = review_diff(diff_text, file_context=file_context, on_usage=_on_usage)
        record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])

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

        evidence = get_latest_evidence(dsn, installation_id, repo_full_name)
        if evidence is None:
            continue

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
            if reachability_flipped:
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

            insert_endpoint_health(
                dsn,
                installation_id,
                repo_full_name,
                method,
                path,
                reachable,
                status_code,
                latency_ms,
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
    # The one-time full build uses the same tier model as managed audits
    # (see model_tiers.py) rather than a fixed model - an Indie repo's
    # first AIRview build is written by the same DeepSeek model as its
    # ongoing updates, an Enterprise repo's by Claude Opus. Falls back
    # toward DeepSeek if a higher tier's provider key isn't configured
    # yet, so a build never hard-fails on missing infra.
    return writing_adapter_for_plan(plan)


def _live_wiki_update_writing_adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=live_wiki.UPDATE_MODEL,
    )


def _store_wiki_generation(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    evidence: dict,
    fresh_records: list[dict],
    writing_adapter,
    source_commit: str | None,
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

    overview = live_wiki.generate_overview(evidence, list(all_records.values()), writing_adapter)
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
    plan = installation["plan"] if installation is not None else "indie"
    model_used = model_for_plan(plan)

    naming_adapter = _live_wiki_naming_adapter()
    writing_adapter = _live_wiki_full_build_writing_adapter(plan)
    records = live_wiki.generate_subsystems(
        evidence,
        naming_adapter,
        writing_adapter,
        cache_lookup=lambda packet: lookup_cached_result(dsn, installation_id, repo_full_name, packet),
        cache_write=lambda packet, output, used: store_result(
            dsn, installation_id, repo_full_name, packet, output, used
        ),
        model_used=model_used,
    )
    _store_wiki_generation(dsn, installation_id, repo_full_name, evidence, records, writing_adapter, None)


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
    naming_adapter = _live_wiki_naming_adapter()
    writing_adapter = _live_wiki_update_writing_adapter()
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
    )
    _store_wiki_generation(
        settings.database_url, installation_id, repo_full_name, evidence, records, writing_adapter, head_sha
    )
