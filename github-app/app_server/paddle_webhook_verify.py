"""Paddle webhook signature verification."""

import hashlib
import hmac
import time


def verify_paddle_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
    # Paddle's own retry ledger (claim_webhook_delivery) already blocks
    # replay, so widening this only trades a little freshness for not
    # 401ing every billing webhook on modest host clock drift - 5s was
    # tight enough that a customer could pay and never get upgraded.
    tolerance_seconds: int = 60,
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
    return hmac.compare_digest(expected.encode(), h1.encode())
