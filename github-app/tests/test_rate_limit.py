import time

from app_server.rate_limit import (
    acquire_concurrency_slot,
    cooldown_seconds_for_loc,
    is_rate_limited,
    release_concurrency_slot,
    total_loc_from_evidence,
)


def test_cooldown_seconds_for_loc_small_repo_is_three_hours():
    assert cooldown_seconds_for_loc(0) == 3 * 3600
    assert cooldown_seconds_for_loc(10_000) == 3 * 3600


def test_cooldown_seconds_for_loc_medium_repo_is_six_hours():
    assert cooldown_seconds_for_loc(10_001) == 6 * 3600
    assert cooldown_seconds_for_loc(50_000) == 6 * 3600


def test_cooldown_seconds_for_loc_large_repo_is_twelve_hours():
    assert cooldown_seconds_for_loc(50_001) == 12 * 3600
    assert cooldown_seconds_for_loc(150_000) == 12 * 3600


def test_cooldown_seconds_for_loc_very_large_repo_is_twenty_four_hours():
    assert cooldown_seconds_for_loc(150_001) == 24 * 3600
    assert cooldown_seconds_for_loc(1_000_000) == 24 * 3600


def test_total_loc_from_evidence_sums_all_languages():
    evidence = {
        "repository": {
            "languages": [
                {"name": "Python", "files": 10, "lines": 1000},
                {"name": "JavaScript", "files": 5, "lines": 500},
            ]
        }
    }
    assert total_loc_from_evidence(evidence) == 1500


def test_total_loc_from_evidence_handles_missing_sections():
    assert total_loc_from_evidence({}) == 0
    assert total_loc_from_evidence({"repository": {}}) == 0


def test_is_rate_limited_allows_calls_under_the_limit(redis_conn):
    key = "test:ratelimit:under"
    for _ in range(5):
        assert is_rate_limited(redis_conn, key, limit=5, window_seconds=60) is False


def test_is_rate_limited_blocks_calls_over_the_limit(redis_conn):
    key = "test:ratelimit:over"
    for _ in range(3):
        assert is_rate_limited(redis_conn, key, limit=3, window_seconds=60) is False
    assert is_rate_limited(redis_conn, key, limit=3, window_seconds=60) is True


def test_is_rate_limited_keys_are_independent(redis_conn):
    for _ in range(3):
        is_rate_limited(redis_conn, "test:ratelimit:a", limit=3, window_seconds=60)
    # A different key starts its own fresh window.
    assert is_rate_limited(redis_conn, "test:ratelimit:b", limit=3, window_seconds=60) is False


def test_acquire_concurrency_slot_admits_up_to_capacity(redis_conn):
    key = "test:concurrency:cap"
    assert acquire_concurrency_slot(redis_conn, key, capacity=2, slot_id="a", ttl_seconds=60) is True
    assert acquire_concurrency_slot(redis_conn, key, capacity=2, slot_id="b", ttl_seconds=60) is True
    assert acquire_concurrency_slot(redis_conn, key, capacity=2, slot_id="c", ttl_seconds=60) is False


def test_release_concurrency_slot_frees_capacity_for_a_new_holder(redis_conn):
    key = "test:concurrency:release"
    acquire_concurrency_slot(redis_conn, key, capacity=1, slot_id="a", ttl_seconds=60)
    assert acquire_concurrency_slot(redis_conn, key, capacity=1, slot_id="b", ttl_seconds=60) is False

    release_concurrency_slot(redis_conn, key, "a")

    assert acquire_concurrency_slot(redis_conn, key, capacity=1, slot_id="b", ttl_seconds=60) is True


def test_acquire_concurrency_slot_evicts_a_crashed_holders_leaked_slot(redis_conn):
    """A holder that dies without releasing (crash, OOM kill) must not
    permanently occupy its slot - it should age out after ttl_seconds."""
    key = "test:concurrency:leak"
    stale_timestamp = time.time() - 120
    redis_conn.zadd(key, {"crashed-holder": stale_timestamp})

    admitted = acquire_concurrency_slot(redis_conn, key, capacity=1, slot_id="new-holder", ttl_seconds=60)

    assert admitted is True
    assert redis_conn.zscore(key, "crashed-holder") is None


def test_acquire_concurrency_slot_keys_are_independent(redis_conn):
    acquire_concurrency_slot(redis_conn, "test:concurrency:a", capacity=1, slot_id="x", ttl_seconds=60)
    assert (
        acquire_concurrency_slot(redis_conn, "test:concurrency:b", capacity=1, slot_id="y", ttl_seconds=60)
        is True
    )
