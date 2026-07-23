import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def content_hash(report_text: str) -> str:
    return hashlib.sha256(report_text.encode()).hexdigest()


def sign_report(report_text: str, private_key_hex: str) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return private_key.sign(content_hash(report_text).encode()).hex()


def verify_report(report_text: str, signature_hex: str, public_key_hex: str) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), content_hash(report_text).encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def public_key_hex_from_private(private_key_hex: str) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return private_key.public_key().public_bytes_raw().hex()
