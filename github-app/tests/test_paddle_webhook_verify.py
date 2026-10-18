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


def test_non_ascii_signature_header_rejected_not_raised():
    body = b'{"event_type": "subscription.created"}'
    header = f"ts={int(time.time())};h1=café"
    assert verify_paddle_signature(body, header, SECRET) is False


def test_non_utf8_body_rejected_not_raised():
    # Before this fix, raw_body.decode('utf-8') raised UnicodeDecodeError
    # uncaught - any body containing invalid UTF-8 bytes crashed the whole
    # request with a 500 instead of the intended 401.
    body = b"\xff\xfe not valid utf-8"
    ts = int(time.time())
    header = f"ts={ts};h1=deadbeef"
    assert verify_paddle_signature(body, header, SECRET) is False


def test_oversized_timestamp_rejected_not_raised():
    # Code-review finding: a ts with a few hundred digits parses fine as a
    # Python int (ValueError only fires past CPython's ~4300-digit limit)
    # but is far larger than a C double can hold - abs(time.time() - ts)
    # used to raise OverflowError uncaught, crashing the handler on any
    # unauthenticated request with an oversized ts, before signature
    # verification ever ran.
    body = b'{"event_type": "subscription.created"}'
    oversized_ts = "9" * 400
    header = f"ts={oversized_ts};h1=deadbeef"
    assert verify_paddle_signature(body, header, SECRET) is False


def test_ts_with_a_leading_zero_still_verifies():
    # Code-review finding: the HMAC used to be recomputed over the
    # parsed-and-re-stringified int (f"{ts}"), not the header's own ts
    # string verbatim. int("0" + str(ts)) == ts, so a ts arriving with a
    # leading zero would have hashed a byte string Paddle never actually
    # signed, rejecting a genuinely valid, correctly-timed webhook.
    body = b'{"event_type": "subscription.created"}'
    ts = int(time.time())
    padded_ts_str = f"0{ts}"
    signed_payload = f"{padded_ts_str}:{body.decode()}"
    digest = hmac.new(SECRET.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    header = f"ts={padded_ts_str};h1={digest}"
    assert verify_paddle_signature(body, header, SECRET) is True
