# Aletheore Paddle Subscription Claim Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link a Paddle subscription purchased on the unauthenticated marketing site to the right Aletheore GitHub App installation, for both brand-new and existing customers, with the webhook as the sole authoritative writer of `installations.plan`.

**Architecture:** A client-generated claim token travels two ways — as a `.aletheore.com`-scoped cookie and as Paddle `custom_data` — so a signature-verified webhook can record what was purchased, and a signed-in claim page on `app.aletheore.com` can apply it to the right installation.

**Tech Stack:** FastAPI (async, `asyncpg`), Postgres, vanilla JS (marketing site, no build step).

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-24-aletheore-paddle-subscription-claim-design.md` (merged to master, PR #28). Consult it for the "why" behind any decision below.
- **DB access house style for `app_server/`**: `async def fn(pool: asyncpg.Pool, ...) -> ...`, `$1`/`$2` placeholders, `await pool.execute(...)`/`await pool.fetchrow(...)`. Confirmed against `create_session`, `upsert_installation`, `get_installation_by_token_hash` (`app_server/db.py`) — this is different from `scan_worker/db.py`'s sync `psycopg` style and from the one sync-`psycopg` exception in `app_server/db.py` (`get_installation_by_token_hash_for_mcp`, used only by the hosted-MCP tool layer, which runs outside FastAPI's async request context). Everything in this plan runs inside normal async FastAPI routes — use `asyncpg`, not `psycopg`.
- **The webhook handler is the only code path that ever writes `installations.plan`** in this feature. The claim page only ever *applies* a plan value the webhook already resolved and stored.
- **Every webhook handler must be idempotent** — Paddle delivers at-least-once and retries on any non-2xx. UPSERT keyed on `paddle_subscription_id`, not on `event.eventId` (no separate dedup ledger needed here — a subscription's identity is inherently stable across retries).
- **Webhook signature verification is mandatory and must reject before touching the DB** — invalid signature, missing header, or expired timestamp all return a non-2xx (this plan uses `401`) without any write.
- **`next` redirect params must be validated as same-origin relative paths only** — accepting an arbitrary `next` value would be a real open-redirect vulnerability.
- **No admin UI for the abandoned-claim recovery path in this plan.** Per the design spec, that's intentionally a direct DB query (`SELECT * FROM pending_subscription_claims WHERE paddle_customer_email = $1 AND claimed_at IS NULL`) run manually against production when a support request comes in — not a built page. Building that UI is a reasonable future addition once/if it becomes a frequent enough need to justify it, not part of this plan.

---

### Task 1: DB migration — claims table and installation columns

**Files:**
- Create: `github-app/migrations/018_pending_subscription_claims.sql`
- Modify: `github-app/app_server/db.py`
- Test: `github-app/tests/test_app_server_db.py`

**Interfaces:**
- Produces: `insert_pending_subscription_claim(pool, claim_token, paddle_subscription_id, paddle_customer_id, paddle_customer_email, plan) -> None` (UPSERT on `paddle_subscription_id`), `get_pending_subscription_claim_by_token(pool, claim_token) -> dict | None`, `mark_subscription_claim_claimed(pool, claim_token, installation_id) -> None`, `add_paddle_ids_to_installation(pool, installation_id, paddle_subscription_id, paddle_customer_id) -> None`.

- [ ] **Step 1: Write the migration**

```sql
CREATE TABLE pending_subscription_claims (
    id BIGSERIAL PRIMARY KEY,
    claim_token TEXT NOT NULL UNIQUE,
    paddle_subscription_id TEXT NOT NULL UNIQUE,
    paddle_customer_id TEXT NOT NULL,
    paddle_customer_email TEXT,
    plan TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    claimed_by_installation_id BIGINT REFERENCES installations(installation_id)
);
CREATE INDEX pending_subscription_claims_token ON pending_subscription_claims (claim_token);

ALTER TABLE installations ADD COLUMN paddle_subscription_id TEXT;
ALTER TABLE installations ADD COLUMN paddle_customer_id TEXT;
```

- [ ] **Step 2: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_insert_pending_subscription_claim_upserts_on_subscription_id(db_pool):
    await insert_pending_subscription_claim(
        db_pool, "tok_a", "sub_123", "ctm_1", "buyer@example.com", "team"
    )
    row = await get_pending_subscription_claim_by_token(db_pool, "tok_a")
    assert row["plan"] == "team"
    assert row["claimed_at"] is None

    # retry delivery with the same subscription id (different claim_token
    # would be unusual in practice, but the UPSERT key is subscription_id)
    await insert_pending_subscription_claim(
        db_pool, "tok_a", "sub_123", "ctm_1", "buyer@example.com", "team"
    )
    row_again = await get_pending_subscription_claim_by_token(db_pool, "tok_a")
    assert row_again["id"] == row["id"]  # same row, not a duplicate


@pytest.mark.asyncio
async def test_mark_subscription_claim_claimed(db_pool):
    await insert_installation_row(db_pool, installation_id=1, account_login="acme", plan="free")
    await insert_pending_subscription_claim(db_pool, "tok_b", "sub_456", "ctm_2", None, "indie")

    await mark_subscription_claim_claimed(db_pool, "tok_b", 1)

    row = await get_pending_subscription_claim_by_token(db_pool, "tok_b")
    assert row["claimed_at"] is not None
    assert row["claimed_by_installation_id"] == 1


@pytest.mark.asyncio
async def test_add_paddle_ids_to_installation(db_pool):
    await insert_installation_row(db_pool, installation_id=2, account_login="acme", plan="free")
    await add_paddle_ids_to_installation(db_pool, 2, "sub_789", "ctm_3")
    row = await get_installation(db_pool, 2)
    assert row["paddle_subscription_id"] == "sub_789"
    assert row["paddle_customer_id"] == "ctm_3"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_app_server_db.py -k "pending_subscription_claim or add_paddle_ids" -v`
Expected: FAIL (functions don't exist)

- [ ] **Step 4: Implement**

Append to `github-app/app_server/db.py`:

```python
async def insert_pending_subscription_claim(
    pool: asyncpg.Pool,
    claim_token: str,
    paddle_subscription_id: str,
    paddle_customer_id: str,
    paddle_customer_email: str | None,
    plan: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO pending_subscription_claims
            (claim_token, paddle_subscription_id, paddle_customer_id, paddle_customer_email, plan)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (paddle_subscription_id) DO UPDATE SET
            claim_token = EXCLUDED.claim_token,
            paddle_customer_id = EXCLUDED.paddle_customer_id,
            paddle_customer_email = EXCLUDED.paddle_customer_email,
            plan = EXCLUDED.plan
        """,
        claim_token, paddle_subscription_id, paddle_customer_id, paddle_customer_email, plan,
    )


async def get_pending_subscription_claim_by_token(pool: asyncpg.Pool, claim_token: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT claim_token, paddle_subscription_id, paddle_customer_id, paddle_customer_email,
               plan, created_at, claimed_at, claimed_by_installation_id
        FROM pending_subscription_claims
        WHERE claim_token = $1
        """,
        claim_token,
    )
    return dict(row) if row else None


async def mark_subscription_claim_claimed(pool: asyncpg.Pool, claim_token: str, installation_id: int) -> None:
    await pool.execute(
        """
        UPDATE pending_subscription_claims
        SET claimed_at = now(), claimed_by_installation_id = $2
        WHERE claim_token = $1
        """,
        claim_token, installation_id,
    )


async def add_paddle_ids_to_installation(
    pool: asyncpg.Pool, installation_id: int, paddle_subscription_id: str, paddle_customer_id: str
) -> None:
    await pool.execute(
        """
        UPDATE installations
        SET paddle_subscription_id = $2, paddle_customer_id = $3
        WHERE installation_id = $1
        """,
        installation_id, paddle_subscription_id, paddle_customer_id,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_app_server_db.py -k "pending_subscription_claim or add_paddle_ids" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add github-app/migrations/018_pending_subscription_claims.sql github-app/app_server/db.py github-app/tests/test_app_server_db.py
git commit -m "feat: add pending_subscription_claims table and installation Paddle ID columns"
```

---

### Task 2: Webhook signature verification (pure, unit-testable in isolation)

**Files:**
- Create: `github-app/app_server/paddle_webhook_verify.py`
- Test: `github-app/tests/test_paddle_webhook_verify.py`

**Interfaces:**
- Produces: `verify_paddle_signature(raw_body: bytes, signature_header: str, secret: str, tolerance_seconds: int = 5) -> bool`.

Algorithm (verified against Paddle's own docs, not guessed): header format `ts=<unix_ts>;h1=<hex>`. Signed payload is the literal string `f"{ts}:{raw_body_as_text}"` — the exact raw bytes as received, never re-serialized. HMAC-SHA256 of that string using the webhook destination's secret (not the API key) as the key, hex digest, compared to `h1` via a timing-safe comparison. Reject if `abs(now - ts) > tolerance_seconds` (Paddle's own SDK default tolerance is 5 seconds).

- [ ] **Step 1: Write the failing tests**

```python
import hashlib
import hmac
import time

import pytest

from app_server.paddle_webhook_verify import verify_paddle_signature

SECRET = "pdl_ntfset_test_secret"


def _sign(raw_body: bytes, ts: int, secret: str = SECRET) -> str:
    signed_payload = f"{ts}:{raw_body.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def test_valid_signature_accepted():
    body = b'{"event_type": "subscription.created"}'
    ts = int(time.time())
    header = _sign(body, ts)
    assert verify_paddle_signature(body, header, SECRET) is True


def test_tampered_body_rejected():
    body = b'{"event_type": "subscription.created"}'
    ts = int(time.time())
    header = _sign(body, ts)
    tampered_body = b'{"event_type": "subscription.created", "extra": "injected"}'
    assert verify_paddle_signature(tampered_body, header, SECRET) is False


def test_wrong_secret_rejected():
    body = b'{"event_type": "subscription.created"}'
    ts = int(time.time())
    header = _sign(body, ts, secret="wrong_secret")
    assert verify_paddle_signature(body, header, SECRET) is False


def test_expired_timestamp_rejected():
    body = b'{"event_type": "subscription.created"}'
    ts = int(time.time()) - 3600  # 1 hour old
    header = _sign(body, ts)
    assert verify_paddle_signature(body, header, SECRET) is False


def test_malformed_header_rejected():
    body = b'{"event_type": "subscription.created"}'
    assert verify_paddle_signature(body, "not-a-valid-header", SECRET) is False
    assert verify_paddle_signature(body, "", SECRET) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_paddle_webhook_verify.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Paddle webhook signature verification - raw HMAC-SHA256, no SDK dependency.

Algorithm per https://developer.paddle.com/webhooks/about/signature-verification:
header is `ts=<unix_ts>;h1=<hex_hmac>`; signed payload is the literal string
"{ts}:{raw_body}" using the exact raw bytes as received (never re-serialized
JSON, which would not byte-match what Paddle signed).
"""

import hashlib
import hmac
import time


def verify_paddle_signature(
    raw_body: bytes, signature_header: str, secret: str, tolerance_seconds: int = 5
) -> bool:
    parts = dict(
        part.split("=", 1) for part in signature_header.split(";") if "=" in part
    )
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

    signed_payload = f"{ts}:{raw_body.decode('utf-8')}"
    expected = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_paddle_webhook_verify.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/paddle_webhook_verify.py github-app/tests/test_paddle_webhook_verify.py
git commit -m "feat: raw HMAC-SHA256 Paddle webhook signature verification"
```

---

### Task 3: Price ID → plan mapping

**Files:**
- Create: `github-app/app_server/paddle_pricing.py`
- Test: `github-app/tests/test_paddle_pricing.py`

**Interfaces:**
- Produces: `PADDLE_PRICE_TO_PLAN: dict[str, str]`, `resolve_plan_for_price_id(price_id: str) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
from app_server.paddle_pricing import resolve_plan_for_price_id


def test_resolves_all_six_real_price_ids():
    assert resolve_plan_for_price_id("pri_01ky9jwz35hvj5xs6f8xqw6htt") == "indie"
    assert resolve_plan_for_price_id("pri_01ky9jwzd6k9rhmnj8b4drbygg") == "indie"
    assert resolve_plan_for_price_id("pri_01ky9jx0gbx02mnn4d166yp3vc") == "team"
    assert resolve_plan_for_price_id("pri_01ky9jx0rkkkz75atfb29me9mn") == "team"
    assert resolve_plan_for_price_id("pri_01ky9jx1bkbbkfd9zspcgzd7p8") == "enterprise"
    assert resolve_plan_for_price_id("pri_01ky9jx1pbbpsexbmtbk1wfej1") == "enterprise"


def test_unknown_price_id_returns_none():
    assert resolve_plan_for_price_id("pri_totally_unknown") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python -m pytest tests/test_paddle_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Paddle price ID -> Aletheore plan mapping, from the sandbox catalog seeded
this session (github-app/scripts or the seed script's output - update this
mapping if the catalog is ever recreated with new price IDs)."""

PADDLE_PRICE_TO_PLAN: dict[str, str] = {
    "pri_01ky9jwz35hvj5xs6f8xqw6htt": "indie",       # Indie monthly
    "pri_01ky9jwzd6k9rhmnj8b4drbygg": "indie",       # Indie yearly
    "pri_01ky9jx0gbx02mnn4d166yp3vc": "team",        # Team monthly
    "pri_01ky9jx0rkkkz75atfb29me9mn": "team",        # Team yearly
    "pri_01ky9jx1bkbbkfd9zspcgzd7p8": "enterprise",  # Enterprise monthly
    "pri_01ky9jx1pbbpsexbmtbk1wfej1": "enterprise",  # Enterprise yearly
}


def resolve_plan_for_price_id(price_id: str) -> str | None:
    return PADDLE_PRICE_TO_PLAN.get(price_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python -m pytest tests/test_paddle_pricing.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/paddle_pricing.py github-app/tests/test_paddle_pricing.py
git commit -m "feat: Paddle price ID to Aletheore plan mapping"
```

---

### Task 4: Webhook endpoint

**Files:**
- Create: `github-app/app_server/webhooks/paddle.py`
- Modify: `github-app/app_server/config.py` (add `paddle_webhook_secret` setting)
- Modify: `github-app/app_server/main.py` (register route)
- Test: `github-app/tests/test_webhooks_paddle.py`

**Interfaces:**
- Consumes: Task 1's `insert_pending_subscription_claim`; Task 2's `verify_paddle_signature`; Task 3's `resolve_plan_for_price_id`.
- Produces: `paddle_webhook_router` (FastAPI `APIRouter`), `POST /webhooks/paddle`.

- [ ] **Step 1: Add the config field**

In `github-app/app_server/config.py`, add `paddle_webhook_secret: str` to the `Settings` dataclass and `paddle_webhook_secret=_required_env("PADDLE_WEBHOOK_SECRET")` to `get_settings()`.

- [ ] **Step 2: Write the failing tests**

```python
import hashlib
import hmac
import json
import time

import pytest


def _sign(raw_body: bytes, secret: str) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}:{raw_body.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _subscription_created_payload(price_id: str, claim_token: str) -> dict:
    return {
        "event_type": "subscription.created",
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "custom_data": {"claim_token": claim_token},
            "items": [{"price": {"id": price_id}}],
        },
    }


@pytest.mark.asyncio
async def test_valid_subscription_created_creates_pending_claim(test_client, webhook_secret):
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_abc")).encode()
    response = await test_client.post(
        "/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body, webhook_secret)}
    )
    assert response.status_code == 200

    from app_server.db import get_pending_subscription_claim_by_token
    claim = await get_pending_subscription_claim_by_token(test_client.app.state.db_pool, "claim_abc")
    assert claim["plan"] == "indie"
    assert claim["paddle_subscription_id"] == "sub_test_123"


@pytest.mark.asyncio
async def test_invalid_signature_rejected_with_no_write(test_client, webhook_secret):
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_xyz")).encode()
    response = await test_client.post(
        "/webhooks/paddle", content=body, headers={"paddle-signature": "ts=1;h1=deadbeef"}
    )
    assert response.status_code == 401

    from app_server.db import get_pending_subscription_claim_by_token
    claim = await get_pending_subscription_claim_by_token(test_client.app.state.db_pool, "claim_xyz")
    assert claim is None


@pytest.mark.asyncio
async def test_missing_signature_header_rejected(test_client):
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_none")).encode()
    response = await test_client.post("/webhooks/paddle", content=body)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_price_id_returns_200_but_writes_no_claim(test_client, webhook_secret):
    # Unrecognized price ID isn't a delivery failure (retrying won't help),
    # so ack normally rather than burning Paddle's retry budget on it - just log it.
    body = json.dumps(_subscription_created_payload("pri_totally_unknown", "claim_unknown")).encode()
    response = await test_client.post(
        "/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body, webhook_secret)}
    )
    assert response.status_code == 200
    from app_server.db import get_pending_subscription_claim_by_token
    assert await get_pending_subscription_claim_by_token(test_client.app.state.db_pool, "claim_unknown") is None


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent(test_client, webhook_secret):
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_dup")).encode()
    headers = {"paddle-signature": _sign(body, webhook_secret)}
    r1 = await test_client.post("/webhooks/paddle", content=body, headers=headers)
    r2 = await test_client.post("/webhooks/paddle", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    from app_server.db import get_pending_subscription_claim_by_token
    claim = await get_pending_subscription_claim_by_token(test_client.app.state.db_pool, "claim_dup")
    assert claim is not None  # one row, no crash on the second delivery
```

(`webhook_secret` fixture: a test-only value matching `PADDLE_WEBHOOK_SECRET` in the test environment — add alongside existing test fixtures following whatever pattern `test_client`/`db_pool` already use in this test suite's `conftest.py`.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_webhooks_paddle.py -v`
Expected: FAIL — route doesn't exist (404s)

- [ ] **Step 4: Implement `webhooks/paddle.py`**

```python
import logging

from fastapi import APIRouter, Request, Response

from app_server.config import get_settings
from app_server.db import insert_pending_subscription_claim
from app_server.paddle_pricing import resolve_plan_for_price_id
from app_server.paddle_webhook_verify import verify_paddle_signature

paddle_webhook_router = APIRouter()
logger = logging.getLogger(__name__)


@paddle_webhook_router.post("/webhooks/paddle")
async def handle_paddle_webhook(request: Request) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("paddle-signature", "")
    settings = get_settings()

    if not signature or not verify_paddle_signature(raw_body, signature, settings.paddle_webhook_secret):
        return Response(status_code=401)

    payload = await request.json()
    event_type = payload.get("event_type")

    if event_type == "subscription.created":
        data = payload["data"]
        claim_token = (data.get("custom_data") or {}).get("claim_token")
        items = data.get("items") or []
        price_id = items[0]["price"]["id"] if items else None
        plan = resolve_plan_for_price_id(price_id) if price_id else None

        if claim_token and plan:
            await insert_pending_subscription_claim(
                request.app.state.db_pool,
                claim_token,
                data["id"],
                data["customer_id"],
                None,  # customer email arrives on a separate customer.* event, not this one
                plan,
            )
        else:
            logger.warning(
                "subscription.created missing claim_token or unresolvable price_id (price_id=%s)", price_id
            )

    return Response(status_code=200)
```

Register in `main.py`, alongside the other routers:
```python
from app_server.webhooks.paddle import paddle_webhook_router
app.include_router(paddle_webhook_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_webhooks_paddle.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Backfill customer email for the abandoned-claim support path**

The design spec's abandoned-payment recovery relies on looking up `pending_subscription_claims` by `paddle_customer_email` — but `subscription.created` doesn't carry the customer's email, only `customer_id`, so Step 4's handler always leaves that column `NULL`. Fix: also handle `customer.created`/`customer.updated`, subscribe to both in the notification destination (Task 9), and backfill.

Add to `db.py`:
```python
async def backfill_customer_email_for_claims(pool: asyncpg.Pool, paddle_customer_id: str, email: str) -> None:
    await pool.execute(
        """
        UPDATE pending_subscription_claims
        SET paddle_customer_email = $2
        WHERE paddle_customer_id = $1 AND paddle_customer_email IS NULL
        """,
        paddle_customer_id, email,
    )
```

Add a test mirroring the `subscription.created` tests above: a `customer.updated` event for a `paddle_customer_id` that already has a pending, email-less claim results in that claim's `paddle_customer_email` being set; a `customer.updated` for an unknown/unrelated customer id is a no-op, not an error (200, no matching rows).

Extend `handle_paddle_webhook`'s branching:
```python
    elif event_type in ("customer.created", "customer.updated"):
        data = payload["data"]
        email = data.get("email")
        if email:
            await backfill_customer_email_for_claims(request.app.state.db_pool, data["id"], email)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_webhooks_paddle.py -v`
Expected: PASS (all tests, including the new customer-email backfill ones)

- [ ] **Step 8: Commit**

```bash
git add github-app/app_server/webhooks/paddle.py github-app/app_server/db.py github-app/app_server/config.py github-app/app_server/main.py github-app/tests/test_webhooks_paddle.py
git commit -m "feat: Paddle webhook endpoint for subscription.created and customer email backfill"
```

---

### Task 5: `next` redirect support in the existing GitHub sign-in flow

**Files:**
- Modify: `github-app/app_server/auth.py`
- Test: `github-app/tests/test_auth.py`

**Interfaces:**
- Produces: `_is_safe_next_path(next_path: str | None) -> str` (returns a validated same-origin relative path, or the `/dashboard` default), modified `login(next: str | None = None)` and `callback(...)` signatures.

- [ ] **Step 1: Write the failing tests**

```python
from app_server.auth import _is_safe_next_path


def test_safe_relative_path_accepted():
    assert _is_safe_next_path("/subscribe/claim") == "/subscribe/claim"


def test_missing_next_defaults_to_dashboard():
    assert _is_safe_next_path(None) == "/dashboard"


def test_absolute_url_rejected():
    assert _is_safe_next_path("https://evil.example.com/phish") == "/dashboard"


def test_protocol_relative_url_rejected():
    assert _is_safe_next_path("//evil.example.com/phish") == "/dashboard"


def test_path_not_starting_with_slash_rejected():
    assert _is_safe_next_path("evil.example.com") == "/dashboard"


@pytest.mark.asyncio
async def test_login_sets_next_cookie_and_callback_redirects_there(test_client):
    login_response = await test_client.get("/auth/login?next=/subscribe/claim", follow_redirects=False)
    next_cookie = login_response.cookies.get("aletheore_oauth_next")
    assert next_cookie == "/subscribe/claim"
    # (full callback redirect assertion belongs in the existing OAuth-mocking
    # test fixture this file already uses for /auth/callback, following its
    # established pattern for stubbing the GitHub token/user HTTP calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_auth.py -k "safe_next or login_sets_next" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add to `auth.py`:

```python
NEXT_COOKIE_NAME = "aletheore_oauth_next"


def _is_safe_next_path(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/dashboard"
    return next_path
```

Modify `login()`:
```python
@auth_router.get("/auth/login")
async def login(next: str | None = None):
    settings = get_settings()
    state = secrets.token_urlsafe(32)
    safe_next = _is_safe_next_path(next)
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.public_base_url}/auth/callback"
        f"&state={state}"
    )
    response = RedirectResponse(url=url, status_code=307)
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        sign_oauth_state(state, settings.session_secret),
        httponly=True, secure=True, samesite="lax", max_age=int(OAUTH_STATE_TTL.total_seconds()),
    )
    response.set_cookie(
        NEXT_COOKIE_NAME, safe_next,
        httponly=True, secure=True, samesite="lax", max_age=int(OAUTH_STATE_TTL.total_seconds()),
    )
    return response
```

Modify `callback()`'s final redirect (replace the hardcoded `"/dashboard"`):
```python
    next_path = _is_safe_next_path(request.cookies.get(NEXT_COOKIE_NAME))
    response = RedirectResponse(url=next_path, status_code=307)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sign_session_id(session_id, settings.session_secret),
        httponly=True, secure=True, samesite="lax", max_age=int(SESSION_TTL.total_seconds()),
    )
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME)
    response.delete_cookie(NEXT_COOKIE_NAME)
    return response
```

Re-validate `next_path` from the cookie through `_is_safe_next_path` again in `callback()` (not just at `login()` time) — the cookie is `httponly`/signed-adjacent via being server-set, but re-validating on read is cheap insurance against any future code path that might set this cookie less carefully.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_auth.py -v`
Expected: PASS (all `test_auth.py` tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/auth.py github-app/tests/test_auth.py
git commit -m "feat: safe next-path redirect support in GitHub sign-in flow"
```

---

### Task 6: GitHub App install link

**Files:**
- Modify: `github-app/app_server/config.py`
- Create: `github-app/app_server/github_install.py`
- Test: `github-app/tests/test_github_install.py`

**Interfaces:**
- Produces: `github_app_install_url(next_path: str) -> str`.

- [ ] **Step 1: Add config field**

In `config.py`: add `github_app_slug: str` to `Settings`, `github_app_slug=_required_env("GITHUB_APP_SLUG")` in `get_settings()`.

- [ ] **Step 2: Write the failing test**

```python
from unittest.mock import patch

from app_server.github_install import github_app_install_url


def test_install_url_includes_slug_and_state():
    with patch("app_server.github_install.get_settings") as mock_settings:
        mock_settings.return_value.github_app_slug = "aletheore"
        mock_settings.return_value.public_base_url = "https://aletheore.com"
        url = github_app_install_url("/subscribe/claim")
        assert url.startswith("https://github.com/apps/aletheore/installations/new")
        assert "state=" in url
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd github-app && python -m pytest tests/test_github_install.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement**

```python
from urllib.parse import quote

from app_server.config import get_settings


def github_app_install_url(next_path: str) -> str:
    settings = get_settings()
    # GitHub redirects back through /auth/callback after an app install (see
    # the existing comment in auth.py's callback() about this entry point) -
    # the state param here just needs to survive the round trip as the next
    # destination; auth.py's callback() falls back to /dashboard if this
    # install path never goes through /auth/login's own next-cookie at all,
    # so pass next_path directly as GitHub's own `state` passthrough isn't
    # read by our callback (it only checks state against the OAuth-cookie
    # path) - the actual next destination for this entry point is carried by
    # having the "Install" link point through /auth/login?next=... first,
    # not by relying on GitHub's install-flow state value.
    return (
        f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        f"?state={quote(next_path)}"
    )
```

Note on the comment above: since `auth.py`'s existing `callback()` only honors the `next`/`aletheore_oauth_next` cookie set by `/auth/login`, the "Install the GitHub App" link on the claim page (Task 7) must point at `/auth/login?next=/subscribe/claim` first (which redirects to GitHub's OAuth authorize, and from there the user separately installs the app), not directly at `github_app_install_url()`, for the return trip to land back on the claim page correctly. Keep `github_app_install_url()` as a documented building block for a future direct-install-link entry point, but Task 7's actual "Install" button uses `/auth/login?next=/subscribe/claim`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd github-app && python -m pytest tests/test_github_install.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/config.py github-app/app_server/github_install.py github-app/tests/test_github_install.py
git commit -m "feat: GitHub App install URL builder"
```

---

### Task 7: Claim page — GET /subscribe/claim and POST /subscribe/claim/apply

**Files:**
- Modify: `github-app/app_server/frontend.py`
- Test: `github-app/tests/test_frontend_claim.py`

**Interfaces:**
- Consumes: `get_current_session` (existing, `auth.py`), Task 1's DB helpers, existing `get_installations_for_user`-equivalent (verify the exact existing helper name for "installations this signed-in user administers" — reuse whatever `dashboard.py`'s `/app/repos` endpoint already uses rather than duplicating that query; if no such helper is exported/importable, add one to `db.py` following the same async pattern rather than inlining the query in `frontend.py`).
- Produces: `CLAIM_TOKEN_COOKIE_NAME = "claim_token"`, `GET /subscribe/claim`, `POST /subscribe/claim/apply`.

- [ ] **Step 1: Confirm the existing "installations for this user" query**

Run: `grep -n "app/repos" -A 20 github-app/app_server/dashboard.py` and read the result before writing Step 4 — reuse that exact helper/query rather than reimplementing it.

- [ ] **Step 2: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_claim_page_redirects_to_login_when_not_signed_in(test_client):
    response = await test_client.get("/subscribe/claim", follow_redirects=False)
    assert response.status_code == 307
    assert "/auth/login" in response.headers["location"]
    assert "next=%2Fsubscribe%2Fclaim" in response.headers["location"]


@pytest.mark.asyncio
async def test_claim_page_shows_polling_state_when_claim_not_found_yet(test_client, signed_in_session):
    test_client.cookies.set("claim_token", "tok_not_found_yet")
    response = await test_client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "Confirming your payment" in response.text


@pytest.mark.asyncio
async def test_claim_page_shows_already_claimed_state(test_client, signed_in_session, db_pool):
    await insert_installation_row(db_pool, installation_id=1, account_login="acme", plan="team")
    await insert_pending_subscription_claim(db_pool, "tok_claimed", "sub_1", "ctm_1", None, "team")
    await mark_subscription_claim_claimed(db_pool, "tok_claimed", 1)
    test_client.cookies.set("claim_token", "tok_claimed")

    response = await test_client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "already activated" in response.text.lower()


@pytest.mark.asyncio
async def test_claim_page_zero_installations_prompts_install(test_client, signed_in_session, db_pool):
    await insert_pending_subscription_claim(db_pool, "tok_zero", "sub_2", "ctm_2", None, "indie")
    test_client.cookies.set("claim_token", "tok_zero")

    response = await test_client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "Install the Aletheore GitHub App" in response.text


@pytest.mark.asyncio
async def test_claim_page_one_installation_shows_confirm(test_client, signed_in_session, db_pool):
    # signed_in_session fixture's user administers installation_id=1 - see existing
    # fixture used by dashboard tests for the admin-membership check pattern
    await insert_installation_row(db_pool, installation_id=1, account_login="acme", plan="free")
    await insert_pending_subscription_claim(db_pool, "tok_one", "sub_3", "ctm_3", None, "team")
    test_client.cookies.set("claim_token", "tok_one")

    response = await test_client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "Apply" in response.text and "acme" in response.text


@pytest.mark.asyncio
async def test_apply_updates_installation_plan_and_marks_claimed(test_client, signed_in_session, db_pool):
    await insert_installation_row(db_pool, installation_id=1, account_login="acme", plan="free")
    await insert_pending_subscription_claim(db_pool, "tok_apply", "sub_4", "ctm_4", None, "enterprise")

    response = await test_client.post(
        "/subscribe/claim/apply", data={"claim_token": "tok_apply", "installation_id": "1"}
    )
    assert response.status_code == 200

    installation = await get_installation(db_pool, 1)
    assert installation["plan"] == "enterprise"
    assert installation["paddle_subscription_id"] == "sub_4"

    claim = await get_pending_subscription_claim_by_token(db_pool, "tok_apply")
    assert claim["claimed_at"] is not None


@pytest.mark.asyncio
async def test_apply_rejects_installation_user_does_not_administer(test_client, signed_in_session, db_pool):
    # installation_id=999 is not one signed_in_session's user administers
    await insert_installation_row(db_pool, installation_id=999, account_login="someone-else", plan="free")
    await insert_pending_subscription_claim(db_pool, "tok_forbidden", "sub_5", "ctm_5", None, "team")

    response = await test_client.post(
        "/subscribe/claim/apply", data={"claim_token": "tok_forbidden", "installation_id": "999"}
    )
    assert response.status_code == 403
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_frontend_claim.py -v`
Expected: FAIL — routes don't exist

- [ ] **Step 4: Implement**

Append to `frontend.py` (exact query for "installations this user administers" — substitute whatever Step 1 found `/app/repos` actually calls, in place of the placeholder `get_installations_administered_by_user` name below if it differs):

```python
CLAIM_TOKEN_COOKIE_NAME = "claim_token"


def _claim_polling_page() -> str:
    return _page_head("Confirming your subscription — Aletheore") + """
<div class="claim-page">
  <h1>Confirming your payment…</h1>
  <p>This usually takes a few seconds.</p>
  <script>setTimeout(() => location.reload(), 2000);</script>
</div>
"""


def _claim_already_claimed_page() -> str:
    return _page_head("Subscription active — Aletheore") + """
<div class="claim-page">
  <h1>Already activated</h1>
  <p>This subscription is already applied to your account.</p>
  <a class="btn btn-primary" href="/dashboard">Go to dashboard</a>
</div>
"""


def _claim_install_prompt_page(plan: str) -> str:
    return _page_head("Install the GitHub App — Aletheore") + f"""
<div class="claim-page">
  <h1>Install the Aletheore GitHub App</h1>
  <p>Install the app to activate your {plan.title()} plan.</p>
  <a class="btn btn-primary" href="/auth/login?next=/subscribe/claim">Install the GitHub App</a>
</div>
"""


def _claim_selection_page(plan: str, installations: list[dict], claim_token: str) -> str:
    options = "\n".join(
        f'<label><input type="radio" name="installation_id" value="{i["installation_id"]}"> {i["account_login"]}</label>'
        for i in installations
    )
    return _page_head("Apply your plan — Aletheore") + f"""
<div class="claim-page">
  <h1>Apply your {plan.title()} plan</h1>
  <form method="post" action="/subscribe/claim/apply">
    <input type="hidden" name="claim_token" value="{claim_token}">
    {options}
    <button class="btn btn-primary" type="submit">Apply</button>
  </form>
</div>
"""


@frontend_router.get("/subscribe/claim", response_class=HTMLResponse)
async def subscribe_claim_page(request: Request):
    session = await get_current_session(request)
    if session is None:
        return RedirectResponse(url="/auth/login?next=/subscribe/claim", status_code=307)

    claim_token = request.cookies.get(CLAIM_TOKEN_COOKIE_NAME)
    if not claim_token:
        return _no_store_html(_claim_polling_page())

    pool = request.app.state.db_pool
    claim = await get_pending_subscription_claim_by_token(pool, claim_token)
    if claim is None:
        return _no_store_html(_claim_polling_page())
    if claim["claimed_at"] is not None:
        return _no_store_html(_claim_already_claimed_page())

    installations = await get_installations_administered_by_user(pool, session["github_login"])
    if not installations:
        return _no_store_html(_claim_install_prompt_page(claim["plan"]))

    return _no_store_html(_claim_selection_page(claim["plan"], installations, claim_token))


@frontend_router.post("/subscribe/claim/apply")
async def subscribe_claim_apply(request: Request):
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401)

    form = await request.form()
    claim_token = form["claim_token"]
    installation_id = int(form["installation_id"])

    pool = request.app.state.db_pool
    installations = await get_installations_administered_by_user(pool, session["github_login"])
    if installation_id not in {i["installation_id"] for i in installations}:
        raise HTTPException(status_code=403)

    claim = await get_pending_subscription_claim_by_token(pool, claim_token)
    if claim is None or claim["claimed_at"] is not None:
        raise HTTPException(status_code=409)

    await update_installation_plan(pool, installation_id, claim["plan"])
    await add_paddle_ids_to_installation(
        pool, installation_id, claim["paddle_subscription_id"], claim["paddle_customer_id"]
    )
    await mark_subscription_claim_claimed(pool, claim_token, installation_id)

    response = _no_store_html(_claim_already_claimed_page())
    response.delete_cookie(CLAIM_TOKEN_COOKIE_NAME)
    return response
```

`update_installation_plan(pool, installation_id, plan)` — if no existing helper with this exact name exists, add it to `db.py` following the same `async def fn(pool: asyncpg.Pool, ...) -> None: await pool.execute("UPDATE installations SET plan = $2 ... WHERE installation_id = $1", ...)` pattern as the other Task 1 helpers, rather than inlining the SQL here.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_frontend_claim.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/frontend.py github-app/app_server/db.py github-app/tests/test_frontend_claim.py
git commit -m "feat: subscription claim page and apply endpoint"
```

---

### Task 8: Marketing site — claim token generation and Paddle custom_data

**Files:**
- Modify: `website/paddle-checkout.js`
- Test: manual/browser verification (no test framework exists for `website/`'s vanilla JS today — matches existing precedent; verify via Task 10's real browser check instead)

**Interfaces:**
- Modifies: `subscribe(tierKey)` in `paddle-checkout.js`.

- [ ] **Step 1: Implement**

```javascript
function generateClaimToken() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function subscribe(tierKey) {
  const paddle = await initPaddle();
  const tier = TIERS[tierKey];
  const claimToken = generateClaimToken();

  document.cookie = `claim_token=${claimToken}; domain=.aletheore.com; path=/; max-age=3600; secure; samesite=lax`;

  paddle.Checkout.open({
    items: [{ priceId: tier.priceId[billingInterval], quantity: 1 }],
    customData: { claim_token: claimToken },
    settings: {
      displayMode: "overlay",
      variant: "one-page",
      successUrl: "https://app.aletheore.com/subscribe/claim",
    },
  });
}
```

Two changes from the current implementation: `customData` added to the `Checkout.open()` call (verify the exact camelCase-vs-snake_case key Paddle.js's browser SDK expects for this field — the MCP/API layer uses `custom_data`, but the client-side `Paddle.js` object's own JS API conventionally uses `customData`; confirm against the already-working checkout's browser network tab or Paddle.js's own type definitions before assuming, since getting this key wrong means it silently never reaches the webhook), and `successUrl` changed from the marketing site's own `/welcome` to `app.aletheore.com/subscribe/claim` — the claim flow replaces `/welcome` as the post-checkout destination. `website/welcome.html` can stay as a fallback/dead page or be removed in a follow-up; not deleting it in this task since nothing currently depends on that decision either way.

- [ ] **Step 2: Commit**

```bash
git add website/paddle-checkout.js
git commit -m "feat: claim token generation and Paddle custom_data on marketing-site checkout"
```

---

### Task 9: Deploy and real end-to-end verification

**Files:** None (deployment + manual verification).

- [ ] **Step 1: Add the new required env vars to the server**

`PADDLE_WEBHOOK_SECRET` (from creating the notification destination in Paddle's dashboard — sandbox first — pointed at `https://app.aletheore.com/webhooks/paddle`) and `GITHUB_APP_SLUG` both need adding to `github-app/.env` on the server before deploying, following the same pattern used earlier this session for `AUDIT_SIGNING_PRIVATE_KEY` (both are `_required_env()`, so a missing value crash-loops `app-server` on startup).

- [ ] **Step 2: Create the Paddle notification destination**

Via the dashboard (`Paddle > Developer tools > Notifications`, sandbox first) since the MCP `execute` tool's auth bug (documented in this session, unresolved) blocks doing this programmatically — subscribe to `subscription.created`, `customer.created`, and `customer.updated`. Copy the destination secret into `PADDLE_WEBHOOK_SECRET`.

- [ ] **Step 3: Deploy**

Follow this session's established pattern: push, PR, real CI, merge, then `git pull && docker compose build app-server && docker compose up -d` on the server (migrations 018 auto-apply via the existing `migrate.py && exec uvicorn` startup command).

- [ ] **Step 4: Real end-to-end verification**

Using the real sandbox Paddle account: complete a real checkout from the deployed marketing pricing page, confirm the webhook fires (check `Paddle > Developer tools > Notifications > [destination] > Logs` for a `200`), then follow the `successUrl` redirect to `/subscribe/claim`, sign in, confirm the installation-selection UI shows the right plan, apply it, and verify `installations.plan` actually updated in the production DB. This is the same "real verification, not a self-reported pass" discipline used for hosted MCP and the checkout integration earlier this session.

- [ ] **Step 5: Verify the negative case too**

Confirm a request to `/webhooks/paddle` with a missing or tampered signature is actually rejected in production (`curl -X POST https://app.aletheore.com/webhooks/paddle -d '{}'` with no signature header should return `401`, not `200` or a crash) — this is the one endpoint in this feature where a mistake is a real security hole, worth a direct production check rather than trusting the test suite alone.
