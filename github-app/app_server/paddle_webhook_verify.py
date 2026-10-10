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
    try:
        # Code-review finding: a ts with a few hundred digits parses fine
        # as a Python int (ValueError above only fires past CPython's
        # ~4300-digit string-to-int limit) but is far larger than a C
        # double can hold - abs(time.time() - ts) then raises OverflowError,
        # uncaught here and uncaught by this function's only caller, so an
        # unauthenticated request with an oversized ts crashed the handler
        # before signature verification ever ran. Treated the same as any
        # other malformed timestamp: reject, don't crash.
        if abs(time.time() - ts) > tolerance_seconds:
            return False
    except OverflowError:
        return False

    try:
        # Hash the header's own ts string verbatim, not a value re-derived
        # from the parsed int: if ts_str ever arrives with formatting int()
        # normalizes away (a leading zero, a "+" sign), re-deriving it here
        # would hash a different byte string than Paddle actually signed,
        # rejecting a genuinely valid, correctly-timed webhook with a false
        # signature mismatch. Paddle's real ts format is a plain unpadded
        # unix integer today, so this is a zero-cost hardening, not a
        # behavior change.
        signed_payload = f"{ts_str}:{raw_body.decode('utf-8')}"
    except UnicodeDecodeError:
        return False
    expected = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), h1.encode())
