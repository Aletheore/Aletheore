import hashlib
import json
import os
import warnings
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from aletheore import __version__
from aletheore.air_schema import validate_evidence
from aletheore.architecture import build_clusters, detect_layer_violations, load_architecture_config
from aletheore.dead_code import find_dead_code
from aletheore.endpoints import map_api_endpoints
from aletheore.evidence_resolution import find_symbol_at_location
from aletheore.git_intel.analyzer import analyze_git, compute_hotspots
from aletheore.licenses import check_dependency_licenses
from aletheore.repo_config import load_repo_config
from aletheore.schema_map import extract_schema, skipped_schema
from aletheore.scanner.detect import (
    _nested_git_roots,
    detect_ai_usage,
    detect_build_tools,
    detect_database,
    detect_environment_variables,
    detect_frameworks,
    detect_infrastructure,
    detect_languages,
    detect_monorepo,
    detect_policy_docs,
)
from aletheore.scanner.graph import build_module_graph
from aletheore.secrets import (
    DEFAULT_SECRETS_HISTORY_TIMEOUT_SECONDS,
    find_secrets,
    find_secrets_in_history,
    load_secrets_baseline,
)
from aletheore.toon_encoding import ToonEncodingError, to_toon
from aletheore.vulnerabilities import check_vulnerabilities as check_dependency_vulnerabilities

EVIDENCE_VERSION = "0.5.0"


def _version_compatibility_key(version: str) -> tuple[int, int] | None:
    """(major, minor) of a version string, or None if it can't be parsed.

    Pre-1.0 (major == 0), semver's own convention treats a MINOR bump as
    the potential breaking change, not just MAJOR - 0.1.0 and 0.1.7 are
    the same schema, 0.1.0 and 0.2.0 are not assumed to be. Once this
    project ships EVIDENCE_VERSION >= 1.0.0, this should move to
    comparing MAJOR alone; not needed yet since nothing written has ever
    been >= 1.0.
    """
    parts = version.split(".")
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def is_evidence_version_compatible(version: object) -> bool:
    """Whether an air.json's recorded aletheore_version matches this
    build's schema closely enough to read safely.

    EVIDENCE_VERSION is written into every scan (see scan_repository below)
    but, until this check, nothing ever read it back - a schema change
    between the version that wrote an air.json and the version reading it
    could silently misread or KeyError deep in a consumer instead of
    failing with a clear "re-scan" message at the one place that already
    knows both versions.
    """
    if not isinstance(version, str):
        return False
    current = _version_compatibility_key(EVIDENCE_VERSION)
    given = _version_compatibility_key(version)
    return current is not None and current == given


class IncompatibleEvidenceVersionError(Exception):
    pass


class MalformedEvidenceError(Exception):
    pass


def load_evidence_file(evidence_path: Path) -> dict:
    """Reads and validates a raw air.json or snapshot file, refusing evidence
    written by an incompatible schema version or with the wrong shape.

    Every direct reader of an evidence file (CLI query/index/diff/healthcheck,
    the MCP server) should route through this or load_evidence rather than a
    bare json.loads - see is_evidence_version_compatible for why.

    Two distinct failures are possible and they need different messages. A
    version mismatch means "this file is fine, your build is different" and
    re-scanning fixes it. A schema violation on a *compatible* version means
    the file is truncated, hand-edited, or not AIR at all - re-scanning may
    not help, and the caller needs to know which key is wrong rather than
    discovering it as a KeyError three modules away.
    """
    evidence = json.loads(evidence_path.read_text())
    written_version = evidence.get("aletheore_version") if isinstance(evidence, dict) else None
    if not is_evidence_version_compatible(written_version):
        raise IncompatibleEvidenceVersionError(
            f"{evidence_path} was written by aletheore_version={written_version!r}, which "
            f"isn't compatible with this build's evidence schema ({EVIDENCE_VERSION}) - "
            "re-run 'aletheore scan' to refresh it"
        )
    problems = validate_evidence(evidence)
    if problems:
        detail = "; ".join(problems[:5])
        if len(problems) > 5:
            detail += f" (and {len(problems) - 5} more)"
        raise MalformedEvidenceError(
            f"{evidence_path} claims a compatible aletheore_version but does not match the "
            f"AIR schema: {detail} - re-run 'aletheore scan' to regenerate it"
        )
    return evidence


def load_evidence(repo_path: Path) -> dict:
    """The repo's own .aletheore/air.json, validated for schema compatibility.

    Raises FileNotFoundError if no evidence has been written yet.
    """
    evidence_path = repo_path / ".aletheore" / "air.json"
    if not evidence_path.exists():
        raise FileNotFoundError(
            f"no evidence found at {evidence_path} - run 'aletheore scan {repo_path}' first"
        )
    return load_evidence_file(evidence_path)

# Unset by default - a developer scanning their own repo locally wants full
# history, and isn't running inside a memory-constrained container. The
# hosted scan-worker sets this (see scan_worker/jobs.py's
# GRAPH_COLD_SYNC_DEPTH_CAP) before invoking `aletheore scan` as a
# subprocess, so a customer's very first scan of an oversized repo (e.g.
# torvalds/linux scale) can't OOM this call before persistence-layer code
# even runs - reproduced directly in a container at the same 1GB limit as
# that worker.
_GIT_HISTORY_DEPTH_CAP_ENV = "ALETHEORE_GIT_HISTORY_DEPTH_CAP"


def _git_history_depth_cap() -> int | None:
    raw = os.environ.get(_GIT_HISTORY_DEPTH_CAP_ENV)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# Separate env var from the git-graph cap above: `git log -p` (full unified
# diffs) is far more expensive per commit than the graph engine's
# `--name-only` walk - confirmed by direct measurement, ~2s/1.4MB per 1000
# commits, meaning a repo at torvalds/linux's scale would take git itself
# ~50 minutes and stream over 2GB of diff text regardless of memory
# bounding. A hosted PR scan can't spend that long on every run, so this
# gets its own, independently tunable cap rather than reusing the graph
# engine's value.
_SECRETS_HISTORY_DEPTH_CAP_ENV = "ALETHEORE_SECRETS_HISTORY_DEPTH_CAP"

# Independent of the depth cap above: this is a wall-clock safety valve for
# when git itself stalls mid-read (e.g. blob reads blocking on a slow or
# network-backed filesystem) rather than a way to bound expected cost -
# reproduced directly (see secrets.py's find_secrets_in_history) as a scan
# that hung for 7+ minutes at ~0% CPU with no user-visible feedback. Always
# on, even for an uncapped local scan, since "no timeout at all" turns a
# slow environment into an indefinite hang rather than a slow-but-bounded
# wait.
_SECRETS_HISTORY_TIMEOUT_SECONDS_ENV = "ALETHEORE_SECRETS_HISTORY_TIMEOUT_SECONDS"


def _secrets_history_timeout_seconds() -> float:
    raw = os.environ.get(_SECRETS_HISTORY_TIMEOUT_SECONDS_ENV)
    if not raw:
        return DEFAULT_SECRETS_HISTORY_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_SECRETS_HISTORY_TIMEOUT_SECONDS

# Unset by default - true incremental scanning needs a persistent, kept-up-
# to-date local checkout to diff against (a fresh clone has no "last time"
# to compare to), which only the hosted scan-worker maintains. Points at a
# JSON file: {"modules": {<path>: <build_module_graph module dict>},
# "endpoints": {<path>: [<endpoint dict>, ...]}} for files the worker has
# determined are unchanged since their data was last computed - see
# scan_worker/jobs.py for how that file gets built and where it points.
_UNCHANGED_SCAN_CACHE_ENV = "ALETHEORE_UNCHANGED_SCAN_CACHE"

# Set unconditionally by every hosted scan-worker subprocess (see
# scan_worker/jobs.py's _run_scan) to opt fully out of the local,
# content-hash-keyed scan cache below. That cache is only safe when the
# person reading it also controls (or trusts) the checkout it lives in -
# a plain local `aletheore scan` on your own working copy. On the hosted
# path, the checkout is someone else's repo: they can commit both a file
# AND a matching .aletheore/scan-cache.json entry whose cached "parse
# result" says whatever they want, since the hash has no secret and the
# scanner never re-parses on a hit. It also provides zero legitimate
# caching benefit there anyway - every hosted scan clones a fresh
# checkout that gets deleted afterward, so nothing written ever survives
# to the next scan except what an attacker deliberately committed.
_DISABLE_LOCAL_SCAN_CACHE_ENV = "ALETHEORE_DISABLE_LOCAL_SCAN_CACHE"


def _load_unchanged_scan_cache() -> tuple[dict[str, dict] | None, dict[str, list[dict]] | None]:
    raw_path = os.environ.get(_UNCHANGED_SCAN_CACHE_ENV)
    if not raw_path:
        return None, None
    try:
        data = json.loads(Path(raw_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    return data.get("modules"), data.get("endpoints")


# The hosted worker's cache above requires the persistent-checkout diffing
# only it maintains - a plain local `aletheore scan` had no incremental
# path at all, always re-parsing every file from scratch even when nothing
# changed since the last run. This is a fully self-contained equivalent:
# written by every scan of a given repo, read by the next one. Keyed by
# each file's own content hash rather than mtime - a git checkout that
# touches mtimes without changing content (switching branches back and
# forth) shouldn't cause a false cache miss.
_LOCAL_SCAN_CACHE_FILENAME = "scan-cache.json"


def _local_scan_cache_path(repo_path: Path) -> Path:
    return repo_path / ".aletheore" / _LOCAL_SCAN_CACHE_FILENAME


_HASH_CHUNK_BYTES = 1024 * 1024


def _hash_file(path: Path) -> str | None:
    # Streamed rather than path.read_bytes() - this runs once per module on
    # every scan (cache-hit check), on the shared scan-worker where an
    # unusually large committed file (a data dump, a vendored bundle) read
    # in full would risk OOMing the container for every installation's scan
    # running alongside it. Chunked hashing keeps memory bounded regardless
    # of file size instead of skipping large files outright, so caching
    # still works for them.
    try:
        hasher = hashlib.blake2b(digest_size=16)
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _load_local_scan_cache(
    repo_path: Path,
) -> tuple[dict[str, dict] | None, dict[str, list[dict]] | None]:
    cache_path = _local_scan_cache_path(repo_path)
    if not cache_path.exists():
        return None, None
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None

    # A content hash alone says nothing about whether the *code* that
    # produced a cached parse result is still today's code - a scanner
    # upgrade that changes parsing/detection logic without touching the
    # scanned file's own bytes would otherwise silently keep serving the
    # old version's (possibly wrong) results forever. Treating any
    # version mismatch (including a pre-this-fix cache with no version key
    # at all) the same as a missing cache file forces a full re-parse,
    # which then overwrites the cache with a correctly-stamped one.
    if cache.get("aletheore_version") != __version__:
        return None, None

    cached_hashes = cache.get("hashes", {})
    cached_modules = cache.get("modules", {})
    cached_endpoints = cache.get("endpoints", {})

    unchanged_modules: dict[str, dict] = {}
    unchanged_endpoints: dict[str, list[dict]] = {}
    for rel_path, cached_hash in cached_hashes.items():
        if _hash_file(repo_path / rel_path) != cached_hash:
            continue
        if rel_path in cached_modules:
            unchanged_modules[rel_path] = cached_modules[rel_path]
        if rel_path in cached_endpoints:
            unchanged_endpoints[rel_path] = cached_endpoints[rel_path]

    return (unchanged_modules or None), (unchanged_endpoints or None)


def _write_local_scan_cache(
    repo_path: Path, modules: list[dict], api_endpoints: list[dict]
) -> None:
    hashes: dict[str, str] = {}
    modules_by_path: dict[str, dict] = {}
    for module in modules:
        rel_path = module["path"]
        modules_by_path[rel_path] = module
        file_hash = _hash_file(repo_path / rel_path)
        if file_hash is not None:
            hashes[rel_path] = file_hash

    endpoints_by_path: dict[str, list[dict]] = {}
    for endpoint in api_endpoints:
        file_path = endpoint.get("file")
        if file_path is None:
            continue
        endpoints_by_path.setdefault(file_path, []).append(endpoint)
        if file_path not in hashes:
            file_hash = _hash_file(repo_path / file_path)
            if file_hash is not None:
                hashes[file_path] = file_hash

    cache_path = _local_scan_cache_path(repo_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "aletheore_version": __version__,
                    "hashes": hashes,
                    "modules": modules_by_path,
                    "endpoints": endpoints_by_path,
                }
            )
        )
    except OSError:
        # Best-effort: a failure to write the cache (disk full, permissions)
        # must never fail the scan itself - it only costs the next run its
        # incremental speedup, not correctness.
        pass


def _secrets_history_depth_cap() -> int | None:
    raw = os.environ.get(_SECRETS_HISTORY_DEPTH_CAP_ENV)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _noop_progress(_message: str) -> None:
    pass


def _license_progress_reporter(
    report: Callable[[str], None],
) -> Callable[[int, int, str], None]:
    def on_progress(current: int, total: int, name: str) -> None:
        report(f"Checking dependency licenses: {current}/{total} ({name})")

    return on_progress


def scan_repository(
    repo_path: Path,
    check_vulnerabilities: bool = True,
    scan_git_history: bool = True,
    check_licenses: bool = True,
    map_endpoints: bool = True,
    map_schema: bool = True,
    # Why a caller-supplied reason rather than a fixed literal: this section
    # is skipped for two unrelated causes - an explicit --no-map-schema, or
    # an installation without the entitlement - and a reader who sees
    # `checked: false` needs to know which. Keeping the string out here
    # leaves evidence.py with no knowledge of plans or authentication,
    # matching every other check it runs.
    map_schema_skip_reason: str = "skipped (--no-map-schema)",
    progress: Callable[[str], None] | None = None,
) -> dict:
    report = progress or _noop_progress
    repo_path = repo_path.resolve()
    ignored_paths = load_repo_config(repo_path)["ignored_paths"]

    # _nested_git_roots is @lru_cache'd for the life of the process, not one
    # scan - a long-lived scan_worker reuses the same on-disk checkout_dir
    # across many scans of different commits, and a linked worktree or
    # submodule can appear or disappear between two of them. Clearing here
    # keeps the within-scan caching win (it's still called up to 6 times per
    # scan) without carrying a stale answer into the next scan of this path.
    _nested_git_roots.cache_clear()

    # Must run before any file-counting walk below (detect_languages is the
    # first one), not after the scan in write_evidence as before - creating
    # or appending to .gitignore here is itself a countable file-system
    # change, and doing it post-scan meant THIS scan's counts (e.g.
    # scanned_files) reflected the repo's pre-mutation state while the NEXT
    # scan of the same, otherwise-untouched repo reflected the post-mutation
    # state - a real repo scanned twice in a row, without a single file of
    # its own changing, reported different numbers (22 -> 23 -> 23,
    # confirmed empirically). Running it first makes every scan - including
    # the very first one - already reflect whatever .gitignore state results,
    # so counts are stable and reproducible from the first call onward.
    _ensure_aletheore_dir_gitignored(repo_path)

    report("Detecting languages, frameworks, and build tools")
    languages = detect_languages(repo_path, ignored_paths)
    frameworks = detect_frameworks(repo_path)
    ai_usage = detect_ai_usage(repo_path)
    policy_docs = detect_policy_docs(repo_path)
    build_tools = detect_build_tools(repo_path)
    monorepo = detect_monorepo(repo_path)
    database = detect_database(repo_path)
    infrastructure = detect_infrastructure(repo_path)
    environment_variables = detect_environment_variables(repo_path)

    # The hosted worker's own cache (env var) takes priority when set; a
    # plain local `aletheore scan` has no such env var, so it falls back to
    # the CLI's own self-contained, content-hash-keyed cache from the
    # previous scan of this repo.
    using_hosted_cache = bool(os.environ.get(_UNCHANGED_SCAN_CACHE_ENV))
    local_cache_disabled = bool(os.environ.get(_DISABLE_LOCAL_SCAN_CACHE_ENV))
    unchanged_modules, unchanged_endpoints = _load_unchanged_scan_cache()
    if not using_hosted_cache and not local_cache_disabled:
        unchanged_modules, unchanged_endpoints = _load_local_scan_cache(repo_path)
        # Surfaced here, not left implicit: a CLI user who only ever sees
        # "Scanning..." has no way to know a scan is cached at all, so a
        # fast repeat scan reads as suspicious (did it actually check
        # everything?) rather than as the cache working as intended.
        if unchanged_modules:
            report(
                f"Reusing cached results for {len(unchanged_modules)} unchanged "
                "file(s) from the last scan of this repo"
            )
        else:
            report(
                "No previous scan cache found for this repo - this first scan "
                "will take longer. Results are cached automatically, so future "
                "scans only re-parse files that actually changed."
            )

    report("Building module dependency graph (parsing source with tree-sitter)")
    modules, dependency_graph, unparseable_files = build_module_graph(
        repo_path, unchanged_modules=unchanged_modules, ignored_paths=ignored_paths
    )

    report("Analyzing git history and ownership")
    git_data = analyze_git(repo_path, modules, depth_cap=_git_history_depth_cap())

    report("Scanning working tree for secrets")
    secrets_baseline = load_secrets_baseline(repo_path)
    secrets_data = find_secrets(repo_path, baseline=secrets_baseline)
    # Symbol attribution only applies to the working-tree findings above, not
    # the history_findings merged in below: those have no line number (a
    # historical diff hunk, not a location in the current tree) and, even if
    # they did, this scan's module graph reflects the CURRENT working tree -
    # attaching today's symbol name to a bygone commit's line would describe
    # code that may have since moved, been renamed, or been deleted.
    _symbol_evidence = {"repository": {"modules": modules}}
    for finding in secrets_data["findings"]:
        finding["symbol"] = find_symbol_at_location(_symbol_evidence, finding["path"], finding["line"])
    if scan_git_history:
        report("Scanning git history for secrets (can be slow on large histories)")
        history_data = find_secrets_in_history(
            repo_path,
            baseline=secrets_baseline,
            max_commits=_secrets_history_depth_cap(),
            timeout_seconds=_secrets_history_timeout_seconds(),
        )
        if history_data.get("history_scan_timed_out"):
            report(
                "Secrets history scan timed out before finishing - findings above "
                "reflect only the commits reached before the timeout"
            )
    else:
        history_data = {"history_scanned_commits": 0, "history_findings": []}
    secrets_data = {**secrets_data, **history_data}

    report("Clustering modules and checking layer conventions")
    architecture_config = load_architecture_config(repo_path)
    resolution = architecture_config["cluster_resolution"] if architecture_config else 1.0
    custom_markers = architecture_config["layer_markers"] if architecture_config else None
    clusters, cross_cluster_edges = build_clusters(dependency_graph, resolution=resolution)
    layer_violations = detect_layer_violations(dependency_graph, custom_markers=custom_markers)

    report("Detecting dead code")
    dead_code_data = find_dead_code(repo_path, modules, architecture_config, ignored_paths)

    if git_data.get("available"):
        report("Computing git hotspots")
        git_data["hotspots"] = compute_hotspots(repo_path, modules)

    if check_vulnerabilities:
        report("Checking dependencies for known vulnerabilities (OSV.dev)")
        vulnerabilities_data = check_dependency_vulnerabilities(repo_path)
    else:
        vulnerabilities_data = {
            "checked": False,
            "reason": "skipped (--no-check-vulnerabilities)",
            "findings": [],
        }

    if check_licenses:
        report("Checking dependency licenses (one registry lookup per pinned dependency)")
        licenses_data = check_dependency_licenses(
            repo_path, on_progress=_license_progress_reporter(report)
        )
    else:
        licenses_data = {
            "checked": False,
            "reason": "skipped (--no-check-licenses)",
            "repo_license": {"category": "unknown", "detected_from": None},
            "findings": [],
        }

    if map_schema:
        report("Mapping database schema from migrations")
        schema_data = extract_schema(repo_path, [
            entry["path"] for entry in database["migration_directories"] if "path" in entry
        ])
    else:
        schema_data = skipped_schema(map_schema_skip_reason)

    if map_endpoints:
        report("Mapping API endpoints")
        api_endpoints_data = map_api_endpoints(
            repo_path, unchanged_endpoints=unchanged_endpoints, ignored_paths=ignored_paths
        )
    else:
        api_endpoints_data = {
            "checked": False,
            "reason": "skipped (--no-map-endpoints)",
            "endpoints": [],
        }

    if not using_hosted_cache and not local_cache_disabled:
        # --no-map-endpoints leaves api_endpoints_data["endpoints"] empty for
        # this run only - don't write that as if it were real, or the next
        # normal scan would wrongly treat every file as having zero
        # endpoints instead of re-checking them.
        _write_local_scan_cache(
            repo_path, modules, api_endpoints_data["endpoints"] if map_endpoints else []
        )

    report("Done")

    return {
        "aletheore_version": EVIDENCE_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "repo_path": str(repo_path),
        "repository": {
            "languages": languages,
            "frameworks": frameworks,
            "ai_usage": ai_usage,
            "policy_docs": policy_docs,
            "build_tools": build_tools,
            "monorepo": monorepo,
            "database": {**database, "schema": schema_data},
            "infrastructure": infrastructure,
            "environment_variables": environment_variables,
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable_files,
            "api_endpoints": api_endpoints_data,
            "dead_code": dead_code_data,
        },
        "git": git_data,
        "security": {
            "secrets": secrets_data,
            "dependency_vulnerabilities": vulnerabilities_data,
            "dependency_licenses": licenses_data,
        },
        "architecture": {
            "clusters": clusters,
            "cross_cluster_edges": cross_cluster_edges,
            "layer_violations": layer_violations,
            "config_applied": architecture_config,
        },
    }


_GITIGNORE_ALETHEORE_ENTRIES = (".aletheore/", ".aletheore", "/.aletheore/", "/.aletheore")


def _ensure_aletheore_dir_gitignored(repo_path: Path) -> None:
    # Best-effort, and only inside an actual git checkout - a plain
    # directory has no .gitignore worth creating. Without this, .aletheore/'s
    # scan cache and evidence output (which can include secrets-scan match
    # previews and the absolute repo path) is one `git add .` away from
    # landing in a commit.
    #
    # Hosted and demo scans DO reach this - they run against real `git clone`
    # output, so the .git check does not exclude them - but it is inert there:
    # ephemeral clones are deleted with the job, persistent checkouts are
    # reset by the `git checkout -f` + `git clean -fdx` that precedes every
    # reuse, incremental scans diff commit-to-commit rather than against the
    # working tree, and Docs repo commits are built through the GitHub API
    # from explicit content rather than pushed from this tree. Anything that
    # changes one of those - in particular, committing from the checkout -
    # needs to gate this call rather than assume it never fires server-side.
    if not (repo_path / ".git").exists():
        return
    gitignore_path = repo_path / ".gitignore"
    existing = ""
    if gitignore_path.exists():
        try:
            existing = gitignore_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        if any(line.strip() in _GITIGNORE_ALETHEORE_ENTRIES for line in existing.splitlines()):
            return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    try:
        with gitignore_path.open("a", encoding="utf-8") as f:
            f.write(f"{separator}.aletheore/\n")
    except OSError:
        pass


def write_evidence(evidence: dict, repo_path: Path) -> Path:
    # Usually already a no-op by the time evidence written via
    # scan_repository() gets here - see the call at the top of
    # scan_repository for why it moved there. Kept here too as a safety net
    # for any caller that writes evidence without going through
    # scan_repository.
    _ensure_aletheore_dir_gitignored(repo_path)
    aletheore_dir = repo_path / ".aletheore"
    aletheore_dir.mkdir(parents=True, exist_ok=True)
    output_path = aletheore_dir / "air.json"
    output_path.write_text(json.dumps(evidence, indent=2))

    # A second, TOON-encoded copy exists specifically for the audit command's
    # coding-agent adapter to read instead of the JSON one - the agent's own
    # token budget is what actually pays for reading this file, and AIR's
    # shape (uniform arrays of same-shaped objects almost everywhere) is
    # exactly TOON's best case. air.json stays the canonical machine-
    # readable copy (the dashboard's JS and any external tooling need real
    # JSON), so this is additive, not a replacement - a TOON encoding
    # failure must never take scan down with it, since air.json (the file
    # that actually matters) is already written by this point.
    try:
        (aletheore_dir / "air.toon").write_text(to_toon(evidence))
    except ToonEncodingError as exc:
        warnings.warn(
            f"could not write .aletheore/air.toon ({exc}) - air.json (the canonical "
            "evidence file) was written successfully; only 'aletheore audit' needs "
            "the TOON copy, and it will fail with a clear error if it's missing "
            "when audit next runs",
            stacklevel=2,
        )

    return output_path
