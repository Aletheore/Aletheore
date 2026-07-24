import hashlib
import hmac
import time

from app_server.paddle_webhook_verify import verify_paddle_signature

SECRET = "pdl_ntfset_test_secret"


def _sign(raw_body: bytes, ts: int, secret: str = SECRET) -> str:
    signed_payload = f"{ts}:{raw_body.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def test_valid_signature_accepted():
    body = b'{"event_type": "subscription.created"}'
    assert verify_paddle_signature(body, _sign(body, int(time.time())), SECRET) is True


def test_tampered_body_rejected():
    body = b'{"event_type": "subscription.created"}'
    header = _sign(body, int(time.time()))
    tampered_body = b'{"event_type": "subscription.created", "extra": "injected"}'
    assert verify_paddle_signature(tampered_body, header, SECRET) is False


def test_wrong_secret_rejected():
    body = b'{"event_type": "subscription.created"}'
    header = _sign(body, int(time.time()), secret="wrong_secret")
    assert verify_paddle_signature(body, header, SECRET) is False


def test_expired_timestamp_rejected():
    body = b'{"event_type": "subscription.created"}'
    assert verify_paddle_signature(body, _sign(body, int(time.time()) - 3600), SECRET) is False


def test_malformed_header_rejected():
    body = b'{"event_type": "subscription.created"}'
    assert verify_paddle_signature(body, "not-a-valid-header", SECRET) is False
    assert verify_paddle_signature(body, "", SECRET) is False
