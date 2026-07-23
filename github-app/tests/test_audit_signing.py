from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app_server.audit_signing import (
    content_hash,
    public_key_hex_from_private,
    sign_report,
    verify_report,
)


def _fresh_private_key_hex() -> str:
    return Ed25519PrivateKey.generate().private_bytes_raw().hex()


def test_content_hash_is_deterministic_sha256():
    assert content_hash("report body") == content_hash("report body")
    assert content_hash("report body") != content_hash("different body")
    assert len(content_hash("report body")) == 64


def test_sign_and_verify_round_trip():
    private_key_hex = _fresh_private_key_hex()
    public_key_hex = public_key_hex_from_private(private_key_hex)
    signature = sign_report("the report text", private_key_hex)

    assert verify_report("the report text", signature, public_key_hex) is True


def test_verify_fails_for_tampered_text():
    private_key_hex = _fresh_private_key_hex()
    public_key_hex = public_key_hex_from_private(private_key_hex)
    signature = sign_report("the original report", private_key_hex)

    assert verify_report("a tampered report", signature, public_key_hex) is False


def test_verify_fails_for_wrong_public_key():
    private_key_hex = _fresh_private_key_hex()
    other_public_key_hex = public_key_hex_from_private(_fresh_private_key_hex())
    signature = sign_report("the report text", private_key_hex)

    assert verify_report("the report text", signature, other_public_key_hex) is False


def test_verify_fails_for_malformed_signature():
    public_key_hex = public_key_hex_from_private(_fresh_private_key_hex())

    assert verify_report("the report text", "not-hex-at-all", public_key_hex) is False
