import json
import shutil
import subprocess
from pathlib import Path

import yaml

from aletheore.static_analysis._exclusions import count_real_files, excluded_dir_names, filter_findings

# Matches the real, live-verified invocation from tonight's evaluation
# (docs/audits/deterministic_scanner_evaluation.md): `--config=auto` pulls
# Semgrep's community registry (1074 rules loaded against a real Go repo,
# no login required for the public ruleset) - confirmed working without any
# API key. Our own custom rules (oauth-state-not-random.yaml and anything
# added alongside it) load from the sibling semgrep_rules/ dir via a second
# --config, which Semgrep merges rather than replaces.
_CUSTOM_RULES_DIR = Path(__file__).parent / "semgrep_rules"

# Real bug found by an independent benchmark run the same night: a flat
# 180s (this package's original default) was fast enough for this repo's
# own ~3,331-file tree (12.8s, real-measured) but timed out on every real
# full-scan pass against a genuinely monorepo-scale Go repo
# (grafana/grafana) - the same "fast on a moderate repo, not fast on a
# huge one" gap already found and fixed for Bearer. Scaled the same way,
# generously over the one real rate this package has measured (~3.8ms/file
# at 3331 files/12.8s) rather than a tight fit to it - Semgrep is on by
# default (unlike Bearer/Joern), so under-scaling here silently degrades
# every large-repo scan's coverage, not just an opt-in one's.
SEMGREP_BASE_TIMEOUT_SECONDS = 60
SEMGREP_PER_FILE_SECONDS = 0.05
# 30 minutes is a real, meaningful cost for an on-by-default check on a
# large enough repo - flagged in the integration scope doc as worth a
# second look, not silently accepted as fine just because it's bounded.
SEMGREP_MAX_TIMEOUT_SECONDS = 1800

# Semgrep's own three-level severity (ERROR/WARNING/INFO) has no
# blocker/critical split, so ERROR is mapped to "critical" rather than
# "blocker" - "blocker" is reserved for a tool (SonarQube) that actually
# distinguishes the two. This is the draft severity/type mapping the
# integration scope doc flagged as a real judgment call worth a second
# look, not a settled taxonomy - revisit if it misranks real findings once
# results start flowing through PR review.
_SEVERITY_MAP = {"ERROR": "critical", "WARNING": "major", "INFO": "minor"}

# Semgrep rule metadata.category is a free-text field maintained per-rule by
# rule authors, not a closed enum - this covers the values actually seen in
# the registry's own rule set. Anything else (including a missing category)
# falls back to "bug", the safest default for a rule flagging real code
# behavior rather than a style/security concern specifically.
_CATEGORY_TYPE_MAP = {
    "security": "vulnerability",
    "correctness": "bug",
    "best-practice": "code_smell",
    "maintainability": "code_smell",
    "performance": "bug",
    "portability": "bug",
    "compatibility": "bug",
}


def _scaled_timeout(repo_path: Path) -> int:
    file_count = count_real_files(repo_path)
    return min(SEMGREP_MAX_TIMEOUT_SECONDS, int(SEMGREP_BASE_TIMEOUT_SECONDS + file_count * SEMGREP_PER_FILE_SECONDS))


def _relative_path(raw_path: str, repo_path: Path) -> str:
    try:
        return str(Path(raw_path).resolve().relative_to(repo_path.resolve()))
    except ValueError:
        return raw_path


def _custom_rule_ids() -> set[str]:
    ids: set[str] = set()
    for yaml_file in _CUSTOM_RULES_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(yaml_file.read_text())
        except (yaml.YAMLError, OSError):
            continue
        for rule in (data or {}).get("rules", []):
            rule_id = rule.get("id")
            if rule_id:
                ids.add(rule_id)
    return ids


def _clean_rule_id(check_id: str, custom_rule_ids: set[str]) -> str:
    # Real bug found live: Semgrep namespaces a LOCAL (non-registry) rule's
    # check_id by dot-joining the full path it was loaded from (confirmed:
    # loading semgrep_rules/ by its absolute path produced
    # "Users.arihantkaul.Documents.GitHub.Veridion.src.aletheore.static_
    # analysis.semgrep_rules.oauth-state-not-random" - unusable in a PR
    # comment). The rule's own short `id:` from its YAML always survives as
    # check_id's final dot-segment regardless of path depth, so any
    # check_id ending in one of our own known custom rule ids gets
    # rewritten to just that id. Registry rule ids (already clean, already
    # dotted on purpose, e.g. "go.lang.security.audit.xss.import-text-
    # template...") are left exactly as Semgrep produced them.
    last_segment = check_id.rsplit(".", 1)[-1]
    return last_segment if last_segment in custom_rule_ids else check_id


def check_semgrep(repo_path: Path, timeout: int | None = None) -> dict:
    binary = shutil.which("semgrep")
    if binary is None:
        return {"checked": False, "reason": "semgrep not installed", "findings": []}

    if timeout is None:
        timeout = _scaled_timeout(repo_path)

    cmd = [
        binary,
        "--config=auto",
        "--config", str(_CUSTOM_RULES_DIR),
        "--json",
        "--quiet",
        str(repo_path),
    ]
    # Real bug found live wiring this up: passing --metrics=off alongside
    # --config=auto is a hard Semgrep error ("Cannot create auto config
    # when metrics are off"), exit code 2, empty stdout - json.loads("{}")
    # on the empty fallback would have silently reported "checked: True,
    # zero findings" for what was actually a total scan failure. Not
    # passing --metrics=off at all, matching tonight's real, working,
    # live-verified invocation exactly - Semgrep's `auto` registry access
    # is designed around metrics being on, not a knob this wrapper can
    # override.
    for name in excluded_dir_names(repo_path):
        cmd.append(f"--exclude={name}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=repo_path)
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"semgrep timed out after {timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"semgrep failed to run: {exc}", "findings": []}

    # Semgrep's own exit codes: 0 = clean, 1 = findings present, >=2 = a
    # real failure (bad config, parse error the JSON output never
    # represents). Only 0/1 get treated as "ran successfully" - anything
    # else must surface as checked:False rather than risk parsing
    # leftover/partial stdout as if it were a complete, trustworthy result.
    if result.returncode not in (0, 1):
        return {
            "checked": False,
            "reason": f"semgrep exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"semgrep produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    custom_rule_ids = _custom_rule_ids()
    findings = []
    for item in payload.get("results", []):
        extra = item.get("extra", {})
        severity = extra.get("severity", "INFO")
        category = extra.get("metadata", {}).get("category", "")
        findings.append(
            {
                "tool": "semgrep",
                "rule_id": _clean_rule_id(item.get("check_id", ""), custom_rule_ids),
                "severity": _SEVERITY_MAP.get(severity, "minor"),
                "type": _CATEGORY_TYPE_MAP.get(category, "bug"),
                "path": _relative_path(item.get("path", ""), repo_path),
                "line": item.get("start", {}).get("line", 0),
                "message": (extra.get("message") or "").strip(),
            }
        )
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
