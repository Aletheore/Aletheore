import json
import types

import httpx
import pytest

from aletheore import skill_hub, skill_hub_sync


def _resp(method: str, url: str, status: int = 200, **json_body):
    # httpx.Response.raise_for_status() needs a request attached even on a
    # 2xx response (it reads self._request unconditionally) - a bare
    # httpx.Response(200, json=...) with no `request=` blows up there, so
    # every mocked response in this file goes through this helper instead.
    return httpx.Response(status, request=httpx.Request(method, url), json=json_body)


# --- Test doubles -----------------------------------------------------------
#
# skill_hub_sync calls module-level httpx.get/httpx.put directly (the same
# convention search_index.py already uses for its own direct httpx calls),
# so these fixtures monkeypatch those two attributes on skill_hub_sync's own
# `httpx` reference rather than injecting a client object.


@pytest.fixture
def http_client_that_raises_if_called(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("HTTP must not be called")

    monkeypatch.setattr(skill_hub_sync.httpx, "put", _fail)
    monkeypatch.setattr(skill_hub_sync.httpx, "get", _fail)


class _Call:
    def __init__(self, method, url, **kwargs):
        self.method = method
        self.url = url
        self.timeout = kwargs.get("timeout")
        self.json = kwargs.get("json")
        self.headers = kwargs.get("headers")


@pytest.fixture
def recording_http_client(monkeypatch):
    recorder = types.SimpleNamespace(last_call=None)

    def _put(url, **kwargs):
        recorder.last_call = _Call("PUT", url, **kwargs)
        return _resp(
            "PUT",
            url,
            name="my-review",
            content=kwargs.get("json", {}).get("content"),
            updated_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)
    return recorder


@pytest.fixture
def http_client_that_times_out(monkeypatch):
    def _put(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)


@pytest.fixture
def http_client_that_raises(monkeypatch):
    def _get(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)


@pytest.fixture
def fake_server_with_one_skill(monkeypatch):
    """Server has exactly one skill the local machine has never seen."""

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp(
                "GET",
                url,
                skills=[{"name": "server-only-skill", "updated_at": "2026-01-01T00:00:00+00:00"}],
            )
        if url.endswith("/v1/custom-skills/server-only-skill"):
            return _resp(
                "GET",
                url,
                name="server-only-skill",
                content="server content",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        raise AssertionError(f"unexpected GET {url}")

    def _put(*args, **kwargs):
        raise AssertionError("PUT must not be called when only pulling a server-only skill")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)


@pytest.fixture
def fake_server_recording_puts(monkeypatch):
    """Server has no skills at all - any local skill is offline-added and
    must be pushed up at the end of the merge loop."""
    recorder = types.SimpleNamespace(calls={})

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp("GET", url, skills=[])
        raise AssertionError(f"unexpected GET {url}")

    def _put(url, **kwargs):
        name = url.rsplit("/", 1)[-1]
        recorder.calls[name] = kwargs.get("json", {}).get("content")
        return _resp(
            "PUT", url, name=name, content=recorder.calls[name], updated_at="2026-01-02T00:00:00+00:00"
        )

    def received(name):
        return name in recorder.calls

    recorder.received = received
    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)
    return recorder


@pytest.fixture
def fake_server_matching_last_sync_state(tmp_path, monkeypatch):
    """The sidecar sync state already records this exact content and this
    exact server updated_at - a true tie: nothing changed on either side."""
    synced_at = "2026-01-01T00:00:00+00:00"
    content_hash = skill_hub_sync._content_hash("same content")
    state = {"unchanged-skill": {"synced_at": synced_at, "content_hash": content_hash}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")

    recorder = types.SimpleNamespace(get_content_called=False, put_called=False)

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp("GET", url, skills=[{"name": "unchanged-skill", "updated_at": synced_at}])
        recorder.get_content_called = True
        raise AssertionError(f"unexpected content GET {url}")

    def _put(*args, **kwargs):
        recorder.put_called = True
        raise AssertionError("PUT must not be called on a true tie")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)
    return recorder


@pytest.fixture
def fake_server_with_newer_updated_at(tmp_path, monkeypatch):
    """Sidecar state records a hash for OLD content that is not what the
    test is about to write locally - simulating a local edit since the
    last sync - and the server's updated_at has also moved on. The
    content-hash mismatch, not the timestamp, must decide this one."""
    old_hash = skill_hub_sync._content_hash("stale content from the last sync")
    state = {"edited-skill": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": old_hash}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")

    recorder = types.SimpleNamespace(pushed_content=None)

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp(
                "GET", url, skills=[{"name": "edited-skill", "updated_at": "2026-06-01T00:00:00+00:00"}]
            )
        raise AssertionError(f"unexpected content GET {url}")

    def _put(url, **kwargs):
        recorder.pushed_content = kwargs.get("json", {}).get("content")
        return _resp(
            "PUT",
            url,
            name="edited-skill",
            content=recorder.pushed_content,
            updated_at="2026-06-02T00:00:00+00:00",
        )

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)
    return recorder


@pytest.fixture
def fake_server_with_server_side_update(tmp_path, monkeypatch):
    """Sidecar state records a hash that MATCHES what the test will write
    locally (untouched since the last sync), but the server's updated_at
    is newer than what was recorded - the server changed since we last
    synced, on another machine. This is the one branch that overwrites
    existing local content and must be hardened."""
    synced_hash = skill_hub_sync._content_hash("old synced content")
    state = {"synced-skill": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": synced_hash}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp(
                "GET", url, skills=[{"name": "synced-skill", "updated_at": "2026-06-01T00:00:00+00:00"}]
            )
        if url.endswith("/v1/custom-skills/synced-skill"):
            return _resp(
                "GET",
                url,
                name="synced-skill",
                content="new content from another machine",
                updated_at="2026-06-01T00:00:00+00:00",
            )
        raise AssertionError(f"unexpected GET {url}")

    def _put(*args, **kwargs):
        raise AssertionError("PUT must not be called when pulling a server-side update")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)


# --- push_custom_skill_best_effort ------------------------------------------


def test_push_is_noop_with_no_personal_token(monkeypatch, http_client_that_raises_if_called):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    skill_hub_sync.push_custom_skill_best_effort("my-review", "x")  # must not raise, must not call http


def test_push_sends_put_with_timeout(monkeypatch, recording_http_client):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.push_custom_skill_best_effort("my-review", "x", timeout=5.0)
    assert recording_http_client.last_call.timeout == 5.0
    assert recording_http_client.last_call.method == "PUT"


def test_push_swallows_network_failure(monkeypatch, http_client_that_times_out):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.push_custom_skill_best_effort("my-review", "x")  # must not raise


def test_push_records_sync_state_on_success(tmp_path, monkeypatch, recording_http_client):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.push_custom_skill_best_effort("my-review", "x", timeout=5.0)
    state = json.loads((tmp_path / ".sync_state.json").read_text(encoding="utf-8"))
    assert state["my-review"]["synced_at"] == "2026-01-01T00:00:00+00:00"
    assert state["my-review"]["content_hash"] == skill_hub_sync._content_hash("x")


def test_push_does_not_record_sync_state_on_non_2xx(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")

    def _put(*args, **kwargs):
        return httpx.Response(500, json={"detail": "boom"})

    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)
    skill_hub_sync.push_custom_skill_best_effort("my-review", "x")  # must not raise
    assert not (tmp_path / ".sync_state.json").exists()


# --- pull_merge_custom_skills: five merge branches --------------------------


def test_pull_merge_is_noop_with_no_personal_token(monkeypatch, http_client_that_raises_if_called):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    skill_hub_sync.pull_merge_custom_skills()  # must not raise, must not call http


def test_pull_merge_writes_server_skill_missing_locally(tmp_path, monkeypatch, fake_server_with_one_skill):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.pull_merge_custom_skills()
    assert skill_hub.read_custom_skill_file("server-only-skill") == "server content"
    state = json.loads((tmp_path / ".sync_state.json").read_text(encoding="utf-8"))
    assert state["server-only-skill"]["content_hash"] == skill_hub_sync._content_hash("server content")


def test_pull_merge_pushes_local_skill_never_synced_before(tmp_path, monkeypatch, fake_server_recording_puts):
    # Server's list doesn't even mention this skill - never synced from
    # this machine (or anywhere), so the local copy wins.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("local-only-skill", "fresh")
    skill_hub_sync.pull_merge_custom_skills()
    assert fake_server_recording_puts.received("local-only-skill")
    assert fake_server_recording_puts.calls["local-only-skill"] == "fresh"


def test_pull_merge_unchanged_since_last_sync_is_noop(tmp_path, monkeypatch, fake_server_matching_last_sync_state):
    # Local content hash matches what the sidecar state recorded at the last
    # sync, and the server's updated_at hasn't advanced past that sync either
    # - nothing to push or pull. Deterministic no-op, not a raise.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("unchanged-skill", "same content")
    skill_hub_sync.pull_merge_custom_skills()  # must not raise
    assert fake_server_matching_last_sync_state.put_called is False
    assert fake_server_matching_last_sync_state.get_content_called is False


def test_pull_merge_local_edited_since_last_sync_pushes_even_if_server_also_moved(
    tmp_path, monkeypatch, fake_server_with_newer_updated_at
):
    # The content-hash mismatch (not a timestamp comparison) is what decides
    # this case: local changed since the sidecar's last recorded hash, so
    # local wins and gets pushed - this is what makes the merge decision
    # survive a git checkout or file copy that only touches mtime, never content.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("edited-skill", "newly edited content")
    skill_hub_sync.pull_merge_custom_skills()  # must not raise regardless of outcome
    assert fake_server_with_newer_updated_at.pushed_content == "newly edited content"


def test_pull_merge_server_changed_since_last_sync_pulls_and_backs_up(
    tmp_path, monkeypatch, fake_server_with_server_side_update
):
    # The one branch that overwrites existing local content with content
    # that arrived over the network - must back up the previous content
    # to <name>.md.bak before overwriting.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("synced-skill", "old synced content")
    skill_hub_sync.pull_merge_custom_skills()
    assert skill_hub.read_custom_skill_file("synced-skill") == "new content from another machine"
    backup_path = tmp_path / "synced-skill.md.bak"
    assert backup_path.read_text(encoding="utf-8") == "old synced content"


def test_pull_merge_server_changed_since_last_sync_logs_visible_notice(
    tmp_path, monkeypatch, fake_server_with_server_side_update, caplog
):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("synced-skill", "old synced content")
    with caplog.at_level("WARNING"):
        skill_hub_sync.pull_merge_custom_skills()
    assert any(
        "synced-skill" in record.message and "synced from another machine" in record.message
        for record in caplog.records
    )


def test_pull_merge_network_failure_is_logged_not_raised(monkeypatch, http_client_that_raises):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.pull_merge_custom_skills()  # must not raise


def test_pull_merge_sends_requests_with_given_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    seen = {}

    def _get(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _resp("GET", url, skills=[])

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    skill_hub_sync.pull_merge_custom_skills(timeout=42.0)
    assert seen["timeout"] == 42.0


# --- _personal_token guarded-read pattern -----------------------------------


def test_personal_token_returns_none_when_has_api_key_false(monkeypatch):
    monkeypatch.setattr(skill_hub_sync.credentials, "has_api_key", lambda *a, **k: False)

    def _fail_get_api_key(*args, **kwargs):
        raise AssertionError("get_api_key must not be called when has_api_key is False")

    monkeypatch.setattr(skill_hub_sync.credentials, "get_api_key", _fail_get_api_key)
    assert skill_hub_sync._personal_token() is None


def test_personal_token_returns_saved_token_when_has_api_key_true(monkeypatch):
    monkeypatch.setattr(skill_hub_sync.credentials, "has_api_key", lambda *a, **k: True)
    monkeypatch.setattr(skill_hub_sync.credentials, "get_api_key", lambda *a, **k: "saved-tok")
    assert skill_hub_sync._personal_token() == "saved-tok"
