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
