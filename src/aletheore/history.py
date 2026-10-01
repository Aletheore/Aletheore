import json
from collections import Counter
from pathlib import Path

from aletheore.repo_config import load_repo_config
from aletheore.vulnerabilities import filter_by_severity


def _history_dir(repo_path: Path) -> Path:
    return repo_path / ".aletheore" / "history"


def _snapshot_sort_key(path: Path) -> tuple[str, int]:
    # Primary key is the snapshot's own real scanned_at (read from its
    # content, not guessed from the filename) - callers are free to save
    # snapshots out of physical order with an explicit scanned_at
    # (test_list_snapshots_returns_chronological_order does exactly this
    # on purpose), and that logical timestamp, not save order, is what
    # "chronological" means here in the normal case.
    #
    # mtime (real filesystem write time, nanosecond resolution) is only
    # the TIEBREAKER, for the one case scanned_at alone can't order: two
    # snapshots saved within the same wall-clock second get a
    # disambiguating "-N" suffix inserted before the .json extension
    # (_save_json_with_rotation's collision loop) - real bug this closes:
    # a plain filename-string sort put that suffixed (chronologically
    # LATER) file BEFORE the unsuffixed one, since '-' (0x2D) sorts
    # before '.' (0x2E) in ASCII, so "...-1.json" < "....json" lexically
    # even though the "-1" file was saved second. _rotate then deleted
    # the wrong (older-looking but actually newer) snapshot when rotating
    # past the keep limit - a real, reachable scenario (two `aletheore
    # scan` runs seconds apart, common in CI or a fast local loop), not
    # cosmetic display-order noise: it discarded a newer scan's real
    # evidence while keeping an older one.
    try:
        scanned_at = json.loads(path.read_text(encoding="utf-8")).get("scanned_at", "")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # Flash Review finding: read_text() can raise UnicodeDecodeError
        # for a non-UTF-8 *.json file, which this only caught OSError/
        # JSONDecodeError for - one corrupt or unrelated non-UTF-8 file
        # in the history directory made both snapshot listing and
        # rotation fail entirely, a regression from the previous
        # filename-only sort, which never inspected file contents at all.
        scanned_at = ""
    if not isinstance(scanned_at, str):
        scanned_at = ""
    return (scanned_at, path.stat().st_mtime_ns)


def _sorted_snapshots(history_dir: Path) -> list[Path]:
    return sorted(history_dir.glob("*.json"), key=_snapshot_sort_key)


def _rotate(history_dir: Path, keep: int) -> None:
    snapshots = _sorted_snapshots(history_dir)
    excess = len(snapshots) - keep
    if excess <= 0:
        return
    for path in snapshots[:excess]:
        path.unlink()


def _save_json_with_rotation(data: dict, directory: Path, timestamp: str, keep: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    safe_name = timestamp.replace(":", "-")
    snapshot_path = directory / f"{safe_name}.json"
    suffix = 1
    while snapshot_path.exists():
        snapshot_path = directory / f"{safe_name}-{suffix}.json"
        suffix += 1

    snapshot_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _rotate(directory, keep)
    return snapshot_path


def save_snapshot(evidence: dict, repo_path: Path, keep: int = 20) -> Path:
    return _save_json_with_rotation(evidence, _history_dir(repo_path), evidence["scanned_at"], keep)


def list_snapshots(repo_path: Path) -> list[Path]:
    history_dir = _history_dir(repo_path)
    if not history_dir.exists():
        return []
    return _sorted_snapshots(history_dir)


def _identity_key(finding: dict, fields: tuple[str, ...]) -> tuple:
    return tuple(finding.get(field) for field in fields)


def _new_and_resolved(
    old_findings: list[dict],
    new_findings: list[dict],
    fields: tuple[str, ...] = (),
    key_fn=None,
) -> tuple[list[dict], list[dict]]:
    """`fields` is the common case: identity is just those fields read off
    the finding dict. `key_fn` is for a category whose identity can't be
    expressed as a flat field tuple - static_analysis uses it to prefer a
    content fingerprint over the finding's own (shift-prone) line number,
    see _static_analysis_identity below."""
    key = key_fn if key_fn is not None else (lambda f: _identity_key(f, fields))
    old_keys = {key(f) for f in old_findings}
    new_keys = {key(f) for f in new_findings}
    new_only = [f for f in new_findings if key(f) not in old_keys]
    resolved = [f for f in old_findings if key(f) not in new_keys]
    return new_only, resolved


def _module_symbol_names(module: dict | None) -> tuple[Counter, Counter]:
    """(function_names, class_names) declared in `module`'s scan evidence,
    as multisets, or two empty Counters when `module` is None - the file
    didn't exist as a scanned code module at this commit, either because
    it never existed or because aletheore's scanner doesn't parse it as
    code (docs, config, binary assets).

    Counters, not sets - real gap found on final review: the scanner
    stores methods in symbols.functions under their bare name with no
    class qualifier (confirmed live: a file with `A.run`, `B.run`, and a
    top-level `run()` all show up as three unqualified "run" entries). A
    set-based diff collapsed all three into one name, so deleting class B
    (and its `run` method) while class A's own `run` survived elsewhere
    in the file reported zero removed functions. A multiset diff (Counter
    subtraction) counts instances, not distinct names, so removing one of
    two same-named methods correctly shows one removal.
    """
    if module is None:
        return Counter(), Counter()
    symbols = module.get("symbols", {})
    functions = Counter(e["name"] for e in symbols.get("functions", []) if e.get("name"))
    classes = Counter(e["name"] for e in symbols.get("classes", []) if e.get("name"))
    return functions, classes


def summarize_file_changes(old: dict, new: dict, changed_files: list[dict]) -> list[dict]:
    """Per-file function/class-level summary for every file GitHub's
    compare API reports as changed between the commits `old` and `new`
    were scanned at.

    `changed_files` is `github_api.fetch_pr_changed_files_detailed`'s own
    shape: each dict needs "filename", "status", "additions", "deletions",
    and (for a rename) "previous_filename". A renamed file is looked up at
    its *previous* path in `old` and its current path in `new` - looking
    both up at the current path would misreport a pure rename's whole
    function/class set as freshly added, since `old`'s module list was
    never indexed at the new path.

    Returns one dict per input file, in the same order: {"path", "status",
    "additions", "deletions", "previous_path", "functions_added",
    "functions_removed", "classes_added", "classes_removed",
    "has_module_data"}. `has_module_data` is False only when neither
    commit's evidence scanned this path as a code module at all (a
    non-code file, or one aletheore's scanner doesn't parse) - a renderer
    needs this to tell "nothing changed" apart from "never had symbols to
    diff in the first place" instead of reporting both identically.
    """
    old_modules = {m["path"]: m for m in old.get("repository", {}).get("modules", []) if m.get("path")}
    new_modules = {m["path"]: m for m in new.get("repository", {}).get("modules", []) if m.get("path")}

    rows = []
    for file in changed_files:
        path = file["filename"]
        previous_path = file.get("previous_filename")
        old_lookup_path = previous_path if previous_path else path
        old_module = old_modules.get(old_lookup_path)
        new_module = new_modules.get(path)

        old_functions, old_classes = _module_symbol_names(old_module)
        new_functions, new_classes = _module_symbol_names(new_module)

        rows.append({
            "path": path,
            "status": file.get("status", "modified"),
            "additions": file.get("additions", 0),
            "deletions": file.get("deletions", 0),
            "previous_path": previous_path,
            "functions_added": sorted((new_functions - old_functions).elements()),
            "functions_removed": sorted((old_functions - new_functions).elements()),
            "classes_added": sorted((new_classes - old_classes).elements()),
            "classes_removed": sorted((old_classes - new_classes).elements()),
            "has_module_data": old_module is not None or new_module is not None,
        })
    return rows


def _endpoint_block(evidence: dict) -> dict:
    return evidence["repository"].get(
        "api_endpoints", {"checked": False, "reason": "not present in older evidence", "endpoints": []}
    )


def _static_analysis_block(evidence: dict) -> dict:
    return evidence["security"].get(
        "static_analysis", {"checked": False, "reason": "not present in older evidence", "findings": []}
    )


# The two markers check_static_analysis's own orchestrator (static_analysis/
# __init__.py) already uses for an INTENTIONAL skip - bearer/joern simply
# not opted into this run, or SonarQube not configured - as opposed to a
# default-on scanner (semgrep/gosec/bandit/trivy/pmd) that was supposed to
# run and didn't (tool missing, timed out, crashed, produced unparseable
# output). Matching on these exact strings rather than a tool allowlist:
# an opted-in bearer/joern run that genuinely fails gets its own scanner's
# real failure reason instead, which won't match either marker and
# correctly reads as unexpected.
_INTENTIONAL_SKIP_MARKERS = ("skipped (opt-in", "SonarQube not configured")


def _unexpected_tool_skips(tools_skipped: list[dict]) -> list[dict]:
    """tools_skipped entries that mean a scanner was SUPPOSED to run this
    pass but didn't - as opposed to bearer/joern/SonarQube's normal,
    expected not-opted-in/not-configured state. Real gap this closes: a
    default-on scanner failing (registry outage, binary missing, a crash)
    left new_findings empty for the exact same reason a genuinely clean
    repo would - nothing distinguished "nothing wrong" from "didn't look"
    anywhere downstream of compute_diff."""
    return [
        skip
        for skip in tools_skipped
        if not str(skip.get("reason", "")).startswith(_INTENTIONAL_SKIP_MARKERS)
    ]


def _rename_aware_findings(findings: list[dict], renamed_paths: dict[str, str]) -> list[dict]:
    """Return `findings` with each finding's "path" remapped through
    `renamed_paths` ({old_path: new_path}, GitHub compare API's own
    previous_filename -> filename shape) when it names an old path that
    was renamed - a shallow copy per remapped finding, so the caller's
    original list/dicts are never mutated. A finding whose path isn't a
    renamed_paths key is returned unchanged (same dict, not copied)."""
    if not renamed_paths:
        return findings
    return [
        {**f, "path": renamed_paths[f["path"]]} if f.get("path") in renamed_paths else f
        for f in findings
    ]


def _compute_curated_diff(old: dict, new: dict, renamed_paths: dict[str, str] | None = None) -> dict:
    result: dict = {}
    caveats = []

    old_vuln_checked = old["security"]["dependency_vulnerabilities"]["checked"]
    new_vuln_checked = new["security"]["dependency_vulnerabilities"]["checked"]
    if old_vuln_checked != new_vuln_checked:
        caveats.append(
            "dependency-vulnerability checking state changed between scans "
            f"(was checked={old_vuln_checked}, now checked={new_vuln_checked}) - "
            "new/resolved vulnerability findings below may reflect checking being "
            "toggled on/off, not necessarily real changes"
        )

    # .get(..., 0) rather than a direct index - real bug found via audit:
    # history_scanned_commits was added to the secrets section after this
    # module's original schema, so a genuinely older on-disk snapshot (a
    # real, plausible .aletheore/history/*.json file predating this
    # field, diffed after a CLI upgrade) crashed with KeyError instead of
    # degrading the same way _endpoint_block already does for
    # api_endpoints just above - this module's own established pattern
    # for exactly this case, applied inconsistently. 0 is the correct
    # default: "not present" and "0 commits scanned" both mean
    # old_history_scanned/new_history_scanned should read False.
    old_history_scanned = old["security"]["secrets"].get("history_scanned_commits", 0) > 0
    new_history_scanned = new["security"]["secrets"].get("history_scanned_commits", 0) > 0
    if old_history_scanned != new_history_scanned:
        caveats.append(
            "git-history secret scanning state changed between scans "
            f"(was scanned={old_history_scanned}, now scanned={new_history_scanned}) - "
            "new/resolved history secret findings below may reflect scanning being "
            "toggled on/off, not necessarily real changes"
        )

    old_api_endpoints = _endpoint_block(old)
    new_api_endpoints = _endpoint_block(new)
    old_endpoints_checked = old_api_endpoints["checked"]
    new_endpoints_checked = new_api_endpoints["checked"]
    if old_endpoints_checked != new_endpoints_checked:
        caveats.append(
            "API endpoint mapping state changed between scans "
            f"(was checked={old_endpoints_checked}, now checked={new_endpoints_checked}) - "
            "new/resolved endpoint findings below may reflect mapping being toggled on/off, "
            "not necessarily real changes"
        )

    old_static_analysis = _static_analysis_block(old)
    new_static_analysis = _static_analysis_block(new)
    old_static_analysis_checked = old_static_analysis["checked"]
    new_static_analysis_checked = new_static_analysis["checked"]
    if old_static_analysis_checked != new_static_analysis_checked:
        caveats.append(
            "static analysis scanning state changed between scans "
            f"(was checked={old_static_analysis_checked}, now checked={new_static_analysis_checked}) - "
            "new/resolved static analysis findings below may reflect scanning being "
            "toggled on/off, not necessarily real changes"
        )

    if caveats:
        result["caveats"] = caveats

    new_secrets, resolved_secrets = _new_and_resolved(
        old["security"]["secrets"]["findings"],
        new["security"]["secrets"]["findings"],
        _secret_identity_fields(
            old["security"]["secrets"]["findings"], new["security"]["secrets"]["findings"]
        ),
    )
    result["secrets"] = {"new": new_secrets, "resolved": resolved_secrets}

    # Same older-schema gap as history_scanned_commits above - history_findings
    # is absent, not just empty, in evidence that predates git-history secret
    # scanning.
    new_history_secrets, resolved_history_secrets = _new_and_resolved(
        old["security"]["secrets"].get("history_findings", []),
        new["security"]["secrets"].get("history_findings", []),
        ("commit", "path", "pattern"),
    )
    result["history_secrets"] = {"new": new_history_secrets, "resolved": resolved_history_secrets}

    new_vulns, resolved_vulns = _new_and_resolved(
        old["security"]["dependency_vulnerabilities"]["findings"],
        new["security"]["dependency_vulnerabilities"]["findings"],
        ("ecosystem", "package", "advisory_id"),
    )
    # severity_threshold only filters what's surfaced here (PR comments,
    # SARIF, --fail-on-new-vulnerabilities) - evidence.json's own findings
    # list, read by "new" above, is never filtered, so the scan record
    # itself stays complete regardless of this repo's config.
    new_repo_path = new.get("repo_path")
    severity_threshold = (
        load_repo_config(Path(new_repo_path))["severity_threshold"] if new_repo_path else None
    )
    new_vulns = filter_by_severity(new_vulns, severity_threshold)
    resolved_vulns = filter_by_severity(resolved_vulns, severity_threshold)
    result["vulnerabilities"] = {"new": new_vulns, "resolved": resolved_vulns}

    # Two complementary identity mechanisms, both needed:
    # - content_fingerprint (a hash of the source lines around a finding -
    #   see static_analysis/__init__.py's _add_content_fingerprints) is
    #   preferred over exact line number, so an unrelated edit earlier in
    #   the same file that shifts every later finding's line doesn't read
    #   as that finding being resolved and a new one appearing in its
    #   place - real case: PR #888, where fixing one Bandit finding
    #   shifted 4 unrelated, pre-existing findings below it and failed the
    #   static-analysis check run on all 4. Falls back to the old (tool,
    #   rule_id, path, line) identity per-finding when no fingerprint
    #   exists (a line-less misconfig finding, or evidence from before
    #   this field existed) - see
    #   docs/audits/2026-10-01-static-analysis-dismissal-and-line-shift.md.
    # - renamed_paths (below, via _rename_aware_findings) covers the other
    #   half of the same failure mode: a pure file rename, where even a
    #   matching content_fingerprint can't help because "path" is still
    #   part of the identity tuple and differs between the old and new
    #   finding.
    new_static_analysis_tool_skips = new_static_analysis.get("tools_skipped", [])
    new_static_analysis, resolved_static_analysis = _new_and_resolved(
        _rename_aware_findings(old_static_analysis["findings"], renamed_paths or {}),
        new_static_analysis["findings"],
        key_fn=_static_analysis_identity_fn(old_static_analysis["findings"], new_static_analysis["findings"]),
    )
    result["static_analysis"] = {
        "new": new_static_analysis,
        "resolved": resolved_static_analysis,
        "unexpected_tool_skips": _unexpected_tool_skips(new_static_analysis_tool_skips),
    }

    new_violations, resolved_violations = _new_and_resolved(
        old["architecture"]["layer_violations"]["violations"],
        new["architecture"]["layer_violations"]["violations"],
        ("from", "to"),
    )
    result["layer_violations"] = {"new": new_violations, "resolved": resolved_violations}

    new_endpoints, resolved_endpoints = _new_and_resolved(
        old_api_endpoints["endpoints"],
        new_api_endpoints["endpoints"],
        ("method", "path"),
    )
    result["endpoints"] = {"new": new_endpoints, "resolved": resolved_endpoints}

    result["aggregate_deltas"] = {
        "module_count": len(new["repository"]["modules"]) - len(old["repository"]["modules"]),
        "dependency_graph_edge_count": (
            len(new["repository"]["dependency_graph"]["edges"])
            - len(old["repository"]["dependency_graph"]["edges"])
        ),
        "total_commits": new["git"].get("total_commits", 0) - old["git"].get("total_commits", 0),
    }

    return result


def _flatten(obj, prefix: str = "") -> dict:
    flat: dict = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            flat.update(_flatten(val, new_prefix))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            flat.update(_flatten(val, f"{prefix}[{idx}]"))
    else:
        flat[prefix] = obj
    return flat


def _compute_full_diff(old: dict, new: dict) -> dict:
    old_flat = _flatten(old)
    new_flat = _flatten(new)

    added = [
        {"path": path, "value": value}
        for path, value in sorted(new_flat.items())
        if path not in old_flat
    ]
    removed = [
        {"path": path, "value": value}
        for path, value in sorted(old_flat.items())
        if path not in new_flat
    ]
    changed = [
        {"path": path, "old_value": old_flat[path], "new_value": new_flat[path]}
        for path in sorted(old_flat.keys() & new_flat.keys())
        if old_flat[path] != new_flat[path]
    ]

    return {"added": added, "removed": removed, "changed": changed}


_HASHED_PREVIEW_PREFIX = "sha256:"


def _has_legacy_previews(findings: list[dict]) -> bool:
    return any(
        not str(finding.get("match_preview", "")).startswith(_HASHED_PREVIEW_PREFIX)
        for finding in findings
    )


def _secret_identity_fields(old_findings: list[dict], new_findings: list[dict]) -> tuple[str, ...]:
    """Which fields identify "the same secret" across two scans.

    Normally (path, pattern, match_preview). But match_preview's format
    changed - it used to be four real leading and trailing characters of the
    value, and is now a salted hash (see secrets._redact) - so on the single
    scan that straddles that change, every previously-known secret has a
    different identity than its own prior record. Diffed naively, that
    reports the entire existing findings list as newly added and the entire
    prior list as resolved: PR comments naming every long-known secret as
    new, and `--fail-on-new-secrets` failing a build that introduced nothing.

    Detected from the data rather than a version number, so it also covers a
    snapshot written by any older build regardless of what version string it
    carries. Falling back to (path, pattern) for that one diff still surfaces
    a genuinely new secret (new file, or a new pattern in a known file) while
    matching the pre-existing ones to their old records. It is coarser only
    in that two secrets of the same pattern in the same file collapse
    together for that scan; the next scan compares hash-to-hash and this
    stops applying on its own.
    """
    if _has_legacy_previews(old_findings) and not _has_legacy_previews(new_findings):
        return ("path", "pattern")
    return ("path", "pattern", "match_preview")


def _has_legacy_static_analysis_identity(findings: list[dict]) -> bool:
    """True when every finding that could carry a content_fingerprint
    (i.e. has a real line - see static_analysis/__init__.py's
    _add_content_fingerprints) doesn't. A line-less misconfig finding is
    never fingerprinted regardless of scanner version, so it's excluded
    here rather than making every scan look "legacy" forever because of it.
    """
    fingerprintable = [f for f in findings if isinstance(f.get("line"), int) and f.get("line", 0) >= 1]
    return bool(fingerprintable) and all(not f.get("content_fingerprint") for f in fingerprintable)


def _static_analysis_identity(finding: dict, coarse: bool) -> tuple:
    if coarse:
        return (finding.get("tool"), finding.get("rule_id"), finding.get("path"))
    return (
        finding.get("tool"),
        finding.get("rule_id"),
        finding.get("path"),
        finding.get("content_fingerprint") or ("line", finding.get("line")),
    )


def _static_analysis_identity_fn(old_findings: list[dict], new_findings: list[dict]):
    """Same straddling-upgrade problem _secret_identity_fields solves for
    secrets' match_preview format change, for static_analysis's own
    content_fingerprint addition (see static_analysis/__init__.py). Old
    evidence computed before that field existed has no fingerprint on any
    finding; freshly-scanned evidence does. Matched naively, that one diff
    reports every pre-existing static-analysis finding as both newly added
    and resolved, purely because the old record's identity has no
    fingerprint to compare against the new record's real one - exactly
    the `aletheore changes` path, which diffs the two most recent stored
    snapshots (src/aletheore/cli.py's _query_changes), would hit on the
    first scan after upgrading.

    Falling back to (tool, rule_id, path) for that one diff - dropping
    line and fingerprint both - still surfaces a genuinely new finding
    (new file, or a new rule firing in a known file) while matching
    pre-existing ones to their old records. Coarser only in that two
    findings of the same rule in the same file collapse together for that
    one scan; the next scan compares fingerprint-to-fingerprint (or
    line-to-line, for a finding neither side could fingerprint) and this
    stops applying on its own - same self-healing shape as the secrets
    case.
    """
    coarse = _has_legacy_static_analysis_identity(old_findings) and not _has_legacy_static_analysis_identity(
        new_findings
    )
    return lambda f: _static_analysis_identity(f, coarse)


def compute_diff(
    old: dict, new: dict, full: bool = False, renamed_paths: dict[str, str] | None = None
) -> dict:
    """`renamed_paths` ({old_path: new_path}) is optional, curated-mode-only
    data (GitHub compare API's own previous_filename -> filename shape, the
    same data summarize_file_changes already consumes) that makes the
    static-analysis new/resolved split rename-aware. Omitting it preserves
    the prior behavior exactly - a renamed file's carried-over findings
    still read as both resolved and new, same as every caller that can't
    supply rename data."""
    if full:
        return _compute_full_diff(old, new)
    return _compute_curated_diff(old, new, renamed_paths=renamed_paths)


def _aletheore_version() -> str:
    import importlib.metadata

    try:
        return importlib.metadata.version("aletheore")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _sarif_result(
    rule_id: str, level: str, message: str, *, file_path: str | None = None, line: int | None = None
) -> dict:
    result: dict = {"ruleId": rule_id, "level": level, "message": {"text": message}}
    if file_path is not None:
        location: dict = {"physicalLocation": {"artifactLocation": {"uri": file_path}}}
        if line is not None and line >= 1:
            location["physicalLocation"]["region"] = {"startLine": line}
        result["locations"] = [location]
    return result


def to_sarif(curated_diff: dict) -> dict:
    """Renders a curated (non-full) `compute_diff` result as a SARIF 2.1.0
    log, covering the same three categories `--fail-on-new-*` already
    treats as CI-worthy (secrets, dependency vulnerabilities, layer
    violations) - GitHub code scanning and other SARIF consumers can then
    ingest `aletheore diff`'s findings the same way they ingest any other
    static analysis tool's, instead of only via this project's own JSON
    shape and PR-comment upsert.

    Only "new" findings become results - "resolved" ones aren't a defect
    to report, they're the absence of one.
    """
    results = []

    for finding in curated_diff.get("secrets", {}).get("new", []):
        level = "note" if finding.get("likely_placeholder") else "error"
        message = f"Possible {finding['pattern']} secret ({finding['match_preview']})"
        if finding.get("accepted"):
            message += " - accepted in .aletheore.json baseline"
        results.append(
            _sarif_result("aletheore/secret", level, message, file_path=finding.get("path"), line=finding.get("line"))
        )

    for finding in curated_diff.get("history_secrets", {}).get("new", []):
        level = "note" if finding.get("likely_placeholder") else "error"
        commit = (finding.get("commit") or "")[:12]
        message = f"Possible {finding['pattern']} secret in git history at commit {commit} ({finding['match_preview']})"
        if finding.get("accepted"):
            message += " - accepted in .aletheore.json baseline"
        # No line number: a history finding is a hunk in a commit's diff, not
        # a location in the current working tree - see secrets.py's
        # find_secrets_in_history, which never records one.
        results.append(_sarif_result("aletheore/secret-history", level, message, file_path=finding.get("path")))

    for finding in curated_diff.get("vulnerabilities", {}).get("new", []):
        message = (
            f"{finding.get('ecosystem')}/{finding.get('package')}: {finding.get('advisory_id')} - "
            f"{finding.get('summary') or 'no summary available'}"
        )
        results.append(_sarif_result("aletheore/dependency-vulnerability", "warning", message))

    for finding in curated_diff.get("layer_violations", {}).get("new", []):
        message = finding.get("reason") or f"layer violation: {finding.get('from')} -> {finding.get('to')}"
        results.append(_sarif_result("aletheore/layer-violation", "warning", message, file_path=finding.get("from")))

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "aletheore",
                        "informationUri": "https://aletheore.com",
                        "version": _aletheore_version(),
                        "rules": [
                            {"id": "aletheore/secret", "name": "Secret detected in working tree"},
                            {"id": "aletheore/secret-history", "name": "Secret detected in git history"},
                            {"id": "aletheore/dependency-vulnerability", "name": "Dependency vulnerability"},
                            {"id": "aletheore/layer-violation", "name": "Architecture layer violation"},
                        ],
                    }
                },
                "results": results,
            }
        ],
    }
