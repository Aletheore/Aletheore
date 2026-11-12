"""Thin wrappers over libsodium (PyNaCl). Nothing in here invents cryptography: every call is a standard libsodium construction.

* Signatures: Ed25519 (detached).
* Sealed boxes: X25519 + XSalsa20-Poly1305 ("encrypt to a public key, only its owner can open").
* Message encryption: XChaCha20-Poly1305 (IETF) with a random 24-byte nonce and associated data.
* Files: libsodium's secretstream, which authenticates every chunk, its order and the end of the stream, so a dropped,
  swapped, replayed or truncated chunk is refused. Decryption writes to a temporary file and only renames it into place after
  the whole stream verified, so a failed decrypt never leaves partial plaintext behind.
* Keys derived from a secret use keyed BLAKE2b with the context as the "personalisation" string.

Every failure to decrypt or open raises `CryptoError`; `verify` returns False instead of raising.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import nacl.bindings as nb
import nacl.encoding
import nacl.exceptions
import nacl.hash
import nacl.public
import nacl.signing
import nacl.utils

NONCE_BYTES = nb.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES
HEADER_BYTES = nb.crypto_secretstream_xchacha20poly1305_HEADERBYTES
STREAM_OVERHEAD = nb.crypto_secretstream_xchacha20poly1305_ABYTES
DEFAULT_CHUNK = 1024 * 1024
MAX_CHUNK = 16 * 1024 * 1024  # the largest chunk a decryptor will accept: a hostile length prefix cannot make us allocate more
_TAG_MESSAGE = nb.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
_TAG_FINAL = nb.crypto_secretstream_xchacha20poly1305_TAG_FINAL
_LIB_ERRORS = (nacl.exceptions.CryptoError, nacl.exceptions.RuntimeError, ValueError, TypeError)


class CryptoError(Exception):
    """Decryption, opening or verification failed. The message never contains key material."""


def random_bytes(n: int) -> bytes:
    return nacl.utils.random(n)


# -- signatures ------------------------------------------------------------------------------------------------------
def generate_signing_keypair() -> tuple[bytes, bytes]:
    sk = nacl.signing.SigningKey.generate()
    return bytes(sk), bytes(sk.verify_key)


def sign(sk: bytes, data: bytes) -> bytes:
    return nacl.signing.SigningKey(sk).sign(data).signature


def verify(vk: bytes, data: bytes, sig: bytes) -> bool:
    try:
        nacl.signing.VerifyKey(vk).verify(data, sig)
        return True
    except (nacl.exceptions.BadSignatureError, ValueError, TypeError, nacl.exceptions.CryptoError):
        return False


# -- public-key encryption -------------------------------------------------------------------------------------------
def generate_box_keypair() -> tuple[bytes, bytes]:
    sk = nacl.public.PrivateKey.generate()
    return bytes(sk), bytes(sk.public_key)


def seal(pk: bytes, data: bytes) -> bytes:
    return nacl.public.SealedBox(nacl.public.PublicKey(pk)).encrypt(data)


def open_sealed(box_sk: bytes, box_pk: bytes, blob: bytes) -> bytes:
    try:
        return nacl.public.SealedBox(nacl.public.PrivateKey(box_sk)).decrypt(blob)
    except _LIB_ERRORS as exc:
        raise CryptoError("could not open the sealed message") from exc


# -- authenticated encryption ----------------------------------------------------------------------------------------
def aead_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    nonce = random_bytes(NONCE_BYTES)
    return nonce, nb.crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, aad, nonce, key)


def aead_decrypt(key: bytes, nonce: bytes, ct: bytes, aad: bytes = b"") -> bytes:
    try:
        return nb.crypto_aead_xchacha20poly1305_ietf_decrypt(ct, aad, nonce, key)
    except _LIB_ERRORS as exc:
        raise CryptoError("could not decrypt (wrong key, wrong context or tampered data)") from exc


# -- files -----------------------------------------------------------------------------------------------------------
def stream_encrypt(key: bytes, src_path, dst_path, chunk: int = DEFAULT_CHUNK) -> bytes:
    """Encrypt a file in authenticated chunks. Returns the stream header, which the caller stores next to the file."""
    if not 1 <= chunk <= MAX_CHUNK:
        raise ValueError("chunk size out of range")
    state = nb.crypto_secretstream_xchacha20poly1305_state()
    header = nb.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    dst = Path(dst_path)
    part = dst.with_name(dst.name + ".part")
    try:
        with open(src_path, "rb") as src, open(part, "wb") as out:
            current = src.read(chunk)
            while True:
                following = src.read(chunk)
                last = not following
                ct = nb.crypto_secretstream_xchacha20poly1305_push(state, current, None, _TAG_FINAL if last else _TAG_MESSAGE)
                out.write(struct.pack(">I", len(ct)) + ct)
                if last:
                    break
                current = following
        os.replace(part, dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return header


def stream_decrypt(key: bytes, header: bytes, src_path, dst_path) -> None:
    dst = Path(dst_path)
    part = dst.with_name(dst.name + ".part")
    try:
        if len(header) != HEADER_BYTES:
            raise CryptoError("bad stream header")
        state = nb.crypto_secretstream_xchacha20poly1305_state()
        try:
            nb.crypto_secretstream_xchacha20poly1305_init_pull(state, header, key)
        except _LIB_ERRORS as exc:
            raise CryptoError("bad stream header or key") from exc
        with open(src_path, "rb") as src, open(part, "wb") as out:
            finished = False
            while True:
                prefix = src.read(4)
                if not prefix:
                    break
                if finished:
                    raise CryptoError("data after the end of the stream")
                if len(prefix) < 4:
                    raise CryptoError("truncated stream")
                (n,) = struct.unpack(">I", prefix)
                if n < STREAM_OVERHEAD or n > MAX_CHUNK + STREAM_OVERHEAD:
                    raise CryptoError("bad chunk length")
                ct = src.read(n)
                if len(ct) < n:
                    raise CryptoError("truncated stream")
                try:
                    plain, tag = nb.crypto_secretstream_xchacha20poly1305_pull(state, ct, None)
                except _LIB_ERRORS as exc:
                    raise CryptoError("could not decrypt (wrong key, or the data was changed, reordered or cut)") from exc
                out.write(plain)
                finished = tag == _TAG_FINAL
            if not finished:
                raise CryptoError("the stream ended early (truncated)")
        os.replace(part, dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


# -- keys and names --------------------------------------------------------------------------------------------------
def derive_key(secret: bytes, context: str, length: int = 32) -> bytes:
    """A key for one purpose from a secret. Different contexts give unrelated keys."""
    if not 16 <= length <= 64:
        raise ValueError("length must be between 16 and 64 bytes")
    person = hashlib.blake2b(context.encode("utf-8"), digest_size=16).digest()
    key = secret if len(secret) <= 64 else hashlib.blake2b(secret, digest_size=64).digest()
    return nacl.hash.blake2b(b"", digest_size=length, key=key, person=person, encoder=nacl.encoding.RawEncoder)


def fingerprint(vk: bytes, box_pk: bytes) -> str:
    """A stable name for a device's public keys (hex). Length-prefixed so two keys cannot be re-split into another pair."""
    data = b"council/fingerprint/v1" + struct.pack(">H", len(vk)) + vk + struct.pack(">H", len(box_pk)) + box_pk
    return hashlib.blake2b(data, digest_size=32).hexdigest()
