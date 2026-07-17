import hashlib
import hmac

from app_server.signature import verify_signature

SECRET = "test-webhook-secret"


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    payload = b'{"action": "opened"}'
    assert verify_signature(payload, _sign(payload, SECRET), SECRET) is True


def test_tampered_payload_fails():
    payload = b'{"action": "opened"}'
    header = _sign(payload, SECRET)
    assert verify_signature(b'{"action": "closed"}', header, SECRET) is False


def test_wrong_secret_fails():
    payload = b'{"action": "opened"}'
    assert verify_signature(payload, _sign(payload, "wrong-secret"), SECRET) is False


def test_missing_header_fails():
    assert verify_signature(b"{}", "", SECRET) is False


def test_malformed_header_fails():
    assert verify_signature(b"{}", "not-a-real-signature", SECRET) is False
