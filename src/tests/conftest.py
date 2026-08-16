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


@pytest.fixture(autouse=True)
def _isolate_saved_credentials(tmp_path, monkeypatch):
    """Global safety net: no test run should ever read this machine's real
    ~/.config/aletheore/credentials.json.

    Same class of bug as the cache isolation above, and it bit for real:
    after `aletheore login` saved a hosted-embeddings token, eight
    test_search_index.py tests started failing on this machine and passing in
    CI. build_index/search_index prefer hosted embeddings whenever a token
    resolves, so with a real credential on disk they took the hosted path and
    never called the `embed_texts` those tests patch - the suite was reading
    developer machine state, not the code under test.

    DEFAULT_CREDENTIALS_PATH is computed from Path.home() at import time and
    has no env override, so pointing it at tmp_path is the only isolation
    that does not also relocate HOME (which breaks package resolution).
    Tests that specifically exercise credential storage pass their own
    credentials_path already, so this never gets in their way.
    """
    import aletheore.credentials as credentials

    fake = tmp_path / "credentials.json"
    real_default = credentials.DEFAULT_CREDENTIALS_PATH
    real_loader = credentials._load_saved_key

    def _loader_ignoring_the_real_file(provider_name, credentials_path):
        # Redirect only the default path. Callers that pass an explicit path
        # (test_credentials.py, and the CLI's own --credentials flag) are
        # exercising credential storage deliberately and must keep working.
        if credentials_path == real_default:
            credentials_path = fake
        return real_loader(provider_name, credentials_path)

    # Patched at the loader rather than at DEFAULT_CREDENTIALS_PATH: several
    # call sites reach it through a *default argument value*, which Python
    # binds once at def time (e.g. search_index._embed_in_batches ->
    # get_api_key(...) with no credentials_path). Rebinding the module
    # constant afterwards cannot reach those.
    monkeypatch.setattr(credentials, "_load_saved_key", _loader_ignoring_the_real_file)
