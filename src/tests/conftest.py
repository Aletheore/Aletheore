import pytest


@pytest.fixture(autouse=True)
def _disable_cli_telemetry_in_tests(monkeypatch):
    """Global safety net: no test run should ever make a real network call
    to the hosted telemetry endpoint (aletheore.telemetry.report_scan_event
    fires from cli.py's _scan() on every real scan). Tests that specifically
    want to exercise aletheore.telemetry's own behavior override this
    locally via their own monkeypatch.delenv, same pattern as
    test_telemetry.py."""
    monkeypatch.setenv("ALETHEORE_TELEMETRY_DISABLED", "1")


@pytest.fixture(autouse=True)
def _isolate_license_and_vulnerability_caches(tmp_path, monkeypatch):
    """Global safety net: no test run should ever read or write this
    machine's real ~/.cache/aletheore/ - a stale real-world cache entry
    (from actually running the CLI by hand) silently swallowed a mocked
    HTTP response and made an otherwise-correct test fail nondeterministically
    depending on this machine's disk state, not the code under test."""
    monkeypatch.setattr(
        "aletheore.licenses.DEFAULT_LICENSE_CACHE_PATH", tmp_path / "license-cache.json"
    )
    monkeypatch.setattr(
        "aletheore.vulnerabilities.DEFAULT_VULNERABILITY_CACHE_PATH",
        tmp_path / "vulnerability-cache.json",
    )
