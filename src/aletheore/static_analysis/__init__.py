from pathlib import Path

from aletheore.static_analysis.bandit_scanner import check_bandit
from aletheore.static_analysis.bearer_scanner import check_bearer
from aletheore.static_analysis.gosec_scanner import check_gosec
from aletheore.static_analysis.joern_scanner import check_joern
from aletheore.static_analysis.semgrep_scanner import check_semgrep
from aletheore.static_analysis.sonarqube_scanner import check_sonarqube

# Semgrep/gosec/Bandit are stateless CLI subprocess calls with no infra
# dependency and bounded, predictable runtime - real value demonstrated
# live tonight (docs/audits/deterministic_scanner_evaluation.md), on by
# default the same way dependency_vulnerabilities/dependency_licenses
# already are. Bearer, Joern, and SonarQube are each opt-in, for different
# real reasons found live: SonarQube per the integration scope doc's
# hosting-cost tradeoff; Bearer because its full-repo runtime doesn't
# scale cleanly with repo size (18.7s on a 241-file subtree, still
# running past 300s on this repo's real ~3,331-file tree); Joern because a
# CPG build is real JVM-startup-plus-parsing cost (several real seconds
# even for one mid-sized package) and requires a whole separate toolchain
# most installs won't have. All three are genuinely useful - real findings
# nothing else here produces - but real enough a cost that a full
# `aletheore scan` shouldn't pay it without being asked. See cli.py's
# interactive ask-and-warn prompt for how Bearer specifically is opted
# into; Joern and SonarQube are flag/env-var opt-in without a prompt,
# since neither is likely to be installed/configured by default at all.
_SCANNERS = (
    ("semgrep", check_semgrep),
    ("gosec", check_gosec),
    ("bandit", check_bandit),
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
        result = scanner(repo_path)
        if result["checked"]:
            tools_run.append(name)
            findings.extend(result["findings"])
        else:
            tools_skipped.append({"tool": name, "reason": result["reason"]})

    for name, scanner, skip_reason in _OPTIONAL_SCANNERS:
        if opted_in[name]:
            result = scanner(repo_path)
            if result["checked"]:
                tools_run.append(name)
                findings.extend(result["findings"])
            else:
                tools_skipped.append({"tool": name, "reason": result["reason"]})
        else:
            tools_skipped.append({"tool": name, "reason": skip_reason})

    sonarqube_result = check_sonarqube(repo_path, host_url=sonarqube_host_url)
    if sonarqube_result["checked"]:
        tools_run.append("sonarqube")
        findings.extend(sonarqube_result["findings"])
    else:
        tools_skipped.append({"tool": "sonarqube", "reason": sonarqube_result["reason"]})

    return {
        "checked": True,
        "tools_run": tools_run,
        "tools_skipped": tools_skipped,
        "findings": findings,
    }
