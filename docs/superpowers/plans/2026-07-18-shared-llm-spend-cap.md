# Shared Per-Installation LLM Spend Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real, hard per-installation monthly dollar cap on all DeepSeek spend — $7 base + $2/extra seat — retrofitted onto the already-shipped managed-audit feature, and built as a genuinely shared mechanism so the (not-yet-built) Flash PR-review feature can use the exact same cap and pool.

**Architecture:** Track real cost (from actual API token usage, not estimates) in a new `llm_spend` table, one row per `(installation_id, month)`. Before any DeepSeek call runs, check whether the installation is already at/over its cap; after the call completes, record its real cost. Cost capture is added via an optional constructor-time hook on `OpenAICompatibleAdapter` — never by changing `AgentAdapter.invoke()`'s shared `str`-returning contract, which all 9 BYOK adapters (used by the local CLI, where customers pay their own way) also implement and must not be disturbed.

**Tech Stack:** Python 3.12, asyncpg (app_server), psycopg (scan_worker), pytest.

## Global Constraints

- Base cap: **$7.00/month per installation**. Extra-seat scaling: **+$2.00/month per seat** (`installations.extra_seats`, new column, defaults to `0`). Selling/setting extra seats is separate future work with no self-serve flow yet — `extra_seats` is real and usable in the cap formula starting now, but only settable manually (e.g. a direct `UPDATE installations SET extra_seats = ...`) until that future work exists.
- Cost is always calculated using the cache-**miss** (more expensive) rate, never attempting to detect or apply DeepSeek's cache-hit discount. This is deliberate: parsing the real cache-hit/miss token split would require an unverified provider-specific response field, and overestimating cost is the safe direction for a protective cap — it can only make the cap trigger *earlier* than a perfectly cache-aware calculation would, never later.
- `AgentAdapter.invoke()`'s `str` return contract (`prototype/aletheore/adapters/base.py:17`) is never changed — it's shared by all 9 BYOK adapters used by the local CLI's `audit` command, which has no need for server-side cost tracking.
- All new SQL/Python file paths below are relative to `github-app/` unless stated otherwise (files under `prototype/` are called out explicitly with that prefix).

---

### Task 1: Migration — `extra_seats` column and `llm_spend` table

**Files:**
- Create: `migrations/005_llm_spend_cap.sql`

**Interfaces:**
- Produces: `installations.extra_seats` (INT, default 0), and table `llm_spend(installation_id, month, total_cost_usd)` — consumed by every later task.

- [ ] **Step 1: Write the migration**

```sql
ALTER TABLE installations ADD COLUMN IF NOT EXISTS extra_seats INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS llm_spend (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    month            DATE NOT NULL,
    total_cost_usd   NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (installation_id, month)
);
```

- [ ] **Step 2: Apply it to the local test Postgres and update the test glob**

Run: `PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test -f migrations/005_llm_spend_cap.sql`
Expected: `ALTER TABLE` then `CREATE TABLE`.

In `tests/conftest.py`, update the migration glob from `00[234]_*.sql` to `00[2345]_*.sql`.

- [ ] **Step 3: Commit**

```bash
git add migrations/005_llm_spend_cap.sql tests/conftest.py
git commit -m "feat: add extra_seats column and llm_spend table"
```

---

### Task 2: Cost-rate calculation module

**Files:**
- Create: `app_server/llm_cost.py`
- Test: `tests/test_llm_cost.py`

**Interfaces:**
- Produces: `cost_for_usage(model: str, prompt_tokens: int, completion_tokens: int) -> float`, `monthly_cap_for_installation(base_cap_usd: float, extra_seats: int) -> float` — consumed by Task 5 (adapter hook usage), Task 7/8 (jobs.py wiring).

- [ ] **Step 1: Write the failing tests**

```python
from app_server.llm_cost import cost_for_usage, monthly_cap_for_installation


def test_cost_for_usage_deepseek_v4_pro():
    # 1M prompt tokens at $0.435/M + 1M completion tokens at $0.87/M
    assert cost_for_usage("deepseek-v4-pro", 1_000_000, 1_000_000) == pytest.approx(0.435 + 0.87)


def test_cost_for_usage_deepseek_v4_flash():
    assert cost_for_usage("deepseek-v4-flash", 1_000_000, 1_000_000) == pytest.approx(0.14 + 0.28)


def test_cost_for_usage_small_real_call():
    # A realistic small call: 2,000 prompt tokens, 300 completion tokens on Flash
    expected = (2_000 * 0.14 + 300 * 0.28) / 1_000_000
    assert cost_for_usage("deepseek-v4-flash", 2_000, 300) == pytest.approx(expected)


def test_monthly_cap_for_installation_base_only():
    assert monthly_cap_for_installation(7.00, 0) == pytest.approx(7.00)


def test_monthly_cap_for_installation_with_extra_seats():
    assert monthly_cap_for_installation(7.00, 3) == pytest.approx(13.00)
```

Add `import pytest` at the top of `tests/test_llm_cost.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_llm_cost.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.llm_cost'`

- [ ] **Step 3: Implement it**

```python
# app_server/llm_cost.py

# Cache-MISS rates only, deliberately - see Global Constraints in the plan this
# module was built from: overestimating cost is the safe direction for a cap.
DEEPSEEK_RATES_PER_MILLION_USD = {
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
}

EXTRA_SEAT_MONTHLY_COST_USD = 2.00


def cost_for_usage(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = DEEPSEEK_RATES_PER_MILLION_USD[model]
    return (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1_000_000


def monthly_cap_for_installation(base_cap_usd: float, extra_seats: int) -> float:
    return base_cap_usd + EXTRA_SEAT_MONTHLY_COST_USD * extra_seats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_llm_cost.py -v`
Expected: All 5 pass.

- [ ] **Step 5: Commit**

```bash
git add app_server/llm_cost.py tests/test_llm_cost.py
git commit -m "feat: add DeepSeek cost calculation and per-installation cap formula"
```

---

### Task 3: Async spend-tracking functions (`app_server/db.py`)

**Files:**
- Modify: `app_server/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: real Postgres via the `pool` fixture.
- Produces: `get_llm_spend_this_month(pool, installation_id: int) -> float`, `record_llm_spend(pool, installation_id: int, cost_usd: float) -> None`, `get_extra_seats(pool, installation_id: int) -> int` — consumed by Task 7/8.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py`'s import block: `get_extra_seats, get_llm_spend_this_month, record_llm_spend,` (alphabetically among the existing imports).

```python
@pytest.mark.asyncio
async def test_get_llm_spend_this_month_returns_zero_when_no_rows(pool):
    await upsert_installation(pool, 500, "octocat")
    assert await get_llm_spend_this_month(pool, 500) == 0.0


@pytest.mark.asyncio
async def test_record_llm_spend_accumulates_within_the_same_month(pool):
    await upsert_installation(pool, 500, "octocat")
    await record_llm_spend(pool, 500, 0.05)
    await record_llm_spend(pool, 500, 0.03)
    assert await get_llm_spend_this_month(pool, 500) == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_record_llm_spend_is_independent_per_installation(pool):
    await upsert_installation(pool, 500, "octocat")
    await upsert_installation(pool, 501, "acme")
    await record_llm_spend(pool, 500, 1.00)
    await record_llm_spend(pool, 501, 2.00)
    assert await get_llm_spend_this_month(pool, 500) == pytest.approx(1.00)
    assert await get_llm_spend_this_month(pool, 501) == pytest.approx(2.00)


@pytest.mark.asyncio
async def test_get_extra_seats_defaults_to_zero(pool):
    await upsert_installation(pool, 500, "octocat")
    assert await get_extra_seats(pool, 500) == 0


@pytest.mark.asyncio
async def test_get_extra_seats_reads_the_real_column(pool):
    await upsert_installation(pool, 500, "octocat")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE installations SET extra_seats = 3 WHERE installation_id = $1", 500)
    assert await get_extra_seats(pool, 500) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_db.py -k "llm_spend or extra_seats" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement them**

Add to `app_server/db.py`, near `check_and_reserve_managed_audit`:

```python
async def get_llm_spend_this_month(pool: asyncpg.Pool, installation_id: int) -> float:
    row = await pool.fetchrow(
        """
        SELECT total_cost_usd FROM llm_spend
        WHERE installation_id = $1 AND month = date_trunc('month', now())::date
        """,
        installation_id,
    )
    return float(row["total_cost_usd"]) if row else 0.0


async def record_llm_spend(pool: asyncpg.Pool, installation_id: int, cost_usd: float) -> None:
    await pool.execute(
        """
        INSERT INTO llm_spend (installation_id, month, total_cost_usd)
        VALUES ($1, date_trunc('month', now())::date, $2)
        ON CONFLICT (installation_id, month) DO UPDATE
        SET total_cost_usd = llm_spend.total_cost_usd + EXCLUDED.total_cost_usd
        """,
        installation_id,
        cost_usd,
    )


async def get_extra_seats(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        "SELECT extra_seats FROM installations WHERE installation_id = $1", installation_id
    )
    return row["extra_seats"] if row else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add app_server/db.py tests/test_db.py
git commit -m "feat: add async llm_spend tracking and extra_seats reader"
```

---

### Task 4: Sync mirrors (`scan_worker/db.py`)

**Files:**
- Modify: `scan_worker/db.py`
- Test: `tests/test_scan_worker_db.py`

**Interfaces:**
- Produces: `get_llm_spend_this_month(dsn: str, installation_id: int) -> float`, `record_llm_spend(dsn: str, installation_id: int, cost_usd: float) -> None`, `get_extra_seats(dsn: str, installation_id: int) -> int` — same names as Task 3's async versions (different module, sync signatures), consumed by Task 7/8's jobs.py wiring.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scan_worker_db.py`'s import block: `get_extra_seats, get_llm_spend_this_month, record_llm_spend,`.

```python
@pytest.mark.asyncio
async def test_record_llm_spend_accumulates_sync(pool):
    await _insert_installation(pool, 301, "a")
    record_llm_spend(TEST_DATABASE_URL, 301, 0.10)
    record_llm_spend(TEST_DATABASE_URL, 301, 0.05)
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 301) == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_get_extra_seats_sync_defaults_to_zero(pool):
    await _insert_installation(pool, 301, "a")
    assert get_extra_seats(TEST_DATABASE_URL, 301) == 0
```

Add `import pytest` at the top if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scan_worker_db.py -k "llm_spend or extra_seats" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement them**

Add to `scan_worker/db.py`, near `check_and_reserve_managed_audit`:

```python
def get_llm_spend_this_month(dsn: str, installation_id: int) -> float:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT total_cost_usd FROM llm_spend
                WHERE installation_id = %s AND month = date_trunc('month', now())::date
                """,
                (installation_id,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0


def record_llm_spend(dsn: str, installation_id: int, cost_usd: float) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_spend (installation_id, month, total_cost_usd)
                VALUES (%s, date_trunc('month', now())::date, %s)
                ON CONFLICT (installation_id, month) DO UPDATE
                SET total_cost_usd = llm_spend.total_cost_usd + EXCLUDED.total_cost_usd
                """,
                (installation_id, cost_usd),
            )
        conn.commit()


def get_extra_seats(dsn: str, installation_id: int) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extra_seats FROM installations WHERE installation_id = %s",
                (installation_id,),
            )
            row = cur.fetchone()
            return row[0] if row else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scan_worker_db.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/db.py tests/test_scan_worker_db.py
git commit -m "feat: add sync llm_spend tracking and extra_seats reader"
```

---

### Task 5: `on_usage` hook on `OpenAICompatibleAdapter`

**Files:**
- Modify: `prototype/aletheore/adapters/openai_compatible.py`
- Test: `prototype/tests/test_openai_compatible_adapter.py`

**Interfaces:**
- Produces: `OpenAICompatibleAdapter(..., on_usage: Callable[[int, int], None] | None = None)` — called once per tool-calling round with `(prompt_tokens, completion_tokens)` from that round's real API response. Consumed by Task 6.

- [ ] **Step 1: Write the failing test**

In `prototype/tests/test_openai_compatible_adapter.py`, update `_mock_response` to optionally set usage (existing tests don't pass `usage`, so they keep working unchanged):

```python
def _mock_response(tool_calls=None, usage=(100, 20)):
    message = MagicMock()
    message.tool_calls = tool_calls
    message.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": (
            [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
            if tool_calls
            else None
        ),
    }
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    if usage is not None:
        response.usage = MagicMock(prompt_tokens=usage[0], completion_tokens=usage[1])
    else:
        response.usage = None
    return response
```

Add this test next to `test_invoke_assembles_all_required_sections_in_order`:

```python
@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_calls_on_usage_once_per_round_with_real_totals(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    responses = _write_all_sections_then_finish_responses()
    mock_client.chat.completions.create.side_effect = responses

    usage_calls = []
    adapter = _adapter(tmp_path, on_usage=lambda p, c: usage_calls.append((p, c)))
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    assert len(usage_calls) == len(responses)
    assert all(call == (100, 20) for call in usage_calls)


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_simple_completion_calls_on_usage(mock_openai_class, tmp_path):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_response(usage=(40, 8))

    usage_calls = []
    adapter = _adapter(tmp_path, on_usage=lambda p, c: usage_calls.append((p, c)))
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.simple_completion("system prompt", "user prompt", cwd=str(tmp_path))

    assert usage_calls == [(40, 8)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python3 -m pytest tests/test_openai_compatible_adapter.py -k "on_usage" -v`
Expected: both FAIL with `TypeError: OpenAICompatibleAdapter.__init__() got an unexpected keyword argument 'on_usage'`

- [ ] **Step 3: Implement the hook**

In `prototype/aletheore/adapters/openai_compatible.py`, update the constructor:

```python
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key_env_var: str,
        model: str,
        needs_key: bool = True,
        requires_consent: bool = True,
        supports_tool_choice: bool = True,
        request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
        credentials_path: Path | None = None,
        on_usage: Callable[[int, int], None] | None = None,
    ) -> None:
        self.name = name
        self.requires_consent = requires_consent
        self._base_url = base_url
        self._api_key_env_var = api_key_env_var
        self._model = model
        self._request_timeout_seconds = request_timeout_seconds
        self._needs_key = needs_key
        self._supports_tool_choice = supports_tool_choice
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._on_usage = on_usage
```

Add `from collections.abc import Callable` to the imports if not already present.

In `invoke()`'s tool-calling loop, right after `response = client.chat.completions.create(messages=messages, **create_kwargs)` (inside the `try` block's success path, i.e. right after the line, not inside the `except`):

```python
            try:
                response = client.chat.completions.create(messages=messages, **create_kwargs)
            except Exception as exc:
                raise AdapterInvocationError(
                    f"{self.name} invocation failed: {type(exc).__name__}"
                ) from exc
            if self._on_usage is not None and response.usage is not None:
                self._on_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
            message = response.choices[0].message
```

Also wire it into `simple_completion()` — a future consumer (the planned Flash PR-review feature) uses this single-shot method, not the tool-calling `invoke()` loop, and needs cost tracking too:

```python
    def simple_completion(self, system_prompt: str, user_prompt: str, cwd: str) -> str:
        api_key = None
        if self._needs_key:
            api_key = get_api_key(self._api_key_env_var, self.name, self._credentials_path)
            if not api_key:
                raise AdapterInvocationError(f"no API key available for {self.name}")

        client = OpenAI(base_url=self._base_url, api_key=api_key or "not-needed")
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise AdapterInvocationError(
                f"{self.name} invocation failed: {type(exc).__name__}"
            ) from exc
        if self._on_usage is not None and response.usage is not None:
            self._on_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content or ""
```

- [ ] **Step 4: Run tests to verify they pass, and the full adapter suite**

Run: `cd prototype && python3 -m pytest tests/test_openai_compatible_adapter.py -v`
Expected: All pass, including the new test and every pre-existing one (unaffected since `usage` defaults to `(100, 20)` and `on_usage` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
cd prototype
git add aletheore/adapters/openai_compatible.py tests/test_openai_compatible_adapter.py
git commit -m "feat: add on_usage hook to OpenAICompatibleAdapter for cost tracking"
```

---

### Task 6: Thread `on_usage` through `run_managed_audit`

**Files:**
- Modify: `scan_worker/managed_audit.py`
- Test: `tests/test_managed_audit.py`

**Interfaces:**
- Consumes: `OpenAICompatibleAdapter(..., on_usage=...)` (Task 5).
- Produces: `run_managed_audit(repo_path, manual_dir=None, on_usage=None) -> str` — consumed by Task 7/8.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_managed_audit.py`:

```python
def test_run_managed_audit_threads_on_usage_to_the_adapter(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.toon").write_text("fake toon evidence")

    captured_adapters = []

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        captured_adapters.append(adapter)
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nfindings here")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)

    received = []
    run_managed_audit(repo_path, on_usage=lambda p, c: received.append((p, c)))

    adapter = captured_adapters[0]
    assert adapter._on_usage is not None
    adapter._on_usage(50, 10)
    assert received == [(50, 10)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_managed_audit.py::test_run_managed_audit_threads_on_usage_to_the_adapter -v`
Expected: FAIL with `TypeError: run_managed_audit() got an unexpected keyword argument 'on_usage'`

- [ ] **Step 3: Implement it**

In `scan_worker/managed_audit.py`:

```python
from pathlib import Path
from typing import Callable

import aletheore.cli as _aletheore_cli
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.report import run_reasoning_phase


def run_managed_audit(
    repo_path: Path,
    manual_dir: str | None = None,
    on_usage: Callable[[int, int], None] | None = None,
) -> str:
    adapter = OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
        supports_tool_choice=False,
        on_usage=on_usage,
    )
    report_path = run_reasoning_phase(
        adapter,
        str(repo_path),
        manual_dir or _aletheore_cli.MANUAL_DIR,
    )
    return Path(report_path).read_text()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_managed_audit.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/managed_audit.py tests/test_managed_audit.py
git commit -m "feat: thread on_usage through run_managed_audit"
```

---

### Task 7: Wire the spend cap into `run_managed_audit_pr_job`

**Files:**
- Modify: `scan_worker/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `get_llm_spend_this_month`, `record_llm_spend`, `get_extra_seats` (Task 4), `monthly_cap_for_installation`, `cost_for_usage` (Task 2), `run_managed_audit(..., on_usage=...)` (Task 6).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_jobs.py`, next to `test_managed_audit_pr_job_skips_llm_call_when_rate_limited`:

```python
def test_managed_audit_pr_job_skips_llm_call_when_spend_cap_reached(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha], check=True
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_managed_audit", lambda *a, **k: llm_called.append(True)
    )
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
    from scan_worker.jobs import AUDIT_COMMENT_MARKER, run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert llm_called == []
    assert "spend cap" in posted["body"].lower()
    assert posted["marker"] == AUDIT_COMMENT_MARKER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_jobs.py::test_managed_audit_pr_job_skips_llm_call_when_spend_cap_reached -v`
Expected: FAIL with `AttributeError: module 'scan_worker.jobs' has no attribute 'get_llm_spend_this_month'`

- [ ] **Step 3: Wire it in**

Update the imports at the top of `scan_worker/jobs.py`:

```python
from app_server.llm_cost import cost_for_usage, monthly_cap_for_installation
from scan_worker.db import (
    check_and_reserve_managed_audit,
    get_extra_seats,
    get_installation as get_installation_row,
    get_last_endpoint_health,
    get_latest_evidence,
    get_llm_spend_this_month,
    insert_endpoint_health,
    insert_repo_history,
    list_monitored_installations,
    list_repos_for_installation,
    record_llm_spend,
)
```

Update `run_managed_audit_pr_job`:

```python
def run_managed_audit_pr_job(installation_id: int, repo_full_name: str, pr_number: int) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        repo_dir = job_dir / "head"
        _clone_pr_head(_clone_url(repo_full_name, token), pr_number, repo_dir)
        evidence_path = _run_scan(repo_dir)

        evidence = json.loads(evidence_path.read_text())
        cooldown_seconds = cooldown_seconds_for_loc(total_loc_from_evidence(evidence))
        client = httpx.Client(base_url="https://api.github.com")

        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
        current_spend = get_llm_spend_this_month(settings.database_url, installation_id)

        if not check_and_reserve_managed_audit(
            settings.database_url, installation_id, repo_full_name, cooldown_seconds
        ):
            body = (
                f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                f"Rate limited: this repo can run one managed audit every "
                f"{cooldown_seconds // 3600} hours. Try again later."
            )
        elif current_spend >= monthly_cap:
            body = (
                f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                f"Monthly spend cap reached for this installation (${monthly_cap:.2f}). "
                f"Try again next month, or contact support to increase your limit."
            )
        else:
            spend_accumulator = {"total": 0.0}

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    "deepseek-v4-pro", prompt_tokens, completion_tokens
                )

            report_text = run_managed_audit(repo_dir, on_usage=_on_usage)
            record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
            body = f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n{report_text}"
        upsert_pr_comment(
            client,
            token,
            repo_full_name,
            pr_number,
            body,
            marker=AUDIT_COMMENT_MARKER,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass, and the two pre-existing related tests still pass**

Run: `python3 -m pytest tests/test_jobs.py -k managed_audit_pr_job -v`
Expected: All 3 pass (`test_managed_audit_pr_job_clones_pr_head_runs_audit_and_replies`, `test_managed_audit_pr_job_skips_llm_call_when_rate_limited`, and the new spend-cap test) — the two pre-existing tests need `monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)` and `monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)` added to their setup so they don't hit a real (unreachable, `postgresql://unused`) database — add those two lines to both existing tests alongside their other `monkeypatch.setattr` calls.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/jobs.py tests/test_jobs.py
git commit -m "feat: enforce shared spend cap in run_managed_audit_pr_job"
```

---

### Task 8: Wire the spend cap into the API-triggered path

**Files:**
- Modify: `scan_worker/jobs.py` (`run_managed_audit_api_job` — add `installation_id` parameter), `app_server/managed_audit_api.py` (pass `installation_id` at enqueue time)
- Test: `tests/test_jobs.py`, `tests/test_managed_audit_api.py`

**Interfaces:**
- Consumes: same functions as Task 7.
- Produces: `run_managed_audit_api_job(installation_id: int, evidence: dict | str) -> str` (signature change — `installation_id` is now required, since spend tracking needs it and the function had no way to know which installation it was running for before this task).

- [ ] **Step 1: Write the failing tests**

Update the existing test in `tests/test_jobs.py` (currently `run_managed_audit_api_job(evidence={"scanned_at": "2026-01-01"})`) to pass `installation_id` too, and add a spend-cap test:

```python
def test_managed_audit_api_job_returns_report_text(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(installation_id=100, evidence={"scanned_at": "2026-01-01"})

    assert result == "# API Report"


def test_managed_audit_api_job_raises_when_spend_cap_reached(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_managed_audit", lambda *a, **k: llm_called.append(True)
    )
    from scan_worker.jobs import run_managed_audit_api_job

    with pytest.raises(Exception, match="spend cap"):
        run_managed_audit_api_job(installation_id=100, evidence={"scanned_at": "2026-01-01"})
    assert llm_called == []
```

Add `import pytest` at the top of `tests/test_jobs.py` if not already present.

In `tests/test_managed_audit_api.py`, update `test_managed_audit_enqueues_job_for_paid_token`'s assertion to also check `installation_id`:

```python
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_api_job"
    assert kwargs["evidence"] == evidence_toon
    assert kwargs["installation_id"] == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_jobs.py -k managed_audit_api_job tests/test_managed_audit_api.py::test_managed_audit_enqueues_job_for_paid_token -v`
Expected: FAIL — `run_managed_audit_api_job()` doesn't accept `installation_id` yet, and the enqueue call doesn't pass it.

- [ ] **Step 3: Wire it in**

In `scan_worker/jobs.py`, update `run_managed_audit_api_job`:

```python
def run_managed_audit_api_job(installation_id: int, evidence: dict | str) -> str:
    settings = get_settings()
    extra_seats = get_extra_seats(settings.database_url, installation_id)
    monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
    current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
    if current_spend >= monthly_cap:
        raise RuntimeError(
            f"monthly spend cap reached for this installation (${monthly_cap:.2f})"
        )

    job_dir = _job_temp_dir()
    try:
        if isinstance(evidence, dict):
            write_evidence(evidence, job_dir)
        else:
            aletheore_dir = job_dir / ".aletheore"
            aletheore_dir.mkdir(parents=True, exist_ok=True)
            (aletheore_dir / "air.toon").write_text(evidence)
            (aletheore_dir / "air.json").write_text(json.dumps({"managed_evidence": True}))

        spend_accumulator = {"total": 0.0}

        def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
            spend_accumulator["total"] += cost_for_usage(
                "deepseek-v4-pro", prompt_tokens, completion_tokens
            )

        result = run_managed_audit(job_dir, on_usage=_on_usage)
        record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
        return result
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
```

In `app_server/managed_audit_api.py`, update the enqueue call in `start_managed_audit` to pass `installation_id`:

```python
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_managed_audit_api_job",
        installation_id=installation["installation_id"],
        evidence=evidence,
    )
```

- [ ] **Step 4: Run tests to verify they pass, and the full test_jobs.py + test_managed_audit_api.py suites**

Run: `python3 -m pytest tests/test_jobs.py tests/test_managed_audit_api.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/jobs.py app_server/managed_audit_api.py tests/test_jobs.py tests/test_managed_audit_api.py
git commit -m "feat: enforce shared spend cap in the API-triggered managed audit path"
```

---

### Task 9: Real end-to-end verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full test suite for both packages**

Run: `cd prototype && python3 -m pytest tests/ -q`
Run: `cd github-app && TEST_DATABASE_URL="postgresql://postgres:test@localhost:55433/aletheore_test" python3 -m pytest tests/ -q`
Expected: all pass in both.

- [ ] **Step 2: Apply the migration to the production database**

This touches the production Postgres — confirm with the user before running, per this project's standing rule that direct production SQL migrations need explicit sign-off:

```bash
ssh $PROD_SSH "cd /root/aletheore && git pull origin master && cd github-app && docker compose exec -T postgres psql -U aletheore -d aletheore_app -f /root/aletheore/github-app/migrations/005_llm_spend_cap.sql"
```

Expected: `ALTER TABLE` then `CREATE TABLE`.

- [ ] **Step 3: Run one real managed audit through the new path and confirm real cost gets recorded**

Check the DeepSeek balance before, run a real managed audit (same technique already proven this session — construct `OpenAICompatibleAdapter` directly with the real server `DEEPSEEK_API_KEY` and call `run_reasoning_phase`, passing a real `on_usage` closure that prints what it receives), check the balance after, and confirm the observed balance drop roughly matches `cost_for_usage("deepseek-v4-pro", *accumulated_tokens)`'s calculated total (they won't match exactly, since `cost_for_usage` deliberately uses cache-miss rates only and the real DeepSeek billing may apply cache-hit discounts on repeated system-prompt content — the calculated cost should be equal to or slightly *higher* than the real balance drop, never lower; if the calculated cost comes out lower than the real drop, that's a bug in the rate table or token accounting to fix before shipping).

- [ ] **Step 4: Confirm the spend actually lands in the database**

Run (against the production database, read-only):
```bash
ssh $PROD_SSH "cd /root/aletheore/github-app && docker compose exec -T postgres psql -U aletheore -d aletheore_app -c \"SELECT * FROM llm_spend;\""
```
Expected: a real row for the test installation's ID, with `total_cost_usd` matching what Step 3 calculated.

- [ ] **Step 5: No commit needed — this task is verification-only**

If Steps 1-4 all pass, the shared spend cap is confirmed working end-to-end against the real production database and a real DeepSeek call, not just unit tests.
