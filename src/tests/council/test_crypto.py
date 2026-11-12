"""The libsodium wrapper: every primitive round-trips, and every tampering we can think of is refused."""

import os
import struct

import pytest

from aletheore.council import crypto
from aletheore.council.crypto import CryptoError


def test_sign_and_verify_round_trip_and_reject_changes():
    sk, vk = crypto.generate_signing_keypair()
    sig = crypto.sign(sk, b"hello")
    assert crypto.verify(vk, b"hello", sig) is True
    assert crypto.verify(vk, b"hellO", sig) is False
    assert crypto.verify(vk, b"hello", sig[:-1] + bytes([sig[-1] ^ 1])) is False
    other_sk, other_vk = crypto.generate_signing_keypair()
    assert crypto.verify(other_vk, b"hello", sig) is False
    assert crypto.verify(vk, b"hello", b"short") is False  # malformed input is a plain False, never an exception


def test_sealed_box_round_trip_and_wrong_key_fails():
    sk, pk = crypto.generate_box_keypair()
    blob = crypto.seal(pk, b"room key")
    assert crypto.open_sealed(sk, pk, blob) == b"room key"
    other_sk, other_pk = crypto.generate_box_keypair()
    with pytest.raises(CryptoError):
        crypto.open_sealed(other_sk, other_pk, blob)
    with pytest.raises(CryptoError):
        crypto.open_sealed(sk, pk, blob[:-1] + bytes([blob[-1] ^ 1]))


def test_aead_round_trip():
    key = crypto.random_bytes(32)
    nonce, ct = crypto.aead_encrypt(key, b"secret message", b"room-1")
    assert crypto.aead_decrypt(key, nonce, ct, b"room-1") == b"secret message"
    n2, ct2 = crypto.aead_encrypt(key, b"secret message", b"room-1")
    assert n2 != nonce and ct2 != ct  # a fresh nonce every time


def test_tampered_ciphertext_or_wrong_aad_fails():
    key = crypto.random_bytes(32)
    nonce, ct = crypto.aead_encrypt(key, b"secret message", b"room-1")
    with pytest.raises(CryptoError):
        crypto.aead_decrypt(key, nonce, ct[:-1] + bytes([ct[-1] ^ 1]), b"room-1")
    with pytest.raises(CryptoError):
        crypto.aead_decrypt(key, nonce, ct, b"room-2")
    with pytest.raises(CryptoError):
        crypto.aead_decrypt(key, bytes(24), ct, b"room-1")


def test_wrong_key_never_decrypts_and_never_returns_partial_data(tmp_path):
    key, wrong = crypto.random_bytes(32), crypto.random_bytes(32)
    nonce, ct = crypto.aead_encrypt(key, b"x" * 1000, b"")
    with pytest.raises(CryptoError):
        crypto.aead_decrypt(wrong, nonce, ct, b"")
    src, enc, dst = tmp_path / "in", tmp_path / "enc", tmp_path / "out"
    src.write_bytes(os.urandom(5000))
    header = crypto.stream_encrypt(key, src, enc, chunk=1024)
    with pytest.raises(CryptoError):
        crypto.stream_decrypt(wrong, header, enc, dst)
    assert not dst.exists()  # no half-written plaintext is left behind
    assert not list(tmp_path.glob("out*"))


def _chunks(path):
    data, out, pos = path.read_bytes(), [], 0
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        out.append(data[pos:pos + 4 + n])
        pos += 4 + n
    return out


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1025, 5000])
def test_stream_round_trip_at_chunk_edges(tmp_path, size):
    key = crypto.random_bytes(32)
    src, enc, dst = tmp_path / "in", tmp_path / "enc", tmp_path / "out"
    payload = os.urandom(size)
    src.write_bytes(payload)
    header = crypto.stream_encrypt(key, src, enc, chunk=1024)
    assert enc.read_bytes()[:] != payload or size == 0
    crypto.stream_decrypt(key, header, enc, dst)
    assert dst.read_bytes() == payload


def test_stream_encrypt_detects_truncation_and_reordering(tmp_path):
    key = crypto.random_bytes(32)
    src, enc = tmp_path / "in", tmp_path / "enc"
    src.write_bytes(os.urandom(4000))
    header = crypto.stream_encrypt(key, src, enc, chunk=1000)
    parts = _chunks(enc)
    assert len(parts) >= 4

    def attempt(blob: bytes, name: str):
        bad = tmp_path / name
        bad.write_bytes(blob)
        dst = tmp_path / (name + ".out")
        with pytest.raises(CryptoError):
            crypto.stream_decrypt(key, header, bad, dst)
        assert not dst.exists()

    attempt(b"".join(parts[:1] + parts[2:]), "dropped_middle")
    attempt(b"".join([parts[1], parts[0]] + parts[2:]), "swapped")
    attempt(b"".join(parts[:-1]), "dropped_last")  # a clean cut at a chunk boundary
    attempt(enc.read_bytes()[:-5], "cut_mid_chunk")
    attempt(enc.read_bytes() + b"extra", "trailing_bytes")
    attempt(b"".join(parts + parts[:1]), "replayed_chunk")


def test_stream_rejects_a_wrong_header(tmp_path):
    key = crypto.random_bytes(32)
    src, enc, dst = tmp_path / "in", tmp_path / "enc", tmp_path / "out"
    src.write_bytes(b"data" * 500)
    header = crypto.stream_encrypt(key, src, enc, chunk=512)
    other = crypto.stream_encrypt(key, src, tmp_path / "enc2", chunk=512)
    assert header != other
    with pytest.raises(CryptoError):
        crypto.stream_decrypt(key, other, enc, dst)
    with pytest.raises(CryptoError):
        crypto.stream_decrypt(key, b"short", enc, dst)


def test_fingerprint_is_stable_and_differs_per_key():
    _, vk = crypto.generate_signing_keypair()
    _, pk = crypto.generate_box_keypair()
    assert crypto.fingerprint(vk, pk) == crypto.fingerprint(vk, pk)
    _, vk2 = crypto.generate_signing_keypair()
    _, pk2 = crypto.generate_box_keypair()
    assert crypto.fingerprint(vk2, pk) != crypto.fingerprint(vk, pk)
    assert crypto.fingerprint(vk, pk2) != crypto.fingerprint(vk, pk)


def test_derive_key_is_deterministic_and_separated_by_context():
    secret = crypto.random_bytes(32)
    a = crypto.derive_key(secret, "council/invite/v1")
    assert a == crypto.derive_key(secret, "council/invite/v1")
    assert len(a) == 32
    assert crypto.derive_key(secret, "council/other/v1") != a
    assert crypto.derive_key(crypto.random_bytes(32), "council/invite/v1") != a
    assert len(crypto.derive_key(secret, "x", length=16)) == 16
    with pytest.raises(ValueError):
        crypto.derive_key(secret, "x", length=8)


def test_random_bytes_length_and_variety():
    assert len(crypto.random_bytes(24)) == 24
    assert crypto.random_bytes(32) != crypto.random_bytes(32)
