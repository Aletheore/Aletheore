import hmac

from fastapi import APIRouter, HTTPException, Request
from rq import Queue, Worker
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry

from app_server.config import get_settings
from app_server.redis_client import get_redis_client

metrics_router = APIRouter()


@metrics_router.get("/v1/internal/queue-stats")
async def queue_stats(request: Request):
    settings = get_settings()
    if not settings.internal_metrics_token:
        raise HTTPException(status_code=404, detail="not found")

    auth_header = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth_header, f"Bearer {settings.internal_metrics_token}"):
        raise HTTPException(status_code=401, detail="missing or invalid token")

    redis_conn = get_redis_client()
    # "health" runs on its own queue/worker, separate from "scans" (see
    # scan_worker/scheduler.py) so a slow AI job never delays the endpoint
    # health sweep - reported separately here too, since a stat that only
    # covered "scans" would be blind to the health queue backing up or its
    # worker going down.
    queues = {name: Queue(name, connection=redis_conn) for name in ("scans", "health")}
    return {
        "queues": {
            name: {
                "queue_depth": queue.count,
                "started_count": StartedJobRegistry(queue=queue).count,
                "failed_count": FailedJobRegistry(queue=queue).count,
                "finished_count": FinishedJobRegistry(queue=queue).count,
                "worker_count": Worker.count(connection=redis_conn, queue=queue),
            }
            for name, queue in queues.items()
        },
        "worker_count": Worker.count(connection=redis_conn),
    }
