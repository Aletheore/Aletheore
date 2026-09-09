# Dollar-Credit Pricing — Dashboard + Emails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the customer a real, honest view of the dollar-credit balance the backend now enforces (base credit remaining + top-up balance), a way to buy more, and two new transactional emails when that balance gets low or runs out - the customer-facing half of the redesign that makes the new promise legible instead of opaque.

**Architecture:** A new, dedicated "Usage" section on the dashboard settings page (broken out from the existing mixed settings block, not folded into it - the current inline "AI usage this month" line is being replaced, not extended), fed by extending the existing `admin.py` settings API response with the two new balance fields. "Buy more credit" is a plain amount input that redirects to Paddle checkout for the real one-time credit price. The two new emails are two new render functions registered in the existing `_EMAIL_TEMPLATES` dispatch dict - no new email infrastructure, since `enqueue_transactional_email`/`send_transactional_email_job` already exist and already handle delivery, dedup, and retry.

**Tech Stack:** Python (FastAPI backend serving hand-built HTML/JS, no frontend framework - `app_server/frontend.py` is server-rendered strings), Paddle Checkout (client-side overlay, matching the existing subscribe/buy-seat buttons already on this page).

**Spec:** `docs/superpowers/specs/2026-09-09-dollar-credit-pricing-design.md`

## Global Constraints

- Zero real paying customers exist on Flash or AIR (confirmed via direct production DB query 2026-09-09) - no need to handle a customer who has an old-style "reviews used this month" mental model to migrate away from.
- The credit balance display must be a genuinely separate "Usage" (or "Billing") section, not folded into the existing mixed settings block that currently also covers seats, API tokens, and health check targets - a real product decision made this session, not a suggestion.
- **This plan depends on the backend plan** (`docs/superpowers/plans/2026-09-09-dollar-credit-pricing-backend.md`) for two things specifically: the `installations` columns it adds (`base_credit_remaining_usd`, `topup_credit_balance_usd`), and the exact email-trigger interface its Task 6 defines. Do not start Task 3 (emails) or Task 2's balance-reading half until those columns exist - check with the other agent or `git log`/`git pull` on the shared branch before starting if timing is unclear.
- **Exact interface this plan must match** (defined by the backend plan, not renegotiable without updating both plans): two `template_name` values, `"credit_low_balance"` and `"credit_exhausted"`, each called with `template_arg` as a dict shaped `{"account_login": str, "plan": str, "base_credit_remaining_usd": float, "topup_credit_balance_usd": float}`. Both entries must be registered in `_EMAIL_TEMPLATES` (`github-app/scan_worker/jobs.py`).
- `CREDIT_TOPUP_PRICE_ID` (`github-app/app_server/paddle_pricing.py`) is a $1.00/unit, one-time, quantity-billed Paddle price - the backend plan creates the real price id. The checkout call this plan writes sets `quantity` from the customer's chosen dollar amount ($5 minimum, no maximum).

---

### Task 1: Extend the settings API response with the real balance

**Files:**
- Modify: `github-app/app_server/admin.py:540-568` (the settings endpoint that currently returns `llm_spend_cap` - read the full function first, it's shown in context below but re-read the live file since line numbers shift)
- Test: `github-app/tests/test_admin.py`

**Interfaces:**
- Consumes: `installations.base_credit_remaining_usd`, `installations.topup_credit_balance_usd` (from the backend plan's Task 1 migration - these must exist before this task can pass its tests).
- Produces: the settings API response gains `"base_credit_remaining_usd": float` and `"topup_credit_balance_usd": float` keys.

- [ ] **Step 1: Confirm the installation dict already carries the new columns**

Run: `grep -n "async def get_installation" github-app/app_server/db.py`

Read that function - if it does `SELECT *` or explicitly names columns, confirm the two new columns come through automatically (a `SELECT *` picks up new columns with no code change; an explicit column list needs the two new names added). If it's an explicit list, add `base_credit_remaining_usd` and `topup_credit_balance_usd` to it as part of this task before writing the test below.

- [ ] **Step 2: Write the failing test**

```python
@pytest.mark.asyncio
async def test_settings_response_includes_credit_balance(async_client, pool):
    installation_id = await _insert_installation(pool, "acme", plan="flash")
    await pool.execute(
        "UPDATE installations SET base_credit_remaining_usd = 3.50, "
        "topup_credit_balance_usd = 12.00 WHERE installation_id = $1",
        installation_id,
    )
    response = await async_client.get(f"/api/admin/acme/settings")  # match this repo's real route path
    assert response.status_code == 200
    data = response.json()
    assert data["base_credit_remaining_usd"] == pytest.approx(3.50)
    assert data["topup_credit_balance_usd"] == pytest.approx(12.00)
```

Note: confirm the real route path and auth/session setup this test needs by reading an existing passing test in `test_admin.py` for the same settings endpoint first (e.g. whatever test currently covers `llm_spend_cap`) and copy its exact fixture/auth pattern - don't guess the route or the client fixture name.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_admin.py -k "credit_balance" -v`
Expected: FAIL (`KeyError` or missing keys in the response)

- [ ] **Step 4: Add the two fields to the settings response**

In the same function that currently sets `"llm_spend_cap": llm_spend_cap` (`github-app/app_server/admin.py`), add:

```python
        "base_credit_remaining_usd": float(installation["base_credit_remaining_usd"]),
        "topup_credit_balance_usd": float(installation["topup_credit_balance_usd"]),
```

Leave the existing `llm_spend_month_to_date`/`llm_spend_cap`/`flash_reviews_month_to_date` keys in place for this task - Task 2 replaces how the frontend *displays* usage, but removing these keys is a separate decision (they may still be useful as a secondary "spend so far" stat even once the primary display is the credit balance); don't delete them speculatively.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_admin.py -k "credit_balance" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/admin.py github-app/tests/test_admin.py
git commit -m "feat: expose credit balance in the settings API response"
```

---

### Task 2: New "Usage" dashboard section — balance display + buy-more flow

**Files:**
- Modify: `github-app/app_server/frontend.py` (the settings page JS around the existing `llmSpend`/`llmCap`/`usageHtml` block, ~line 2327-2340 - re-find the exact current lines, this file is a hotspot and shifts often; also wherever the settings page's section HTML gets assembled/inserted, to add a new section rather than editing the existing one in place)

**Interfaces:**
- Consumes: `base_credit_remaining_usd`, `topup_credit_balance_usd` from the settings API (Task 1). `CREDIT_TOPUP_PRICE_ID` from the backend plan (for the checkout call).
- Produces: a new, visually separate "Usage" (or "Billing") section on the dashboard settings page.

- [ ] **Step 1: Read the existing settings-page assembly to find where sections are composed**

Run: `sed -n '2280,2420p' github-app/app_server/frontend.py`

Confirm how `usageHtml` (and its sibling `seatBillingHtml`) get inserted into the final page - whether they're concatenated into one big settings block or already rendered as separate `<div class="settings-block">` cards side by side (the earlier read showed `usageHtml` as its own `.settings-block` div already, which is a good sign - it may only need to be pulled out of whatever combined container currently groups it with unrelated settings, into its own top-level section with its own heading).

- [ ] **Step 2: Replace the existing `usageHtml` block with a full "Usage" section**

Replace the current `usageHtml` construction (the `'<div class="settings-block">' + '<div class="settings-block-label">AI usage this month</div>' + ...` block) with:

```javascript
  const baseCredit = data.base_credit_remaining_usd || 0;
  const topupCredit = data.topup_credit_balance_usd || 0;
  const combinedCredit = baseCredit + topupCredit;
  const usageHtml =
    '<section class="settings-section" id="usage-section">' +
      '<h2>Usage</h2>' +
      '<div class="settings-block">' +
        '<div class="settings-block-label">Credit balance</div>' +
        '<div class="settings-block-hint">$' + baseCredit.toFixed(2) + ' included this month' +
          (topupCredit > 0 ? ' + $' + topupCredit.toFixed(2) + ' purchased (never expires)' : '') +
        '</div>' +
        '<div class="settings-block-hint">$' + combinedCredit.toFixed(2) + ' total available for AI reviews and builds</div>' +
        '<div class="form-row" style="margin-top: 10px;">' +
          '<input type="number" id="topup-amount" min="5" step="1" value="10" style="width: 80px;">' +
          '<button class="btn" onclick="buyCredit()" style="margin-left: 6px;">Buy more credit</button>' +
        '</div>' +
        '<div id="topup-status" class="settings-block-hint"></div>' +
      '</div>' +
    '</section>';
```

Keep this as its own top-level `<section>`, inserted as a sibling of the existing settings sections (not nested inside the block that also covers seats/tokens/health targets) - matches the "separate section" decision. Find the real insertion point in the surrounding page-assembly code (Step 1's read) and place it there, rather than assuming it slots in at the exact same spot the old `usageHtml` did if that spot is inside a combined container.

- [ ] **Step 3: Write the `buyCredit()` checkout function**

Add near the existing `buySeat()`/`openBillingPortal()` functions in the same file (read one of them first for the exact Paddle Checkout call pattern already used here - don't invent a new one):

```javascript
function buyCredit() {
  const amount = parseInt(document.getElementById('topup-amount').value, 10);
  const statusEl = document.getElementById('topup-status');
  if (!amount || amount < 5) {
    statusEl.textContent = 'Minimum purchase is $5.';
    return;
  }
  statusEl.textContent = 'Opening checkout...';
  Paddle.Checkout.open({
    items: [{ priceId: window._creditTopupPriceId, quantity: amount }],
    customData: { installation_token: window._installationToken },
  });
}
```

Confirm the exact real global variable names this page already uses for the signed installation token and any existing Paddle price id constants exposed to the frontend (Step 1's read of the existing `buySeat()`/subscribe buttons will show the real pattern - `window._installationToken`/`window._creditTopupPriceId` above are illustrative, match whatever's actually there).

- [ ] **Step 4: Expose `CREDIT_TOPUP_PRICE_ID` to the frontend**

Find where existing price ids (e.g. the extra-seat price) get passed from the server-rendered page into a `window._...` JS global, and add `CREDIT_TOPUP_PRICE_ID` the same way - this constant lives in `app_server/paddle_pricing.py` on the backend plan's side; import and use it, don't hardcode a duplicate string here.

- [ ] **Step 5: Manual verification in the browser**

This is server-rendered HTML/JS with no test harness for visual layout - use the preview tooling to actually load the dashboard settings page for a real installation, confirm the "Usage" section renders as a distinct section (not merged into the seats/tokens block), the two balance numbers display correctly, and clicking "Buy more credit" with a real test amount opens the Paddle Checkout overlay (a sandbox/test transaction, not a real charge - use Paddle's sandbox mode per this repo's existing `paddle:sandbox-testing` conventions if available, don't complete a real purchase against production Paddle).

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/frontend.py
git commit -m "feat: dedicated Usage section on the dashboard with credit balance and buy-more flow"
```

---

### Task 3: Two new email templates

**Files:**
- Modify: `github-app/app_server/email_templates.py` (add two new render functions, following the exact shape of the existing `payment_failed_email`/`subscription_canceled_email` functions - read both first)
- Modify: `github-app/scan_worker/jobs.py` (`_EMAIL_TEMPLATES` dict, register the two new template names)
- Test: `github-app/tests/test_email_templates.py` (check the exact existing filename)

**Interfaces:**
- Consumes: the exact `template_arg` shape the backend plan's Task 6 defines: `{"account_login": str, "plan": str, "base_credit_remaining_usd": float, "topup_credit_balance_usd": float}`.
- Produces: `credit_low_balance_email(account_login: str, plan: str, base_credit_remaining_usd: float, topup_credit_balance_usd: float) -> dict`, `credit_exhausted_email(...)` (same signature) - both returning the existing `{"subject": str, "html": str, "text": str}` shape every other template in this file returns.

- [ ] **Step 1: Read the existing template shape and helpers**

Run: `sed -n '158,204p' github-app/app_server/email_templates.py`

Confirm the exact real signature/return shape of `payment_failed_email`, and the existing `_shell`/`_button`/`_plan_display_name` helpers this file already provides - the two new templates should use them, not duplicate HTML boilerplate.

- [ ] **Step 2: Write the failing tests**

```python
from app_server.email_templates import credit_low_balance_email, credit_exhausted_email


def test_credit_low_balance_email_mentions_both_balances():
    message = credit_low_balance_email(
        account_login="acme", plan="flash",
        base_credit_remaining_usd=0.70, topup_credit_balance_usd=0.00,
    )
    assert "subject" in message and "html" in message and "text" in message
    assert "0.70" in message["text"]
    assert "acme" in message["text"]


def test_credit_exhausted_email_mentions_buying_more():
    message = credit_exhausted_email(
        account_login="acme", plan="air",
        base_credit_remaining_usd=0.00, topup_credit_balance_usd=0.00,
    )
    assert "subject" in message and "html" in message and "text" in message
    # Must give the customer an actual next step, not just "you're out."
    assert "credit" in message["text"].lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python3 -m pytest tests/test_email_templates.py -k "credit_low_balance or credit_exhausted" -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 4: Write the two templates**

```python
def credit_low_balance_email(
    account_login: str, plan: str, base_credit_remaining_usd: float, topup_credit_balance_usd: float
) -> dict:
    plan_name = _plan_display_name(plan)
    combined = base_credit_remaining_usd + topup_credit_balance_usd
    subject = f"Your Aletheore {plan_name} credit is running low"
    preheader = f"${combined:.2f} remaining this cycle."
    text = (
        f"Hi {account_login},\n\n"
        f"Your Aletheore {plan_name} plan has ${combined:.2f} of AI credit left "
        "this billing cycle (${base_credit_remaining_usd:.2f} included, "
        f"${topup_credit_balance_usd:.2f} from purchased top-ups). "
        "Once it runs out, automatic PR reviews and other AI-powered features "
        "will pause until your next renewal or you buy more.\n\n"
        "Buy more credit any time from your dashboard - it never expires."
    )
    html = _shell(
        preheader,
        f"<p>Your Aletheore {plan_name} plan has <strong>${combined:.2f}</strong> of AI credit "
        "left this billing cycle.</p>"
        f"<p>${base_credit_remaining_usd:.2f} included, ${topup_credit_balance_usd:.2f} from "
        "purchased top-ups.</p>"
        "<p>Once it runs out, automatic PR reviews and other AI-powered features will pause "
        "until your next renewal or you buy more.</p>"
        + _button("Buy more credit", "https://app.aletheore.com/"),
    )
    return {"subject": subject, "html": html, "text": text}


def credit_exhausted_email(
    account_login: str, plan: str, base_credit_remaining_usd: float, topup_credit_balance_usd: float
) -> dict:
    plan_name = _plan_display_name(plan)
    subject = f"Your Aletheore {plan_name} credit has run out"
    preheader = "AI-powered features are paused until you buy more credit or your plan renews."
    text = (
        f"Hi {account_login},\n\n"
        f"Your Aletheore {plan_name} plan has run out of AI credit for this billing cycle. "
        "Automatic PR reviews and other AI-powered features are paused - deterministic "
        "scanning and everything in Community continues to work normally.\n\n"
        "Buy more credit any time to resume immediately, or wait for your next renewal "
        "when your included credit refreshes."
    )
    html = _shell(
        preheader,
        f"<p>Your Aletheore {plan_name} plan has run out of AI credit for this billing cycle.</p>"
        "<p>Automatic PR reviews and other AI-powered features are paused - deterministic "
        "scanning and everything in Community continues to work normally.</p>"
        + _button("Buy more credit", "https://app.aletheore.com/"),
    )
    return {"subject": subject, "html": html, "text": text}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python3 -m pytest tests/test_email_templates.py -k "credit_low_balance or credit_exhausted" -v`
Expected: PASS

- [ ] **Step 6: Register both in `_EMAIL_TEMPLATES`**

In `github-app/scan_worker/jobs.py`, add to the existing `_EMAIL_TEMPLATES` dict:

```python
    "credit_low_balance": credit_low_balance_email,
    "credit_exhausted": credit_exhausted_email,
```

Add the corresponding import at the top of the file alongside the existing `email_templates` imports.

- [ ] **Step 7: Run the full email-related test suite**

Run: `cd github-app && python3 -m pytest tests/test_email_templates.py tests/test_jobs.py -k "email" -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add github-app/app_server/email_templates.py github-app/scan_worker/jobs.py github-app/tests/test_email_templates.py
git commit -m "feat: credit_low_balance and credit_exhausted email templates"
```

---

## Self-Review

**Spec coverage:**
- Balance display, distinct "Usage" section (per this session's explicit steer, not the spec's original wording) — Task 2. ✓
- "Buy more credit" flow, customer-chosen amount, $5 minimum — Task 2. ✓
- Two new email templates, exact interface match with the backend plan — Task 3. ✓
- Settings API extended to carry the real balance — Task 1. ✓

**Placeholder scan:** none - every code block is real, runnable code, not a description.

**Type consistency:** `credit_low_balance_email`/`credit_exhausted_email`'s parameter names (`account_login`, `plan`, `base_credit_remaining_usd`, `topup_credit_balance_usd`) match the backend plan's Task 6 `template_arg` dict keys exactly - this was checked against the other plan's file, not assumed.

**Cross-plan dependency, stated plainly:** this plan cannot fully pass its own tests until the backend plan's Task 1 (migration) has landed - Task 1 here reads columns that plan creates. Coordinate on that ordering before starting either plan's Task 1 in parallel.
