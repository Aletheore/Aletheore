import importlib
import math

import httpx
import pytest

from app_server import hosted_embedder as he


def _response(status, vectors=None, order=None, usage="default"):
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors or [])]
    if order:
        data = [data[i] for i in order]
    body = {"data": data}
    if usage == "default":
        body["usage"] = {"prompt_tokens": 7, "total_tokens": 7}
    elif usage is not None:
        body["usage"] = usage
    return httpx.Response(status, json=body, request=httpx.Request("POST", he.DEEPINFRA_EMBEDDINGS_URL))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key-not-real")
    monkeypatch.delenv("DEEPINFRA_EMBED_MODEL", raising=False)


def test_provider_defaults_to_local_and_rejects_unknown_values(monkeypatch):
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    assert he.provider() == "local"
    monkeypatch.setenv("EMBED_PROVIDER", "DeepInfra")
    assert he.provider() == "deepinfra"
    monkeypatch.setenv("EMBED_PROVIDER", "openai")
    assert he.provider() == "local"


def test_vectors_come_back_in_input_order_and_unit_length(monkeypatch):
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0], [0.0, 2.0]], order=[1, 0]))

    vectors, tokens = he.embed_batch(["a", "b"])

    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert vectors[1] == pytest.approx([0.0, 1.0])
    assert all(math.isclose(sum(v * v for v in vec), 1.0) for vec in vectors)


def test_request_carries_model_input_and_bearer_key(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json)
        return _response(200, [[1.0]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    he.embed_batch(["x"])

    assert seen["url"] == he.DEEPINFRA_EMBEDDINGS_URL
    assert seen["headers"] == {"Authorization": "Bearer test-key-not-real"}
    assert seen["json"] == {
        "model": "Qwen/Qwen3-Embedding-8B", "input": ["x"], "encoding_format": "float", "dimensions": 1024,
    }


def test_large_input_is_split_into_bounded_calls_and_reassembled_in_order(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(len(json["input"]))
        return _response(200, [[float(int(t))] for t in json["input"]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    texts = [str(i) for i in range(he.MAX_TEXTS_PER_CALL * 2 + 5)]

    vectors, tokens = he.embed_batch(texts)

    assert max(calls) <= he.MAX_TEXTS_PER_CALL and len(calls) == 3
    assert tokens == 7 * 3  # summed across the three calls
    assert [int(round(v[0] * int(t))) if int(t) else 0 for v, t in zip(vectors, texts)] == [int(t) for t in texts]


def test_retryable_status_is_retried_with_backoff_then_succeeds(monkeypatch):
    results = iter([_response(429), _response(503), _response(200, [[1.0]])])
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: next(results))
    slept = []
    monkeypatch.setattr(he.time, "sleep", slept.append)

    assert he.embed_batch(["x"]) == ([[1.0]], 7)
    assert slept == [0.5, 1.0]  # waits before each retry, doubling, and not before the first attempt


def test_non_retryable_status_fails_immediately_without_leaking_the_body(monkeypatch):
    attempts = []

    def fake_post(*a, **k):
        attempts.append(1)
        return httpx.Response(400, json={"error": "bad input: SECRET SOURCE LINE"}, request=httpx.Request("POST", "x"))

    monkeypatch.setattr(he.httpx, "post", fake_post)

    with pytest.raises(he.EmbedderUnavailable) as err:
        he.embed_batch(["x"])

    assert len(attempts) == 1
    assert "SECRET" not in str(err.value)


def test_missing_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("DEEPINFRA_API_KEY")
    with pytest.raises(he.EmbedderUnavailable):
        he.embed_batch(["x"])


def test_wrong_vector_count_is_rejected(monkeypatch):
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[1.0]]))
    with pytest.raises(he.EmbedderUnavailable):
        he.embed_batch(["a", "b"])


def test_embed_text_uses_a_distinct_cache_tag_and_degrades_to_none(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "deepinfra")
    import scan_worker.embedding_client as ec

    try:
        ec = importlib.reload(ec)
        assert ec.CURRENT_EMBEDDER == "deepinfra:Qwen/Qwen3-Embedding-8B@1024"
        assert ec.CURRENT_EMBEDDER != ec.LOCAL_EMBEDDER

        monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0]]))
        assert ec.embed_text("diff") == pytest.approx([0.6, 0.8])

        monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(500))
        assert ec.embed_text("diff") is None
    finally:
        monkeypatch.delenv("EMBED_PROVIDER")
        importlib.reload(ec)
        assert ec.CURRENT_EMBEDDER == ec.LOCAL_EMBEDDER


def test_tokens_come_from_the_providers_usage_and_fall_back_to_a_generous_estimate(monkeypatch):
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[1.0]], usage={"prompt_tokens": 123}))
    assert he.embed_batch(["x"])[1] == 123

    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[1.0]], usage=None))
    _, estimated = he.embed_batch(["y" * 250])
    assert estimated == 84  # 250 chars / 3.0, rounded up


def test_embed_text_reports_tokens_only_inside_a_metering_block(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "deepinfra")
    import scan_worker.embedding_client as ec
    import scan_worker.embedding_spend as spend

    charged = []
    monkeypatch.setattr(spend, "charge_embedding_tokens", lambda *args: charged.append(args))
    try:
        ec = importlib.reload(ec)
        monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0]]))

        ec.embed_text("outside any block")
        assert charged == []

        with ec.metered_embedding("dsn", 42, "flash_review_embedding"):
            ec.embed_text("one")
            ec.embed_text("two")
        assert charged == [("dsn", 42, 14, "flash_review_embedding")]

        # A block whose embeddings were all served from the in-memory vector cache charges nothing.
        with ec.metered_embedding("dsn", 42, "flash_review_embedding"):
            pass
        assert len(charged) == 1
    finally:
        monkeypatch.delenv("EMBED_PROVIDER")
        importlib.reload(ec)


def test_charge_draws_credit_ledgers_it_and_never_raises(monkeypatch):
    import scan_worker.embedding_spend as spend

    calls = []
    monkeypatch.setattr(spend, "reserve_llm_spend", lambda dsn, iid, amount: calls.append(("reserve", amount)) or True)
    monkeypatch.setattr(spend, "record_llm_spend", lambda dsn, iid, cost, feature: calls.append(("record", cost, feature)))

    charged = spend.charge_embedding_tokens("dsn", 1, 1_000_000, "hosted_embeddings")

    assert charged == pytest.approx(0.01)  # $0.01 per million tokens
    assert calls == [("reserve", pytest.approx(0.01)), ("record", pytest.approx(0.01), "hosted_embeddings")]
    assert spend.charge_embedding_tokens("dsn", 1, 0, "hosted_embeddings") == 0.0

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(spend, "reserve_llm_spend", boom)
    assert spend.charge_embedding_tokens("dsn", 1, 10, "hosted_embeddings") == 0.0


def test_charge_drains_what_is_left_when_the_balance_cannot_cover_the_cost(monkeypatch):
    import scan_worker.embedding_spend as spend

    reserved = []

    def reserve(dsn, iid, amount):
        reserved.append(amount)
        return amount <= 0.002  # the full cost does not fit, the remainder does

    monkeypatch.setattr(spend, "reserve_llm_spend", reserve)
    monkeypatch.setattr(spend, "get_installation", lambda dsn, iid: {"base_credit_remaining_usd": 0.001, "topup_credit_balance_usd": 0.0005})
    monkeypatch.setattr(spend, "record_llm_spend", lambda *a, **k: None)

    spend.charge_embedding_tokens("dsn", 1, 1_000_000, "hosted_embeddings")

    assert reserved == [pytest.approx(0.01), pytest.approx(0.0015)]


def test_a_single_oversized_text_is_cut_to_the_per_call_bound(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["sizes"] = [len(t) for t in json["input"]]
        return _response(200, [[1.0]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    he.embed_batch(["z" * (he.MAX_CHARS_PER_CALL + 5000)])

    assert seen["sizes"] == [he.MAX_CHARS_PER_CALL]


def test_charge_never_raises_even_if_pricing_itself_fails(monkeypatch):
    import scan_worker.embedding_spend as spend

    def boom(*args, **kwargs):
        raise KeyError("model missing from the rate table")

    monkeypatch.setattr(spend, "cost_for_usage", boom)
    assert spend.charge_embedding_tokens("dsn", 1, 10, "hosted_embeddings") == 0.0


def test_dimensions_default_to_1024_and_can_be_changed_or_turned_off(monkeypatch):
    monkeypatch.delenv("DEEPINFRA_EMBED_DIMENSIONS", raising=False)
    assert he.deepinfra_dimensions() == 1024
    assert he.embedder_label() == "Qwen/Qwen3-Embedding-8B@1024"

    monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "2048")
    assert he.embedder_label() == "Qwen/Qwen3-Embedding-8B@2048"

    monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "native")
    assert he.deepinfra_dimensions() is None
    assert he.embedder_label() == "Qwen/Qwen3-Embedding-8B"

    monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "banana")
    assert he.deepinfra_dimensions() == 1024


def test_native_size_omits_the_dimensions_option_from_the_request(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "native")
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["json"] = json
        return _response(200, [[1.0]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    he.embed_batch(["x"])

    assert "dimensions" not in seen["json"]


def test_a_different_vector_size_gets_a_different_cache_tag(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "deepinfra")
    import scan_worker.embedding_client as ec

    try:
        monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "1024")
        first = importlib.reload(ec).CURRENT_EMBEDDER
        monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "2048")
        second = importlib.reload(ec).CURRENT_EMBEDDER
        assert first != second  # rows cached at one size can never be matched against the other
    finally:
        monkeypatch.delenv("EMBED_PROVIDER")
        monkeypatch.delenv("DEEPINFRA_EMBED_DIMENSIONS")
        importlib.reload(ec)


def test_a_network_timeout_is_retried_like_a_retryable_status(monkeypatch):
    outcomes = iter([httpx.ReadTimeout("slow"), httpx.ConnectError("refused"), _response(200, [[1.0]])])

    def fake_post(*a, **k):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(he.httpx, "post", fake_post)
    monkeypatch.setattr(he.time, "sleep", lambda s: None)

    assert he.embed_batch(["x"])[0] == [[1.0]]


def test_giving_up_after_the_last_attempt_reports_the_last_status_and_sleeps_only_between_attempts(monkeypatch):
    attempts = []
    slept = []
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: attempts.append(1) or _response(429))
    monkeypatch.setattr(he.time, "sleep", slept.append)

    with pytest.raises(he.EmbedderUnavailable, match="429"):
        he.embed_batch(["x"])

    assert len(attempts) == he.RETRIES
    assert slept == [0.5, 1.0]  # two waits for three attempts, none after the last


def test_a_single_attempt_never_sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(503))
    monkeypatch.setattr(he.time, "sleep", slept.append)

    with pytest.raises(he.EmbedderUnavailable):
        he.embed_batch(["x"], retries=1)

    assert slept == []


def test_the_in_flight_slot_is_released_while_backing_off(monkeypatch):
    # A call waiting out a 429 must not hold one of the process-wide slots, or a throttled
    # provider would also stall every other request that could have used it.
    free_during_sleep = []
    results = iter([_response(429), _response(200, [[1.0]])])
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: next(results))
    monkeypatch.setattr(he.time, "sleep", lambda s: free_during_sleep.append(he._IN_FLIGHT._value))

    he.embed_batch(["x"])

    assert free_during_sleep == [he.MAX_IN_FLIGHT_CALLS]


def test_one_failed_batch_stops_the_batches_that_have_not_started(monkeypatch):
    monkeypatch.setattr(he, "MAX_PARALLEL_CALLS", 1)  # strictly one at a time, so the rest are still queued
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(len(json["input"]))
        return httpx.Response(400, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(he.httpx, "post", fake_post)
    texts = ["t"] * (he.MAX_TEXTS_PER_CALL * 5)  # five batches

    with pytest.raises(he.EmbedderUnavailable):
        he.embed_batch(texts)

    assert len(calls) == 1


def _remote_client(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "deepinfra")
    import scan_worker.embedding_client as ec

    ec = importlib.reload(ec)  # a fresh module also means a fresh circuit breaker
    monkeypatch.setattr(he.time, "sleep", lambda s: None)
    return ec


def _restore_local(monkeypatch):
    import scan_worker.embedding_client as ec

    monkeypatch.delenv("EMBED_PROVIDER")
    importlib.reload(ec)


def test_breaker_stops_calling_a_failing_provider_after_three_failed_lookups(monkeypatch):
    ec = _remote_client(monkeypatch)
    calls = []
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: calls.append(1) or _response(503))
    try:
        for _ in range(ec.BREAKER_FAILURES):
            assert ec.embed_text("diff") is None
        made = len(calls)
        assert made == ec.BREAKER_FAILURES * ec.REMOTE_ATTEMPTS  # each lookup used all its attempts

        for _ in range(10):
            assert ec.embed_text("diff") is None  # skipped: an instant miss, no network
        assert len(calls) == made
    finally:
        _restore_local(monkeypatch)


def test_breaker_lets_one_probe_through_after_the_cooldown_and_closes_on_success(monkeypatch):
    ec = _remote_client(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(ec.time, "monotonic", lambda: clock["now"])
    state = {"healthy": False}
    monkeypatch.setattr(
        he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0]]) if state["healthy"] else _response(503)
    )
    try:
        for _ in range(ec.BREAKER_FAILURES):
            ec.embed_text("diff")
        assert ec.embed_text("diff") is None  # open

        clock["now"] += ec.BREAKER_COOLDOWN_SECONDS + 1
        state["healthy"] = True
        assert ec.embed_text("diff") == pytest.approx([0.6, 0.8])  # the probe succeeds and closes it
        assert ec.embed_text("diff") == pytest.approx([0.6, 0.8])  # and stays closed
    finally:
        _restore_local(monkeypatch)


def test_a_failed_probe_reopens_the_breaker_immediately(monkeypatch):
    ec = _remote_client(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(ec.time, "monotonic", lambda: clock["now"])
    calls = []
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: calls.append(1) or _response(503))
    try:
        for _ in range(ec.BREAKER_FAILURES):
            ec.embed_text("diff")
        clock["now"] += ec.BREAKER_COOLDOWN_SECONDS + 1
        before = len(calls)
        assert ec.embed_text("diff") is None  # the probe, which fails
        probe_calls = len(calls) - before
        assert probe_calls == ec.REMOTE_ATTEMPTS

        assert ec.embed_text("diff") is None  # open again straight away
        assert len(calls) == before + probe_calls
    finally:
        _restore_local(monkeypatch)


def test_one_success_in_between_keeps_the_breaker_closed(monkeypatch):
    ec = _remote_client(monkeypatch)
    results = [503, 503, 200, 503, 503]  # failures never reach three in a row
    outcomes = iter([r for code in results for r in ([code] * (ec.REMOTE_ATTEMPTS if code != 200 else 1))])
    monkeypatch.setattr(
        he.httpx, "post", lambda *a, **k: _response(200, [[1.0]]) if next(outcomes) == 200 else _response(503)
    )
    try:
        for _ in results:
            ec.embed_text("diff")
        assert ec._remote_allowed()
    finally:
        _restore_local(monkeypatch)


def test_a_non_positive_dimension_value_is_logged_not_silently_replaced(monkeypatch, caplog):
    monkeypatch.setenv("DEEPINFRA_EMBED_DIMENSIONS", "-5")
    with caplog.at_level("ERROR"):
        assert he.deepinfra_dimensions() == 1024
    assert "DEEPINFRA_EMBED_DIMENSIONS" in caplog.text


def test_in_flight_cap_holds_under_load_and_is_actually_reached(monkeypatch):
    # The cap is what protects the provider from a burst, so check both directions: no
    # more than MAX_IN_FLIGHT_CALLS calls overlap, and the cap is really used (a cap
    # that silently allowed fewer would throttle indexing without anyone noticing).
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    lock = threading.Lock()
    running = 0
    peak = 0

    def slow_post(*args, **kwargs):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return _response(200, [[1.0]])

    monkeypatch.setattr(he.httpx, "post", slow_post)
    monkeypatch.setattr(he, "_IN_FLIGHT", threading.BoundedSemaphore(he.MAX_IN_FLIGHT_CALLS))

    with ThreadPoolExecutor(he.MAX_IN_FLIGHT_CALLS * 3) as pool:
        list(pool.map(lambda _: he.embed_batch(["x"]), range(he.MAX_IN_FLIGHT_CALLS * 3)))

    assert peak == he.MAX_IN_FLIGHT_CALLS
