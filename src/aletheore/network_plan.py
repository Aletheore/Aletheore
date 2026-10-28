"""Local-only disclosure of the configured deterministic scan's network paths."""

import os
import shutil

from aletheore.preferences import is_crash_reporting_enabled
from aletheore.static_analysis.semgrep_scanner import _semgrep_disabled


def scan_network_plan(*, vulnerabilities: bool, licenses: bool, static_analysis: bool) -> list[dict]:
    """Describe potential traffic, not a packet trace or a request-count estimate."""
    semgrep = static_analysis and not _semgrep_disabled() and bool(shutil.which("semgrep"))
    sonar = static_analysis and bool(os.environ.get("SONARQUBE_HOST_URL")) and bool(shutil.which("sonar-scanner"))
    return [
        {
            "service": "OSV.dev",
            "enabled": vulnerabilities,
            "sends": "Dependency ecosystems, package names and versions; batch queries plus vulnerability-detail requests. Actual traffic depends on dependencies and cache.",
            "disable": "--no-check-vulnerabilities",
        },
        {
            "service": "Package registries / GitHub license API",
            "enabled": licenses,
            "sends": "Dependency names and versions to ecosystem registries (including PyPI, npm, Go, crates.io, Maven, RubyGems, Packagist, NuGet) or GitHub for Swift licenses; cache may avoid requests.",
            "disable": "--no-check-licenses",
        },
        {
            "service": "Semgrep",
            "enabled": semgrep,
            "sends": "Installed scanner uses --config=auto: registry rule downloads and Semgrep-controlled metrics. This wrapper does not disable metrics; third-party configuration governs additional behavior.",
            "disable": "ALETHEORE_DISABLE_SEMGREP=1 or --no-check-static-analysis",
        },
        {
            "service": "SonarQube",
            "enabled": sonar,
            "sends": "Installed sonar-scanner submits analysis data, potentially including source, to the configured SonarQube server; polls task status and retrieves issues. Server URL and credentials are not printed here.",
            "disable": "unset SONARQUBE_HOST_URL or --no-check-static-analysis",
        },
        {
            "service": "Sentry crash reporting",
            "enabled": is_crash_reporting_enabled(),
            "sends": "On an unexpected CLI crash only: scrubbed diagnostic exception data. No general usage reporting from Aletheore; exception messages may contain repository-derived details.",
            "disable": "ALETHEORE_CRASH_REPORTING=0 or aletheore config crash-reporting off",
        },
    ]


_SHIELD_OFF = "ALETHEORE_SHIELD_OFFLINE=1 or registry_lookups: false in the shield config"


def shield_network_plan(*, registry_lookups: bool) -> list[dict]:
    """Describe the package-registry lookups Shield's guard may make."""
    sends = (
        "Only the package name of each newly added dependency, to check that it exists and when it was first published. "
        "Never versions, repository names, file paths or source code."
    )
    return [
        {"service": "PyPI registry", "enabled": registry_lookups, "sends": sends, "disable": _SHIELD_OFF},
        {"service": "npm registry", "enabled": registry_lookups, "sends": sends, "disable": _SHIELD_OFF},
    ]
