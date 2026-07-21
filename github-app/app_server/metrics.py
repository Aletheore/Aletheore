from fastapi import APIRouter, HTTPException, Request
from redis import Redis
from rq import Queue, Worker
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry

from app_server.config import get_settings

metrics_router = APIRouter()


@metrics_router.get("/v1/internal/queue-stats")
async def queue_stats(request: Request):
    settings = get_settings()
    if not settings.internal_metrics_token:
        raise HTTPException(status_code=404, detail="not found")

    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {settings.internal_metrics_token}":
        raise HTTPException(status_code=401, detail="missing or invalid token")

    redis_conn = Redis.from_url(settings.redis_url)
    queue = Queue("scans", connection=redis_conn)
    return {
        "queue_depth": queue.count,
        "started_count": StartedJobRegistry(queue=queue).count,
        "failed_count": FailedJobRegistry(queue=queue).count,
        "finished_count": FinishedJobRegistry(queue=queue).count,
        "worker_count": Worker.count(connection=redis_conn),
    }
