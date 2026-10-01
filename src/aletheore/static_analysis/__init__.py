import hashlib
from pathlib import Path

from aletheore.static_analysis.bandit_scanner import check_bandit
from aletheore.static_analysis.bearer_scanner import check_bearer
from aletheore.static_analysis.gosec_scanner import check_gosec
from aletheore.static_analysis.joern_scanner import check_joern
from aletheore.static_analysis.pmd_scanner import check_pmd
from aletheore.static_analysis.semgrep_scanner import check_semgrep
from aletheore.static_analysis.sonarqube_scanner import check_sonarqube
from aletheore.static_analysis.trivy_scanner import check_trivy

# Semgrep/gosec/Bandit/Trivy are stateless CLI subprocess calls with no
# infra dependency and bounded, predictable runtime - real value
# demonstrated live (docs/audits/deterministic_scanner_evaluation.md), on
# by default the same way dependency_vulnerabilities/dependency_licenses
# already are. Trivy specifically real-timed on this repo before being
# added here, not assumed safe by category alone (2026-09-21): 3.15s on
# the github-app/ subtree (241 files), 10.46s on the real full ~3,331-file
# tree - well-scaling, not the pathological blowup Bearer's own real
# timing showed at the same jump (18.7s -> 300s+). Caught a real, live
# OpenAI API key in this repo's own .env that secrets.py has zero pattern
# coverage for (confirmed: grep -i openai src/aletheore/secrets.py matches
# nothing), and real Dockerfile misconfigurations detect_infrastructure
# structurally cannot produce (pure file-inventory, no misconfig
# analysis) - real, demonstrated value, not just a plausible addition.
# PMD is the same real-timed-first-then-decided addition (2026-09-21):
# 2.74s on google/gson (264 real Java files), 5.0s on apache/commons-lang
# (629 files) - well-scaling, no JVM-startup-blowup problem. Its default
# bestpractices+errorprone+security ruleset combo was NOT trustworthy
# as-is though: unfiltered against gson it produced 3,582 violations, 70%
# of them two JUnit-authoring-convention rules (WrongTestAnnotation,
# UnitTestContainsTooManyAsserts) flagging test-code style, not bugs, and
# CloseResource (a real bug-class rule in principle) sampled as a real
# false positive on the same repo (flagged a JsonTreeWriter - an in-memory
# tree builder whose close() is a no-op, not a real I/O resource). See
# pmd_scanner.py's _NOISY_RULES for the full, evidence-based exclude list -
# the remaining ~271 findings on that same repo were spot-checked as
# bug-shaped before trusting them.
# Bearer, Joern, and SonarQube are each opt-in, for different real reasons
# found live: SonarQube per the integration scope doc's hosting-cost
# tradeoff; Bearer because its full-repo runtime doesn't scale cleanly
# with repo size (18.7s on a 241-file subtree, still running past 300s on
# this repo's real ~3,331-file tree); Joern because a CPG build is real
# JVM-startup-plus-parsing cost (several real seconds even for one
# mid-sized package) and requires a whole separate toolchain most installs
# won't have. See cli.py's interactive ask-and-warn prompt for how Bearer
# specifically is opted into; Joern and SonarQube are flag/env-var opt-in
# without a prompt, since neither is likely to be installed/configured by
# default at all.
_SCANNERS = (
    ("semgrep", check_semgrep),
    ("gosec", check_gosec),
    ("bandit", check_bandit),
    ("trivy", check_trivy),
    ("pmd", check_pmd),
)


# One optional scanner, its own opt-in bool flag, and its own skip-reason
# when not opted into - kept as a tuple of (name, flag, scanner, skip
# reason) rather than three near-identical if/else blocks. SonarQube isn't
# here: it's gated by a host_url, not a plain bool, and always runs last.
_OPTIONAL_SCANNERS = (
    (
        "bearer",
        check_bearer,
        "skipped (opt-in - pass --check-bearer to include it; useful but "
        "can take significantly longer than the other scanners on a large repo)",
    ),
    (
        "joern",
        check_joern,
        "skipped (opt-in - pass --check-joern to include it; requires Joern installed "
        "separately, and a CPG build is real per-scan JVM/parsing cost, not a fast "
        "stateless subprocess call like the other scanners here)",
    ),
)


def _run_scanner_safely(name: str, scanner, *args, **kwargs) -> dict:
    """Real bug found via audit (2026-09-21): every scanner here already
    self-skips gracefully ({"checked": False, "reason": ...}) for its own
    EXPECTED failure modes (tool not installed, no matching source, CLI
    exits non-zero), but nothing guarded against an UNEXPECTED one - a
    parsing bug on a real tool's malformed-but-valid-JSON output (e.g. an
    explicit `null` in a field a scanner's own parser assumed present, see
    semgrep_scanner.py's fix the same night this was found) would raise
    straight out of this loop and abort the whole static-analysis pass,
    not just that one tool's contribution - contradicting the "each
    scanner self-skips gracefully" design promise every other failure mode
    here honors."""
    try:
        return scanner(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {
            "checked": False,
            "reason": f"{name} raised an unexpected error: {type(exc).__name__}: {exc}",
            "findings": [],
        }


def _content_fingerprint(repo_path: Path, path: str, line: int, file_cache: dict[str, list[str] | None]) -> str | None:
    """Hash of the 3-line window (line-1, line, line+1, each trimmed) around
    a finding's own reported line, read from the checkout while it's still
    on disk here - this is history.py's one alternative to matching a
    static-analysis finding's identity on its exact line number (see
    _static_analysis_identity there). An unrelated edit earlier in the same
    file shifts every later finding's line number without changing what's
    actually on that line; this fingerprint doesn't move when the line
    doesn't move with it, only when the surrounding content itself does.

    Returns None - no fingerprint, history.py falls back to its old
    line-based identity - when there's nothing real to hash: no real line
    (the misconfig-finding case _static_analysis_annotations in jobs.py
    already special-cases, e.g. a Dockerfile-wide finding with no single
    offending line), the path isn't readable as UTF-8 text, or the line
    number is past the end of the file.

    Known, accepted imprecision, same class as dismissed_findings.py's own
    issue-text fingerprint: two genuinely different findings that happen to
    sit on identical surrounding lines (e.g. the same boilerplate repeated
    twice in one file) collapse to the same fingerprint. Narrower than
    hashing the rule's own (often generic, identical-across-every-call-site)
    message text alone would be, but not immune to it.
    """
    if path not in file_cache:
        try:
            file_cache[path] = (repo_path / path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            file_cache[path] = None
    lines = file_cache[path]
    if lines is None or line < 1 or line > len(lines):
        return None
    idx = line - 1
    window = [lines[i].strip() if 0 <= i < len(lines) else "" for i in (idx - 1, idx, idx + 1)]
    return hashlib.sha256("\n".join(window).encode()).hexdigest()[:16]


def _add_content_fingerprints(findings: list[dict], repo_path: Path) -> list[dict]:
    file_cache: dict[str, list[str] | None] = {}
    for finding in findings:
        path = finding.get("path")
        line = finding.get("line")
        if isinstance(path, str) and path and isinstance(line, int):
            finding["content_fingerprint"] = _content_fingerprint(repo_path, path, line, file_cache)
        else:
            finding["content_fingerprint"] = None
    return findings


def check_static_analysis(
    repo_path: Path,
    run_bearer: bool = False,
    run_joern: bool = False,
    sonarqube_host_url: str | None = None,
) -> dict:
    findings: list[dict] = []
    tools_run: list[str] = []
    tools_skipped: list[dict] = []
    opted_in = {"bearer": run_bearer, "joern": run_joern}

    for name, scanner in _SCANNERS:
        result = _run_scanner_safely(name, scanner, repo_path)
        if result["checked"]:
            tools_run.append(name)
            findings.extend(result["findings"])
        else:
            tools_skipped.append({"tool": name, "reason": result["reason"]})

    for name, scanner, skip_reason in _OPTIONAL_SCANNERS:
        if opted_in[name]:
            result = _run_scanner_safely(name, scanner, repo_path)
            if result["checked"]:
                tools_run.append(name)
                findings.extend(result["findings"])
            else:
                tools_skipped.append({"tool": name, "reason": result["reason"]})
        else:
            tools_skipped.append({"tool": name, "reason": skip_reason})

    sonarqube_result = _run_scanner_safely(
        "sonarqube", check_sonarqube, repo_path, host_url=sonarqube_host_url
    )
    if sonarqube_result["checked"]:
        tools_run.append("sonarqube")
        findings.extend(sonarqube_result["findings"])
    else:
        tools_skipped.append({"tool": "sonarqube", "reason": sonarqube_result["reason"]})

    return {
        "checked": True,
        "tools_run": tools_run,
        "tools_skipped": tools_skipped,
        "findings": _add_content_fingerprints(findings, repo_path),
    }
