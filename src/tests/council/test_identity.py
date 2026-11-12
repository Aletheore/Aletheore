"""Device identity and its key file."""

import os
import stat

import pytest

from aletheore.council import crypto, identity
from aletheore.council.crypto import CryptoError
from aletheore.council.identity import Identity, Keystore, PublicIdentity, safety_words


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    # Argon2id at its real strength is deliberately slow; the tests use the lightest setting the library allows.
    import nacl.pwhash.argon2id as a2
    monkeypatch.setattr(identity, "KDF_OPS", a2.OPSLIMIT_MIN)
    monkeypatch.setattr(identity, "KDF_MEM", a2.MEMLIMIT_MIN)


def test_load_or_create_makes_one_identity_and_then_reuses_it(tmp_path):
    ks = Keystore(tmp_path / "keys")
    first = ks.load_or_create("device")
    again = Keystore(tmp_path / "keys").load_or_create("device")
    assert first.device_id == again.device_id
    assert first.public() == again.public()
    other = ks.load_or_create("second")
    assert other.device_id != first.device_id


def test_public_identity_carries_only_public_parts_and_signs_verifiably(tmp_path):
    me = Keystore(tmp_path).load_or_create("device")
    pub = me.public()
    assert isinstance(pub, PublicIdentity)
    sig = me.sign(b"hi")
    assert crypto.verify(pub.sign_vk, b"hi", sig)
    blob = crypto.seal(pub.box_pk, b"for you")
    assert me.open_sealed(blob) == b"for you"
    assert pub.device_id == me.device_id
    assert pub.fingerprint == crypto.fingerprint(pub.sign_vk, pub.box_pk)


def test_keystore_file_is_private(tmp_path):
    ks = Keystore(tmp_path / "keys")
    ks.load_or_create("device")
    files = list((tmp_path / "keys").glob("*.key"))
    assert len(files) == 1
    if os.name == "nt":
        import subprocess
        acl = subprocess.run(["icacls", str(files[0])], capture_output=True, text=True).stdout
        user = os.environ["USERNAME"].lower()
        entries = [ln for ln in acl.splitlines() if "(" in ln and ":" in ln]
        # An elevated process (a CI runner, an administrator's shell) also leaves SYSTEM, Administrators and OWNER RIGHTS on a new file;
        # they can reach it anyway. What must never appear is anyone else (Users, Everyone, Authenticated Users).
        always_there = ("nt authority\\system", "builtin\\administrators", "owner rights")
        assert entries and all(user in ln.lower() or any(p in ln.lower() for p in always_there) for ln in entries), acl
        assert any(user in ln.lower() for ln in entries), acl
    else:
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "keys").stat().st_mode) == 0o700


def test_corrupt_key_file_is_reported_not_replaced(tmp_path):
    ks = Keystore(tmp_path)
    ks.load_or_create("device")
    path = next(tmp_path.glob("*.key"))
    path.write_text("not json")
    with pytest.raises(CryptoError):
        Keystore(tmp_path).load_or_create("device")
    assert path.read_text() == "not json"  # never silently make a new identity over a damaged one


def test_export_import_requires_the_right_passphrase(tmp_path):
    ks = Keystore(tmp_path / "a")
    me = ks.load_or_create("device")
    blob = ks.export("device", "correct horse")
    assert b"device" not in blob  # the name is inside the encryption
    other = Keystore(tmp_path / "b")
    with pytest.raises(CryptoError):
        other.import_(blob, "wrong passphrase")
    assert list((tmp_path / "b").glob("*.key")) == []
    assert other.import_(blob, "correct horse") == "device"
    assert other.load_or_create("device").public() == me.public()


def test_import_refuses_to_overwrite_and_rejects_hostile_blobs(tmp_path):
    ks = Keystore(tmp_path)
    ks.load_or_create("device")
    blob = ks.export("device", "pw")
    with pytest.raises(FileExistsError):
        ks.import_(blob, "pw")
    for bad in (b"", b"garbage", blob[:20], blob[:-1] + bytes([blob[-1] ^ 1])):
        with pytest.raises(CryptoError):
            Keystore(tmp_path / "x").import_(bad, "pw")


def test_names_cannot_escape_the_key_directory(tmp_path):
    ks = Keystore(tmp_path / "keys")
    for bad in ("../evil", "a/b", "", ".", "..", "x" * 200):
        with pytest.raises(ValueError):
            ks.load_or_create(bad)


def test_safety_words_are_deterministic_and_four_words(tmp_path):
    me = Keystore(tmp_path).load_or_create("device")
    words = safety_words(me.public().fingerprint)
    assert words == safety_words(me.public().fingerprint)
    assert len(words.split("-")) == 4
    assert len(identity.WORDS) == 256 and len(set(identity.WORDS)) == 256
    other = Keystore(tmp_path / "o").load_or_create("device")
    assert safety_words(other.public().fingerprint) != words


def test_secrets_never_appear_in_repr_or_str_of_identity_objects(tmp_path):
    me = Keystore(tmp_path).load_or_create("device")
    secrets = [me._sign_sk, me._box_sk]  # noqa: SLF001 (the test looks at the secret on purpose)
    for text in (repr(me), str(me), repr(me.public()), str(me.public())):
        for s in secrets:
            assert s.hex() not in text and repr(s) not in text and str(list(s)) not in text
    assert "device_id" in repr(me) or me.device_id in repr(me)
    with pytest.raises(TypeError):
        import pickle
        pickle.dumps(me)  # an Identity must not be serialisable by accident
