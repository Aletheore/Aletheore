import fnmatch
import json
from pathlib import Path

DISABLEABLE_CHECKS = {"vulnerabilities", "licenses", "endpoints", "secrets_history", "schema", "static_analysis"}
SEVERITY_LEVELS = ("critical", "high", "medium", "low")

DEFAULT_CONFIG = {
    "layer_markers": {},
    "cluster_resolution": 1.0,
    "dead_code_entry_points": [],
    "accepted_secrets": [],
    "ignored_paths": [],
    "disabled_checks": [],
    "severity_threshold": None,
}


def load_repo_config(repo_path: Path) -> dict:
    """Reads .aletheore.json if present, returns DEFAULT_CONFIG merged with
    whatever valid keys/types it contains. Never raises - malformed JSON, a
    missing file, or a wrong-typed value for a key all fall back to that
    key's default rather than surfacing an error mid-scan.
    """
    result = dict(DEFAULT_CONFIG)
    config_file = repo_path / ".aletheore.json"
    if not config_file.exists():
        return result

    try:
        text = config_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return result
    return parse_repo_config(text)


def parse_repo_config(raw_text: str | None) -> dict:
    """Same parsing/validation as load_repo_config, for a caller that
    already has the config file's text some other way than a local
    filesystem path - e.g. fetched from GitHub's Contents API for a repo
    that was never cloned (see scan_worker.jobs._run_flash_review, which
    reviews a PR diff purely via the GitHub API with no local checkout to
    read a Path from). raw_text is None for "no .aletheore.json at this
    ref" (GitHub's Contents API 404), same as load_repo_config's missing-
    file case.
    """
    result = dict(DEFAULT_CONFIG)
    if raw_text is None:
        return result

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return result
    if not isinstance(data, dict):
        return result

    layer_markers = data.get("layer_markers", {})
    if isinstance(layer_markers, dict):
        # Real bug found via audit: an unvalidated rank flows straight into
        # architecture.detect_layer_violations's `from_rank < to_rank`
        # comparison. A string rank (e.g. from a user who quoted their
        # numbers) silently compares lexicographically instead of
        # numerically ("2" < "10" is False), producing false negatives -
        # a real inner-to-outer violation goes unreported with no error.
        # Mixing a string-ranked custom marker with any of this module's
        # own built-in int-ranked markers raises TypeError mid-comparison,
        # crashing the whole `aletheore scan`, not just layer-violation
        # detection. cluster_resolution two lines below is already
        # type-checked for exactly this reason; this closes the same gap
        # for layer_markers.
        result["layer_markers"] = {
            name: rank
            for name, rank in layer_markers.items()
            if isinstance(name, str) and isinstance(rank, int) and not isinstance(rank, bool)
        }

    cluster_resolution = data.get("cluster_resolution", 1.0)
    if isinstance(cluster_resolution, (int, float)) and not isinstance(cluster_resolution, bool):
        result["cluster_resolution"] = float(cluster_resolution)

    dead_code_entry_points = data.get("dead_code_entry_points", [])
    if isinstance(dead_code_entry_points, list):
        result["dead_code_entry_points"] = [p for p in dead_code_entry_points if isinstance(p, str)]

    accepted_secrets = data.get("accepted_secrets", [])
    if isinstance(accepted_secrets, list):
        result["accepted_secrets"] = [e for e in accepted_secrets if isinstance(e, dict)]

    ignored_paths = data.get("ignored_paths", [])
    if isinstance(ignored_paths, list):
        result["ignored_paths"] = [p for p in ignored_paths if isinstance(p, str)]

    disabled_checks = data.get("disabled_checks", [])
    if isinstance(disabled_checks, list):
        result["disabled_checks"] = [c for c in disabled_checks if isinstance(c, str) and c in DISABLEABLE_CHECKS]

    severity_threshold = data.get("severity_threshold")
    if isinstance(severity_threshold, str) and severity_threshold in SEVERITY_LEVELS:
        result["severity_threshold"] = severity_threshold

    return result


def validate_repo_config_text(raw_text: str | None) -> list[str]:
    """Human-readable problems with a .aletheore.json's text. Empty when the
    text is None (no file) or fully valid. parse_repo_config still applies the
    defaults for whatever is wrong; this only reports it, so a typo is visible
    instead of being silently ignored."""
    if raw_text is None:
        return []
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return [
            f".aletheore.json is not valid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno}); "
            "every setting falls back to its default"
        ]
    if not isinstance(data, dict):
        return [
            f".aletheore.json must be a JSON object, not {type(data).__name__}; "
            "every setting falls back to its default"
        ]
    problems: list[str] = []
    for key in data:
        if key not in DEFAULT_CONFIG:
            problems.append(
                f".aletheore.json: unknown key {key!r} ignored (known keys: {', '.join(sorted(DEFAULT_CONFIG))})"
            )

    def is_number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    if "layer_markers" in data:
        markers = data["layer_markers"]
        if not isinstance(markers, dict):
            problems.append(".aletheore.json: layer_markers must be an object of name to integer rank; ignored")
        else:
            for name, rank in markers.items():
                if not isinstance(name, str):
                    problems.append(f".aletheore.json: layer_markers key {name!r} must be a string; entry ignored")
                elif not (isinstance(rank, int) and not isinstance(rank, bool)):
                    problems.append(f".aletheore.json: layer_markers[{name!r}] must be an integer rank; entry ignored")
    if "cluster_resolution" in data:
        value = data["cluster_resolution"]
        if not is_number(value):
            problems.append(f".aletheore.json: cluster_resolution must be a number, got {value!r}; using 1.0")
        elif value <= 0:
            problems.append(f".aletheore.json: cluster_resolution must be greater than 0, got {value!r}")
    for key, item_type in (("dead_code_entry_points", str), ("ignored_paths", str), ("accepted_secrets", dict)):
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, list):
            problems.append(f".aletheore.json: {key} must be a list; ignored")
        else:
            bad = sum(1 for entry in value if not isinstance(entry, item_type))
            if bad:
                problems.append(
                    f".aletheore.json: {key} has {bad} entr{'y' if bad == 1 else 'ies'} that "
                    f"{'is' if bad == 1 else 'are'} not {'a string' if item_type is str else 'an object'}; ignored"
                )
    if "disabled_checks" in data:
        value = data["disabled_checks"]
        if not isinstance(value, list):
            problems.append(".aletheore.json: disabled_checks must be a list; ignored")
        else:
            for check in value:
                if not isinstance(check, str) or check not in DISABLEABLE_CHECKS:
                    problems.append(
                        f".aletheore.json: disabled_checks has unknown value {check!r} ignored "
                        f"(valid: {', '.join(sorted(DISABLEABLE_CHECKS))})"
                    )
    if "severity_threshold" in data:
        value = data["severity_threshold"]
        if value is not None and not (isinstance(value, str) and value in SEVERITY_LEVELS):
            problems.append(
                f".aletheore.json: severity_threshold must be one of {', '.join(SEVERITY_LEVELS)} or null, "
                f"got {value!r}; no threshold applied"
            )
    return problems


def load_repo_config_warnings(repo_path: Path) -> list[str]:
    """validate_repo_config_text for the repository's own .aletheore.json."""
    config_file = repo_path / ".aletheore.json"
    if not config_file.exists():
        return []
    try:
        text = config_file.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return [f".aletheore.json could not be read ({exc.strerror or exc.__class__.__name__}); every setting falls back to its default"]
    return validate_repo_config_text(text)


def _segments_match(pattern_segments: list[str], candidate_segments: list[str]) -> bool:
    """True if pattern_segments matches a PREFIX of candidate_segments - so a
    directory-shaped pattern excludes everything beneath it (the pattern is
    allowed to run out before the candidate does; the reverse isn't a
    match). "**" is the one construct allowed to cross a "/" boundary
    (matches zero or more whole segments); a plain "*"/"?" stays scoped to
    one segment via fnmatch, matching real gitignore semantics rather than
    fnmatch's own no-path-awareness "*" (which matches straight through
    "/" - confirmed directly: the old whole-string fnmatch call made
    "docs/*" match "docs/sub/x.md" too, when gitignore's "*" doesn't cross
    directory boundaries).

    Real bug found via Flash Review on this same change: a naive recursive
    "**" branch with no memoization forks into len(candidate_segments)+1
    calls, and a pattern with several "**" segments multiplies that
    branching at every level - exponential in the number of "**" segments.
    ignored_paths comes from the scanned repo's own .aletheore.json,
    untrusted input by design, so a crafted config is a real
    denial-of-service vector - confirmed directly, a 10x"**" pattern
    against a 25-segment path took ~60s before this fix. Memoizing by
    (pattern index, candidate index) - scoped to this one call, not a
    module-level cache, so it can't grow across unrelated calls - bounds
    the whole match to O(len(pattern_segments) * len(candidate_segments))
    states.
    """
    memo: dict[tuple[int, int], bool] = {}

    def match(pi: int, ci: int) -> bool:
        key = (pi, ci)
        cached = memo.get(key)
        if cached is not None:
            return cached
        if pi == len(pattern_segments):
            result = True
        else:
            head = pattern_segments[pi]
            if head == "**":
                result = any(
                    match(pi + 1, ci + skip) for skip in range(len(candidate_segments) - ci + 1)
                )
            elif ci == len(candidate_segments):
                result = False
            else:
                result = fnmatch.fnmatch(candidate_segments[ci], head) and match(pi + 1, ci + 1)
        memo[key] = result
        return result

    return match(0, 0)


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """rel_path is repo-root-relative, forward-slash separated (as_posix()).

    Gitignore-style anchoring (per this module's own design spec, which
    calls these "gitignore-style glob patterns"): a pattern containing a
    "/" anywhere but a lone trailing character is anchored to the repo
    root and only ever checked from position 0; a bare pattern with no
    "/" (or only a trailing one) is unanchored and matches at any depth,
    not just at the repo root.

    Real bug found via audit: the previous implementation always anchored
    every pattern to the root regardless of whether it contained a "/" -
    confirmed directly, a plain "vendor" pattern (this function's own
    docstring's example of something that should "exclude everything
    under vendor/") excluded a root-level vendor/ directory but not a
    nested one (e.g. "packages/some-lib/vendor/file.go"), the opposite of
    gitignore's own documented unanchored-bare-name behavior this module
    is explicitly designed to follow. A pattern matches if it equals the
    whole path, or a parent-directory prefix of it (from the root for an
    anchored pattern, from anywhere for an unanchored one) - so "vendor/**"
    (anchored) or plain "vendor" (unanchored) both exclude everything
    under a vendor/ directory without every file inside needing to match
    individually, "vendor/**" only at the repo root, "vendor" at any
    depth.
    """
    if not patterns:
        return False
    path_segments = rel_path.split("/")
    for pattern in patterns:
        body = pattern[:-1] if pattern.endswith("/") else pattern
        anchored = pattern.startswith("/") or "/" in body
        pattern_segments = pattern.strip("/").split("/")
        starts = (0,) if anchored else range(len(path_segments))
        if any(_segments_match(pattern_segments, path_segments[start:]) for start in starts):
            return True
    return False
