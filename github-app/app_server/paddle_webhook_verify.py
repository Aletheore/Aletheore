"""Paddle webhook signature verification."""

import hashlib
import hmac
import time


def verify_paddle_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
    tolerance_seconds: int = 5,
) -> bool:
    try:
        parts = dict(part.split("=", 1) for part in signature_header.split(";") if "=" in part)
    except ValueError:
        return False
    ts_str = parts.get("ts")
    h1 = parts.get("h1")
    if ts_str is None or h1 is None:
        return False
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if abs(time.time() - ts) > tolerance_seconds:
        return False

    try:
        signed_payload = f"{ts}:{raw_body.decode('utf-8')}"
    except UnicodeDecodeError:
        return False
    expected = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)
