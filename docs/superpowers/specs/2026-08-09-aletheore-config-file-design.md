# .aletheore.json per-repo config: ignored paths, disabled checks, severity threshold

## Goal

Extend the existing `.aletheore.json` config file (currently: `layer_markers`,
`cluster_resolution`, `dead_code_entry_points`, `accepted_secrets`) with three
new settings — `ignored_paths`, `disabled_checks`, `severity_threshold` — and
consolidate the two ad-hoc readers of that file into one shared loader.

## Why extend .aletheore.json rather than add a new file

A `.aletheore.json` mechanism already exists: `aletheore init` scaffolds it,
`architecture.py::load_architecture_config` and `secrets.py` each read it
independently (two separate `json.loads(config_file.read_text())` calls on
the same file). Adding a second config file/format would mean defining
precedence between two files and duplicating the "where's the repo root"
logic a third time. One file, one loader, no migration.

## Where this plugs into the existing pipeline

`src/aletheore/evidence.py::scan_repository()` is the single scan
orchestrator. It's called two ways:
- Directly, by the CLI's `aletheore scan` command (`cli.py::_scan`).
- Indirectly, by the hosted GitHub App: `github-app/scan_worker/jobs.py`
  shells out to `subprocess.run(["aletheore", "scan", repo_dir])` against the
  cloned customer repo, then reads the resulting `evidence.json`.

Because the hosted path always goes through the same CLI entry point against
a full repo checkout, wiring config-driven behavior into `scan_repository()`
and the file-walk helpers in `src/aletheore/` is sufficient — nothing in
`github-app/` needs to change to pick up `.aletheore.json` for hosted scans.

Similarly, PR-comment surfacing goes through one choke point on both paths:
`aletheore diff old.json new.json` invokes `history.py::compute_diff()`,
whose output feeds `pr_comment.py::format_diff_comment()`. The GitHub Action
(`action.yml`) and the hosted worker's PR-check step both call this same
`diff` command — no separate hosted-only code path to duplicate filtering
into.

## 1. Shared config loader

New module `src/aletheore/repo_config.py`:

```python
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
    whatever valid keys/types it contains. Never raises - malformed JSON or
    a wrong-typed value for a key falls back to that key's default, same
    tolerance load_architecture_config already has today."""
```

- `architecture.py::load_architecture_config` and `secrets.py`'s inline
  reader both switch to calling `load_repo_config` instead of parsing the
  file themselves. `load_architecture_config` keeps its existing signature
  and return shape (just `layer_markers` + `cluster_resolution`) for
  backward compatibility with its one caller in `evidence.py`.
- `aletheore init` (`cli.py`) scaffolds the 3 new keys with sensible empty
  defaults and adds them to its help table.
- `disabled_checks` values are validated against a fixed set:
  `{"vulnerabilities", "licenses", "endpoints", "secrets_history"}`. Unknown
  values are ignored (not an error — forward-compatible if the set grows).
- `severity_threshold` accepts `"critical" | "high" | "medium" | "low"` or
  `null`/absent (no filtering). Invalid values fall back to `None`.

## 2. `ignored_paths`

Gitignore-style glob patterns (e.g. `"vendor/**"`, `"*.generated.go"`),
matched against the path relative to repo root. New helper in
`repo_config.py`:

```python
def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """fnmatch-based match against each pattern; also checks each parent
    directory segment so a directory pattern like "vendor/**" excludes
    everything under it without every file needing to match individually."""
```

No new dependency — `fnmatch` from the standard library is sufficient for
this pattern set (`*`, `**`, `?`, `[seq]`).

Called at every existing file-walk site, alongside today's `IGNORED_DIRS`
check, so a path is excluded from every check uniformly rather than
per-check:
- `scanner/detect.py` (3 walk sites — language/framework detection)
- `scanner/graph.py` (module dependency graph walk)
- `secrets.py` (working-tree secrets scan)
- `dead_code.py`
- `licenses.py` (manifest file discovery)
- `endpoints.py` (route-mapping walk)

Each site already threads a `dirnames[:] = [d for d in dirnames if d not in
IGNORED_DIRS]`-style filter; `is_ignored` is added as an additional
per-file/per-dir condition at the same point, not a separate pass.

## 3. `disabled_checks`

`scan_repository()` already accepts `check_vulnerabilities`,
`scan_git_history`, `check_licenses`, `map_endpoints` as booleans, currently
only ever set from CLI flags declared as `typer.Option(True, "--flag/--no-
flag", ...)` — always `True` or `False`, with no way to tell "user didn't
pass anything" from "user passed the default explicitly."

To let config set the default while a CLI flag still overrides, the four
`scan` command options change from `bool = typer.Option(True, ...)` to
`bool | None = typer.Option(None, ...)` (Typer/Click already supports a
tri-state `bool | None` option — omitted stays `None`, `--flag`/`--no-flag`
resolve to `True`/`False`). `cli.py::_scan()` then resolves each:

```python
config = load_repo_config(repo)
resolved_check_vulnerabilities = (
    check_vulnerabilities
    if check_vulnerabilities is not None
    else "vulnerabilities" not in config["disabled_checks"]
)
# same pattern for scan_git_history/"secrets_history",
# check_licenses/"licenses", map_endpoints/"endpoints"
```

`scan_repository()`'s own signature and defaults (`bool = True`) are
unchanged — it's `_scan()` that resolves the tri-state before calling it, so
every other caller of `scan_repository()` (tests, `_audit`) is unaffected.

Working-tree secrets scanning and dead-code/architecture detection are not
included in `disabled_checks` — they stay always-on. (Per user decision:
these are core to the evidence-grounded security pitch and shouldn't be
silently disabled via a committed config file.)

## 4. `severity_threshold`

Scope for v1: **dependency vulnerabilities only**, and **filters PR-comment
surfacing only** — `evidence.json` always contains every finding at every
severity; the scan record is never incomplete. Secrets, licenses, dead-code,
and layer violations have no real per-finding severity data today, so
`severity_threshold` doesn't touch them yet.

New helper in `vulnerabilities.py`:

```python
def normalize_severity(osv_severity: list[dict]) -> str | None:
    """OSV's severity field is a list of {type: "CVSS_V3", score: "<vector>"}.
    Parses the CVSS base score out of the first CVSS_V3/CVSS_V4 entry found,
    buckets it: >=9.0 critical, >=7.0 high, >=4.0 medium, else low. Returns
    None if no CVSS entry is present (OSV doesn't guarantee one)."""

def filter_by_severity(findings: list[dict], threshold: str | None) -> list[dict]:
    """threshold=None returns findings unchanged. Otherwise keeps findings
    whose normalize_severity() result is >= threshold in the critical>high>
    medium>low ordering. A finding with no derivable severity (None) is
    always kept - absence of data is not the same as low severity, and
    silently dropping it would hide a real finding."""
```

Applied in `history.py::compute_diff()`: after building
`curated["vulnerabilities"]["new"]` (and `["resolved"]`, for symmetry), if
the new evidence's repo has a `severity_threshold` configured, filter that
list through `filter_by_severity` before it's returned. `compute_diff`
already takes the full `new` evidence dict, which carries `repo_path`, so it
can call `load_repo_config` itself rather than needing a new parameter
threaded through every caller.

The CLI's raw `aletheore scan` output and `evidence.json` are never
filtered — only the `diff` step's `vulnerabilities` section, which is what
feeds `pr_comment.py`. The CLI dashboard, `query vulnerabilities`, and the
MCP `vulnerabilities` tool all read straight from `evidence.json` and
continue showing everything regardless of `severity_threshold`.

## Testing

- `repo_config.py`: loader defaults, malformed JSON, unknown `disabled_checks`
  values ignored, invalid `severity_threshold` falls back to `None`.
- `is_ignored`: file match, directory-prefix match, no match.
- Each file-walk site: one test per module confirming an ignored path is
  excluded from that check's output.
- `_scan()`: config-set `disabled_checks` is honored; an explicit CLI flag
  overrides the config default in both directions (config says skip, flag
  says run; config says run, flag says skip).
- `normalize_severity`: CVSS score buckets, no-CVSS-entry returns `None`.
- `filter_by_severity`: threshold filtering, `None` threshold no-op, findings
  with no derivable severity always kept.
- `compute_diff`: `severity_threshold` filters `vulnerabilities.new`/
  `.resolved`, absent config leaves the diff unchanged.
- End-to-end: a repo with a `.aletheore.json` setting all three new keys,
  scanned via `aletheore scan` + `aletheore diff`, confirming the PR comment
  output reflects `ignored_paths` (excluded findings don't appear anywhere),
  `disabled_checks` (that check's `checked: False` in evidence.json), and
  `severity_threshold` (only qualifying vulnerabilities appear in the diff).
