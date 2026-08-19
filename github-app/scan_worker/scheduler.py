import time

from redis import Redis
from rq import Queue

from app_server.config import get_settings
from app_server.heartbeat import touch_heartbeat
from app_server.logging_config import configure_json_logging

HEALTH_SWEEP_INTERVAL_SECONDS = 180
# The sweep pings every endpoint for every repo for every monitored
# installation, serially, each bounded to 5s by run_healthcheck's own
# per-request timeout. 600s is a deliberate ceiling well above normal
# runtime that still guarantees the job can't run forever and pile up
# against the next scheduler tick indefinitely.
HEALTH_SWEEP_JOB_TIMEOUT_SECONDS = 600
# A single indexed DELETE against a small table - generous but bounded.
SESSION_CLEANUP_JOB_TIMEOUT_SECONDS = 60
# Same shape as the session cleanup: one range DELETE over an index on
# webhook_deliveries.received_at. Runs on every tick, which is far more
# often than a 30-day retention window needs, but it costs a no-op query
# and means the table can never quietly grow unbounded if the scheduler
# spends a long stretch restarting.
WEBHOOK_DELIVERY_CLEANUP_JOB_TIMEOUT_SECONDS = 60
# Local orphaned per-job repo clones live under scan_worker.jobs.JOBS_ROOT
# and can survive worker SIGKILLs; this is a bounded age-based filesystem
# sweep over direct children only.
JOB_TEMP_DIR_CLEANUP_JOB_TIMEOUT_SECONDS = 60
ENDPOINT_HEALTH_CLEANUP_JOB_TIMEOUT_SECONDS = 60
# The sweep itself only calls run_live_docs_full_build_job for repos that
# list_paid_repos_due_for_docs_catchup already filtered to "genuinely due"
# (48h+ since last sweep, real activity since then) - most ticks this
# enqueues, checks the due list, and finds nothing to do. Bounded well
# above normal runtime for the rare tick where several repos are due at
# once, same reasoning as HEALTH_SWEEP_JOB_TIMEOUT_SECONDS.
DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS = 600
# Same reasoning as DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS - run_live_wiki_
# catchup_sweep_job only calls run_live_wiki_full_build_job for repos
# list_paid_repos_due_for_wiki_catchup already filtered to genuinely due.
WIKI_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS = 600
# Reads the due list (a cheap join over installations/digest_sends), then
# enqueues one send per recipient onto "email" rather than sending inline -
# the actual Resend calls happen there, not in this job, so this stays
# bounded even if many installations are due the same tick.
WEEKLY_DIGEST_SWEEP_JOB_TIMEOUT_SECONDS = 300
# A single max(checked_at) query plus, on the rare stale tick, one email -
# nothing here can run long.
HEALTH_SWEEP_STALENESS_CHECK_JOB_TIMEOUT_SECONDS = 60
# App health, queue-depth/failed-job counters, and backup freshness checks.
# Each is bounded to a short HTTP request, Redis reads, or local filesystem
# metadata, with email alerting only on threshold breach.
OPS_MONITOR_JOB_TIMEOUT_SECONDS = 60

SCANS_QUEUE_NAME = "scans"
# The health sweep gets its own queue, consumed by a dedicated worker (see
# health_worker.py), instead of sharing "scans" with push-triggered Flash
# reviews, AIRview builds, and managed audits. Those are real LLM calls
# that can run for minutes; on a single shared queue, one long job delays
# every health sweep enqueued behind it - observed live in prod, where one
# Flash review run backed up three consecutive sweep ticks. Endpoint
# monitoring needs to keep running on its own ~3-minute cadence regardless
# of what the AI job queue is doing, especially now that the public status
# API drops any endpoint not checked in the last 15 minutes.
HEALTH_QUEUE_NAME = "health"


def run_forever(
    interval_seconds: int = HEALTH_SWEEP_INTERVAL_SECONDS,
    max_iterations: int | None = None,
) -> None:
    settings = get_settings()
    redis_conn = Redis.from_url(settings.redis_url)
    scans_queue = Queue(SCANS_QUEUE_NAME, connection=redis_conn)
    health_queue = Queue(HEALTH_QUEUE_NAME, connection=redis_conn)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        health_queue.enqueue(
            "scan_worker.jobs.run_health_check_sweep_job",
            job_timeout=HEALTH_SWEEP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_session_cleanup_job",
            job_timeout=SESSION_CLEANUP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_webhook_delivery_cleanup_job",
            job_timeout=WEBHOOK_DELIVERY_CLEANUP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_job_temp_dir_cleanup_job",
            job_timeout=JOB_TEMP_DIR_CLEANUP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_endpoint_health_cleanup_job",
            job_timeout=ENDPOINT_HEALTH_CLEANUP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_live_docs_catchup_sweep_job",
            job_timeout=DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_live_wiki_catchup_sweep_job",
            job_timeout=WIKI_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_weekly_digest_sweep_job",
            job_timeout=WEEKLY_DIGEST_SWEEP_JOB_TIMEOUT_SECONDS,
        )
        # Deliberately on "scans" (scan-worker), not "health" (health-worker)
        # - the whole point is that this keeps running, and alerting, even
        # if the health queue/worker specifically is what's silently broken.
        scans_queue.enqueue(
            "scan_worker.jobs.run_health_sweep_staleness_check_job",
            job_timeout=HEALTH_SWEEP_STALENESS_CHECK_JOB_TIMEOUT_SECONDS,
        )
        scans_queue.enqueue(
            "scan_worker.jobs.run_ops_monitor_job",
            job_timeout=OPS_MONITOR_JOB_TIMEOUT_SECONDS,
        )
        # Touched only after every enqueue call above succeeds - a hang on
        # any one of them (e.g. Redis unreachable) means this loop is stuck
        # and the heartbeat correctly goes stale, which is what the Docker
        # HEALTHCHECK on this container is watching for.
        touch_heartbeat()
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(interval_seconds)


if __name__ == "__main__":
    configure_json_logging()
    run_forever()
