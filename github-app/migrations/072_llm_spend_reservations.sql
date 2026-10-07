-- Persists each _IncrementalSpendBudget instance's current outstanding
-- reservation so a process killed between can_start_next_call() reserving
-- and record_usage()/on_call_failed()/release_unused_reservation()
-- resolving (OOM-kill, SIGKILL, host crash - anything that stops Python
-- code from running, not just a raised exception) leaves a real row
-- behind. reserve_llm_spend is an immediate real DB balance deduction,
-- not an in-memory marker, so without this there is no other path back
-- to the balance for that specific leak. See
-- docs/audits/overnight_review_2026_10_05.md and
-- run_llm_spend_reservation_sweep_job in scan_worker/jobs.py, which sweeps
-- rows here older than its own staleness threshold and releases them.
CREATE TABLE IF NOT EXISTS llm_spend_reservations (
    id               BIGSERIAL PRIMARY KEY,
    reservation_key  TEXT NOT NULL UNIQUE,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    feature          TEXT NOT NULL,
    reserve_usd      DOUBLE PRECISION NOT NULL,
    topup_usd        DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The sweep's only query: find rows older than its staleness threshold.
CREATE INDEX IF NOT EXISTS llm_spend_reservations_stale_sweep
ON llm_spend_reservations (updated_at);
