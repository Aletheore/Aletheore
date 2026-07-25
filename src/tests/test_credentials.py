import json

from aletheore.credentials import clear_api_key, get_api_key, has_api_key, save_api_token


def test_has_api_key_true_from_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("TESTPROVIDER_API_KEY", "sk-abc123")
    assert has_api_key("TESTPROVIDER_API_KEY", "testprovider", tmp_path / "creds.json") is True


def test_has_api_key_false_when_nothing_present(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    assert has_api_key("TESTPROVIDER_API_KEY", "testprovider", tmp_path / "creds.json") is False


def test_has_api_key_true_from_saved_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({"testprovider": "sk-saved"}))
    assert has_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path) is True


def test_get_api_key_returns_env_var_without_prompting(monkeypatch, tmp_path):
    monkeypatch.setenv("TESTPROVIDER_API_KEY", "sk-abc123")

    def fail_if_called(_msg):
        raise AssertionError("should not prompt when env var is set")

    result = get_api_key(
        "TESTPROVIDER_API_KEY", "testprovider", tmp_path / "creds.json", fail_if_called
    )
    assert result == "sk-abc123"


def test_get_api_key_returns_saved_key_without_prompting(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({"testprovider": "sk-saved"}))

    def fail_if_called(_msg):
        raise AssertionError("should not prompt when a saved key exists")

    result = get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path, fail_if_called)
    assert result == "sk-saved"


def test_get_api_key_prompts_and_discards_when_choice_is_once(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"
    responses = iter(["sk-entered", "once"])

    result = get_api_key(
        "TESTPROVIDER_API_KEY", "testprovider", creds_path, lambda _msg: next(responses)
    )

    assert result == "sk-entered"
    assert not creds_path.exists()


def test_get_api_key_prompts_and_saves_when_choice_is_save(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"
    responses = iter(["sk-entered", "save"])

    result = get_api_key(
        "TESTPROVIDER_API_KEY", "testprovider", creds_path, lambda _msg: next(responses)
    )

    assert result == "sk-entered"
    saved = json.loads(creds_path.read_text())
    assert saved["testprovider"] == "sk-entered"


def test_get_api_key_returns_none_when_prompt_cancelled(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"

    result = get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path, lambda _msg: "")

    assert result is None


def test_get_api_key_skips_prompt_when_not_a_tty_and_using_default_prompt_fn(monkeypatch, tmp_path):
    # Regression test for a real production incident: a worker process (no
    # stdin to answer a prompt) called get_api_key with the default
    # prompt_fn=input, which blocked until EOFError killed the job instead
    # of failing cleanly. Not passing prompt_fn here is the point - it
    # exercises the real default, not a test double.
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    creds_path = tmp_path / "creds.json"

    result = get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path)

    assert result is None


def test_get_api_key_still_prompts_when_a_custom_prompt_fn_is_supplied_even_off_tty(monkeypatch, tmp_path):
    # A caller that explicitly hands in its own prompt_fn (tests, or any
    # future caller with its own answer source) has opted out of the tty
    # guard - only the default input() path should be skipped.
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    creds_path = tmp_path / "creds.json"

    result = get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path, lambda _msg: "sk-from-double")

    assert result == "sk-from-double"


def test_save_key_sets_restrictive_permissions(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"
    responses = iter(["sk-entered", "save"])

    get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path, lambda _msg: next(responses))

    mode = creds_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_key_preserves_other_providers_existing_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("PROVIDER_B_KEY", raising=False)
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({"provider_a": "sk-a"}))
    responses = iter(["sk-b", "save"])

    get_api_key("PROVIDER_B_KEY", "provider_b", creds_path, lambda _msg: next(responses))

    saved = json.loads(creds_path.read_text())
    assert saved == {"provider_a": "sk-a", "provider_b": "sk-b"}


def test_save_api_token_is_readable_via_get_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"

    save_api_token("testprovider", "sk-from-device-flow", creds_path)

    def fail_if_called(_msg):
        raise AssertionError("should not prompt when a saved key exists")

    result = get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path, fail_if_called)
    assert result == "sk-from-device-flow"


def test_clear_api_key_removes_saved_key(tmp_path):
    path = tmp_path / "credentials.json"
    save_api_token("aletheore-managed-audit", "tok-123", path)
    assert has_api_key("UNUSED_ENV", "aletheore-managed-audit", credentials_path=path)

    removed = clear_api_key("aletheore-managed-audit", path)

    assert removed is True
    assert not has_api_key("UNUSED_ENV", "aletheore-managed-audit", credentials_path=path)


def test_clear_api_key_returns_false_when_nothing_to_clear(tmp_path):
    path = tmp_path / "credentials.json"
    assert clear_api_key("aletheore-managed-audit", path) is False
