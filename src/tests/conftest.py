import pytest


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


@pytest.fixture(autouse=True)
def _isolate_crash_reporting_preferences(tmp_path, monkeypatch):
    """Global safety net, same class of bug as _isolate_saved_credentials
    above: no test run should ever read or write this machine's real
    ~/.config/aletheore/preferences.json. Also forces crash reporting off
    for the whole suite by default (ALETHEORE_CRASH_REPORTING=0) - unlike
    the backend's Sentry setup (SENTRY_DSN simply unset in every test
    environment), this CLI's DSN is a baked-in constant, so nothing else
    stops a test that reaches main()'s crash path from configuring a real
    Sentry client against the live aletheore-cli project. Tests that
    specifically exercise the capture call override this env var
    themselves and stub sentry_sdk.capture_exception directly - never a
    real client.
    """
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")

    import aletheore.preferences as preferences

    fake = tmp_path / "preferences.json"
    real_default = preferences.DEFAULT_PREFERENCES_PATH
    real_load = preferences._load_preferences
    real_save = preferences._save_preference

    def _load_ignoring_the_real_file(preferences_path):
        if preferences_path == real_default:
            preferences_path = fake
        return real_load(preferences_path)

    def _save_ignoring_the_real_file(preferences_path, key, value):
        if preferences_path == real_default:
            preferences_path = fake
        return real_save(preferences_path, key, value)

    monkeypatch.setattr(preferences, "_load_preferences", _load_ignoring_the_real_file)
    monkeypatch.setattr(preferences, "_save_preference", _save_ignoring_the_real_file)
