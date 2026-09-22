import json
import shutil
import subprocess
from pathlib import Path

from aletheore.static_analysis._exclusions import count_real_files, excluded_dir_names, filter_findings, has_real_file

# Real noise found live testing this against gson (264 real Java files,
# 2026-09-21): the unfiltered bestpractices+errorprone+security combo
# produced 3,582 violations, 70% of them two JUnit-authoring-convention
# rules (WrongTestAnnotation, UnitTestContainsTooManyAsserts) that flag
# test-code style, not bugs. CloseResource - a real bug-class rule in
# principle - sampled as a real false positive on this same repo
# (Gson.java:545 flags a JsonTreeWriter, an in-memory tree builder whose
# close() is a no-op, not a real I/O resource PMD's heuristic can't tell
# apart from one that is). Excluded here rather than assumed safe by
# category alone - the remaining ruleset (271 findings on the same repo,
# CloseResource excluded) is bug-shaped: AssignmentInOperand,
# NullAssignment, AvoidCatchingGenericException, CompareObjectsWithEquals,
# and similar, each spot-checked against real source before trusting it.
_NOISY_RULES = frozenset(
    {
        "WrongTestAnnotation",
        "UnitTestContainsTooManyAsserts",
        "UnitTestShouldIncludeAssert",
        "TestClassWithoutTestCases",
        "AvoidDuplicateLiterals",
        "LooseCoupling",
        "ReplaceJavaUtilDate",
        "ReplaceJavaUtilCalendar",
        "AvoidLiteralsInIfCondition",
        "CloseResource",
    }
)

_RULESETS = "category/java/bestpractices.xml,category/java/errorprone.xml,category/java/security.xml"

DEFAULT_PMD_TIMEOUT_SECONDS = 180
PMD_BASE_TIMEOUT_SECONDS = 60
PMD_PER_FILE_SECONDS = 0.05
PMD_MAX_TIMEOUT_SECONDS = 1800

# PMD's own priority scale (1 = High, 5 = Low - see
# https://docs.pmd-code.org/latest/pmd_userdocs_extending_rules.html) -
# mapped down to the shared taxonomy the same way every other scanner
# here maps its own tool-specific scale.
_SEVERITY_MAP = {1: "critical", 2: "major", 3: "major", 4: "minor", 5: "info"}


def _scaled_timeout(repo_path: Path) -> int:
    file_count = count_real_files(repo_path)
    scaled = PMD_BASE_TIMEOUT_SECONDS + int(file_count * PMD_PER_FILE_SECONDS)
    return min(scaled, PMD_MAX_TIMEOUT_SECONDS)


def _exclude_args(repo_path: Path) -> list[str]:
    # Only top-level occurrences - PMD's --exclude takes a literal path,
    # not a confirmed glob (unlike Trivy's --skip-dirs), so this is a
    # best-effort native speedup for the common case (.git, node_modules,
    # vendor sitting at repo root); filter_findings below is the
    # authoritative backstop regardless, same as every other scanner here.
    args = []
    for name in excluded_dir_names(repo_path):
        candidate = repo_path / name
        if candidate.is_dir():
            args.append(f"--exclude={candidate}")
    return args


def check_pmd(repo_path: Path, timeout: int | None = None) -> dict:
    if not has_real_file(repo_path, "*.java"):
        return {"checked": True, "reason": None, "findings": []}

    binary = shutil.which("pmd")
    if binary is None:
        return {"checked": False, "reason": "pmd not installed", "findings": []}

    resolved_timeout = timeout if timeout is not None else _scaled_timeout(repo_path)
    cmd = [
        binary, "check",
        "-d", str(repo_path),
        "-R", _RULESETS,
        "-f", "json",
        "--no-cache",
        *_exclude_args(repo_path),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=resolved_timeout + 10, cwd=repo_path
        )
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"pmd timed out after {resolved_timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"pmd failed to run: {exc}", "findings": []}

    # 0 = no violations, 4 = violations found (real PMD convention,
    # confirmed live) - neither is a failure. Anything else (1 = a real
    # configuration/runtime error, confirmed live via a bad ruleset name)
    # is.
    if result.returncode not in (0, 4):
        return {
            "checked": False,
            "reason": f"pmd exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"pmd produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    findings = []
    for file_entry in payload.get("files", []):
        raw_path = file_entry.get("filename", "")
        # PMD's "filename" is absolute when -d is given an absolute path
        # (confirmed live, same real gap gosec_scanner.py already
        # relativizes for) - so paths line up with every other tool's
        # `path` field and with the diff-scoping this evidence feeds
        # elsewhere in the pipeline.
        try:
            path = str(Path(raw_path).resolve().relative_to(repo_path.resolve()))
        except ValueError:
            path = raw_path
        for violation in file_entry.get("violations") or []:
            rule = violation.get("rule", "")
            if rule in _NOISY_RULES:
                continue
            ruleset = violation.get("ruleset", "")
            findings.append(
                {
                    "tool": "pmd",
                    "rule_id": rule,
                    "severity": _SEVERITY_MAP.get(violation.get("priority"), "minor"),
                    "type": "vulnerability" if ruleset == "Security" else "bug",
                    "path": path,
                    "line": violation.get("beginline", 0),
                    "message": violation.get("description", "").strip(),
                }
            )

    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
