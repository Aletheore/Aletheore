import functools

from redis import Redis

from app_server.config import get_settings


@functools.lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    """Process-wide pooled Redis client.

    Every caller across app_server and scan_worker used to construct its
    own Redis.from_url(...) per request or per job - each one opening a
    fresh connection pool and never explicitly closing it, relying on GC to
    eventually close the underlying socket. Under load, connections pile up
    faster than GC reclaims them. lru_cache makes this a singleton exactly
    once per process (same convention as get_settings()), so every caller
    shares the same pooled connection instead.

    Short connect/socket timeouts rather than redis-py's multi-second
    default: every use of this client is a fast, simple operation (a rate-
    limit INCR, an RQ enqueue, a healthcheck ping) that should fail fast
    into a fallback or a 5xx if Redis is slow or unreachable, not hang a
    request behind it. First applied narrowly to admin.py's installation-id
    cache; every other caller needs the exact same property, so it's the
    default here rather than something each caller opts into separately.
    """
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
