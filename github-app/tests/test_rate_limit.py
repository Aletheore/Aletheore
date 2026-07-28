from app_server.rate_limit import cooldown_seconds_for_loc, is_rate_limited, total_loc_from_evidence


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
