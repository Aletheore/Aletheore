import hashlib

import pytest

from aletheore.council.bundle_store import LocalBundleStore


def test_local_bundle_store_roundtrip_and_id_validation(tmp_path):
    store = LocalBundleStore(tmp_path / "b")
    src = tmp_path / "x.bundle"
    src.write_bytes(b"data")
    sha = hashlib.sha256(b"data").hexdigest()
    store.put_bundle("room-1", sha, src)
    dest = tmp_path / "y" / "z.bundle"
    store.get_bundle("room-1", sha, dest)
    assert dest.read_bytes() == b"data"
    for bad_room, bad_sha in (("../x", sha), ("room-1", "abc"), ("room-1", sha.upper()), ("a/b", sha)):
        with pytest.raises(ValueError):
            store.put_bundle(bad_room, bad_sha, src)
        with pytest.raises(ValueError):
            store.get_bundle(bad_room, bad_sha, dest)


def test_local_put_rejects_content_that_does_not_match_sha(tmp_path):
    store = LocalBundleStore(tmp_path / "b")
    src = tmp_path / "x.bundle"
    src.write_bytes(b"data")
    with pytest.raises(ValueError):
        store.put_bundle("room-1", "0" * 64, src)


def test_local_get_missing_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        LocalBundleStore(tmp_path / "b").get_bundle("room-1", "0" * 64, tmp_path / "o")


def test_local_put_rejects_oversized(tmp_path, monkeypatch):
    monkeypatch.setattr("aletheore.council.bundle_store.MAX_BUNDLE_BYTES", 10)
    store = LocalBundleStore(tmp_path / "b")
    src = tmp_path / "x.bundle"
    src.write_bytes(b"x" * 11)
    with pytest.raises(ValueError):
        store.put_bundle("room-1", hashlib.sha256(b"x" * 11).hexdigest(), src)
