import hashlib

import pytest
from fastapi.testclient import TestClient

from aletheore.council.attachments import (
    MAX_ATTACHMENTS, MAX_IMAGE_BYTES, fetch_attachment, load_image, sniff_image, upload_image,
)
from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def test_sniff_accepts_real_images_by_content_not_name():
    assert sniff_image(PNG) == ("image/png", "png") and sniff_image(JPG) == ("image/jpeg", "jpg")
    assert sniff_image(GIF) == ("image/gif", "gif") and sniff_image(WEBP) == ("image/webp", "webp")
    for bad in (b"<svg xmlns='x'><script>alert(1)</script></svg>", b"MZ\x90\x00" + b"\x00" * 40, b"just text", b""):
        assert sniff_image(bad) is None


def test_load_image_checks_type_and_size(tmp_path):
    ok = tmp_path / "shot.png"
    ok.write_bytes(PNG)
    assert load_image(ok)[0] == "shot.png"
    fake = tmp_path / "evil.png"
    fake.write_bytes(b"MZ" + b"\x00" * 40)
    with pytest.raises(ValueError, match="not a supported image"):
        load_image(fake)
    big = tmp_path / "big.png"
    big.write_bytes(PNG + b"\x00" * MAX_IMAGE_BYTES)
    with pytest.raises(ValueError, match="too large"):
        load_image(big)
    with pytest.raises(ValueError, match="no such file"):
        load_image(tmp_path / "missing.png")
    assert MAX_ATTACHMENTS == 4


def test_upload_then_fetch_round_trip_and_tamper_is_caught(tmp_path):
    blobs = LocalBundleStore(tmp_path / "blobs")
    img = tmp_path / "a b.png"
    img.write_bytes(PNG)
    ref = upload_image(blobs, "r1", img)
    assert ref["sha256"] == hashlib.sha256(PNG).hexdigest() and ref["mime"] == "image/png" and ref["name"] == "a b.png"
    got = fetch_attachment(blobs, "r1", ref, tmp_path / "dest")
    assert got.read_bytes() == PNG and got.parent == tmp_path / "dest" and got.suffix == ".png"
    # a reference that lies about its sha or points at non-image bytes is refused
    with pytest.raises(ValueError):
        fetch_attachment(blobs, "r1", {**ref, "sha256": "0" * 64}, tmp_path / "dest2")
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"MZ" + b"\x00" * 40)
    sha = hashlib.sha256(bad.read_bytes()).hexdigest()
    (tmp_path / "blobs" / "r1" / f"{sha}.blob").write_bytes(bad.read_bytes())  # planted past the upload checks
    with pytest.raises(ValueError, match="not a supported image"):
        fetch_attachment(blobs, "r1", {"name": "x.png", "sha256": sha, "size": 42, "mime": "image/png"}, tmp_path / "dest3")


def test_ref_names_cannot_escape_the_destination(tmp_path):
    blobs = LocalBundleStore(tmp_path / "blobs")
    img = tmp_path / "ok.png"
    img.write_bytes(PNG)
    ref = upload_image(blobs, "r1", img)
    path = fetch_attachment(blobs, "r1", {**ref, "name": "../../../etc/passwd"}, tmp_path / "dest")
    assert path.parent == tmp_path / "dest"


@pytest.fixture
def remote(tmp_path):
    app = create_app(SqliteRoomStore(tmp_path / "s.db"), {"tok-a": "alice"}, tmp_path / "bundles")
    client = TestClient(app)
    store = RemoteRoomStore("http://t", "tok-a", client=client, backoff_s=0)
    store.create_room("r1", "alice")
    return store, client


def test_server_stores_images_by_hash_and_refuses_non_images(remote, tmp_path):
    store, client = remote
    img = tmp_path / "x.png"
    img.write_bytes(PNG)
    sha = hashlib.sha256(PNG).hexdigest()
    store.put_blob("r1", sha, img)
    dest = tmp_path / "got.png"
    store.get_blob("r1", sha, dest)
    assert dest.read_bytes() == PNG
    evil = tmp_path / "e.bin"
    evil.write_bytes(b"MZ" + b"\x00" * 40)
    with pytest.raises(ValueError):
        store.put_blob("r1", hashlib.sha256(evil.read_bytes()).hexdigest(), evil)
    r = client.put(f"/v1/rooms/r1/blobs/{sha}", content=PNG)
    assert r.status_code == 401  # no token
    r = client.put(f"/v1/rooms/r1/blobs/{'0' * 64}", content=PNG, headers={"Authorization": "Bearer tok-a"})
    assert r.status_code == 422  # hash mismatch


# ---- through prompts, the planner, task requests and the daemon -------------------------------------------
from aletheore.council.dispatcher import Dispatcher, send_message
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.runners import ClaudeRunner, CodexRunner, ScriptedRunner
from aletheore.council.state import load_state
from aletheore.council.store import read_all
from tests.council.test_dispatcher import PLAN_JSON
from tests.council.world_fixture import daemon, git, world  # noqa: F401


def png(tmp_path, name="ui.png"):
    p = tmp_path / name
    p.write_bytes(PNG + name.encode())
    return p


def test_send_message_attaches_uploaded_images_and_posts_nothing_if_one_is_bad(world):
    rid, blobs = world["alice"].room_id, world["bundles"]
    thread = send_message(world["store"], rid, "bob", "match this design", images=[png(world["tmp"])], blobs=blobs)
    ev = next(e for e in read_all(world["store"], rid) if e.thread_id == thread)
    ref = ev.body["attachments"][0]
    assert ref["name"] == "ui.png" and len(ref["sha256"]) == 64
    junk = world["tmp"] / "junk.png"
    junk.write_bytes(b"MZ" + b"\0" * 40)
    before = len(read_all(world["store"], rid))
    with pytest.raises(ValueError):
        send_message(world["store"], rid, "bob", "x", images=[png(world["tmp"], "a.png"), junk], blobs=blobs)
    assert len(read_all(world["store"], rid)) == before
    with pytest.raises(ValueError, match="at most"):
        send_message(world["store"], rid, "bob", "x", images=[png(world["tmp"], f"{i}.png") for i in range(5)], blobs=blobs)


def test_planner_sees_the_images_and_every_task_inherits_them(world):
    rid, blobs = world["alice"].room_id, world["bundles"]
    send_message(world["store"], rid, "bob", "build this screen", images=[png(world["tmp"])], blobs=blobs)
    runner = ScriptedRunner(final_text=PLAN_JSON)
    d = Dispatcher(world["store"], world["alice"], "alice", runner, blobs=blobs)
    d.run_once()
    assert len(runner.image_calls[0]) == 1 and runner.image_calls[0][0].suffix == ".png"
    assert "ui" in runner.calls[0]
    world["store"].append(EventDraft(rid, "ok", "room", EventType.SYSTEM, "alice", None, None,
                                     {"subtype": "plan_approved", "plan_id": next(iter(load_state(world["store"], rid).plans))}))
    d.run_once()
    reqs = [e for e in read_all(world["store"], rid) if e.type is EventType.TASK and e.body.get("op") == "request"]
    assert len(reqs) == 2 and all(len(r.body["attachments"]) == 1 for r in reqs)


def test_daemon_downloads_attachments_outside_the_worktree_and_hands_them_to_the_agent(world):
    rid, blobs = world["alice"].room_id, world["bundles"]
    ref = upload_image(blobs, rid, png(world["tmp"]))
    world["alice"].request_task("schema", "design the table like the picture", attachments=[ref])
    d, runner = daemon(world, "bob")
    d.run_once()
    images = runner.image_calls[0]
    assert len(images) == 1 and images[0].is_file() and images[0].read_bytes().startswith(b"\x89PNG")
    wt = d.room.state_dir / "worktrees" / rid / "claude"
    assert wt not in images[0].parents
    assert "ui" in runner.calls[0]  # the prompt names the file so the agent can find it
    # nothing image-like was committed
    assert "png" not in git(world["b"], "ls-tree", "-r", "--name-only", f"council/{rid}/claude")


def test_bad_attachment_does_not_stop_the_task(world):
    world["alice"].request_task("schema", "design the table", attachments=[{"name": "x.png", "sha256": "1" * 64, "size": 3, "mime": "image/png"}])
    d, runner = daemon(world, "bob")
    d.run_once()
    assert runner.image_calls[0] == () and "could not be loaded" in runner.calls[0]


def test_claude_and_codex_get_the_images_on_their_command_lines(tmp_path, monkeypatch):
    import aletheore.council.runners as r
    seen = {}

    def fake(argv, cwd, env, timeout_s):
        seen["argv"] = argv
        return 0, '{"type":"result","result":"ok","is_error":false}\n{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}', "", False

    monkeypatch.setattr(r, "_run_process", fake)
    monkeypatch.setattr(r, "_resolve", lambda b: f"/bin/{b}")
    cfg = tmp_path / "m.json"
    cfg.write_text('{"mcpServers": {}}')
    img = tmp_path / "pics" / "a.png"
    img.parent.mkdir()
    img.write_bytes(PNG)
    ClaudeRunner().run("do it", tmp_path, cfg, {}, 5, images=(img,))
    a = seen["argv"]
    assert a[a.index("--add-dir") + 1] == str(img.parent) and a.index("--add-dir") < a.index("--allowedTools")
    CodexRunner().run("do it", tmp_path, cfg, {}, 5, images=(img,))
    a = seen["argv"]
    assert a[a.index("-i") + 1] == str(img) and a[-1] == "do it" and a.index("-i") < a.index("-C")
