# Dollar-Credit Pricing — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat, static `PLAN_CAP_OVERRIDE_USD` spend ceiling with a real, per-installation, purchasable dollar-credit balance ($5/mo Flash, $18/mo AIR base credit, plus never-expiring customer-purchased top-ups), enforced by the exact same atomic reservation path that exists today, just re-pointed at two new balance columns instead of one shared monthly total.

**Architecture:** Two new columns on `installations` (`base_credit_remaining_usd`, `topup_credit_balance_usd`) hold the real spendable balance, drawn down base-first-then-topup by the same `reserve_llm_spend`/`release_llm_spend_reservation` atomic UPDATE pattern that already exists and is already concurrency-tested. A `current_billing_period_start` column plus one new `balance_epoch` integer let the renewal-reset and top-up-purchase paths detect real state changes idempotently, and double as the dedupe key for two new customer emails sent through the existing `enqueue_transactional_email`/`sent_emails` infrastructure — no new email plumbing needed, only two new template names.

**Tech Stack:** Python, Postgres (psycopg for scan_worker's sync path, asyncpg-style `pool` for app_server's async webhook path), RQ (existing job queue), Paddle webhooks.

**Spec:** `docs/superpowers/specs/2026-09-09-dollar-credit-pricing-design.md`

## Global Constraints

- Zero real paying customers exist on Flash or AIR (confirmed via direct production DB query 2026-09-09) - this is a clean cutover, no migration path for existing balances needed.
- The old `PLAN_CAP_OVERRIDE_USD` flat cap is retired as an enforcement mechanism entirely once this ships - not kept as an additional ceiling layered on top of the new balance.
- Every job that currently fails open / degrades gracefully on cap-reached (never blocks, never errors loudly, never overspends) must keep doing exactly that - only the ceiling's *source* changes.
- The existing atomic-under-concurrency guarantee on the reservation path must be preserved exactly (see `test_reserve_llm_spend_is_atomic_under_real_concurrency`, `github-app/tests/test_scan_worker_db.py`).
- Draw-down order: base credit first, then top-up balance. Same order in reverse on release/true-up.
- `PLAN_BASE_CREDIT_USD = {"flash": 5.00, "air": 18.00}`.
- Extra-seat behavior must be preserved: the current cap formula is `base_cap_usd + EXTRA_SEAT_LLM_CAP_USD * extra_seats` (`monthly_cap_for_installation`, `github-app/app_server/llm_cost.py:141`) - the new base-credit reset must apply this same per-seat bonus, not just the flat plan amount.
- Low-balance threshold: 15% of the balance immediately after the most recent event that raised it (a renewal reset or a top-up purchase) - tracked via the new `balance_epoch` column (see Task 1), not a separately-computed value.
- **Deviation from the committed spec, found while mapping real files** (documented here per writing-plans' self-review requirement, not silently changed): the spec proposed two dedup timestamp columns (`low_balance_email_sent_at`, `exhausted_email_sent_at`). Real code inspection found `enqueue_transactional_email` / `send_transactional_email_job` (`github-app/app_server/email_queue.py`, `github-app/scan_worker/jobs.py`) already provide a working, tested dedupe mechanism keyed on an arbitrary `dedupe_key` string, backed by the real `sent_emails` table via `email_already_sent()`. Using `dedupe_key = f"credit_low_balance:{installation_id}:{balance_epoch}"` (and the equivalent for `credit_exhausted`) reuses that existing infrastructure instead of duplicating it, and the single `balance_epoch` integer does double duty as the spec's "high-water mark" tracker - one new column instead of three, no new dedup logic to write or test.
- **Deviation from the committed spec, found while mapping real files**: the spec proposed attributing a top-up purchase via `customer_id` matching `installations.paddle_customer_id`. Real code inspection found the actual, already-in-use, already-secure mechanism for `transaction.completed` is a signed `installation_token` in `custom_data` (`unsign_checkout_installation_id`, used identically by every subscription webhook handler and the existing `_handle_transaction_completed` in `github-app/app_server/webhooks/paddle.py:357`). Use that mechanism, not customer_id matching - it's simpler (no lookup needed beyond unsigning the token) and already proven secure for exactly this "attribute a transaction to an installation" purpose.

---

### Task 1: Migration — balance columns, epoch, and idempotency table

**Files:**
- Create: `github-app/migrations/063_installation_credit_balance.sql`
- Test: `github-app/tests/test_scan_worker_db.py` (migration is exercised implicitly by every test below via `conftest.py`'s auto-apply-all-migrations fixture - no dedicated migration test needed, matches existing convention for prior migrations in this repo)

**Interfaces:**
- Produces: `installations.base_credit_remaining_usd` (NUMERIC), `installations.topup_credit_balance_usd` (NUMERIC), `installations.current_billing_period_start` (TIMESTAMPTZ), `installations.balance_epoch` (INTEGER), `processed_paddle_transactions` table (id TEXT PRIMARY KEY, processed_at TIMESTAMPTZ).

- [ ] **Step 1: Write the migration**

```sql
-- Real per-installation dollar-credit balance, replacing the flat
-- PLAN_CAP_OVERRIDE_USD ceiling. base_credit_remaining_usd resets every
-- billing-period renewal to PLAN_BASE_CREDIT_USD[plan] + the existing
-- per-seat bonus; topup_credit_balance_usd is a never-expiring balance
-- from customer-purchased credit, drawn down only after base is
-- exhausted. balance_epoch increments on every renewal reset AND every
-- top-up purchase - the "high-water mark" the low-balance/exhausted
-- email dedupe keys off of, via the existing sent_emails/dedupe_key
-- mechanism rather than new timestamp columns.
ALTER TABLE installations
    ADD COLUMN base_credit_remaining_usd NUMERIC NOT NULL DEFAULT 0,
    ADD COLUMN topup_credit_balance_usd  NUMERIC NOT NULL DEFAULT 0,
    ADD COLUMN current_billing_period_start TIMESTAMPTZ,
    ADD COLUMN balance_epoch INTEGER NOT NULL DEFAULT 0;

-- Idempotency ledger for top-up purchases - Paddle retries a
-- transaction.completed it didn't get a 2xx for, with the same
-- transaction id. Recording every id this handler has already applied
-- prevents a retry from crediting the same purchase twice.
CREATE TABLE IF NOT EXISTS processed_paddle_transactions (
    id           TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Run the full local test suite once to confirm the migration applies cleanly**

Run: `cd github-app && python3 -m pytest tests/test_scan_worker_db.py -q`
Expected: all existing tests still pass (new columns default to 0/NULL, nothing existing reads them yet).

- [ ] **Step 3: Commit**

```bash
git add github-app/migrations/063_installation_credit_balance.sql
git commit -m "feat: add per-installation dollar-credit balance columns"
```

---

### Task 2: `PLAN_BASE_CREDIT_USD` and the renewal-reset amount helper

**Files:**
- Modify: `github-app/app_server/llm_cost.py`
- Test: `github-app/tests/test_llm_cost.py` (create if it doesn't exist - check first)

**Interfaces:**
- Consumes: nothing new.
- Produces: `PLAN_BASE_CREDIT_USD: dict[str, float]`, `base_credit_for_plan(plan: str, extra_seats: int) -> float`.

- [ ] **Step 1: Check whether a test file already exists**

Run: `ls github-app/tests/test_llm_cost.py`

- [ ] **Step 2: Write the failing test**

```python
from app_server.llm_cost import base_credit_for_plan


def test_base_credit_for_plan_flash():
    assert base_credit_for_plan("flash", extra_seats=0) == 5.00


def test_base_credit_for_plan_air_no_extra_seats():
    assert base_credit_for_plan("air", extra_seats=0) == 18.00


def test_base_credit_for_plan_air_with_extra_seats():
    # Same per-seat bonus the old monthly_cap_for_installation used -
    # EXTRA_SEAT_LLM_CAP_USD ($3.00) per extra seat.
    assert base_credit_for_plan("air", extra_seats=2) == 18.00 + 2 * 3.00


def test_base_credit_for_plan_unknown_plan_is_zero():
    assert base_credit_for_plan("free", extra_seats=0) == 0.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_llm_cost.py -v`
Expected: FAIL with `ImportError: cannot import name 'base_credit_for_plan'`

- [ ] **Step 4: Add the mapping and helper**

Add near the existing `PLAN_MONTHLY_PRICE_USD` in `github-app/app_server/llm_cost.py`:

```python
# The customer-facing, advertised included credit per plan - replaces the
# "up to 800 reviews/month" style promise with the real dollar unit the
# system already enforces. Deliberately below PLAN_CAP_OVERRIDE_USD
# (real enforced worst-case ceiling: $6.00 flash / $20.00 air) so there's
# real margin between what's promised and what's technically possible,
# same spirit as every other cap-vs-price margin already documented in
# this file.
PLAN_BASE_CREDIT_USD = {
    "flash": 5.00,
    "air": 18.00,
}


def base_credit_for_plan(plan: str, extra_seats: int) -> float:
    """The base credit an installation's balance resets to on a real
    renewal. Applies the same per-seat bonus monthly_cap_for_installation
    already used, so a larger AIR team keeps getting proportionally more
    credit, not the same flat amount regardless of seat count."""
    base = PLAN_BASE_CREDIT_USD.get(plan, 0.0)
    if base == 0.0:
        return 0.0
    return base + EXTRA_SEAT_LLM_CAP_USD * extra_seats
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_llm_cost.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/llm_cost.py github-app/tests/test_llm_cost.py
git commit -m "feat: add PLAN_BASE_CREDIT_USD and base_credit_for_plan"
```

---

### Task 3: Rewrite `reserve_llm_spend`/`release_llm_spend_reservation` for the two-column balance

**Files:**
- Modify: `github-app/scan_worker/db.py:396-460` (the existing `reserve_llm_spend`/`release_llm_spend_reservation` functions - read the current implementation first, it's a real `UPDATE ... WHERE` statement, don't guess its exact SQL)
- Test: `github-app/tests/test_scan_worker_db.py`

**Interfaces:**
- Consumes: `PLAN_BASE_CREDIT_USD`/`base_credit_for_plan` are NOT used here - this task only changes the storage/enforcement shape. The plan-aware reset happens in Task 5.
- Produces: `reserve_llm_spend(dsn: str, installation_id: int, reserve_usd: float) -> bool` (monthly_cap parameter REMOVED - the function now reads the installation's own two balance columns directly), `release_llm_spend_reservation(dsn: str, installation_id: int, reserve_usd: float) -> None` (unchanged signature, changed internals).

- [ ] **Step 1: Read the current implementation exactly as it stands**

Run: `sed -n '396,460p' github-app/scan_worker/db.py`

Confirm the real current SQL and parameter names before writing the replacement - do not assume the shape described above is byte-for-byte accurate to what's on disk.

- [ ] **Step 2: Write the failing tests**

```python
import pytest
from scan_worker.db import reserve_llm_spend, release_llm_spend_reservation


@pytest.mark.asyncio
async def test_reserve_llm_spend_draws_from_base_credit_first(pool):
    await _insert_installation(pool, 3001, "a")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE installations SET base_credit_remaining_usd = 5.00, "
            "topup_credit_balance_usd = 10.00 WHERE installation_id = $1",
            3001,
        )
    ok = reserve_llm_spend(TEST_DATABASE_URL, 3001, 2.00)
    assert ok is True
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT base_credit_remaining_usd, topup_credit_balance_usd "
            "FROM installations WHERE installation_id = $1", 3001,
        )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(3.00)
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(10.00)


@pytest.mark.asyncio
async def test_reserve_llm_spend_spills_into_topup_when_base_insufficient(pool):
    await _insert_installation(pool, 3002, "a")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE installations SET base_credit_remaining_usd = 1.00, "
            "topup_credit_balance_usd = 10.00 WHERE installation_id = $1",
            3002,
        )
    ok = reserve_llm_spend(TEST_DATABASE_URL, 3002, 3.00)
    assert ok is True
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT base_credit_remaining_usd, topup_credit_balance_usd "
            "FROM installations WHERE installation_id = $1", 3002,
        )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(0.00)
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(8.00)


@pytest.mark.asyncio
async def test_reserve_llm_spend_rejects_when_combined_balance_insufficient(pool):
    await _insert_installation(pool, 3003, "a")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE installations SET base_credit_remaining_usd = 1.00, "
            "topup_credit_balance_usd = 1.00 WHERE installation_id = $1",
            3003,
        )
    ok = reserve_llm_spend(TEST_DATABASE_URL, 3003, 5.00)
    assert ok is False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT base_credit_remaining_usd, topup_credit_balance_usd "
            "FROM installations WHERE installation_id = $1", 3003,
        )
    # Rejected reservation must not have mutated either column.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(1.00)
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(1.00)


@pytest.mark.asyncio
async def test_release_llm_spend_reservation_credits_topup_before_base(pool):
    # Reverse of the draw-down order: a release should restore topup
    # first if base is already at its (post-reset) ceiling - but since
    # we don't track a ceiling here, releasing always credits back to
    # topup first, then base, mirroring "spend base first, so give back
    # topup first" as the simplest consistent inverse.
    await _insert_installation(pool, 3004, "a")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE installations SET base_credit_remaining_usd = 0.00, "
            "topup_credit_balance_usd = 8.00 WHERE installation_id = $1",
            3004,
        )
    release_llm_spend_reservation(TEST_DATABASE_URL, 3004, 2.00)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT base_credit_remaining_usd, topup_credit_balance_usd "
            "FROM installations WHERE installation_id = $1", 3004,
        )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(10.00)
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(0.00)


def test_reserve_llm_spend_is_atomic_under_real_concurrency(pool_sync):
    # Real, threaded concurrency test - same style as the existing test
    # this replaces (search git history for the old single-column
    # version if unsure of the exact threading pattern used elsewhere in
    # this test file). Installation has exactly $10.00 combined balance;
    # 20 threads each try to reserve $1.00 concurrently; exactly 10 must
    # succeed, never more.
    import threading

    installation_id = 3005
    _insert_installation_sync(installation_id, "a")
    _set_balance_sync(installation_id, base=10.00, topup=0.00)

    results = []
    lock = threading.Lock()

    def attempt():
        ok = reserve_llm_spend(TEST_DATABASE_URL, installation_id, 1.00)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 10
```

Note: `_insert_installation_sync`/`_set_balance_sync`/`pool_sync` are illustrative - use whatever this test file's existing concurrency test (`test_reserve_llm_spend_is_atomic_under_real_concurrency`, being replaced) already uses for sync DB access in a threaded test, since `reserve_llm_spend` itself is a sync function called from async test fixtures elsewhere in this file. Read that existing test's exact fixture usage before writing this one - don't invent a new pattern.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python3 -m pytest tests/test_scan_worker_db.py -k "reserve_llm_spend or release_llm_spend" -v`
Expected: FAIL (old signature still takes `monthly_cap`, old logic writes to `llm_spend` not the new columns)

- [ ] **Step 4: Rewrite the implementation**

Replace the body of both functions in `github-app/scan_worker/db.py` (keep them at their current location in the file):

```python
def reserve_llm_spend(dsn: str, installation_id: int, reserve_usd: float) -> bool:
    """Atomically reserves reserve_usd against an installation's real
    credit balance (base_credit_remaining_usd, drawn down first, then
    topup_credit_balance_usd) - replaces the old flat monthly_cap
    parameter entirely; the ceiling is now this installation's own
    stored balance, not a constant shared by every installation on the
    same plan. Same atomicity guarantee as before: a single UPDATE ...
    WHERE, so two concurrent callers against the same installation can
    never together reserve more than what's actually available."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE installations
                SET
                    base_credit_remaining_usd = GREATEST(base_credit_remaining_usd - %(reserve)s, 0),
                    topup_credit_balance_usd = topup_credit_balance_usd
                        - GREATEST(%(reserve)s - base_credit_remaining_usd, 0)
                WHERE installation_id = %(installation_id)s
                    AND base_credit_remaining_usd + topup_credit_balance_usd >= %(reserve)s
                RETURNING installation_id
                """,
                {"reserve": reserve_usd, "installation_id": installation_id},
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def release_llm_spend_reservation(dsn: str, installation_id: int, reserve_usd: float) -> None:
    """Undoes one reserve_llm_spend reservation - credits topup_credit_
    balance_usd first, then base_credit_remaining_usd, the reverse of the
    base-first draw-down order (spend base first, so give back topup
    first)."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE installations
                SET topup_credit_balance_usd = topup_credit_balance_usd + %(reserve)s
                WHERE installation_id = %(installation_id)s
                """,
                {"reserve": reserve_usd, "installation_id": installation_id},
            )
        conn.commit()
```

Note on the release simplification: crediting a release entirely back to `topup_credit_balance_usd` (rather than trying to reconstruct exactly which column the original reservation drew from) is deliberate - the combined balance ends up identical either way, and tracking "this specific reservation drew $X from base and $Y from topup" would need a reservation-id ledger this design doesn't otherwise need. A release never increases what a customer can spend beyond what they already had; it only restores it faster into whichever bucket is simplest to credit.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python3 -m pytest tests/test_scan_worker_db.py -k "reserve_llm_spend or release_llm_spend" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add github-app/scan_worker/db.py github-app/tests/test_scan_worker_db.py
git commit -m "feat: reserve_llm_spend/release_llm_spend_reservation use per-installation credit balance"
```

---

### Task 4: True-up the balance on `record_usage`, not just the `llm_spend` accounting table

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (`_IncrementalSpendBudget.record_usage`, around line 3944 - re-read the exact current line numbers before editing, this file has changed during this session)
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `reserve_llm_spend`, `release_llm_spend_reservation` (Task 3).
- Produces: `_IncrementalSpendBudget.record_usage` now also true-ups the credit balance, not just the `llm_spend` accounting table.

**Why this task exists** (found while reading the real code, not in the original spec's data-flow section): `record_usage` reserves an *estimate* (`next_call_reserve_usd`) upfront via `can_start_next_call`, then calls `record_llm_spend(dsn, installation_id, delta, ...)` to true up the *accounting* table once the real cost is known (`delta = real_cost - reserve_usd`). Under the old single-column design, `llm_spend` was both the accounting record and the enforcement source, so this true-up naturally kept both in sync. Under the new design they're separate columns - the true-up must also adjust the credit balance by `delta`, or a systematic under/over-reservation (the estimate is rarely exactly right) will let the stored balance silently drift from real spend over time.

- [ ] **Step 1: Write the failing test**

```python
def test_incremental_spend_budget_record_usage_trues_up_the_credit_balance(pool):
    # real_cost ($0.03) exceeds the $0.01 next_call_reserve_usd that was
    # reserved up front - the extra $0.02 must additionally be reserved
    # from the credit balance, not just recorded in llm_spend.
    installation_id = _insert_installation_sync(pool, "a")
    _set_balance_sync(installation_id, base=5.00, topup=0.00)

    budget = _IncrementalSpendBudget(
        TEST_DATABASE_URL, installation_id, "deepseek-v4-flash",
        next_call_reserve_usd=0.01, feature="airview_incremental",
    )
    assert budget.can_start_next_call() is True
    # deepseek-v4-flash: $0.44/$1.32 per M in/out - sized so prompt_tokens
    # * input_rate + completion_tokens * output_rate lands at $0.03.
    budget.record_usage(prompt_tokens=50000, completion_tokens=10000)

    remaining = _get_balance_sync(pool, installation_id)
    assert remaining["base_credit_remaining_usd"] == pytest.approx(5.00 - 0.03, abs=0.001)


def test_incremental_spend_budget_record_usage_refunds_when_actual_cost_is_lower(pool):
    installation_id = _insert_installation_sync(pool, "a")
    _set_balance_sync(installation_id, base=5.00, topup=0.00)

    budget = _IncrementalSpendBudget(
        TEST_DATABASE_URL, installation_id, "deepseek-v4-flash",
        next_call_reserve_usd=0.10, feature="airview_incremental",
    )
    assert budget.can_start_next_call() is True
    # A tiny real call - actual cost is far below the 0.10 reserved.
    budget.record_usage(prompt_tokens=10, completion_tokens=1)

    remaining = _get_balance_sync(pool, installation_id)
    # Reserved 0.10, actual cost is a few thousandths of a cent - most of
    # the 0.10 reservation must have been refunded back.
    assert remaining["base_credit_remaining_usd"] > 4.95
```

Note: `_get_balance_sync`/`_set_balance_sync` are small new test helpers this task should add near the top of `test_jobs.py`, following whatever pattern `_insert_installation_sync` (if it exists) or `_insert_installation` already use in this file - read the existing helpers first.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -k "record_usage_trues_up or record_usage_refunds" -v`
Expected: FAIL (current `record_usage` only calls `record_llm_spend`, balance columns unaffected)

- [ ] **Step 3: Update `record_usage`**

In `github-app/scan_worker/jobs.py`, inside `_IncrementalSpendBudget.record_usage`, after the existing `cost = cost_for_usage(...)` / `delta = cost - self.next_call_reserve_usd` lines and before the existing `record_llm_spend(...)` call, add:

```python
        if delta > 0:
            reserve_llm_spend(self.dsn, self.installation_id, delta)
        elif delta < 0:
            release_llm_spend_reservation(self.dsn, self.installation_id, -delta)
```

Leave the existing `if delta == 0: return` and `record_llm_spend(...)` lines exactly as they are - this task only adds the balance true-up alongside the existing accounting true-up, it doesn't change what `llm_spend`/`llm_spend_events` record.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -k "record_usage_trues_up or record_usage_refunds" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: true up the credit balance (not just llm_spend accounting) on record_usage"
```

---

### Task 5: Renewal reset + top-up purchase (Paddle webhook branches, `app_server`)

**Files:**
- Modify: `github-app/app_server/webhooks/paddle.py` (the `subscription.updated` branch inside `handle_paddle_webhook_event`, and `_handle_transaction_completed`)
- Modify: `github-app/app_server/paddle_pricing.py` (new `CREDIT_TOPUP_PRICE_ID` placeholder constant - real value filled in by Task 7)
- Modify: `github-app/app_server/db.py` (new async helper functions for the two balance mutations, matching this file's existing async pool pattern - e.g. `get_installation`)
- Test: `github-app/tests/test_paddle_webhook.py` (check the exact existing filename first)

**Interfaces:**
- Consumes: `base_credit_for_plan` (Task 2), `CREDIT_TOPUP_PRICE_ID` (Task 7 fills in the real value; this task can proceed with a clearly-labeled placeholder Paddle price id string since the webhook logic doesn't depend on what the id actually is, only on comparing against it).
- Produces: `reset_billing_period_credit(pool, installation_id, plan, extra_seats, period_start) -> bool` (returns whether a reset actually happened, for the email-trigger interface in Task 6), `credit_topup_purchase(pool, installation_id, amount_usd, transaction_id) -> bool` (returns whether this was a new credit, i.e. not a duplicate).

- [ ] **Step 1: Find the exact current `subscription.updated` handling location**

Run: `grep -n "current_billing_period" github-app/app_server/webhooks/paddle.py`

Confirm whether the payload's billing-period field is already being read anywhere in this handler (it may already be parsed for a different purpose) before adding a new read.

- [ ] **Step 2: Write the failing tests**

```python
import pytest
from app_server.db import reset_billing_period_credit, credit_topup_purchase


@pytest.mark.asyncio
async def test_reset_billing_period_credit_on_genuine_new_period(pool):
    installation_id = await _insert_installation(pool, "a", plan="air")
    changed = await reset_billing_period_credit(
        pool, installation_id, "air", extra_seats=0,
        period_start="2026-09-01T00:00:00Z",
    )
    assert changed is True
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, current_billing_period_start, balance_epoch "
        "FROM installations WHERE installation_id = $1", installation_id,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    assert row["balance_epoch"] == 1


@pytest.mark.asyncio
async def test_reset_billing_period_credit_is_a_noop_on_the_same_period(pool):
    installation_id = await _insert_installation(pool, "a", plan="air")
    await reset_billing_period_credit(
        pool, installation_id, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z",
    )
    # Spend some of it down.
    await pool.execute(
        "UPDATE installations SET base_credit_remaining_usd = 2.00 WHERE installation_id = $1",
        installation_id,
    )
    # Same period_start delivered again (a replayed or unrelated subscription.updated).
    changed = await reset_billing_period_credit(
        pool, installation_id, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z",
    )
    assert changed is False
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd FROM installations WHERE installation_id = $1",
        installation_id,
    )
    # Must NOT have been reset back to 18.00 - the spent-down 2.00 survives.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(2.00)


@pytest.mark.asyncio
async def test_credit_topup_purchase_increments_topup_balance(pool):
    installation_id = await _insert_installation(pool, "a", plan="flash")
    credited = await credit_topup_purchase(pool, installation_id, 8.00, "txn_abc123")
    assert credited is True
    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd, balance_epoch FROM installations "
        "WHERE installation_id = $1", installation_id,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(8.00)
    assert row["balance_epoch"] == 1


@pytest.mark.asyncio
async def test_credit_topup_purchase_is_idempotent_on_replayed_transaction(pool):
    installation_id = await _insert_installation(pool, "a", plan="flash")
    first = await credit_topup_purchase(pool, installation_id, 8.00, "txn_abc123")
    second = await credit_topup_purchase(pool, installation_id, 8.00, "txn_abc123")
    assert first is True
    assert second is False
    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd FROM installations WHERE installation_id = $1",
        installation_id,
    )
    # Only credited once, not twice.
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(8.00)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python3 -m pytest tests/test_paddle_webhook.py -k "reset_billing_period_credit or credit_topup_purchase" -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 4: Implement the two helpers in `app_server/db.py`**

Add near the existing installation-mutation helpers (e.g. `add_paddle_ids_to_installation`) in `github-app/app_server/db.py`:

```python
async def reset_billing_period_credit(
    pool, installation_id: int, plan: str, extra_seats: int, period_start: str
) -> bool:
    """Resets base_credit_remaining_usd to this plan's real included
    credit (base_credit_for_plan, same per-seat bonus the old flat cap
    used) only if period_start is genuinely new for this installation -
    a no-op on a replayed or unrelated subscription.updated event.
    Increments balance_epoch on a real reset, which doubles as the
    dedupe key both new credit-notification emails key off of. Returns
    whether a reset actually happened."""
    from app_server.llm_cost import base_credit_for_plan

    new_credit = base_credit_for_plan(plan, extra_seats)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE installations
            SET base_credit_remaining_usd = $2,
                current_billing_period_start = $3::timestamptz,
                balance_epoch = balance_epoch + 1
            WHERE installation_id = $1
                AND (current_billing_period_start IS DISTINCT FROM $3::timestamptz)
            RETURNING installation_id
            """,
            installation_id, new_credit, period_start,
        )
    return row is not None


async def credit_topup_purchase(
    pool, installation_id: int, amount_usd: float, transaction_id: str
) -> bool:
    """Credits a real, customer-purchased top-up to topup_credit_balance_
    usd, exactly once per transaction_id even if the webhook is
    redelivered. Returns whether this call actually credited anything
    (False on a replay)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchrow(
                "INSERT INTO processed_paddle_transactions (id) VALUES ($1) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                transaction_id,
            )
            if inserted is None:
                return False
            await conn.execute(
                "UPDATE installations SET topup_credit_balance_usd = "
                "topup_credit_balance_usd + $2, balance_epoch = balance_epoch + 1 "
                "WHERE installation_id = $1",
                installation_id, amount_usd,
            )
    return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python3 -m pytest tests/test_paddle_webhook.py -k "reset_billing_period_credit or credit_topup_purchase" -v`
Expected: PASS

- [ ] **Step 6: Wire the renewal-reset call into the real `subscription.updated` handling path**

In `handle_paddle_webhook_event` (`github-app/app_server/webhooks/paddle.py`), after the existing block that resolves `plan` and confirms `installation_id` is attributed (the code around the `previous = await get_installation(pool, installation_id)` line), add a call reading `data.get("current_billing_period", {}).get("starts_at")` and, if present, calling `await reset_billing_period_credit(pool, installation_id, plan, extra_seats, period_start)` - read the surrounding function first to place this correctly relative to the existing plan-flip logic, and to find where `extra_seats` is already available in this function (it's read elsewhere for the seat add-on price item, per `_seat_item_quantity`).

- [ ] **Step 7: Wire the top-up purchase into `_handle_transaction_completed`**

In `_handle_transaction_completed` (`github-app/app_server/webhooks/paddle.py:357`), after the existing `installation_id = ...` / `if installation_id is None: return` block (reuse that exact same attribution logic - do not add a second lookup), add:

```python
    items = data.get("items") or []
    topup_item = next(
        (item for item in items if (item.get("price") or {}).get("id") == CREDIT_TOPUP_PRICE_ID),
        None,
    )
    if topup_item is not None:
        quantity = topup_item.get("quantity")
        transaction_id = data.get("id")
        if quantity and transaction_id:
            await credit_topup_purchase(pool, installation_id, float(quantity), transaction_id)
        else:
            logger.warning(
                "credit topup transaction.completed missing quantity or id: %s",
                data.get("id"),
            )
```

Place this as an early branch in the function, before or after the existing affiliate-commission logic (they're independent - a topup purchase and a referral commission on it aren't mutually exclusive, both can apply to the same transaction). Import `credit_topup_purchase` and `CREDIT_TOPUP_PRICE_ID` at the top of the file alongside the existing imports.

- [ ] **Step 8: Add the placeholder price id constant**

In `github-app/app_server/paddle_pricing.py`, near `EXTRA_SEAT_PRICE_ID`:

```python
# Placeholder until Task 7 creates the real Paddle price via the Paddle
# MCP and replaces this with the real pri_... id - the webhook logic
# only needs to compare against whatever this constant is, so the rest
# of this task can proceed and be tested without the real price
# existing yet.
CREDIT_TOPUP_PRICE_ID = "pri_PLACEHOLDER_credit_topup"
```

- [ ] **Step 9: Run the full paddle webhook test suite to confirm nothing existing broke**

Run: `cd github-app && python3 -m pytest tests/test_paddle_webhook.py -q`
Expected: all pass, including every pre-existing test.

- [ ] **Step 10: Commit**

```bash
git add github-app/app_server/webhooks/paddle.py github-app/app_server/paddle_pricing.py github-app/app_server/db.py github-app/tests/test_paddle_webhook.py
git commit -m "feat: renewal credit reset and top-up purchase Paddle webhook handling"
```

---

### Task 6: Low-balance / exhausted email triggers

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (`_IncrementalSpendBudget.can_start_next_call` and `.record_usage`, plus the direct `reserve_llm_spend` call in the Flash Review path - re-find the exact current line number, it shifts as earlier tasks land)
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `enqueue_transactional_email` (`github-app/app_server/email_queue.py`, already exists, unchanged), `balance_epoch` (Task 1/5).
- Produces: two new email trigger points using `template_name` values `"credit_low_balance"` and `"credit_exhausted"` - **this is the exact interface the dashboard+emails plan must match**. `template_arg` for both is a dict: `{"account_login": str, "plan": str, "base_credit_remaining_usd": float, "topup_credit_balance_usd": float}`. `dedupe_key` is `f"credit_low_balance:{installation_id}:{balance_epoch}"` / `f"credit_exhausted:{installation_id}:{balance_epoch}"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reserve_llm_spend_low_balance_triggers_email_enqueue(pool, monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **kw: enqueued.append((a, kw)),
    )
    installation_id = _insert_installation_sync(pool, "a", plan="flash")
    # balance_epoch=1, high-water-mark implied 5.00 (base_credit_for_plan
    # flash) - 15% of 5.00 is 0.75, so leaving 0.70 remaining crosses it.
    _set_balance_sync(installation_id, base=0.70, topup=0.00, balance_epoch=1)

    result = reserve_llm_spend_with_email_hooks(
        TEST_DATABASE_URL, installation_id, reserve_usd=0.10, feature="flash_review",
    )

    assert result is True
    assert len(enqueued) == 1
    _, kwargs = enqueued[0]
    assert kwargs["template_name"] == "credit_low_balance"
    assert kwargs["dedupe_key"] == f"credit_low_balance:{installation_id}:1"


def test_reserve_llm_spend_rejection_triggers_exhausted_email(pool, monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **kw: enqueued.append((a, kw)),
    )
    installation_id = _insert_installation_sync(pool, "a", plan="flash")
    _set_balance_sync(installation_id, base=0.01, topup=0.00, balance_epoch=1)

    result = reserve_llm_spend_with_email_hooks(
        TEST_DATABASE_URL, installation_id, reserve_usd=5.00, feature="flash_review",
    )

    assert result is False
    assert len(enqueued) == 1
    _, kwargs = enqueued[0]
    assert kwargs["template_name"] == "credit_exhausted"
    assert kwargs["dedupe_key"] == f"credit_exhausted:{installation_id}:1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -k "low_balance_triggers or rejection_triggers" -v`
Expected: FAIL (`reserve_llm_spend_with_email_hooks` doesn't exist yet)

- [ ] **Step 3: Implement the wrapper in `jobs.py`**

Add near `_IncrementalSpendBudget` in `github-app/scan_worker/jobs.py`:

```python
LOW_BALANCE_WARNING_FRACTION = 0.15


def reserve_llm_spend_with_email_hooks(
    dsn: str, installation_id: int, reserve_usd: float, feature: str
) -> bool:
    """Wraps reserve_llm_spend with the two customer-facing email
    triggers - reused by both the Flash Review direct-reservation path
    and _IncrementalSpendBudget.can_start_next_call, so both surfaces
    get identical notification behavior instead of two hand-rolled
    copies."""
    row = get_installation_row(dsn, installation_id)
    if row is None:
        return reserve_llm_spend(dsn, installation_id, reserve_usd)

    before_total = float(row["base_credit_remaining_usd"]) + float(row["topup_credit_balance_usd"])
    ok = reserve_llm_spend(dsn, installation_id, reserve_usd)

    if not ok:
        enqueue_transactional_email(
            redis_url=get_settings().redis_url,
            dedupe_key=f"credit_exhausted:{installation_id}:{row['balance_epoch']}",
            template_name="credit_exhausted",
            template_arg={
                "account_login": row["account_login"],
                "plan": row["plan"],
                "base_credit_remaining_usd": float(row["base_credit_remaining_usd"]),
                "topup_credit_balance_usd": float(row["topup_credit_balance_usd"]),
            },
            to_email=row["alert_email"],
            installation_id=installation_id,
        )
        return False

    after_row = get_installation_row(dsn, installation_id)
    after_total = float(after_row["base_credit_remaining_usd"]) + float(after_row["topup_credit_balance_usd"])
    # This installation's own high-water mark is whatever the combined
    # balance was immediately after its most recent renewal reset or
    # top-up (both of those set balance_epoch and, transitively, the
    # totals this check compares against) - approximated here as
    # before_total when no reservation has yet been made this epoch.
    # A precise high-water-mark value isn't separately stored (see the
    # spec's resolved-questions note); using before_total on the FIRST
    # reservation of an epoch is exact, and is a conservative
    # (slightly-early) trigger on later reservations within the same
    # epoch, which is the safe direction to be wrong in for a warning.
    threshold = before_total * LOW_BALANCE_WARNING_FRACTION
    if after_total <= threshold and before_total > threshold:
        enqueue_transactional_email(
            redis_url=get_settings().redis_url,
            dedupe_key=f"credit_low_balance:{installation_id}:{after_row['balance_epoch']}",
            template_name="credit_low_balance",
            template_arg={
                "account_login": after_row["account_login"],
                "plan": after_row["plan"],
                "base_credit_remaining_usd": float(after_row["base_credit_remaining_usd"]),
                "topup_credit_balance_usd": float(after_row["topup_credit_balance_usd"]),
            },
            to_email=after_row["alert_email"],
            installation_id=installation_id,
        )
    return True
```

Note: this task assumes `get_installation_row` (scan_worker's sync installation reader) already returns `account_login`, `plan`, `alert_email`, `balance_epoch` as dict-accessible keys - confirm the exact real column set this function returns before writing the implementation; add `balance_epoch` to its SELECT if it isn't already included (it's a new column from Task 1, existing SELECT statements won't pick it up automatically).

- [ ] **Step 4: Replace direct `reserve_llm_spend` calls with the wrapper**

In `github-app/scan_worker/jobs.py`, replace the Flash Review path's direct call (`if not reserve_llm_spend(settings.database_url, installation_id, reserved_spend, monthly_cap):`, found at the line identified during Task 3's Step 1 re-read) with `reserve_llm_spend_with_email_hooks(settings.database_url, installation_id, reserved_spend, feature="flash_review")`, and `_IncrementalSpendBudget.can_start_next_call` (Task 3 area) to call `reserve_llm_spend_with_email_hooks(self.dsn, self.installation_id, self.next_call_reserve_usd, self.feature)` instead of `reserve_llm_spend` directly.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -k "low_balance_triggers or rejection_triggers" -v`
Expected: PASS

- [ ] **Step 6: Run the full jobs test suite**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: low-balance and exhausted email triggers on credit reservation"
```

---

### Task 7: Retire the flat-cap call sites; create the real Paddle top-up price

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (every remaining `_llm_spend_cap_reached` call site and every `_IncrementalSpendBudget(...)` constructor call - Task 6's grep already found the real list: lines ~2813/2818, ~4276/4288, ~4452/4467, ~4788/4801, ~4935/4949, plus the two Flash Review sites at ~1526/1558 and ~1637/1656; re-grep at the start of this task since every prior task shifts line numbers)
- Modify: `github-app/app_server/admin.py:549` (`monthly_cap_for_installation(base_cap_for_plan(...))` display)
- Modify: `github-app/app_server/paddle_pricing.py` (replace the Task 5 placeholder with the real price id)
- Delete: nothing yet - `PLAN_CAP_OVERRIDE_USD`, `base_cap_for_plan`, `monthly_cap_for_installation`, `_llm_spend_cap_reached` become dead code once every call site is updated; leave them in place with a comment noting they're superseded, rather than deleting in this pass (deleting is a separate, easy follow-up once this is confirmed working in production - not worth the risk of also needing to touch every test that still references them in the same change that's rewiring the actual enforcement).
- Test: `github-app/tests/test_jobs.py`, `github-app/tests/test_admin.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: nothing new - this task is pure call-site migration.

- [ ] **Step 1: Get the real, current, complete list of call sites**

Run: `grep -n "reserve_llm_spend\|release_llm_spend_reservation\|_llm_spend_cap_reached\|base_cap_for_plan\|monthly_cap_for_installation\|_IncrementalSpendBudget(" github-app/scan_worker/jobs.py github-app/app_server/admin.py`

Use this real, current output as the task list for the remaining steps - do not reuse the line numbers written elsewhere in this plan, they're illustrative only and will have shifted.

- [ ] **Step 2: For each `_llm_spend_cap_reached` call site, replace with a direct balance read**

`_llm_spend_cap_reached(dsn, installation_id, plan)` currently returns `(cap_reached: bool, monthly_cap: float)`. Replace each call with a check against the installation's real combined balance:

```python
row = get_installation_row(dsn, installation_id)
combined_balance = float(row["base_credit_remaining_usd"]) + float(row["topup_credit_balance_usd"])
cap_reached = combined_balance <= 0
```

Every downstream use of the old `monthly_cap` return value in these call sites was for a log/status message (e.g. `f"monthly spend cap reached (${monthly_cap:.2f})"`) - replace with `f"credit balance exhausted (${combined_balance:.2f} remaining)"`, matching the new framing.

- [ ] **Step 3: For each `_IncrementalSpendBudget(...)` constructor, drop the `monthly_cap` argument**

`_IncrementalSpendBudget.__init__` (Task 6 area) still takes a `monthly_cap` parameter left over from before Task 3's rewrite - remove it from the constructor signature and from every call site found in Step 1 (each currently passes `monthly_cap=monthly_cap_for_installation(base_cap_for_plan(plan), extra_seats)` or similar - delete that argument and the now-unused local `monthly_cap`/`extra_seats` computation at each site, unless `extra_seats` is used for something else nearby, which Step 1's grep output will show).

Also remove `_IncrementalSpendBudget.cap_message()`'s reference to `self.monthly_cap` (now removed) - replace with a message reading from the installation's real current balance at the point it's called, same pattern as Step 2.

- [ ] **Step 4: Update the Flash Review direct-reservation sites**

The two sites at `monthly_cap = monthly_cap_for_installation(base_cap_for_plan(plan), extra_seats)` feeding into `reserve_llm_spend_with_email_hooks` (already updated in Task 6) - remove the now-unused `monthly_cap` computation entirely, since `reserve_llm_spend_with_email_hooks` no longer takes it.

- [ ] **Step 5: Update `admin.py`'s cap display**

At the `llm_spend_cap = monthly_cap_for_installation(base_cap_for_plan(installation["plan"]), extra_seats)` line, replace with a read of the installation's real `base_credit_remaining_usd + topup_credit_balance_usd`, matching whatever this admin view currently does with the old `llm_spend_cap` value (read the surrounding code before changing what gets displayed, since this may be an internal-only debug view, not the customer-facing dashboard the other agent's plan owns).

- [ ] **Step 6: Run the full backend test suite**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py tests/test_admin.py tests/test_scan_worker_db.py tests/test_paddle_webhook.py tests/test_llm_cost.py -q`
Expected: all pass. Fix any test that still asserts against the old `monthly_cap`-based behavior - these are real, expected breaks from the call-site migration, not new bugs; update their assertions to match the new balance-based behavior rather than deleting them.

- [ ] **Step 7: Create the real Paddle top-up price via the Paddle MCP**

Use the Paddle MCP tools available in this session to create a real, live product/price: one-time (not recurring), $1.00 USD unit price, quantity-adjustable at checkout, matching how `EXTRA_SEAT_PRICE_ID` and the flash/air plan prices were created (per `paddle_pricing.py`'s own history comments - search MCP tools first to confirm the exact method name before calling, per this repo's own Paddle MCP usage convention).

Replace the `CREDIT_TOPUP_PRICE_ID = "pri_PLACEHOLDER_credit_topup"` placeholder in `github-app/app_server/paddle_pricing.py` with the real returned price id, and update the comment to match the pattern already used for `EXTRA_SEAT_PRICE_ID`'s own history note (what it is, when it was created, that it's real and chargeable).

- [ ] **Step 8: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/app_server/admin.py github-app/app_server/paddle_pricing.py github-app/tests/
git commit -m "feat: retire flat-cap call sites in favor of per-installation credit balance; create real top-up Paddle price"
```

---

## Self-Review

**Spec coverage:**
- Migration (base/topup/period/epoch columns, idempotency table) — Task 1. ✓
- `PLAN_BASE_CREDIT_USD`, extra-seat-aware reset amount — Task 2. ✓
- `reserve_llm_spend`/`release_llm_spend_reservation` rewritten, base-first draw-down, concurrency-safe — Task 3. ✓
- Real-cost true-up (found via code reading, not explicit in spec's data-flow section, but required for correctness) — Task 4. ✓
- Renewal reset (idempotent, scoped to genuine period changes) — Task 5. ✓
- Top-up purchase (real attribution mechanism, idempotent) — Task 5. ✓
- Low-balance / exhausted emails, precise interface for the other plan — Task 6. ✓
- Every existing call site migrated off the flat cap; real Paddle price created — Task 7. ✓
- Old cap retired as *enforcement* (Global Constraints) — Task 7 removes every enforcement call site; the old constants/functions are deliberately left in place as dead code rather than deleted, noted explicitly in Task 7's Files section as a deliberate scope decision, not an oversight.

**Placeholder scan:** `CREDIT_TOPUP_PRICE_ID`'s placeholder value (Task 5, Step 8) is intentional and explicitly resolved by Task 7, Step 7 later in the same plan - not a forgotten TBD.

**Type consistency:** `reserve_llm_spend(dsn, installation_id, reserve_usd)` (Task 3's new signature) is used identically in Task 4, Task 6, and Task 7 - no task calls it with the old `monthly_cap` fourth argument. `reserve_llm_spend_with_email_hooks` (Task 6) matches this same three-argument-plus-feature shape everywhere it's introduced and consumed.
