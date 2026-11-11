"""`council` command line: run a room server, join a room, assign work, raise conflicts, view and approve.

Credentials: the room token is read from the COUNCIL_TOKEN environment variable only (a command-line
argument would be visible to other users in the process list) and is never printed or written to disk.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import threading
import time
from pathlib import Path

import typer
from platformdirs import user_data_dir
from rich.console import Console
from rich.table import Table

from aletheore.council.approval import approve as do_approve, choose_winner, decide_plan, reject as do_reject, set_autonomy
from aletheore.council.bundle_store import BundleStore, LocalBundleStore
from aletheore.council.bundle_transport import TransportError
from aletheore.council.capabilities import inspect_agents as _inspect_builtin, inspect_user_agents

from aletheore.council.conductor import MAX_ROUNDS, MAX_STEPS, Conductor, stop_room
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.dispatcher import Dispatcher, send_ask, send_message, send_task
from aletheore.council.worker_lock import WorkerAlreadyRunning, WorkerLock
from aletheore.council.protocol import ProtocolError
from aletheore.council.resume import ResumeStore
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.room import Room
from aletheore.council.agent_adapters import AdapterError, adapter_runner, known_kinds
from aletheore.council.runners import WORKER_KINDS, ClaudeRunner, CodexRunner, WorkerRunner, worker_kind
from aletheore.council.server import parse_tokens, run_server
from aletheore.council import invites
from aletheore.council.accounts import AccountError
from aletheore.council.crypto import CryptoError
from aletheore.council.keys import GrantError, HostKeyError, KeyStore, KeyStoreError
from aletheore.council.router import RoomRouter
from aletheore.council.identity import Keystore, safety_words
from aletheore.council.server_config import check_bind, load_settings
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.slots import AgentSlots, LimitedRunner, slot_limit
from aletheore.council.state import Area, load_state
from aletheore.council.store import read_all
from aletheore.council.terminal import render_room, run_view


def inspect_agents():
    """The built-in agents plus any the person described in agents.json."""
    return _inspect_builtin() + inspect_user_agents()


app = typer.Typer(help="Aletheore Council: a shared room for coding agents")
console = Console()


def home() -> Path:
    return Path(os.environ.get("COUNCIL_HOME") or user_data_dir("aletheore-council"))


def remote_store(server: str, token: str) -> RemoteRoomStore:
    return RemoteRoomStore(server, token)


def _account_file() -> Path:
    return home() / "account.json"


def _save_account(server: str, handle: str) -> None:
    _account_file().parent.mkdir(parents=True, exist_ok=True)
    _account_file().write_text(json.dumps({"server": server.rstrip("/"), "handle": handle}), encoding="utf-8")


def _saved_account(server: str) -> dict | None:
    try:
        data = json.loads(_account_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("server") == server.rstrip("/") else None


def open_store():
    server = os.environ.get("COUNCIL_SERVER")
    if server:
        token = os.environ.get("COUNCIL_TOKEN")
        if token:
            return remote_store(server, token)
        saved = _saved_account(server)  # a device registered here (council register / join-link) signs in with its own key
        if saved and (home() / "keys" / "device.key").exists():
            store = remote_store(server, "")
            ident = device_identity()
            store.login(ident)
            # Rooms on an accounts server may be sealed: the router picks the right store for each room, so no command has to care.
            return RoomRouter(store, ident, KeyStore(home() / "room-keys.bin", ident))
        raise PermissionError("COUNCIL_SERVER is set but COUNCIL_TOKEN is not, and this device is not registered on that server "
                              "(use council register or council join-link)")
    return SqliteRoomStore(home() / "council.db")


def verify_me(store, me: str) -> None:
    """On a remote room the token already decides who you are; refuse a different --me up front."""
    who = getattr(store, "whoami", None)
    if who is not None:
        actual = who()
        if actual != me:
            raise PermissionError(f"--me {me} does not match this token (the token belongs to {actual})")


def bundle_store(store) -> BundleStore:
    return store if isinstance(store, (RemoteRoomStore, RoomRouter)) else LocalBundleStore(home() / "bundles")


def make_runner(agent: str) -> WorkerRunner:
    """An agent id ends in the kind of agent it is: 'claude', 'codex', 'opencode', 'agy', 'bob-claude', 'alice-codex', or one from agents.json.
    Every run waits for a free slot (COUNCIL_MAX_AGENTS, default 3, across all rooms on this machine)."""
    slots = AgentSlots(home(), slot_limit())
    kind = worker_kind(agent)
    if kind == "claude":
        return LimitedRunner(ClaudeRunner(model=os.environ.get("COUNCIL_CLAUDE_MODEL") or None), slots)
    if kind == "codex":
        return LimitedRunner(CodexRunner(model=os.environ.get("COUNCIL_CODEX_MODEL") or None), slots)
    if kind:
        try:
            return LimitedRunner(adapter_runner(kind), slots)
        except AdapterError as exc:
            raise typer.BadParameter(str(exc)) from None
    kinds = " or ".join(f"'{k}'" for k in known_kinds())
    raise typer.BadParameter(f"agent id must end in {kinds} (for example 'bob-{WORKER_KINDS[0]}'), got {agent!r}")


def guarded(fn):
    """Turn expected failures into one clean line and a non-zero exit, never a traceback (or a token)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise
        except PermissionError as exc:
            msg = f"not permitted: {exc}"
        except KeyError as exc:
            msg = f"not found: {exc.args[0] if exc.args else exc}"
        except (ProtocolError, TransportError, ValueError, WorkerAlreadyRunning, AccountError, GrantError, HostKeyError, KeyStoreError) as exc:
            msg = str(exc)
        except ConnectionError as exc:
            msg = f"cannot reach the room server: {exc}"
        console.print(msg, style="red", markup=False, highlight=False)
        raise typer.Exit(1)

    return wrapper


def _room_config(room_id: str) -> dict | None:
    path = home() / "rooms" / f"{room_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _room(room_id: str) -> Room:
    store = open_store()
    cfg = _room_config(room_id)
    if cfg:
        return Room.open_remote(store, room_id, Path(cfg["repo"]), home())
    return Room.open(store, room_id, home())


def _welcome_actions():
    from aletheore.council.tui_welcome import Actions

    def check(server: str) -> tuple[bool, int, str]:
        import httpx

        started = time.monotonic()
        try:
            resp = httpx.get(server.rstrip("/") + "/healthz", timeout=6)
        except httpx.HTTPError as exc:
            return False, 0, f"could not reach it ({type(exc).__name__}); check the address and your connection"
        ms = int((time.monotonic() - started) * 1000)
        return (True, ms, "the server answered") if resp.status_code == 200 else (False, ms, f"the server answered with status {resp.status_code}")

    def register_(server: str, code: str, handle: str) -> str:
        account = remote_store(server, "").register(code, handle, device_identity())
        _save_account(server, account["handle"])
        return f"registered as {account['handle']}. Run council again to open your rooms."

    def join_(link: str, handle: str) -> str:
        _, _, info, _, req = _ask_to_join(link, handle)
        return (f"asked to join {info.get('title') or info['room_id']}. Tell the host these words: {req['words']}. "
                "Once they approve, run council again.")

    return Actions(check, register_, join_)


def _default_entry(ctx: typer.Context) -> None:
    """`council` with nothing after it: your rooms if this device has an account, otherwise the first-run screen."""
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        typer.echo(ctx.get_help())  # not an interactive terminal: show the commands, as before
        raise typer.Exit()
    try:
        account = json.loads(_account_file().read_text(encoding="utf-8"))
        server, handle = str(account["server"]), str(account["handle"])
    except (OSError, ValueError, KeyError):
        server = handle = ""
    if server and handle:
        os.environ["COUNCIL_SERVER"] = server
        view(room=None, me=handle, plain=False)
        return
    from aletheore.council.tui_welcome import WelcomeApp

    ident = device_identity().public()
    WelcomeApp(ident.device_id, safety_words(ident.fingerprint), _welcome_actions(), home() / "sponsor-shown", os.environ.get("COUNCIL_SERVER", "")).run()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context,
         server: str = typer.Option(None, "--server", envvar="COUNCIL_SERVER", help="room server URL; the token comes from COUNCIL_TOKEN")) -> None:
    if server:
        os.environ["COUNCIL_SERVER"] = server
    if ctx.invoked_subcommand is None:
        _default_entry(ctx)


def serve(
    db: Path = typer.Option(None, "--db"),
    bundle_dir: Path = typer.Option(None, "--bundle-dir"),
    token: list[str] = typer.Option([], "--token", help="NAME=TOKEN (prefer --token-file: arguments are visible in the process list)"),
    token_file: Path = typer.Option(None, "--token-file", help="JSON file of {name: token}"),
    bundle_retention_days: int = typer.Option(None, "--bundle-retention-days"),
    host: str = typer.Option(None, "--host", help="default 127.0.0.1; a LAN address works on a network you trust (plain HTTP)"),
    port: int = typer.Option(None, "--port"),
    config: Path = typer.Option(None, "--config", help="JSON settings file (no secrets in it); environment variables COUNCIL_* override"),
    allow_public: bool = typer.Option(False, "--allow-public", help="allow a public or all-interfaces address (only behind a TLS proxy)"),
    accounts: bool = typer.Option(False, "--accounts", help="people register devices with one-time codes and sign in with their key (no token file needed)"),
    local: bool = typer.Option(False, "--local", help="this machine or your LAN only: no internet tunnel, no question asked"),
    no_wake_lock: bool = typer.Option(False, "--no-wake-lock", help="let the machine sleep (a sleeping host is an offline host)"),
    note: str = typer.Option("", "--note", help="a line shown to people when you stop, such as 'back tomorrow 10:00'"),
) -> None:
    try:
        settings = load_settings(config_file=config, environ=os.environ)
        if accounts or (not token and not token_file):
            settings.accounts = True  # `council serve` with nothing else is a room people join with their own device keys
        tokens = parse_tokens(list(token), str(token_file) if token_file else None, allow_empty=settings.accounts)
        if allow_public:
            settings.allow_public = True
        host = host or settings.host
        port = port or settings.port
        retention = bundle_retention_days or settings.retention_days
        check_bind(host, settings.allow_public)
    except (ValueError, OSError) as exc:
        console.print(f"cannot start: {exc}", style="red", markup=False)
        raise typer.Exit(2)
    mode = "accounts" if settings.accounts else f"{len(tokens)} participant(s)"
    console.print(f"Council room server on {host}:{port} for {mode}", markup=False, highlight=False)
    db_path = db or (Path(settings.db) if settings.db else home() / "server.db")
    try:
        from aletheore.council.host_runtime import announce_returned
        from aletheore.council.sqlite_store import SqliteRoomStore

        announce_returned(SqliteRoomStore(db_path))  # anyone who was waiting for this host sees that it is back
    except Exception:  # noqa: BLE001 - a first run has no rooms yet, and the notice must never stop the server  # nosec B110 - best effort: a failure here is expected and must not stop this path
        pass
    cleanup, public = _start_public_access(local, host, port, no_wake_lock)
    if public and settings.file_transfer_cap_mb_per_day == 0:
        settings.file_transfer_cap_mb_per_day = DEFAULT_PUBLIC_FILE_CAP_MB  # only files are capped, never messages, and not per room
        console.print(f"File transfers (images, work bundles) are capped at {DEFAULT_PUBLIC_FILE_CAP_MB // 1024} GB a day while this room is public. "
                      "Messages are not. Change it with COUNCIL_FILE_CAP_MB_PER_DAY.", markup=False, highlight=False)
    try:
        run_server(db_path, bundle_dir or (Path(settings.bundle_dir) if settings.bundle_dir else home() / "server-bundles"),
                   tokens, retention, host, port, settings=settings, host_identity=device_identity())
    finally:
        cleanup(db_path, note)


DEFAULT_PUBLIC_FILE_CAP_MB = 5 * 1024
# The free tunnel registry (deployed 2026-11-10) and the public half of its signing key, which clients pin. Override with COUNCIL_REGISTRY / COUNCIL_REGISTRY_KEY.
DEFAULT_REGISTRY_URL = "https://registry.aletheorecouncil.com"
DEFAULT_REGISTRY_KEY = "4f729840061e0897993922e7d6588268701fa8e842023f0d8453b3821dc1bb4e"


def _start_public_access(local: bool, host: str, port: int, no_wake_lock: bool):
    """Consent, the free tunnel and the wake lock around the server. Returns a function that undoes it all and tells people we are leaving."""
    from aletheore.council import host_runtime
    from aletheore.council.sponsor import SPONSOR

    stops: list = []
    lock = host_runtime.WakeLock()
    if not no_wake_lock and lock.acquire():
        stops.append(lock.release)
        console.print("Keeping this machine awake while the room is live (--no-wake-lock to turn this off).", markup=False, highlight=False)
    consent = host_runtime.Consent(home())
    wants = False if (local or host not in ("127.0.0.1", "localhost")) else consent.get()
    if wants is None and not local:
        wants = _ask_consent()
        if wants is not None:
            consent.set(wants)
    public = False
    _public_address_file().unlink(missing_ok=True)  # an address from an earlier run must never be put into a new invitation
    if wants:
        address = _open_tunnel(port, stops)
        public = bool(address)
        if address:
            _public_address_file().write_text(address, encoding="utf-8")
            console.print(f"Friends can reach this room at {address}", markup=False, highlight=False)
        console.print(SPONSOR, markup=False, highlight=False)

    def cleanup(db_path, note: str) -> None:
        try:
            from aletheore.council.sqlite_store import SqliteRoomStore

            host_runtime.announce_leaving(SqliteRoomStore(db_path), "", note)  # everyone connected sees it before the tunnel closes
        except Exception:  # noqa: BLE001 - shutting down must finish even if the notice cannot be written  # nosec B110 - best effort: a failure here is expected and must not stop this path
            pass
        for stop in stops:
            try:
                stop()
            except Exception:  # nosec B110 - best effort: a failure here is expected and must not stop this path
                pass

    return cleanup, public


def _ask_consent() -> bool | None:
    """The first-run question, asked once. None when nobody is at the keyboard (then nothing is opened, and it is asked next time)."""
    if not sys.stdin.isatty():
        return None
    return bool(typer.confirm("Make this room reachable from the internet so friends can join? (free tunnel)", default=True))


def _ask_download(question: str) -> bool | None:
    if not sys.stdin.isatty():
        return None
    return bool(typer.confirm(question, default=True))


def _public_address() -> str:
    """The address friends can reach this machine at, when `council serve` has published one (its own commands talk to the local server,
    but an invitation must carry the public address). COUNCIL_PUBLIC_ADDRESS overrides it. Empty when there is none."""
    given = os.environ.get("COUNCIL_PUBLIC_ADDRESS", "").strip()
    if given:
        return given
    try:
        return _public_address_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _public_address_file() -> Path:
    return home() / "public-address.txt"


def _open_tunnel(port: int, stops: list) -> str:
    """Claim a free slot from the registry and run its tunnel. Returns the public address, or "" (with the reason printed)."""
    import threading

    import httpx

    from aletheore.council import host_runtime
    from aletheore.council.registry_client import RegistryClient

    url = os.environ.get("COUNCIL_REGISTRY", DEFAULT_REGISTRY_URL)
    key = os.environ.get("COUNCIL_REGISTRY_KEY", DEFAULT_REGISTRY_KEY if url == DEFAULT_REGISTRY_URL else "")
    if not url or not key or url.lower() == "off":
        console.print("The free tunnel service is not open yet, so this room is reachable on this machine only. "
                      "Use your own tunnel or Tailscale, or run with --local to silence this.", markup=False, highlight=False)
        return ""
    from aletheore.council.cloudflared_helper import ensure_cloudflared

    binary = ensure_cloudflared(home(), _ask_download, lambda m: console.print(m, markup=False, highlight=False))
    if binary is None:
        return ""  # no tunnel program: do not take a slot from the pool that nothing would run
    try:
        client = RegistryClient(httpx.Client(base_url=url, timeout=15), device_identity(), bytes.fromhex(key))
        got = client.claim()
    except (ConnectionError, httpx.HTTPError, ValueError) as exc:
        console.print(f"Could not get a free tunnel: {exc}. This room is reachable on this machine only.", markup=False, highlight=False)
        return ""
    counter = got.get("counter", {})
    if got["status"] != "leased":
        console.print(f"{got.get('message') or 'no free slot'}", markup=False, highlight=False)
        return ""
    console.print(f"Free tunnel slots in use: {counter.get('in_use')} / {counter.get('total')}", markup=False, highlight=False)
    supervisor = host_runtime.TunnelSupervisor(got["token"], binary=str(binary), url=f"http://localhost:{port}")
    supervisor.start_in_background()
    stop_beat = threading.Event()
    threading.Thread(target=host_runtime.heartbeat_loop, args=(client.heartbeat, stop_beat), daemon=True, name="council-heartbeat").start()
    stops.extend([supervisor.stop, stop_beat.set])
    return "https://" + got["hostname"]


app.command("serve", help="run a room server on this machine (people join with their own device keys)")(serve)
app.command("server", hidden=True)(serve)


def device_identity():
    """This machine's identity: created on first use, kept in a private file under the Council home folder."""
    return Keystore(home() / "keys").load_or_create("device")


identity_app = typer.Typer(help="this device's identity", no_args_is_help=True)
admin_app = typer.Typer(help="server administration", no_args_is_help=True)
app.add_typer(identity_app, name="identity")
app.add_typer(admin_app, name="admin")


@identity_app.command("show", help="show this device's public name and safety words (never the private key)")
@guarded
def identity_show() -> None:
    ident = device_identity()
    pub = ident.public()
    console.print(f"Device: {pub.device_id}\nFingerprint: {pub.fingerprint}\nSafety words: {safety_words(pub.fingerprint)}",
                  markup=False, highlight=False)


@identity_app.command("export", help="save this device's key, encrypted with a passphrase, to move it to another machine or back it up")
@guarded
def identity_export(file: Path = typer.Argument(..., help="where to write the encrypted key (must not exist)")) -> None:
    from aletheore.council.identity import _write_private
    if file.exists():
        raise ValueError(f"{file} already exists; choose a new file name")
    device_identity()  # make sure there is a key to export
    passphrase = typer.prompt("Passphrase", hide_input=True, confirmation_prompt=True)
    if not passphrase:
        raise ValueError("the passphrase cannot be empty")
    blob = Keystore(home() / "keys").export("device", passphrase)
    _write_private(file, blob)
    console.print(f"Saved the encrypted key to {file}. Keep it and the passphrase safe: together they are this device.",
                  markup=False, highlight=False)


@identity_app.command("import", help="restore a device key saved with 'identity export' (never overwrites an existing key)")
@guarded
def identity_import(file: Path = typer.Argument(..., help="the encrypted key file")) -> None:
    if not file.is_file():
        raise KeyError(str(file))
    passphrase = typer.prompt("Passphrase", hide_input=True)
    try:
        Keystore(home() / "keys").import_(file.read_bytes(), passphrase)
    except FileExistsError:
        raise ValueError("this machine already has a device key; it is never overwritten") from None
    except CryptoError as exc:
        raise ValueError(str(exc)) from None
    console.print("Restored the device key. Run 'council identity show' to check the safety words.", markup=False, highlight=False)


@app.command(help="first run: show this device's identity and get it onto a server (register, or check that you are signed in)")
@guarded
def setup(
    server: str = typer.Option(None, "--server", envvar="COUNCIL_SERVER", help="room server URL"),
    code: str = typer.Option(None, "--code", envvar="COUNCIL_REGISTRATION_CODE", help="one-time registration code from the server's admin"),
    handle: str = typer.Option(None, "--handle", help="the name you were registered under"),
) -> None:
    ident = device_identity()
    pub = ident.public()
    console.print(f"Device: {pub.device_id}\nSafety words: {safety_words(pub.fingerprint)}  (read these aloud to compare devices)\n"
                  f"Your private key stays in {home() / 'keys'} and never leaves this machine; back that folder up.", markup=False, highlight=False)
    if not server:
        console.print("Next: set COUNCIL_SERVER (or pass --server) to the room server's address.", markup=False, highlight=False)
        return
    if _saved_account(server):
        who = remote_store(server, "").login(ident)
        console.print(f"Signed in as {who} on {server}. You can create rooms (if the admin allowed it) and join invitations.", markup=False, highlight=False)
        return
    if code and handle:
        account = remote_store(server, "").register(code, handle, ident)
        _save_account(server, account["handle"])
        console.print(f"Registered this device as {account['handle']}" + (" (may host rooms)." if account.get("can_host") else "."), markup=False, highlight=False)
        return
    console.print(f"This device is not registered on {server}. Either ask the server's admin to run "
                  "'council admin add-host YOUR-NAME' and send you the code, then run 'council setup --server ... --code ... --handle YOUR-NAME'; "
                  "or open an invitation link with 'council join-link LINK --handle YOUR-NAME'.", markup=False, highlight=False)


@app.command(help="register this device on a room server with the one-time code its admin gave you")
@guarded
def register(
    server: str = typer.Option(..., "--server", envvar="COUNCIL_SERVER", help="room server URL"),
    code: str = typer.Option(..., "--code", envvar="COUNCIL_REGISTRATION_CODE", help="one-time registration code"),
    handle: str = typer.Option(..., "--handle", help="the name you were registered under"),
) -> None:
    account = remote_store(server, "").register(code, handle, device_identity())
    _save_account(server, account["handle"])
    console.print(f"Registered this device as {account['handle']}" + (" (may host rooms)" if account.get("can_host") else "")
                  + f". Device {account['device_id']}.", markup=False, highlight=False)


def _admin_tool(db: Path | None) -> "AdminTool":
    """The admin tool for this machine's server. When an admin token hash is configured (setting, or the file `rotate-admin-token` wrote),
    COUNCIL_ADMIN_TOKEN must match it."""
    from aletheore.council.admin import AdminTool
    from aletheore.council.server import build_accounts, build_store

    settings = load_settings(environ=os.environ)
    path = db or (Path(settings.db) if settings.db else home() / "server.db")
    store = build_store(settings, path)
    accounts = build_accounts(settings, store, path, {})
    hash_file = home() / "admin-token.hash"
    admin_hash = settings.admin_token_hash or (hash_file.read_text().strip() if hash_file.exists() else "")
    bundle_dir = Path(settings.bundle_dir) if settings.bundle_dir else home() / "server-bundles"
    tool = AdminTool(store, accounts, bundle_dir, home() / "admin-audit.log", admin_hash)
    tool.authorize(os.environ.get("COUNCIL_ADMIN_TOKEN", ""))
    return tool


def _admin_guard(fn):
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        from aletheore.council.admin import AdminError

        try:
            return fn(*a, **k)
        except AdminError as exc:
            console.print(str(exc), style="red", markup=False, highlight=False)
            raise typer.Exit(1)

    return run


@admin_app.command("rooms", help="list every room on this server: host, members, events, size, last activity")
@_admin_guard
def admin_rooms(db: Path = typer.Option(None, "--db")) -> None:
    for r in _admin_tool(db).rooms():
        console.print(f"{r['room_id']}  {r['status']:6}  host {r['host']}  {r['members']} member(s)  {r['events']} events  {r['event_bytes']} bytes  "
                      f"last {r['last_activity'] or 'never'}" + ("  sealed" if r["sealed"] else ""), markup=False, highlight=False)


@admin_app.command("usage", help="totals: rooms, events, stored files, rooms per host")
@_admin_guard
def admin_usage(db: Path = typer.Option(None, "--db")) -> None:
    u = _admin_tool(db).usage()
    console.print(f"{u['rooms']} rooms ({u['open_rooms']} open), {u['events']} events ({u['event_bytes']} bytes), {u['files']} files ({u['file_bytes']} bytes)",
                  markup=False, highlight=False)
    for host, n in sorted(u["rooms_per_host"].items()):
        console.print(f"  {host}: {n} room(s)", markup=False, highlight=False)


@admin_app.command("delete-room", help="remove a room completely: events, members, invites, key grants, files. This cannot be undone")
@_admin_guard
def admin_delete_room(room: str = typer.Argument(...), db: Path = typer.Option(None, "--db"),
                      yes: bool = typer.Option(False, "--yes", help="do not ask")) -> None:
    if not yes and not typer.confirm(f"Delete room {room} and everything in it? This cannot be undone.", default=False):
        raise typer.Exit(1)
    done = _admin_tool(db).delete_room(room)
    console.print(f"deleted {room} ({done['files']} file(s), {done.get('invites', 0)} invite(s), {done.get('grants', 0)} key grant(s))", markup=False, highlight=False)


@admin_app.command("revoke-device", help="stop a device signing in (sealed rooms it belongs to should then be re-keyed by their hosts)")
@_admin_guard
def admin_revoke_device(device: str = typer.Argument(...), db: Path = typer.Option(None, "--db")) -> None:
    _admin_tool(db).revoke_device(device)
    console.print(f"revoked {device}", markup=False, highlight=False)


@admin_app.command("rotate-admin-token", help="make a new admin token (shown once); only its hash is stored")
@_admin_guard
def admin_rotate_token(db: Path = typer.Option(None, "--db")) -> None:
    token = _admin_tool(db).rotate_admin_token(home() / "admin-token.hash")
    console.print("New admin token (shown once, keep it somewhere safe; set it as COUNCIL_ADMIN_TOKEN to use the admin commands):", markup=False, highlight=False)
    console.print(token, markup=False, highlight=False, soft_wrap=True)


@admin_app.command("add-host", help="make a one-time registration code for a person (run on the server machine)")
@guarded
def admin_add_host(
    handle: str = typer.Argument(..., help="the person's handle: 3 to 32 lowercase letters, digits, - or _"),
    db: Path = typer.Option(None, "--db", help="the server's database file (SQLite servers)"),
    guest: bool = typer.Option(False, "--guest", help="may join rooms but not host them"),
    ttl_minutes: int = typer.Option(15, "--ttl-minutes", min=1, max=1440),
) -> None:
    from aletheore.council.server import build_accounts, build_store

    settings = load_settings(environ=os.environ)
    path = db or (Path(settings.db) if settings.db else home() / "server.db")
    store = build_store(settings, path)
    service = build_accounts(settings, store, path, {})
    code = service.create_registration_code(handle, can_host=not guest, ttl=ttl_minutes * 60)
    console.print(f"Registration code for {handle} ({'guest' if guest else 'may host'}): {code}\n"
                  f"Give it to them once. It works one time and expires in {ttl_minutes} minutes.", markup=False, highlight=False)


@app.command(help="create a room for a repository")
@guarded
def start(
    repo: Path = typer.Option(..., "--repo"), host: str = typer.Option(..., "--host"),
    title: str = typer.Option("", "--title", help="a name people will recognise"),
    mode: str = typer.Option("interactive", "--mode", help="interactive (people take part) or autonomous (the agents run it)"),
    goal: str = typer.Option("", "--goal", help="autonomous rooms: what the agents should achieve"),
    crypto: str = typer.Option("auto", "--crypto", help="auto (sealed end to end on a server with accounts, plain otherwise), e2e or none"),
) -> None:
    if mode == "autonomous" and not goal.strip():
        raise typer.BadParameter("an autonomous room needs a --goal for the agents to work on")
    if crypto not in ("auto", "e2e", "none"):
        raise typer.BadParameter("--crypto must be auto, e2e or none")
    store = open_store()
    can_seal = isinstance(store, RoomRouter)  # only a signed-in device has the keys to make a sealed room
    if crypto == "e2e" and not can_seal:
        raise ValueError("an end-to-end encrypted room needs a server with accounts: register this device first (council register or "
                         "council setup). A token-file server keeps rooms readable by the server.")
    mode_crypto = "e2e-v1" if (crypto == "e2e" or (crypto == "auto" and can_seal)) else "none"
    room = Room.create(store, repo, host, home(), title=title, mode=mode, crypto=mode_crypto)
    if mode == "autonomous":
        set_autonomy(store, room.room_id, host, True)  # nobody is there to approve: the room is bounded instead (see the conductor)
        send_message(store, room.room_id, host, goal, goal=True)
    console.print(f"Created room {room.room_id}", highlight=False, markup=False)


@app.command("mcp-control", help="run the control tools for an agent over stdio: set up rooms and invitations for you, never let anyone in")
@guarded
def mcp_control(me: str = typer.Option(..., "--me", help="your name in the rooms; fixed for this run")) -> None:
    from aletheore.council import mcp_control as control

    control.main(me)


@app.command(help="list the rooms you are in")
@guarded
def rooms(me: str = typer.Option(..., "--me")) -> None:
    store = open_store()
    verify_me(store, me)
    found = store.list_rooms(me)
    if not found:
        console.print("no rooms yet", highlight=False, markup=False)
    for r in found:
        console.print(f"{r.room_id}  {r.mode:<11} {r.status:<6} host={r.host} members={len(r.members)}  {r.title}",
                      highlight=False, markup=False, soft_wrap=True)


@app.command(help="host only: let someone in (they must be a person this server knows)")
@guarded
def invite(
    participant: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"),
) -> None:
    store = open_store()
    verify_me(store, me)
    if store.host_of(room) != me:
        raise PermissionError("only the host can invite people")
    store.add_member(room, participant)
    if hasattr(store, "grant_member"):
        store.grant_member(room, participant)  # a sealed room: they get its key too
    console.print(f"{participant} can now join {room}", highlight=False, markup=False)


def _host_store(room: str, me: str, action: str):
    store = open_store()
    verify_me(store, me)
    if store.host_of(room) != me:
        raise PermissionError(f"only the host can {action}")
    return store


@app.command("invite-link", help="host only: make a one-time invitation link for one person (you approve them before they get in)")
@guarded
def invite_link(
    room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"),
    ttl_hours: int = typer.Option(24, "--ttl-hours", min=1, max=168),
) -> None:
    store = _host_store(room, me, "make invitation links")
    server = _public_address() or os.environ.get("COUNCIL_SERVER")
    if not server:
        raise ValueError("invitation links need a room server: set COUNCIL_SERVER")
    # A device-registered host (its store is the router, or a signed-in remote store) pins its device in the link; a token host has none.
    ident = device_identity() if isinstance(store, RoomRouter) or getattr(store, "_identity", None) is not None else None
    link = invites.make_invite(store, server, ident, room, ttl=ttl_hours * 3600)
    console.print(f"Invitation link for room {room}:", markup=False, highlight=False)
    console.print(link.url, markup=False, highlight=False, soft_wrap=True)  # one unbroken line, so it can be copied
    console.print(f"Share it with one person. It works once and expires in {ttl_hours} hours. "
                  "Nobody gets in until you approve them (council requests, then council approve-join).", markup=False, highlight=False)


def _find_host(parsed) -> str:
    """The address that really is this host's. The link's own address first; if it is unreachable, or something else now answers there
    (a recycled name), ask the registry where the host is now and check THAT address proves it holds the host's key. Raises the
    original reason when the registry cannot help, so a person is never sent to the wrong place."""
    first_error: Exception | None = None
    try:
        info = remote_store(parsed.server, "").get_invite(parsed.invite_id)  # reachable, and it knows the invitation
        invites.check_host_pin(parsed.host_device_id, info.get("host"))
        invites.check_server_proves_host(parsed.server, parsed.host_device_id)
        return parsed.server
    except (ValueError, ConnectionError, KeyError, OSError) as exc:
        first_error = exc
    url = os.environ.get("COUNCIL_REGISTRY", DEFAULT_REGISTRY_URL)
    key = os.environ.get("COUNCIL_REGISTRY_KEY", DEFAULT_REGISTRY_KEY if url == DEFAULT_REGISTRY_URL else "")
    if not parsed.host_device_id or not url or not key or url.lower() == "off":
        raise first_error
    import httpx

    from aletheore.council.registry_client import RegistryClient

    try:
        where = RegistryClient(httpx.Client(base_url=url, timeout=15), device_identity(), bytes.fromhex(key)).where_is(parsed.host_device_id)
        if where.rstrip("/") == parsed.server.rstrip("/"):
            raise first_error  # the registry says the same place: nothing new to try
        info = remote_store(where, "").get_invite(parsed.invite_id)
        invites.check_host_pin(parsed.host_device_id, info.get("host"))
        invites.check_server_proves_host(where, parsed.host_device_id)
    except (ValueError, ConnectionError, KeyError, OSError) as second:
        if os.environ.get("COUNCIL_DEBUG"):
            print(f"[debug] registry fallback failed: {type(second).__name__}: {second}", file=sys.stderr)
        raise first_error from None
    return where


def _ask_to_join(text: str, handle: str):
    """Check an invitation link against the server, then ask to join with this device. Returns what the caller needs to wait."""
    parsed = invites.parse_link(text)
    server = _find_host(parsed)  # the link's address, or the registry's answer when the link's address is out of date
    if server != parsed.server:
        parsed = invites.ParsedLink(server, parsed.invite_id, parsed.secret, parsed.host_device_id)
    store = remote_store(parsed.server, "")
    info = store.get_invite(parsed.invite_id)
    invites.check_host_pin(parsed.host_device_id, info.get("host"))  # refuse a look-alike server before sending anything
    try:
        invites.unwrap_room_key(parsed.secret, bytes.fromhex(info["wrapped_room_key"]))  # the link must fit the invitation
    except (CryptoError, ValueError):
        raise ValueError("this link's key part does not match its invitation; ask the host for a new link") from None
    ident = device_identity()
    req = store.request_join(parsed.invite_id, handle, ident, invites.claim_key(parsed.secret))
    _save_account(parsed.server, handle)  # so later commands know which server this device is waiting on
    return parsed, store, info, ident, req


@app.command("join-link", help="ask to join a room from an invitation link (the host must approve you)")
@guarded
def join_link(
    link: str = typer.Argument(None, help="the whole link, including the part after #; or pipe it in on standard input"),
    handle: str = typer.Option(..., "--handle", help="the name you want in the room: 3 to 32 lowercase letters, digits, - or _"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="wait here until the host decides"),
    timeout_minutes: int = typer.Option(30, "--timeout-minutes", min=1, max=1440),
) -> None:
    text = link if link else sys.stdin.readline()
    parsed, store, info, ident, req = _ask_to_join(text, handle)
    title = info.get("title") or info["room_id"]
    console.print(f"Asked to join {title} (room {info['room_id']}). Tell the host these safety words so they know it is you: "
                  f"{req['words']}", markup=False, highlight=False)
    if not wait:
        console.print("Your request is waiting for the host. Run this command without --no-wait to wait for the answer here.",
                      markup=False, highlight=False)
        return
    deadline = time.monotonic() + timeout_minutes * 60
    while True:
        status = store.join_status(req["request_id"])
        if status["status"] == "approved":
            break
        if status["status"] == "rejected":
            _account_file().unlink(missing_ok=True)
            console.print("The host declined your request to join.", style="red", markup=False, highlight=False)
            raise typer.Exit(1)
        if time.monotonic() > deadline:
            console.print("Gave up waiting for the host. Ask them for a new link.", style="red", markup=False, highlight=False)
            raise typer.Exit(1)
        time.sleep(2)
    store.login(ident)
    console.print(f"Approved. You are in room {info['room_id']} as {handle}.", markup=False, highlight=False)


@app.command(help="host only: people waiting for you to let them in")
@guarded
def requests(room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    pending = _host_store(room, me, "see join requests").list_join_requests(room)
    if not pending:
        console.print("No one is waiting.", markup=False, highlight=False)
    for p in pending:
        console.print(f"{p['request_id']}  {p['handle']}  words: {p['words']}", markup=False, highlight=False, soft_wrap=True)  # one line: the words are read aloud


@app.command("approve-join", help="host only: let a waiting person into the room (check their safety words first)")
@guarded
def approve_join(request_id: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    store = _host_store(room, me, "approve people")
    done = store.approve_join(room, request_id)
    if hasattr(store, "grant_member"):
        store.grant_member(room, done["handle"])  # a sealed room: hand over its key now, while the host is here to sign it
    console.print(f"{done['handle']} is now in {room}.", markup=False, highlight=False)


@app.command("reject-join", help="host only: turn down a waiting person (their invitation link stops working)")
@guarded
def reject_join(request_id: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    done = _host_store(room, me, "turn people down").reject_join(room, request_id)
    console.print(f"Turned down {done['handle']}.", markup=False, highlight=False)


@app.command(help="host only: take someone out of a room (a sealed room moves to a new key, so they read nothing new)")
@guarded
def remove(participant: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    store = _host_store(room, me, "remove people")
    sealed = getattr(store, "keys_for", lambda r: None)(room) is not None
    store.remove_member(room, participant)
    console.print(f"{participant} was removed from {room}" + (" and the room now uses a new key." if sealed else "."), markup=False, highlight=False)


@app.command(help="host only: close a room (history stays readable, nothing new can be added)")
@guarded
def close(room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    store = open_store()
    verify_me(store, me)
    if store.host_of(room) != me:
        raise PermissionError("only the host can close a room")
    store.close_room(room)
    console.print(f"{room} is closed", highlight=False, markup=False)


@app.command(help="join a room hosted on another machine, using your own clone of the project")
@guarded
def join(
    room: str = typer.Option(..., "--room"),
    me: str = typer.Option(..., "--me"),
    repo: Path = typer.Option(..., "--repo"),
) -> None:
    store = open_store()
    host = store.host_of(room)  # also proves the token works
    Room.open_remote(store, room, repo, home())  # refuses, creating nothing, if this clone lacks the starting commit
    cfg = home() / "rooms" / f"{room}.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"repo": str(Path(repo).resolve()), "me": me}))
    console.print(f"Joined room {room} as {me} (host: {host})", highlight=False, markup=False)


@app.command(help="run this machine's worker: answers tasks and debate turns for your areas")
@guarded
def worker(
    room: str = typer.Option(..., "--room"),
    me: str = typer.Option(..., "--me"),
    agent: str = typer.Option(None, "--agent", help="only run this agent id (default: every agent of your areas)"),
    planner: str = typer.Option(None, "--planner", help="host only: also plan what people type, using this agent (claude or codex)"),
    max_rounds: int = typer.Option(MAX_ROUNDS, "--max-rounds", help="autonomous rooms: most plan rounds before the room stops"),
    max_steps: int = typer.Option(MAX_STEPS, "--max-steps", help="autonomous rooms: most tasks and debates in total before the room stops"),
) -> None:
    r = _room(room)
    dispatcher = None
    if planner:
        if me != r.store.host_of(room):
            raise PermissionError("only the host can run the planner")
        dispatcher = Conductor(Dispatcher(r.store, r, me, make_runner(planner), blobs=bundle_store(r.store), agent_runner=make_runner),
                               max_rounds=max_rounds, max_steps=max_steps)
    lock = WorkerLock(home(), room, me)
    lock.acquire()

    class _MyRunners(dict):
        """Runners for exactly the agents of the areas this participant owns (areas may be defined after start)."""

        def get(self, agent_id, default=None):
            owned = {a.agent_id for a in load_state(r.store, room).areas.values() if a.owner_participant == me}
            if agent_id not in owned or (agent and agent_id != agent):
                return default
            try:
                return make_runner(agent_id)
            except typer.BadParameter:
                return default

    daemon = WorkerDaemon(r.store, r, me, _MyRunners(), bundles=bundle_store(r.store), parallel=True)
    console.print(f"Worker for {me} running in room {room} (Ctrl+C to stop)", highlight=False, markup=False)
    stop = threading.Event()
    if dispatcher is not None:
        def plan_loop() -> None:
            while not stop.is_set():
                try:
                    dispatcher.run_once()
                except ConnectionError:
                    pass
                stop.wait(1.0)

        threading.Thread(target=plan_loop, daemon=True).start()
        console.print(f"Planner ({planner}) is turning prompts into plans for approval", highlight=False, markup=False)
    try:
        daemon.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
        console.print("stopping: waiting for running agent work to finish (Ctrl+C again to force quit)", highlight=False, markup=False)
    finally:
        daemon.close()
        lock.release()


@app.command(help="send a prompt: the host's planner turns it into a plan for the areas (needs host approval)")
@guarded
def ask(
    text: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"),
    image: list[Path] = typer.Option([], "--image", help="attach an image (png, jpg, gif, webp); repeatable"),
) -> None:
    store = open_store()
    verify_me(store, me)
    thread = send_message(store, room, me, text, images=list(image), blobs=bundle_store(store))
    console.print(f"sent as {thread}", highlight=False, markup=False)


@app.command(help="host only: approve or reject the planner's plan so its tasks go out")
@guarded
def plan(
    decision: str = typer.Argument(..., help="approve or reject"), plan_id: str = typer.Argument(...),
    room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"),
) -> None:
    if decision not in ("approve", "reject"):
        raise typer.BadParameter("decision must be 'approve' or 'reject'")
    store = open_store()
    verify_me(store, me)
    decide_plan(store, room, plan_id, me, decision == "approve")
    console.print(f"{plan_id}: {decision}d", highlight=False, markup=False)


@app.command(help="host only: stop an autonomous room's run (anything pending then needs a person again)")
@guarded
def stop(room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    store = open_store()
    verify_me(store, me)
    stop_room(store, room, me)
    console.print(f"{room}: stopped", highlight=False, markup=False)


@app.command(help="ask the room's agents a read-only question (they answer; nothing is changed)")
@guarded
def discuss(
    question: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"),
    to: str = typer.Option("all", "--to", help="all, one agent id, or a person (all of their agents)"),
) -> None:
    store = open_store()
    verify_me(store, me)
    thread = send_ask(store, room, me, to, question)
    console.print(f"asked @{to} as {thread}", highlight=False, markup=False)


@app.command(help="propose work for one area; the host approves it like any plan")
@guarded
def task(
    area: str = typer.Argument(...), instruction: str = typer.Argument(...),
    room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"),
) -> None:
    store = open_store()
    verify_me(store, me)
    thread = send_task(store, room, me, area, instruction)
    console.print(f"proposed as {thread}", highlight=False, markup=False)


@app.command(help="say something to the room without asking the planner to act")
@guarded
def say(text: str = typer.Argument(...), room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me")) -> None:
    store = open_store()
    verify_me(store, me)
    send_message(store, room, me, text, chat=True)
    console.print("sent", highlight=False, markup=False)


@app.command(help="show which agents are installed and what level each can join at")
def agents() -> None:
    table = Table("agent", "installed", "level", "notes")
    for c in inspect_agents():
        table.add_row(c.agent, "yes" if c.installed else "no", c.level, "; ".join(c.notes))
    console.print(table)


@app.command(help="give an area to an agent and run it here (single machine)", hidden=True)
@guarded
def assign(
    instruction: str = typer.Argument(...),
    room: str = typer.Option(..., "--room"),
    area: str = typer.Option(..., "--area"),
    glob: list[str] = typer.Option([], "--glob"),
    owner: str = typer.Option(None, "--owner"),
    agent: str = typer.Option(..., "--agent"),
) -> None:
    r = _room(room)
    if area not in load_state(r.store, room).areas:
        if not glob or not owner:
            console.print("a new area needs --glob and --owner", style="red", markup=False)
            raise typer.Exit(2)
        r.define_area(Area(area, list(glob), owner, agent))
    task_id = r.assign(area, instruction, make_runner(agent))
    console.print(f"{task_id}: {load_state(r.store, room).tasks[task_id].status}", highlight=False, markup=False)


@app.command(help="define an area (who owns which paths and runs which agent)")
@guarded
def area(
    name: str = typer.Argument(...),
    room: str = typer.Option(..., "--room"),
    glob: list[str] = typer.Option(..., "--glob"),
    owner: str = typer.Option(..., "--owner"),
    agent: str = typer.Option(..., "--agent"),
) -> None:
    _room(room).define_area(Area(name, list(glob), owner, agent))
    console.print(f"area {name}: owner {owner}, agent {agent}", highlight=False, markup=False)


@app.command(help="ask an area's owner machine to do some work (their worker runs it)")
@guarded
def request(
    instruction: str = typer.Argument(...),
    room: str = typer.Option(..., "--room"),
    area: str = typer.Option(..., "--area"),
) -> None:
    task_id = _room(room).request_task(area, instruction)
    console.print(f"requested {task_id}", highlight=False, markup=False)


@app.command(help="raise a conflict between two areas and run the debate")
@guarded
def conflict(
    subject: str = typer.Argument(...),
    room: str = typer.Option(..., "--room"),
    thread: str = typer.Option(..., "--thread"),
    a: str = typer.Option(..., "--a"),
    b: str = typer.Option(..., "--b"),
    planner: str = typer.Option("claude", "--planner"),
) -> None:
    r = _room(room)
    areas = load_state(r.store, room).areas
    runners = {areas[n].agent_id: make_runner(areas[n].agent_id) for n in (a, b) if areas[n].owner_participant == r.host}
    runners["planner"] = make_runner(planner)
    outcome = r.raise_conflict(thread, subject, a, b, runners)
    console.print(f"verdict {outcome.verdict_event.body['verdict_id']} after {outcome.rounds} round(s); converged={outcome.converged}", highlight=False, markup=False)


def pick_room(store, me: str) -> str:
    """The room `council view` opens when none is named: your most recently active open room."""
    mine = store.list_rooms(me)
    if not mine:
        raise KeyError("you are not in any rooms yet (create one with `council start`, or ask a host to invite you)")
    open_rooms = [r for r in mine if r.status == "open"] or mine
    return max(open_rooms, key=lambda r: (r.last_activity, r.last_seq)).room_id


@app.command(help="open the live view of a room (ctrl+r switches rooms; approve verdicts you own here)")
@guarded
def view(
    room: str = typer.Option(None, "--room", help="default: your most recently active room"),
    me: str = typer.Option(..., "--me"),
    plain: bool = typer.Option(False, "--plain", hidden=True, help="line-by-line view instead of the full-screen one"),
) -> None:
    store = open_store()
    verify_me(store, me)
    resume = ResumeStore(home() / "resume.json")
    if not room:
        remembered = resume.last_room()
        try:
            mine = {x.room_id for x in store.list_rooms(me)}
        except Exception:
            mine = set()
        room = remembered if remembered in mine else pick_room(store, me)  # where you were, if you are still in it
    r = _room(room)

    def make_apply(room_id: str):
        def apply_and_report(verdict_id: str) -> str:
            try:
                target = r if room_id == room else _room(room_id)
                res = target.apply(verdict_id, bundle_store(target.store))
                return f"applied {verdict_id}: {res.message}"
            except (ValueError, PermissionError, TransportError, KeyError) as exc:
                return f"not applied: {exc}"

        return apply_and_report

    if not plain and sys.stdout.isatty() and sys.stdin.isatty():
        from aletheore.council.tui import CouncilApp

        from aletheore.council.outbox import OutboxStore

        outbox_store = OutboxStore(r.store, home() / f"outbox-{me}.json")  # messages written while the host is away wait here
        CouncilApp(outbox_store, room, me, on_approved=make_apply(room), blobs=bundle_store(r.store), apply_factory=make_apply, resume=resume).run()
        return

    def on_approved(verdict_id: str) -> None:
        console.print(make_apply(room)(verdict_id), highlight=False, markup=False)

    try:
        run_view(r.store, room, me, console=console, capabilities=inspect_agents(), on_approved=on_approved)
    except KeyboardInterrupt:
        pass


def _decide(fn, room: str, verdict: str, me: str) -> None:
    store = open_store()
    verify_me(store, me)
    fn(store, room, verdict, me)
    console.print(f"{verdict}: recorded for {me}", highlight=False, markup=False)


@app.command(help="approve a verdict (area owner, or the host)")
@guarded
def approve(room: str = typer.Option(..., "--room"), verdict: str = typer.Option(..., "--verdict"), me: str = typer.Option(..., "--me")) -> None:
    _decide(do_approve, room, verdict, me)


@app.command(help="reject a verdict (area owner, or the host)")
@guarded
def reject(room: str = typer.Option(..., "--room"), verdict: str = typer.Option(..., "--verdict"), me: str = typer.Option(..., "--me")) -> None:
    _decide(do_reject, room, verdict, me)


@app.command(help="host only: pick the winning side when the planner could not decide")
@guarded
def choose(
    room: str = typer.Option(..., "--room"), verdict: str = typer.Option(..., "--verdict"),
    winner: str = typer.Option(..., "--winner", help="the winning agent id"), me: str = typer.Option(..., "--me"),
) -> None:
    store = open_store()
    verify_me(store, me)
    choose_winner(store, room, verdict, me, winner)
    console.print(f"{verdict}: winner {winner} chosen; it still needs approval", highlight=False, markup=False)


@app.command(help="merge an approved verdict's winning branch (fetches the bundle first if it came from another machine)")
@guarded
def apply(room: str = typer.Option(..., "--room"), verdict: str = typer.Option(..., "--verdict")) -> None:
    r = _room(room)
    res = r.apply(verdict, bundle_store(r.store))
    console.print(res.message, highlight=False, markup=False)
    if not res.ok:
        raise typer.Exit(1)


@app.command(help="turn autonomy mode on or off (host only)")
@guarded
def autonomy(room: str = typer.Option(..., "--room"), me: str = typer.Option(..., "--me"), mode: str = typer.Argument(...)) -> None:
    if mode not in ("on", "off"):
        raise typer.BadParameter("mode must be 'on' or 'off'")
    store = open_store()
    verify_me(store, me)
    set_autonomy(store, room, me, mode == "on")
    console.print(f"autonomy {mode}", highlight=False, markup=False)


@app.command(help="print the current state of a room")
@guarded
def status(room: str = typer.Option(..., "--room")) -> None:
    store = open_store()
    console.print(render_room(load_state(store, room), read_all(store, room), inspect_agents()))
    console.print(f"last_seq: {store.last_seq(room)}", highlight=False, markup=False)


if __name__ == "__main__":
    app()
