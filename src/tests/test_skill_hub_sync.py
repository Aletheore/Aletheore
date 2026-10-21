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
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _fail)


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
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _get)


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
        if url.endswith("/v1/custom-skills/edited-skill"):
            return _resp(
                "GET",
                url,
                name="edited-skill",
                content="edit made on another machine",
                updated_at="2026-06-01T00:00:00+00:00",
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
    # The server also moved on, so its copy is saved before it is overwritten.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("edited-skill", "newly edited content")
    skill_hub_sync.pull_merge_custom_skills()  # must not raise regardless of outcome
    assert fake_server_with_newer_updated_at.pushed_content == "newly edited content"
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob("edited-skill.md.server-*.bak")] == ["edit made on another machine"]


def test_pull_merge_does_not_overwrite_a_server_copy_it_could_not_back_up(tmp_path, monkeypatch):
    # If the account copy cannot be fetched, the local-wins push is skipped
    # for this skill rather than destroying what we could not save.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    state = {"edited-skill": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": skill_hub_sync._content_hash("old")}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")
    skill_hub.write_custom_skill_file("edited-skill", "local edit")
    puts = []

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp("GET", url, skills=[{"name": "edited-skill", "updated_at": "2026-06-01T00:00:00+00:00"}])
        return _resp("GET", url, status=500, detail="boom")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", lambda *a, **k: puts.append(a))
    skill_hub_sync.pull_merge_custom_skills()
    assert puts == []


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
    backups = list(tmp_path.glob("synced-skill.md.synced-*.bak"))
    assert [b.read_text(encoding="utf-8") for b in backups] == ["old synced content"]


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


# --- delete_custom_skill_best_effort + pending-deletes tombstone ------------


@pytest.fixture
def recording_http_client_for_delete(monkeypatch):
    """Server accepts the delete (204 - "gone")."""
    recorder = types.SimpleNamespace(last_call=None)

    def _delete(url, **kwargs):
        recorder.last_call = _Call("DELETE", url, **kwargs)
        return _resp("DELETE", url, status=204)

    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _delete)
    return recorder


@pytest.fixture
def server_that_already_lacks_the_skill(monkeypatch):
    """Server returns 404 - it already doesn't have this skill, which this
    function must treat the same as a successful delete."""

    def _delete(url, **kwargs):
        return _resp("DELETE", url, status=404)

    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _delete)


@pytest.fixture
def fake_server_that_accepts_delete(monkeypatch):
    """The flush's retried DELETE now succeeds (simulating "back online").
    Because the merge's GET /v1/custom-skills list call happens strictly
    *after* the flush, a server that genuinely just accepted the delete no
    longer lists the name by the time that call happens - matching real
    server behavior, not a special case the merge loop needs to know about."""
    recorder = types.SimpleNamespace(delete_called=False, get_content_called=False)

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp("GET", url, skills=[])
        recorder.get_content_called = True
        raise AssertionError(f"unexpected content GET {url}")

    def _delete(url, **kwargs):
        recorder.delete_called = True
        return _resp("DELETE", url, status=204)

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _delete)
    return recorder


@pytest.fixture
def fake_server_that_rejects_delete(monkeypatch):
    """Server's list still has the skill, and the retried DELETE fails
    again (still offline) - the name must stay pending."""
    recorder = types.SimpleNamespace(get_content_called=False)

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp(
                "GET", url, skills=[{"name": "still-pending", "updated_at": "2026-01-01T00:00:00+00:00"}]
            )
        recorder.get_content_called = True
        raise AssertionError(f"unexpected content GET {url} - branch 1 must be suppressed")

    def _delete(*args, **kwargs):
        raise httpx.ConnectError("still offline")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _delete)
    return recorder


def test_delete_best_effort_removes_sync_state_on_success(
    tmp_path, monkeypatch, recording_http_client_for_delete
):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_sync_state("my-review", "2026-01-01T00:00:00Z", "somehash")
    skill_hub_sync.delete_custom_skill_best_effort("my-review")
    assert "my-review" not in skill_hub_sync._load_sync_state()
    assert "my-review" not in skill_hub_sync._load_pending_deletes()


def test_delete_best_effort_treats_404_as_already_gone(
    tmp_path, monkeypatch, server_that_already_lacks_the_skill
):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_sync_state("my-review", "2026-01-01T00:00:00Z", "somehash")
    skill_hub_sync._record_pending_delete("my-review")
    skill_hub_sync.delete_custom_skill_best_effort("my-review")  # must not raise
    assert "my-review" not in skill_hub_sync._load_sync_state()
    assert "my-review" not in skill_hub_sync._load_pending_deletes()


def test_delete_best_effort_records_pending_on_network_failure(tmp_path, monkeypatch, http_client_that_raises):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync.delete_custom_skill_best_effort("my-review")  # must not raise
    assert "my-review" in skill_hub_sync._load_pending_deletes()


def test_delete_best_effort_records_pending_on_server_error(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")

    def _delete(url, **kwargs):
        return _resp("DELETE", url, status=500)

    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _delete)
    skill_hub_sync.delete_custom_skill_best_effort("my-review")  # must not raise
    assert "my-review" in skill_hub_sync._load_pending_deletes()


def test_delete_best_effort_is_noop_with_no_personal_token(monkeypatch, http_client_that_raises_if_called):
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    skill_hub_sync.delete_custom_skill_best_effort("my-review")  # no-op, no http call, no raise


def test_pull_merge_flushes_pending_delete_before_syncing(tmp_path, monkeypatch, fake_server_that_accepts_delete):
    # A name is pending-delete from an earlier offline remove, and still
    # exists server-side. pull_merge's flush step must retry the DELETE;
    # once it succeeds, branch 1 ("local missing -> pull") must NOT fire
    # for that name even though the server still listed it before the flush.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_pending_delete("server-still-has-this")
    skill_hub_sync.pull_merge_custom_skills()
    assert skill_hub.read_custom_skill_file("server-still-has-this") is None
    assert "server-still-has-this" not in skill_hub_sync._load_pending_deletes()
    assert fake_server_that_accepts_delete.delete_called is True
    assert fake_server_that_accepts_delete.get_content_called is False


def test_pull_merge_skips_resurrection_when_flush_still_fails(tmp_path, monkeypatch, fake_server_that_rejects_delete):
    # The retry itself fails again (still offline) - the name must stay
    # pending and branch 1 must still be suppressed for it this run, not
    # silently pull it back down.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub_sync._record_pending_delete("still-pending")
    skill_hub_sync.pull_merge_custom_skills()  # must not raise
    assert skill_hub.read_custom_skill_file("still-pending") is None
    assert "still-pending" in skill_hub_sync._load_pending_deletes()
    assert fake_server_that_rejects_delete.get_content_called is False


def test_load_pending_deletes_tolerates_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    (tmp_path / ".pending_deletes.json").write_text("not valid json")
    assert skill_hub_sync._load_pending_deletes() == set()


def test_load_pending_deletes_returns_empty_set_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    assert skill_hub_sync._load_pending_deletes() == set()


def test_record_and_clear_pending_delete_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub_sync._record_pending_delete("a-skill")
    assert "a-skill" in skill_hub_sync._load_pending_deletes()
    skill_hub_sync._clear_pending_delete("a-skill")
    assert "a-skill" not in skill_hub_sync._load_pending_deletes()
    skill_hub_sync._clear_pending_delete("never-there")  # must not raise


# --- deletes made on another machine, and per-skill failure isolation --------


def _server_listing(monkeypatch, skills, contents=None, puts=None, fail_fetch=()):
    contents = contents or {}

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp("GET", url, skills=skills)
        name = url.rsplit("/", 1)[-1]
        if name in fail_fetch:
            raise httpx.ConnectError("flaky")
        return _resp("GET", url, name=name, content=contents[name], updated_at="2026-06-01T00:00:00+00:00")

    def _put(url, **kwargs):
        name = url.rsplit("/", 1)[-1]
        if puts is not None:
            puts[name] = kwargs.get("json", {}).get("content")
        return _resp("PUT", url, name=name, content="x", updated_at="2026-06-02T00:00:00+00:00")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", _put)
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", lambda *a, **k: _resp("DELETE", a[0], status=204))


def test_pull_merge_mirrors_a_delete_made_on_another_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("gone-elsewhere", "same content")
    state = {"gone-elsewhere": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": skill_hub_sync._content_hash("same content")}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")
    puts = {}
    _server_listing(monkeypatch, skills=[], puts=puts)

    skill_hub_sync.pull_merge_custom_skills()

    # Not re-uploaded: the other machine's delete wins, with a local backup kept.
    assert puts == {}
    assert skill_hub.read_custom_skill_file("gone-elsewhere") is None
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob("gone-elsewhere.md.deleted-*.bak")] == ["same content"]
    assert "gone-elsewhere" not in json.loads((tmp_path / ".sync_state.json").read_text(encoding="utf-8"))


def test_pull_merge_keeps_a_local_edit_made_after_a_remote_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("edited-after-delete", "edited here since the last sync")
    state = {"edited-after-delete": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": skill_hub_sync._content_hash("older")}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")
    puts = {}
    _server_listing(monkeypatch, skills=[], puts=puts)

    skill_hub_sync.pull_merge_custom_skills()

    assert puts == {"edited-after-delete": "edited here since the last sync"}
    assert skill_hub.read_custom_skill_file("edited-after-delete") == "edited here since the last sync"


def test_pull_merge_one_failing_skill_does_not_abort_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    skill_hub.write_custom_skill_file("offline-added", "added while offline")
    puts = {}
    _server_listing(
        monkeypatch,
        skills=[
            {"name": "flaky-one", "updated_at": "2026-06-01T00:00:00+00:00"},
            {"name": "fine-two", "updated_at": "2026-06-01T00:00:00+00:00"},
        ],
        contents={"fine-two": "good content"},
        puts=puts,
        fail_fetch=("flaky-one",),
    )

    skill_hub_sync.pull_merge_custom_skills()

    assert skill_hub.read_custom_skill_file("flaky-one") is None
    assert skill_hub.read_custom_skill_file("fine-two") == "good content"
    assert puts == {"offline-added": "added while offline"}


# --- account switches and re-adding a removed skill ---------------------------


def test_adding_a_skill_back_clears_its_pending_server_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)  # no network in this test
    skill_hub_sync._record_pending_delete("comeback")
    skill_hub.add_custom_skill("comeback", "back again")
    assert "comeback" not in skill_hub_sync._load_pending_deletes()


def test_readded_skill_survives_the_next_login(tmp_path, monkeypatch):
    # remove offline (server unreachable -> pending delete), add the same name
    # back, then log in: the skill must still be on disk afterwards.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    state = {"comeback": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": skill_hub_sync._content_hash("v1")}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")
    skill_hub_sync._record_pending_delete("comeback")
    skill_hub.write_custom_skill_file("comeback", "v1")  # re-added with the same content
    skill_hub_sync._clear_pending_delete("comeback")  # what add_custom_skill now does
    puts = {}
    _server_listing(monkeypatch, skills=[{"name": "comeback", "updated_at": "2026-01-01T00:00:00+00:00"}], puts=puts)
    skill_hub_sync.pull_merge_custom_skills()
    assert skill_hub.read_custom_skill_file("comeback") == "v1"


def test_login_with_a_different_account_moves_local_skills_aside(tmp_path, monkeypatch):
    base = tmp_path / "custom-skills"
    base.mkdir()
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: base)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    (base / ".sync_account").write_text("account-A", encoding="utf-8")
    skill_hub.write_custom_skill_file("a-only", "belongs to account A")
    state = {"a-only": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": skill_hub_sync._content_hash("belongs to account A")}}
    (base / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")
    puts = {}

    def _get(url, **kwargs):
        if url.endswith("/v1/custom-skills"):
            return _resp("GET", url, skills=[], account_id="account-B")
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", lambda url, **k: puts.update({url: k}) or _resp("PUT", url, updated_at="x"))

    skill_hub_sync.pull_merge_custom_skills()

    # Neither uploaded into account B nor deleted as "deleted elsewhere": kept in a backup folder.
    assert puts == {}
    assert skill_hub.read_custom_skill_file("a-only") is None
    backups = list(tmp_path.glob("custom-skills-other-account-*.bak"))
    assert len(backups) == 1
    assert (backups[0] / "a-only.md").read_text(encoding="utf-8") == "belongs to account A"
    assert (base / ".sync_account").read_text(encoding="utf-8") == "account-B"


def test_same_account_login_leaves_local_skills_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    (tmp_path / ".sync_account").write_text("account-A", encoding="utf-8")
    skill_hub.write_custom_skill_file("mine", "x")
    puts = {}

    def _get(url, **kwargs):
        return _resp("GET", url, skills=[], account_id="account-A")

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "put", lambda url, **k: puts.update({url: k}) or _resp("PUT", url, updated_at="x"))
    skill_hub_sync.pull_merge_custom_skills()
    assert skill_hub.read_custom_skill_file("mine") == "x"
    assert len(puts) == 1  # never synced from here before, so it is uploaded as before


def test_repeated_conflicts_never_overwrite_an_earlier_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    first = skill_hub.unique_backup_path("demo", "server")
    first.write_text("one", encoding="utf-8")
    second = skill_hub.unique_backup_path("demo", "server")
    second.write_text("two", encoding="utf-8")
    assert first != second
    assert first.read_text(encoding="utf-8") == "one" and second.read_text(encoding="utf-8") == "two"


def test_removing_a_synced_skill_while_logged_out_leaves_a_tombstone_so_login_does_not_restore_it(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.write_custom_skill_file("was-synced", "content")
    state = {"was-synced": {"synced_at": "2026-01-01T00:00:00+00:00", "content_hash": skill_hub_sync._content_hash("content")}}
    (tmp_path / ".sync_state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)  # logged out
    skill_hub.remove_custom_skill("was-synced")
    assert "was-synced" in skill_hub_sync._load_pending_deletes()

    # next login: the server still lists it, the tombstone is flushed first and it is not pulled back
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: "tok")
    deleted = []
    server = {"skills": [{"name": "was-synced", "updated_at": "2026-01-01T00:00:00+00:00"}]}

    def _get(url, **kwargs):
        return _resp("GET", url, **server)

    def _delete(url, **kwargs):
        deleted.append(url.rsplit("/", 1)[-1])
        server["skills"] = []
        return _resp("DELETE", url, status=204)

    monkeypatch.setattr(skill_hub_sync.httpx, "get", _get)
    monkeypatch.setattr(skill_hub_sync.httpx, "delete", _delete)
    skill_hub_sync.pull_merge_custom_skills()
    assert deleted == ["was-synced"]
    assert skill_hub.read_custom_skill_file("was-synced") is None


def test_removing_a_never_synced_skill_while_logged_out_leaves_no_tombstone(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.write_custom_skill_file("local-only", "content")
    monkeypatch.setattr(skill_hub_sync, "_personal_token", lambda: None)
    skill_hub.remove_custom_skill("local-only")
    assert skill_hub_sync._load_pending_deletes() == set()
