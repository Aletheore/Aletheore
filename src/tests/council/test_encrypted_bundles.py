"""Code bundles and images in an encrypted room: the server stores ciphertext under an id it cannot match to a known file."""

import hashlib
import subprocess

import pytest

from aletheore.council.attachments import fetch_attachment, upload_image
from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.bundle_transport import create_bundle, import_bundle
from aletheore.council.e2e_store import EncryptedBundleStore, object_id, open_object
from tests.council.test_keys import ROOM, Cluster

BRANCH = "council/room1/codex"
PNG = b"\x89PNG\r\n\x1a\n" + b"PIXELS-OF-A-SECRET-DIAGRAM" * 40


@pytest.fixture
def cl(tmp_path):
    return Cluster(tmp_path)


def vault(person, inner=None):
    return EncryptedBundleStore(inner or person.remote, person.keystore.keyring(ROOM), ROOM, key_sync=person.keys.sync)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p, hashlib.sha256(data).hexdigest()


def server_files(cl, kind="bundle"):
    return sorted((cl.tmp / "b" / ROOM).glob(f"*.{kind}"))


def test_stored_object_is_ciphertext_and_its_id_is_not_the_plaintext_hash(cl, tmp_path):
    src, sha = write(tmp_path, "secret.bin", b"PLAINTEXT-MARKER " * 500)
    vault(cl.host).put_bundle(ROOM, sha, src)
    (stored,) = server_files(cl)
    raw = stored.read_bytes()
    assert b"PLAINTEXT-MARKER" not in raw and len(raw) > 8000
    key = cl.host.keystore.keyring(ROOM).get(1)
    assert stored.stem == object_id(key, sha) and stored.stem != sha and len(stored.stem) == 64
    # the same file in a room with another key gets an id the server cannot link to this one
    assert object_id(b"\x01" * 32, sha) != stored.stem


def test_round_trip_returns_identical_bytes_and_verifies_the_plaintext_sha(cl, tmp_path):
    data = bytes(range(256)) * 5000
    src, sha = write(tmp_path, "big.bin", data)
    vault(cl.host).put_bundle(ROOM, sha, src)
    for person in (cl.host, cl.meg):  # megan has no key yet: her first read fetches the grant
        dest = tmp_path / f"out-{person.handle}"
        vault(person).get_bundle(ROOM, sha, dest)
        assert dest.read_bytes() == data
    with pytest.raises(ValueError):
        vault(cl.host).put_bundle(ROOM, "0" * 64, src)  # the file does not match the sha the caller announced
    assert len(server_files(cl)) == 1
    with pytest.raises(KeyError):
        vault(cl.host).get_bundle(ROOM, "1" * 64, tmp_path / "none")
    empty, empty_sha = write(tmp_path, "empty.bin", b"")
    vault(cl.host).put_bundle(ROOM, empty_sha, empty)
    vault(cl.meg).get_bundle(ROOM, empty_sha, tmp_path / "empty-out")
    assert (tmp_path / "empty-out").read_bytes() == b""


def test_truncated_swapped_or_damaged_ciphertext_is_refused(cl, tmp_path):
    a_src, a = write(tmp_path, "a.bin", b"A" * 3000)
    b_src, b = write(tmp_path, "b.bin", b"B" * 3000)
    v = vault(cl.host)
    v.put_bundle(ROOM, a, a_src)
    v.put_bundle(ROOM, b, b_src)
    fa = next(f for f in server_files(cl) if f.stem == object_id(cl.host.keystore.keyring(ROOM).get(1), a))
    fb = next(f for f in server_files(cl) if f.stem == object_id(cl.host.keystore.keyring(ROOM).get(1), b))
    good_a, good_b = fa.read_bytes(), fb.read_bytes()

    def refused(label, content):
        fa.write_bytes(content)
        dest = tmp_path / f"dest-{label}"
        with pytest.raises(ValueError):
            vault(cl.meg).get_bundle(ROOM, a, dest)
        assert not dest.exists() and not list(tmp_path.glob(f"dest-{label}*"))  # nothing half-written is left

    refused("swapped", good_b)  # the server hands back a different, perfectly valid object
    refused("truncated", good_a[:-30])
    refused("cut-at-header", good_a[:20])
    flipped = bytearray(good_a)
    flipped[len(flipped) // 2] ^= 1
    refused("flipped", bytes(flipped))
    refused("badmagic", b"XXXXXXXX" + good_a[8:])
    refused("extra", good_a + b"extra")
    refused("empty", b"")
    fa.write_bytes(good_a)
    vault(cl.meg).get_bundle(ROOM, a, tmp_path / "fine")
    assert (tmp_path / "fine").read_bytes() == b"A" * 3000


def test_a_removed_member_cannot_fetch_or_open_new_objects(cl, tmp_path):
    old_src, old = write(tmp_path, "old.bin", b"old data" * 100)
    new_src, new = write(tmp_path, "new.bin", b"new secret data" * 100)
    vault(cl.host).put_bundle(ROOM, old, old_src)
    assert cl.moe.keys.sync() == 1  # moe holds epoch 1, like any member who has been in the room
    cl.host.keys.remove_member("moe")
    vault(cl.host).put_bundle(ROOM, new, new_src)  # sealed under epoch 2
    with pytest.raises(PermissionError):
        vault(cl.moe).get_bundle(ROOM, new, tmp_path / "moe-new")  # the server refuses them
    with pytest.raises(PermissionError):
        vault(cl.moe).get_bundle(ROOM, old, tmp_path / "moe-old")
    # even with a copy of the server's files (a backup, a compromised server), the new epoch key is not theirs
    stash = LocalBundleStore(tmp_path / "stash", opaque=True)
    (tmp_path / "stash" / ROOM).mkdir(parents=True, exist_ok=True)
    for f in server_files(cl):
        (tmp_path / "stash" / ROOM / f.name).write_bytes(f.read_bytes())
    new_file = next(f for f in server_files(cl) if f.stem == object_id(cl.host.keystore.keyring(ROOM).get(2), new))
    with pytest.raises(ValueError, match="key"):
        open_object(cl.moe.keystore.keyring(ROOM), new_file, tmp_path / "copy-new", new)  # sealed under an epoch they never got
    assert not (tmp_path / "copy-new").exists()
    with pytest.raises(KeyError):
        vault(cl.moe, stash).get_bundle(ROOM, new, tmp_path / "copy-new")  # they cannot even compute where it is stored
    vault(cl.moe, stash).get_bundle(ROOM, old, tmp_path / "copy-old")  # what they already could read stays readable to them
    assert (tmp_path / "copy-old").read_bytes() == b"old data" * 100
    vault(cl.meg).get_bundle(ROOM, new, tmp_path / "meg-new")  # members who remain read both
    assert (tmp_path / "meg-new").read_bytes() == b"new secret data" * 100


def test_server_limits_still_apply_to_ciphertext_size_and_other_rooms_keep_their_checks(tmp_path):
    cl = Cluster(tmp_path, max_bundles_per_room=2)
    v = vault(cl.host)
    for i in range(2):
        src, sha = write(tmp_path, f"f{i}.bin", bytes([i]) * 100)
        v.put_bundle(ROOM, sha, src)
    src, sha = write(tmp_path, "f3.bin", b"three" * 20)
    with pytest.raises(ConnectionError):  # the server answers 429; the remote client reports it as a limit
        v.put_bundle(ROOM, sha, src)
    from aletheore.council.protocol import MAX_BUNDLE_BYTES

    h = cl.raw_headers(cl.host)
    too_big = b"CNCLOBJ1" + b"\x00" * (MAX_BUNDLE_BYTES + 200_000)
    assert cl.client.put(f"/v1/rooms/{ROOM}/bundles/{'a' * 64}", content=too_big, headers=h).status_code == 413
    # an encrypted room does not look inside, but it does insist on the object framing
    assert cl.client.put(f"/v1/rooms/{ROOM}/blobs/{'b' * 64}", content=b"just some bytes", headers=h).status_code == 422
    assert cl.client.put(f"/v1/rooms/{ROOM}/blobs/{'b' * 64}", content=b"CNCLOBJ1" + b"\x00" * 100, headers=h).status_code == 200
    # a plain room still demands real images and a matching hash
    cl.host.remote.create_room("plain", "hosty")
    assert cl.client.put(f"/v1/rooms/plain/blobs/{hashlib.sha256(b'not an image').hexdigest()}", content=b"not an image", headers=h).status_code == 422
    assert cl.client.put(f"/v1/rooms/plain/bundles/{'c' * 64}", content=b"x", headers=h).status_code == 422


def test_images_work_end_to_end_and_are_still_sniffed_after_decrypting(cl, tmp_path):
    img = tmp_path / "diagram.png"
    img.write_bytes(PNG)
    ref = upload_image(vault(cl.host), ROOM, img)  # the same helper the commands use, now through the vault
    assert ref["sha256"] == hashlib.sha256(PNG).hexdigest() and ref["mime"] == "image/png"
    (stored,) = server_files(cl, "blob")
    assert b"PIXELS-OF-A-SECRET-DIAGRAM" not in stored.read_bytes()
    got = fetch_attachment(vault(cl.meg), ROOM, ref, tmp_path / "attachments")
    assert got.read_bytes() == PNG and got.suffix == ".png"
    notimg = tmp_path / "notes.png"
    notimg.write_bytes(b"this is not an image at all")
    with pytest.raises(ValueError):
        upload_image(vault(cl.host), ROOM, notimg)
    # a file the host's client was tricked into sending is still checked by the receiving client
    sneaky = b"<svg onload=alert(1)>" * 20
    sha = hashlib.sha256(sneaky).hexdigest()
    src = tmp_path / "sneaky.bin"
    src.write_bytes(sneaky)
    with pytest.raises(ValueError):
        vault(cl.host).put_blob(ROOM, sha, src)
    from aletheore.council.attachments import MAX_IMAGE_BYTES

    big = tmp_path / "big.png"
    big.write_bytes(PNG + b"\x00" * MAX_IMAGE_BYTES)
    with pytest.raises(ValueError):
        upload_image(vault(cl.host), ROOM, big)


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_a_code_bundle_travels_through_the_vault_and_imports_on_another_clone(cl, tmp_path):
    a = tmp_path / "machine a"
    a.mkdir()
    git(a, "init", "-q", "-b", "main")
    (a / "f.txt").write_text("base\n")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "base")
    b = tmp_path / "machine b"
    git(tmp_path, "clone", "-q", str(a), str(b))
    base = git(a, "rev-parse", "HEAD")
    git(a, "checkout", "-q", "-b", BRANCH, base)
    (a / "n.py").write_text("print('hi')\n")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "work")
    info = create_bundle(a, BRANCH, base, tmp_path / "out.bundle")
    vault(cl.host).put_bundle(ROOM, info.sha256, tmp_path / "out.bundle")
    vault(cl.meg).get_bundle(ROOM, info.sha256, tmp_path / "in.bundle")
    ref = import_bundle(b, "alice", BRANCH, tmp_path / "in.bundle", info.sha256)
    assert git(b, "rev-parse", ref) == info.tip_sha


def test_a_vault_serves_only_its_own_room(cl, tmp_path):
    src, sha = write(tmp_path, "x.bin", b"x" * 100)
    with pytest.raises(ValueError):
        vault(cl.host).put_bundle("another-room", sha, src)
    with pytest.raises(ValueError):
        vault(cl.host).get_bundle("another-room", sha, tmp_path / "d")
    with pytest.raises(ValueError):
        vault(cl.host).put_bundle(ROOM, "not-a-sha", src)


from tests.council.world_fixture import daemon, world  # noqa: E402,F401  (the shared two-machine fixture)


def test_the_daemon_publishes_and_the_host_fetches_through_the_vault(world):
    """The worker daemon and the host use the BundleStore interface only, so a vault drops in: the published branch bundle is sealed on
    the way out and the host imports it after opening it."""
    from aletheore.council.bundle_store import LocalBundleStore
    from aletheore.council.envelope import Keyring

    tmp = world["tmp"]
    ring = Keyring({1: b"\x07" * 32})
    raw = LocalBundleStore(tmp / "sealed-bundles", opaque=True)
    world["bundles"] = EncryptedBundleStore(raw, ring, world["alice"].room_id)
    t = world["alice"].request_task("schema", "design table")
    d, _ = daemon(world, "bob")
    d.run_once()
    room_id = world["alice"].room_id
    (pub,) = [e for e in world["store"].read_after(room_id, 0) if e.body.get("subtype") == "branch_published"]
    stored = list((tmp / "sealed-bundles" / room_id).glob("*.bundle"))
    assert len(stored) == 1 and stored[0].stem != pub.body["sha256"]  # stored under the keyed id, not the bundle's own hash
    assert b"schema/n.sql" not in stored[0].read_bytes() and b"PACK" not in stored[0].read_bytes()
    dest = tmp / "got.bundle"
    world["bundles"].get_bundle(room_id, pub.body["sha256"], dest)
    ref = import_bundle(world["a"], "bob", pub.body["branch"], dest, pub.body["sha256"])
    assert "schema/n.sql" in git(world["a"], "ls-tree", "-r", "--name-only", ref) and t
