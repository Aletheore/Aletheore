import time

# Managed audits are billed to us, not the customer (a shared DeepSeek key, see
# scan_worker/managed_audit.py) - an uncapped endpoint lets anyone with a valid token
# spam audits and drain that balance. Cooldown scales with repo size because that's
# what actually drives cost: a real Procta-sized audit (~147K LOC) cost ~$0.12, while
# Aletheore's own ~21K-LOC self-audit didn't move the balance past two decimal places.
_TIERS = (
    (10_000, 3 * 3600),
    (50_000, 6 * 3600),
    (150_000, 12 * 3600),
)
_DEFAULT_COOLDOWN_SECONDS = 24 * 3600

# The real cooldown is only known after a scan runs (it's derived from the
# evidence the scan produces), which means the actual per-repo cooldown
# can't be checked before doing that work. But every tier is at least this
# long, so a repo whose last run was more recent than this is guaranteed
# to still be cooling down under whatever the real duration turns out to
# be - a cheap, conservative pre-check the managed-audit job runs before
# cloning/scanning, so a burst of repeat triggers gets turned away before
# wasting compute on the shared scans queue instead of after.
MIN_MANAGED_AUDIT_COOLDOWN_SECONDS = _TIERS[0][1]


def cooldown_seconds_for_loc(total_loc: int) -> int:
    for threshold, cooldown_seconds in _TIERS:
        if total_loc <= threshold:
            return cooldown_seconds
    return _DEFAULT_COOLDOWN_SECONDS


def total_loc_from_evidence(evidence: dict) -> int:
    languages = evidence.get("repository", {}).get("languages", [])
    return sum(language.get("lines", 0) for language in languages)


def is_rate_limited(redis_conn, key: str, limit: int, window_seconds: int) -> bool:
    # Fixed-window counter, not a token bucket - simple and sufficient for
    # an abuse guard on a public, unauthenticated endpoint. NX on the
    # EXPIRE call means only the first request in a window sets the TTL,
    # so a burst of concurrent requests can't each reset the window and
    # make the limit unenforceable.
    pipe = redis_conn.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_seconds, nx=True)
    count, _ = pipe.execute()
    return count > limit


# Sorted-set semaphore: each holder is a member scored by acquire time, so a
# holder that dies mid-request (crash, OOM kill) doesn't leak its slot
# forever the way a plain INCR/DECR counter would - ZREMRANGEBYSCORE below
# ages it out ttl_seconds after acquiring regardless of whether anyone ever
# calls release_concurrency_slot for it. Runs as a single Lua script so the
# evict-count-admit sequence is atomic in Redis (single-threaded execution),
# not a check-then-act race across concurrent app-server processes doing
# their own read-then-write.
_ACQUIRE_SLOT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local slot_id = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - ttl)
if redis.call('ZCARD', key) >= capacity then
    return 0
end
redis.call('ZADD', key, now, slot_id)
redis.call('EXPIRE', key, ttl)
return 1
"""


def acquire_concurrency_slot(
    redis_conn, key: str, capacity: int, slot_id: str, ttl_seconds: int
) -> bool:
    """Admit one more concurrent holder under `key`, up to `capacity`.

    ttl_seconds must exceed the real worst-case duration of the work a slot
    guards - the caller is trusted to release its own slot promptly on the
    success path via release_concurrency_slot; this TTL only bounds how long
    a *crashed* holder's slot stays leaked.
    """
    return bool(redis_conn.eval(_ACQUIRE_SLOT_SCRIPT, 1, key, time.time(), ttl_seconds, capacity, slot_id))


def release_concurrency_slot(redis_conn, key: str, slot_id: str) -> None:
    redis_conn.zrem(key, slot_id)
