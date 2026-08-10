"""Enqueues transactional email sends onto the "email" RQ queue (consumed
by health-worker, see scan_worker/health_worker.py and jobs.py's
send_transactional_email_job) - never inline in a request/webhook path,
same reasoning as every other network call this codebase defers to a job.
Shared by auth.py (welcome email) and webhooks/paddle.py (payment-failed,
subscription-canceled).
"""


def enqueue_transactional_email(
    redis_url: str,
    dedupe_key: str,
    template_name: str,
    template_arg: str,
    to_email: str,
    installation_id: int | None = None,
) -> None:
    from rq import Queue

    from app_server.redis_client import get_redis_client

    Queue("email", connection=get_redis_client()).enqueue(
        "scan_worker.jobs.send_transactional_email_job",
        dedupe_key=dedupe_key,
        template_name=template_name,
        template_arg=template_arg,
        to_email=to_email,
        installation_id=installation_id,
    )
