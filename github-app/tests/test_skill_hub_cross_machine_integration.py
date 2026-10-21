"""Task 15: the end-to-end, cross-machine proof of Skill Hub's core claim -

"add a custom skill on one machine, log in on a different, empty machine,
and it's just there locally - with zero further network calls."

This exercises the REAL client code (aletheore.skill_hub, skill_hub_sync,
device_auth) against the REAL server router (app_server.skill_hub_api, via
app_server.main.app) and a REAL Postgres (the same test database every other
github-app test in this directory uses), with no component mocked out
except the literal GitHub API call GitHub itself would make.

Why this lives in github-app/tests/, not src/tests/ (as the plan's letter
says): the hard part of this test is routing the client's synchronous,
module-level `httpx.get`/`httpx.put` calls (skill_hub_sync.py has no
injectable http_client parameter, unlike device_auth.py) into the real
FastAPI router without a live external server. The standard in-process
technique for that - httpx.ASGITransport, as github-app/tests/
test_skill_hub_api.py already uses - is async-only (AsyncClient only); it
cannot carry skill_hub_sync.py's synchronous calls. The fix used here: run
the real `app_server.main.app` (same object, same lifespan, same db_pool
wiring as production) on a real loopback socket in a background thread of
this same test process (see `live_skill_hub_server` below) and simply point
the client at `http://127.0.0.1:<port>`. That is still fully in-process and
local-only - no second OS process, no mocking of the thing this test exists
to prove - it just uses a real bound socket instead of an in-memory ASGI
transport, because the sync call sites leave no other clean seam. This
needs both `aletheore` and `app_server`'s dependencies (FastAPI, asyncpg,
uvicorn) importable in one process, which is exactly what
github-app/.venv-skillhub/ was built for.
"""

import hashlib
import logging
import socket
import threading
import time
import warnings

import httpx
import pytest
import uvicorn

from aletheore import credentials, device_auth, skill_hub, skill_hub_sync
from app_server.config import get_settings
from app_server.main import app

# Both "machines" in this test resolve to this one fake GitHub identity -
# the real device flow would reach this by exchanging a GitHub OAuth token
# for `/user`'s id+login (see app_server.admin._fetch_github_user); this
# test fakes that one GitHub-facing call (see _mock_fixed_github_identity)
# and lets every call after it - personal-token minting, identity
# upsert/dedup, push, pull-merge - run for real.
_FAKE_GITHUB_USER_ID = 424242
_FAKE_GITHUB_LOGIN = "cross-machine-user"


def _mock_fixed_github_identity(monkeypatch) -> None:
    """Same technique test_skill_hub_api.py's _mock_github_user uses -
    patch app_server.admin's GitHub client factory so the server's own
    GET /user call during POST /v1/personal-tokens never leaves this
    process. Returns the same user id/login regardless of which fake
    bearer token either "machine" sends, which is what makes both
    machines' logins dedupe onto one personal_identities row.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/applications/") and request.url.path.endswith("/token"):
            return httpx.Response(200, json={"app": {"client_id": get_settings().github_client_id}})
        return httpx.Response(
            200, json={"id": _FAKE_GITHUB_USER_ID, "login": _FAKE_GITHUB_LOGIN}
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com"
        ),
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LogCapture(logging.Handler):
    """Captures uvicorn's own error-log records (e.g. the OSError it logs
    when a bind fails) so a startup failure can be diagnosed from the
    assertion message itself, not just from pytest's captured stderr.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _start_server(port: int) -> tuple[uvicorn.Server, threading.Thread, list, _LogCapture]:
    """Starts uvicorn.Server.run() in a background thread, capturing both
    any exception it raises (uvicorn exits a failed startup - e.g. a port
    already in use - via sys.exit(), i.e. SystemExit, from inside the
    thread, rather than returning one) and uvicorn's own "uvicorn.error"
    log output (which carries the actual OSError text SystemExit alone
    would lose) so a bind failure is diagnosable from the fixture's own
    assertion message, not only from pytest's captured stderr.
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    errors: list[BaseException] = []
    log_capture = _LogCapture()

    def _run() -> None:
        uvicorn_logger = logging.getLogger("uvicorn.error")
        uvicorn_logger.addHandler(log_capture)
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001 - re-surfaced to the main thread below
            errors.append(exc)
        finally:
            uvicorn_logger.removeHandler(log_capture)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return server, thread, errors, log_capture


def _wait_for_start(server: uvicorn.Server, thread: threading.Thread, deadline_s: float = 10) -> None:
    deadline = time.monotonic() + deadline_s
    # Also bail out early if the thread has already died (a failed bind
    # exits the thread almost immediately; no point burning the full
    # deadline polling a thread that will never flip server.started).
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)


def _is_port_bind_error(log_messages: list[str]) -> bool:
    text = " ".join(log_messages).lower()
    return "address already in use" in text or "errno 48" in text or "errno 98" in text


@pytest.fixture
def live_skill_hub_server():
    """Runs the real app_server FastAPI app - same `app` object
    test_skill_hub_api.py exercises - on a real localhost port, in a
    background thread of this test process, lifespan events included (so
    it creates its own real asyncpg pool against DATABASE_URL, exactly as
    production does; conftest.py already points DATABASE_URL at the test
    Postgres). See the module docstring for why this is the real-socket
    approach rather than ASGITransport.

    Hardening notes:
    - `_free_port()` closes its probe socket before uvicorn binds the same
      port number - a TOCTOU window another process (this machine
      routinely runs multiple concurrent Claude sessions against this
      repo) could win in between. If the first bind loses that race, this
      retries once on a freshly chosen port before giving up.
    - Any exception raised inside the server thread (plus uvicorn's own
      logged OSError detail) is captured and folded into the startup
      assertion's failure message, instead of that failure only ever
      manifesting as a generic "did not start in time" with the real
      cause visible only in pytest's captured stderr.
    """
    port = _free_port()
    server, thread, errors, log_capture = _start_server(port)
    _wait_for_start(server, thread)

    if not server.started and _is_port_bind_error(log_capture.messages):
        # Likely TOCTOU loss on the first port - retry once on a new one.
        port = _free_port()
        server, thread, errors, log_capture = _start_server(port)
        _wait_for_start(server, thread)

    if not server.started:
        detail_parts = []
        if errors:
            detail_parts.append(f"thread exception: {errors[0]!r}")
        if log_capture.messages:
            detail_parts.append(f"uvicorn log: {'; '.join(log_capture.messages)}")
        detail = f" ({'; '.join(detail_parts)})" if detail_parts else ""
        assert False, f"uvicorn test server for skill hub did not start in time{detail}"

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            # The test's own assertions have already run and passed by
            # this point (we're in teardown) - a slow uvicorn shutdown
            # (e.g. a request still in flight) shouldn't fail an
            # otherwise-passing test, but it must not be silent either:
            # the background thread, and the port it's bound to, are
            # still alive after this fixture "finished".
            warnings.warn(
                "live_skill_hub_server: uvicorn server thread did not exit within "
                "10s of should_exit=True; it (and its bound port) may still be "
                "running in the background.",
                stacklevel=2,
            )


@pytest.mark.asyncio
async def test_add_on_one_machine_appears_after_login_on_another(
    tmp_path_factory, pool, monkeypatch, live_skill_hub_server
):
    # A stray ALETHEORE_PERSONAL_TOKEN in the environment would bypass the
    # saved-credentials file entirely (credentials.get_api_key/has_api_key
    # check the env var first) and silently defeat this test's whole
    # per-machine-credentials-file setup.
    monkeypatch.delenv("ALETHEORE_PERSONAL_TOKEN", raising=False)

    _mock_fixed_github_identity(monkeypatch)
    monkeypatch.setattr(skill_hub_sync, "DEFAULT_API_BASE_URL", live_skill_hub_server)

    # ---------------------------------------------------------------
    # "Machine A": configure skill_hub/credentials to use machine_a's
    # paths, log in (mint+save a real personal token via the real
    # endpoint chain), then add a custom skill.
    # ---------------------------------------------------------------
    machine_a = tmp_path_factory.mktemp("machine_a")
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine_a)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine_a / ".config" / "aletheore" / "credentials.json"
    )

    token_a = device_auth.mint_personal_token(
        github_token="fake-github-oauth-token-machine-a",
        label="machine-a (device flow)",
        api_base_url=live_skill_hub_server,
    )
    credentials.save_api_token("aletheore-personal", token_a)

    skill_hub.add_custom_skill("cross-machine-skill", "content from A")

    # Confirm the push in Task 13 actually landed server-side - query the
    # real Postgres row directly (a connection independent of the one the
    # live server's own lifespan-created pool uses), not just that
    # add_custom_skill's local file write succeeded.
    row = await pool.fetchrow(
        "SELECT content FROM custom_skills WHERE skill_name = 'cross-machine-skill'"
    )
    assert row is not None, "push_custom_skill_best_effort did not land the skill server-side"
    assert row["content"] == "content from A"

    # ---------------------------------------------------------------
    # "Machine B": brand-new, empty config dir, same GitHub identity
    # (same fake github_user_id, resolved via the same mocked /user
    # response - see _mock_fixed_github_identity). Confirm it starts
    # with nothing local, then run the login flow.
    # ---------------------------------------------------------------
    machine_b = tmp_path_factory.mktemp("machine_b")
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine_b)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine_b / ".config" / "aletheore" / "credentials.json"
    )

    assert skill_hub.list_custom_skill_names() == []

    token_b = device_auth.mint_personal_token(
        github_token="fake-github-oauth-token-machine-b",
        label="machine-b (device flow)",
        api_base_url=live_skill_hub_server,
    )
    # Different raw token than machine A's, same identity server-side -
    # mirrors two real machines each logging in independently.
    assert token_b != token_a
    credentials.save_api_token("aletheore-personal", token_b)

    skill_hub_sync.pull_merge_custom_skills()

    # ---------------------------------------------------------------
    # Now read it back on "machine B" with the network client disabled -
    # proves the read path is local-only even right after a sync.
    # ---------------------------------------------------------------
    def _network_disabled(*_args, **_kwargs):
        raise AssertionError("unexpected network call from a plain local skill read")

    monkeypatch.setattr(httpx, "get", _network_disabled)
    monkeypatch.setattr(httpx, "put", _network_disabled)

    assert skill_hub.get_skill("custom/cross-machine-skill").content == "content from A"


# ---
# Task 7: integration tests for the deletion/revocation behaviors built in
# Tasks 4-6: the pending-delete tombstone surviving a pull-merge (deletion
# must survive this machine's own resync), and logout's server-side token
# revocation (self and --all-devices) against the real live server.
#
# Same machinery as the original cross-machine test above: the real client
# modules, the real live server fixture, real Postgres state checked via
# `pool`. The one new mocking seam is logout's CLI entry point, which runs
# under typer's CliRunner exactly as src/tests/test_cli.py's own logout
# tests do.
# ---

from aletheore.cli import app as cli_app
from typer.testing import CliRunner


def _confirm_token_works(live_skill_hub_server: str, token: str) -> None:
    """Proves the raw token really authenticates against the real live
    server BEFORE the test revokes it - the precondition that makes the
    later 401 assertion meaningful (an assertion on a token that never
    worked can't fail for the right reason)."""
    response = httpx.get(
        f"{live_skill_hub_server}/v1/custom-skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, (
        f"token should authenticate before revocation, got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_add_remove_then_relogin_does_not_resurrect_the_skill(
    tmp_path_factory, pool, monkeypatch, live_skill_hub_server
):
    monkeypatch.delenv("ALETHEORE_PERSONAL_TOKEN", raising=False)

    _mock_fixed_github_identity(monkeypatch)
    monkeypatch.setattr(skill_hub_sync, "DEFAULT_API_BASE_URL", live_skill_hub_server)

    machine = tmp_path_factory.mktemp("resurrect_machine")
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine / ".config" / "aletheore" / "credentials.json"
    )

    token = device_auth.mint_personal_token(
        github_token="fake-github-oauth-token-resurrect",
        label="resurrect-machine (device flow)",
        api_base_url=live_skill_hub_server,
    )
    credentials.save_api_token("aletheore-personal", token)

    # Add a custom skill and confirm it really landed server-side (same
    # real-Postgres confirmation the original cross-machine test uses).
    skill_hub.add_custom_skill("doomed-skill", "content doomed to be deleted")
    row = await pool.fetchrow(
        "SELECT content FROM custom_skills WHERE skill_name = 'doomed-skill'"
    )
    assert row is not None, "push_custom_skill_best_effort did not land the skill server-side"

    # Remove it, but force the delete call to fail once (simulating an
    # offline machine) so the pending-delete tombstone path is what gets
    # exercised - not a delete that cleanly reached the server.
    def _offline_delete(*_args, **_kwargs):
        raise httpx.ConnectError("simulated offline machine")

    # Scoped, NOT monkeypatch.undo(): undo would also revert the temp-home,
    # credentials-path and server-URL redirects set up above, and the rest of
    # this test would then run against the developer's real config directory
    # and the real production API host.
    with monkeypatch.context() as offline:
        offline.setattr(skill_hub_sync.httpx, "delete", _offline_delete)
        skill_hub.remove_custom_skill("doomed-skill")

    # The local file is gone and the tombstone is recorded, but the server
    # still has the row (the delete never reached it).
    assert skill_hub.read_custom_skill_file("doomed-skill") is None
    assert "doomed-skill" in skill_hub_sync._load_pending_deletes()
    row = await pool.fetchrow(
        "SELECT content FROM custom_skills WHERE skill_name = 'doomed-skill'"
    )
    assert row is not None, "the simulated-offline delete must not have reached the server"

    # "Re-login": the same machine runs pull_merge_custom_skills again, as
    # login does (cli.py's login calls it right after saving a personal
    # token). The flush retries the delete - now unmocked, so it reaches
    # the real server - and branch 1 ("local missing -> pull") must stay
    # suppressed for this name even though the server's list (fetched
    # before the flush's effect was visible) still contains it.
    skill_hub_sync.pull_merge_custom_skills()

    # The skill must still be absent locally - NOT resurrected by branch 1.
    assert skill_hub.read_custom_skill_file("doomed-skill") is None, (
        "pull_merge_custom_skills resurrected a skill this machine deleted"
    )
    assert skill_hub.list_custom_skill_names() == []

    # And server-side, the retried delete must have actually removed the
    # row - real Postgres, not just the client's own view.
    row = await pool.fetchrow(
        "SELECT content FROM custom_skills WHERE skill_name = 'doomed-skill'"
    )
    assert row is None, "the tombstone flush did not delete the skill server-side"
    assert "doomed-skill" not in skill_hub_sync._load_pending_deletes()


@pytest.mark.asyncio
async def test_logout_revoked_token_cannot_be_used_again(
    tmp_path_factory, pool, monkeypatch, live_skill_hub_server
):
    monkeypatch.delenv("ALETHEORE_PERSONAL_TOKEN", raising=False)

    _mock_fixed_github_identity(monkeypatch)

    machine = tmp_path_factory.mktemp("logout_machine")
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine / ".config" / "aletheore" / "credentials.json"
    )

    # Mint a real personal token via the real endpoint chain (the same
    # mint_personal_token -> POST /v1/personal-tokens call login makes).
    token = device_auth.mint_personal_token(
        github_token="fake-github-oauth-token-logout",
        label="logout-machine (device flow)",
        api_base_url=live_skill_hub_server,
    )
    credentials.save_api_token("aletheore-personal", token)

    # Prove the token works before logout, so the post-logout 401 below
    # can only be explained by the revocation itself.
    _confirm_token_works(live_skill_hub_server, token)

    # Run the real `logout` command. It reads the token from the (machine-
    # scoped) credentials file and calls revoke_personal_token, whose
    # api_base_url default points at the real production host - point that
    # default at the live test server instead, exactly as the sync module
    # is pointed in the test above.
    monkeypatch.setattr(
        device_auth.revoke_personal_token,
        "__defaults__",
        (False, live_skill_hub_server, None),
    )

    result = CliRunner().invoke(cli_app, ["logout"])
    assert result.exit_code == 0, result.output

    # The local credential is gone...
    assert not credentials.has_api_key(
        "ALETHEORE_PERSONAL_TOKEN", "aletheore-personal", credentials_path=credentials.DEFAULT_CREDENTIALS_PATH
    )

    # ...and the same raw token is now rejected by the real live server.
    response = httpx.get(
        f"{live_skill_hub_server}/v1/custom-skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401, (
        f"a revoked token must be rejected by the server, got {response.status_code}"
    )

    # Server-side, the token row is really revoked in Postgres.
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = await pool.fetchrow(
        "SELECT revoked_at FROM personal_api_tokens WHERE token_hash = $1", token_hash
    )
    assert row is not None, "logout's revoke never touched this token's row"
    assert row["revoked_at"] is not None


@pytest.mark.asyncio
async def test_logout_all_devices_invalidates_a_second_machines_token(
    tmp_path_factory, pool, monkeypatch, live_skill_hub_server
):
    monkeypatch.delenv("ALETHEORE_PERSONAL_TOKEN", raising=False)

    _mock_fixed_github_identity(monkeypatch)

    # Two machines, same fake GitHub identity (so both tokens belong to one
    # personal_identities row server-side), each with its own real minted
    # token - mirroring the original cross-machine test's two-machine setup.
    machine_a = tmp_path_factory.mktemp("all_devices_machine_a")
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine_a)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine_a / ".config" / "aletheore" / "credentials.json"
    )
    token_a = device_auth.mint_personal_token(
        github_token="fake-github-oauth-token-all-devices-a",
        label="machine-a (device flow)",
        api_base_url=live_skill_hub_server,
    )
    credentials.save_api_token("aletheore-personal", token_a)

    machine_b = tmp_path_factory.mktemp("all_devices_machine_b")
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine_b)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine_b / ".config" / "aletheore" / "credentials.json"
    )
    token_b = device_auth.mint_personal_token(
        github_token="fake-github-oauth-token-all-devices-b",
        label="machine-b (device flow)",
        api_base_url=live_skill_hub_server,
    )
    assert token_b != token_a
    credentials.save_api_token("aletheore-personal", token_b)

    # Both tokens genuinely work before the revoke-all.
    _confirm_token_works(live_skill_hub_server, token_a)
    _confirm_token_works(live_skill_hub_server, token_b)

    # Machine A logs out with --all-devices: revoke every personal token on
    # the identity, not just its own.
    monkeypatch.setattr(skill_hub, "user_home", lambda: machine_a)
    monkeypatch.setattr(
        credentials, "DEFAULT_CREDENTIALS_PATH", machine_a / ".config" / "aletheore" / "credentials.json"
    )
    monkeypatch.setattr(
        device_auth.revoke_personal_token,
        "__defaults__",
        (False, live_skill_hub_server, None),
    )

    result = CliRunner().invoke(cli_app, ["logout", "--all-devices"])
    assert result.exit_code == 0, result.output

    # Machine A's own token is revoked (it authenticated the revoke-all)...
    response_a = httpx.get(
        f"{live_skill_hub_server}/v1/custom-skills",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response_a.status_code == 401, (
        f"machine A's own token must be revoked too, got {response_a.status_code}"
    )

    # ...and so is machine B's - the whole point of --all-devices.
    response_b = httpx.get(
        f"{live_skill_hub_server}/v1/custom-skills",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response_b.status_code == 401, (
        f"--all-devices must invalidate the other machine's token too, got {response_b.status_code}"
    )

    # Real Postgres: every token row for this identity is revoked.
    identity = await pool.fetchrow(
        "SELECT id FROM personal_identities WHERE github_user_id = $1", _FAKE_GITHUB_USER_ID
    )
    assert identity is not None
    unrevoked = await pool.fetchval(
        "SELECT count(*) FROM personal_api_tokens WHERE personal_identity_id = $1 AND revoked_at IS NULL",
        identity["id"],
    )
    assert unrevoked == 0, f"{unrevoked} token(s) for the identity survived --all-devices"
