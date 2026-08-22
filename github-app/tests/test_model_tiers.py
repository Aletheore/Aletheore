import logging
import threading

import pytest

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.model_tiers import (
    LUNA_MODEL,
    OPENAI_FREE_TIER_DAILY_TOKEN_CAP,
    PRO_MODEL,
    VERIFICATION_MODEL,
    FreeTierFallbackExhausted,
    model_for_plan,
    resolve_model,
    run_with_free_tier_fallback,
    verification_adapter,
    writing_adapter_chain_for_free_tier,
    writing_adapter_for,
    writing_adapter_for_airview,
    writing_adapter_for_plan,
)


class _FakeRedis:
    """Minimal in-memory stand-in for the get/incrby/expire surface
    writing_adapter_chain_for_free_tier's token-cap logic uses - real
    Postgres/Redis-backed tests live in test_jobs.py, this just needs to
    exercise the cap logic itself in isolation."""

    def __init__(self, initial: dict[str, int] | None = None):
        self.data = dict(initial or {})
        self.expiries: dict[str, int] = {}
        # Real Redis commands are atomic server-side; a plain dict
        # read-modify-write is not (two operations, a thread can be
        # preempted between them). Locked here so a real multi-threaded
        # test against this fake actually proves something about the
        # production reservation logic's correctness under concurrency,
        # rather than coincidentally passing (or failing) on GIL timing.
        self._lock = threading.Lock()

    def get(self, key):
        value = self.data.get(key)
        return str(value).encode() if value is not None else None

    def incrby(self, key, amount):
        with self._lock:
            self.data[key] = self.data.get(key, 0) + amount
            return self.data[key]

    def expire(self, key, ttl_seconds):
        self.expiries[key] = ttl_seconds


def test_resolve_model_returns_luna_when_openai_key_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    assert resolve_model("some-fallback") == LUNA_MODEL


def test_resolve_model_falls_back_when_openai_key_not_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert resolve_model("some-fallback") == "some-fallback"


def test_writing_adapter_for_builds_openai_adapter_when_key_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = writing_adapter_for("some-fallback")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "OpenAI"
    assert adapter._model == LUNA_MODEL
    assert adapter._base_url == "https://api.openai.com/v1"
    assert adapter._api_key_env_var == "OPENAI_API_KEY"


def test_writing_adapter_for_falls_back_to_deepseek_when_key_not_configured(monkeypatch, caplog):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        adapter = writing_adapter_for("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"
    assert adapter._supports_tool_choice is False
    assert "OPENAI_API_KEY not configured" in caplog.text


def test_writing_adapter_for_threads_on_usage_through_either_branch(monkeypatch):
    for key_configured in (True, False):
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: key_configured)
        received = []
        adapter = writing_adapter_for("deepseek-v4-flash", on_usage=lambda p, c: received.append((p, c)))
        adapter._on_usage(10, 20)
        assert received == [(10, 20)], key_configured


def test_writing_adapter_for_airview_never_uses_luna_even_when_openai_is_configured(monkeypatch):
    # AIRview's own comprehension benchmark (aletheore-benchmarks,
    # AIRVIEW_GAP.md, re-measured 2026-08-22, full 12-question architecture
    # set, 3 judge repeats) found deepseek-v4-flash tied RepoWise (1.88 vs
    # 1.99, inside the judge's own noise floor) while gpt-5.6-luna lost
    # decisively (1.53 vs 2.08, outside it) - same corpus, same day, same
    # rubric. Unlike writing_adapter_for, this must not switch to Luna just
    # because OPENAI_API_KEY is configured.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = writing_adapter_for_airview("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"
    assert adapter._base_url == "https://api.deepseek.com"
    assert adapter._api_key_env_var == "DEEPSEEK_API_KEY"


def test_writing_adapter_for_airview_still_uses_deepseek_when_openai_is_not_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_airview("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"


def test_writing_adapter_for_airview_threads_on_usage_and_before_llm_call(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    received = []
    calls_allowed = []
    adapter = writing_adapter_for_airview(
        "deepseek-v4-flash",
        on_usage=lambda p, c: received.append((p, c)),
        before_llm_call=lambda: calls_allowed.append(True) or True,
    )
    adapter._on_usage(7, 3)
    assert received == [(7, 3)]
    assert adapter._before_llm_call() is True
    assert calls_allowed == [True]


def test_verification_adapter_always_uses_deepseek_even_when_openai_is_configured(monkeypatch):
    # The whole point of independent verification is a model that didn't
    # write the finding checking it - unlike writing_adapter_for, this must
    # not switch to OpenAI/Luna just because it's available.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = verification_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == VERIFICATION_MODEL
    assert adapter._base_url == "https://api.deepseek.com"
    assert adapter._api_key_env_var == "DEEPSEEK_API_KEY"


def test_verification_adapter_still_uses_deepseek_when_openai_is_not_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = verification_adapter()
    assert adapter._model == VERIFICATION_MODEL


def test_verification_adapter_threads_on_usage(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    received = []
    adapter = verification_adapter(on_usage=lambda p, c: received.append((p, c)))
    adapter._on_usage(5, 15)
    assert received == [(5, 15)]


def test_pro_uses_luna_when_openai_key_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = writing_adapter_for_plan("pro")
    assert adapter.name == "OpenAI"
    assert adapter._model == LUNA_MODEL


def test_pro_falls_back_to_deepseek_pro_when_openai_key_not_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_plan("pro")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_MODEL
    assert adapter._supports_tool_choice is False


def test_non_pro_plan_resolves_the_same_as_pro(monkeypatch):
    # free (or any other non-"pro" value) resolves identically - there is
    # only one plan's worth of routing left, this path shouldn't be
    # reachable in practice (managed audits already reject free plan
    # earlier), but the behavior must be safe regardless.
    for key_configured in (True, False):
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: key_configured)
        assert writing_adapter_for_plan("free")._model == writing_adapter_for_plan("pro")._model


def test_model_for_plan_never_drifts_from_writing_adapter_for_plan(monkeypatch):
    # cost_for_usage() prices tokens by whatever model_for_plan() reports -
    # if it ever disagreed with the adapter writing_adapter_for_plan()
    # actually built, Pro's spend would be silently mispriced.
    for key_configured in (True, False):
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: key_configured)
        for plan in ["pro", "free"]:
            adapter = writing_adapter_for_plan(plan)
            assert model_for_plan(plan) == adapter._model, (key_configured, plan)


# ── free-tier adapter chain tests ───────────────────────────────────────


def test_writing_adapter_chain_for_free_tier_includes_configured_providers(monkeypatch):
    def fake_has_api_key(env_var, name, **kwargs):
        return env_var in ("GROQ_API_KEY", "GEMINI_API_KEY")

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", fake_has_api_key)
    chain = writing_adapter_chain_for_free_tier(_FakeRedis())
    names = [a.name for a in chain]
    assert "Groq" in names
    assert "Gemini" in names
    assert "OpenRouter" not in names
    assert "OpenAI-FreeTier" not in names


def test_writing_adapter_chain_for_free_tier_skips_unconfigured_providers(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    chain = writing_adapter_chain_for_free_tier(_FakeRedis())
    assert chain == []


def test_writing_adapter_chain_for_free_tier_builds_correct_adapter_details(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    chain = writing_adapter_chain_for_free_tier(_FakeRedis())
    by_name = {a.name: a for a in chain}

    assert by_name["Groq"]._base_url == "https://api.groq.com/openai/v1"
    assert by_name["Groq"]._model == "openai/gpt-oss-120b"
    assert by_name["Groq"]._api_key_env_var == "GROQ_API_KEY"

    assert by_name["Gemini"]._base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert by_name["Gemini"]._model == "gemini-3.5-flash"

    assert by_name["OpenRouter"]._base_url == "https://openrouter.ai/api/v1"
    assert by_name["OpenRouter"]._model == "nvidia/nemotron-3.5-lightning:free"

    assert by_name["OpenAI-FreeTier"]._base_url == "https://api.openai.com/v1"
    assert by_name["OpenAI-FreeTier"]._model == "gpt-5-nano"
    assert by_name["OpenAI-FreeTier"]._extra_body == {"reasoning_effort": "minimal"}
    assert by_name["OpenAI-FreeTier"]._before_llm_call is not None


def test_openai_free_tier_budget_exceeded_message_names_the_daily_allowance_not_the_monthly_cap(monkeypatch):
    # Real regression this guards: OpenAICompatibleAdapter's default
    # budget_exceeded_message names the monthly LLM spend cap - correct for
    # every other before_llm_call wiring (e.g. jobs.py's
    # spend_budget.can_start_next_call), but wrong for this adapter, whose
    # before_llm_call enforces a different budget entirely (the daily
    # free-tier token allowance). An engineer paged via _send_ops_alert on
    # total free-tier exhaustion would misdiagnose a healthy daily
    # rollover as a billing problem.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    chain = writing_adapter_chain_for_free_tier(_FakeRedis())
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert "daily free-tier token allowance" in openai_adapter._budget_exceeded_message
    assert "monthly LLM spend cap" not in openai_adapter._budget_exceeded_message


def test_writing_adapter_chain_for_free_tier_orders_openai_before_openrouter(monkeypatch):
    # Fallback priority order: Groq, Gemini, OpenAI free-tier key,
    # OpenRouter last - OpenRouter is the weakest/most rate-limit-prone
    # free option of the four, so it's tried only after everything else
    # (including the dollar-costing OpenAI key) has failed.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    chain = writing_adapter_chain_for_free_tier(_FakeRedis())
    names = [a.name for a in chain]
    assert names == ["Groq", "Gemini", "OpenAI-FreeTier", "OpenRouter"]


def test_writing_adapter_chain_for_free_tier_passes_on_usage(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    received = []
    chain = writing_adapter_chain_for_free_tier(_FakeRedis(), on_usage=lambda p, c: received.append((p, c)))
    for adapter in chain:
        adapter._on_usage(10, 20)
    assert received == [(10, 20)] * len(chain)


# ── OpenAI free-tier daily token cap ────────────────────────────────────
# Real free daily allowance (OpenAI's shared-traffic free tier: gpt-5-nano
# and other mini/nano models get 2,500,000 free tokens PER DAY, not per
# month - confirmed against OpenAI's own published free-tier terms), not
# an abuse ceiling. Enforced via before_llm_call (an atomic reserve-then-
# true-up, invoked only when a real call is about to happen) rather than
# by deciding whether to include the adapter in the chain at build time -
# see _reserve_openai_free_tier_budget's docstring for why a plain
# read-then-decide check at build time had a real concurrency gap.


def test_building_the_chain_alone_never_reserves_openai_budget(monkeypatch):
    # Regression guard: reservation must only happen when a real call is
    # about to be attempted (via before_llm_call), not merely because the
    # adapter was included in the chain - otherwise an adapter that's
    # never actually reached (an earlier provider succeeded first) would
    # still burn real budget for nothing.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    from scan_worker.model_tiers import _openai_free_tier_token_key

    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)

    assert "OpenAI-FreeTier" in [a.name for a in chain]
    assert redis_conn.data.get(_openai_free_tier_token_key(), 0) == 0


def test_openai_free_tier_before_llm_call_blocks_once_daily_cap_reached(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    from scan_worker.model_tiers import _openai_free_tier_token_key

    redis_conn = _FakeRedis({_openai_free_tier_token_key(): OPENAI_FREE_TIER_DAILY_TOKEN_CAP})
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert openai_adapter._before_llm_call() is False
    # The refused reservation released itself - the counter isn't left
    # permanently inflated by a reservation nobody got to use.
    assert redis_conn.data[_openai_free_tier_token_key()] == OPENAI_FREE_TIER_DAILY_TOKEN_CAP


def test_openai_free_tier_before_llm_call_allows_under_daily_cap(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    from scan_worker.model_tiers import OPENAI_FREE_TIER_RESERVATION_TOKENS, _openai_free_tier_token_key

    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert openai_adapter._before_llm_call() is True
    assert redis_conn.data[_openai_free_tier_token_key()] == OPENAI_FREE_TIER_RESERVATION_TOKENS


def test_openai_free_tier_reservation_arithmetic_is_correct_at_the_cap_boundary(monkeypatch):
    # Sequential, not concurrent - this checks the reserve/refund
    # arithmetic itself (a first reservation that fits, a second that
    # doesn't and correctly refunds). The real concurrency guarantee is
    # exercised by test_openai_free_tier_reservation_is_atomic_across_real_
    # concurrent_threads below; this one is intentionally the simpler,
    # non-threaded case so a failure here points straight at the
    # arithmetic rather than at thread scheduling.
    from scan_worker.model_tiers import (
        OPENAI_FREE_TIER_RESERVATION_TOKENS,
        _openai_free_tier_token_key,
        _reserve_openai_free_tier_budget,
    )

    redis_conn = _FakeRedis({
        _openai_free_tier_token_key(): OPENAI_FREE_TIER_DAILY_TOKEN_CAP - OPENAI_FREE_TIER_RESERVATION_TOKENS
    })

    first = _reserve_openai_free_tier_budget(redis_conn)
    second = _reserve_openai_free_tier_budget(redis_conn)

    assert first is True
    assert second is False
    assert redis_conn.data[_openai_free_tier_token_key()] == OPENAI_FREE_TIER_DAILY_TOKEN_CAP


def test_openai_free_tier_reservation_is_atomic_across_real_concurrent_threads(monkeypatch):
    # The TOCTOU race this closes: two concurrent reviews both attempting
    # to reserve budget right at the cap boundary. A plain read-then-decide
    # check could let both read "under cap" before either recorded
    # anything. Unlike the sequential test above, this uses real
    # threading.Thread objects and a Barrier so every thread's INCRBY call
    # genuinely races against the others, not just calls made one after
    # another in program order - _FakeRedis.incrby is itself lock-protected
    # (see its docstring) specifically so this test can prove something
    # about real concurrent access rather than getting lucky on GIL timing.
    from scan_worker.model_tiers import (
        OPENAI_FREE_TIER_RESERVATION_TOKENS,
        _openai_free_tier_token_key,
        _reserve_openai_free_tier_budget,
    )

    # Room for exactly one more reservation - of N concurrent attempts,
    # exactly one may succeed.
    redis_conn = _FakeRedis({
        _openai_free_tier_token_key(): OPENAI_FREE_TIER_DAILY_TOKEN_CAP - OPENAI_FREE_TIER_RESERVATION_TOKENS
    })

    thread_count = 10
    results: list[bool] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(thread_count)

    def _attempt():
        barrier.wait()  # maximize actual overlap, not just thread creation order
        result = _reserve_openai_free_tier_budget(redis_conn)
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=_attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == thread_count - 1
    # Every refused reservation released itself - the counter lands
    # exactly at the cap, not above it (overshoot) or below (a refund that
    # over-corrected).
    assert redis_conn.data[_openai_free_tier_token_key()] == OPENAI_FREE_TIER_DAILY_TOKEN_CAP


def test_openai_free_tier_usage_trues_up_the_reservation_to_the_real_total(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    from scan_worker.model_tiers import _openai_free_tier_token_key

    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert openai_adapter._before_llm_call() is True  # reserves the conservative estimate
    openai_adapter._on_usage(1000, 500)  # trues up to the real total

    assert redis_conn.data[_openai_free_tier_token_key()] == 1500
    assert redis_conn.expiries[_openai_free_tier_token_key()] == 2 * 24 * 3600


def test_openai_free_tier_usage_still_forwards_to_the_shared_on_usage(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    received = []
    chain = writing_adapter_chain_for_free_tier(
        _FakeRedis(), on_usage=lambda p, c: received.append((p, c))
    )
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    openai_adapter._on_usage(10, 20)

    assert received == [(10, 20)]


def test_openai_free_tier_on_call_failed_releases_the_reservation(monkeypatch):
    # Real regression this guards: on_usage only fires on a completed call -
    # a failed call (rotated key, outage, transient error exhausted its
    # retries) never reaches it, so without on_call_failed the reservation
    # before_llm_call already made stays stuck forever against zero real
    # tokens. ~18 such failures in a day (18 x 130k ~= 2.34M against the
    # 2.4M daily cap) would silently exhaust the counter and exclude
    # OpenAI-FreeTier from the fallback chain for the rest of the day, even
    # though every failed review still succeeded via the next provider in
    # the chain (Groq/Gemini tried first, OpenRouter last) - the bug is
    # invisible in review outcomes, only visible in the day's shrinking
    # free-tier capacity.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    from scan_worker.model_tiers import _openai_free_tier_token_key

    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert openai_adapter._before_llm_call() is True  # reserves 130,000
    assert redis_conn.data[_openai_free_tier_token_key()] == 130_000
    openai_adapter._on_call_failed()  # the real call then failed outright

    assert redis_conn.data[_openai_free_tier_token_key()] == 0


def test_openai_free_tier_simple_completion_releases_reservation_on_a_failed_call(monkeypatch):
    # End-to-end version of the test above, through the real
    # simple_completion exception path rather than calling the hook
    # directly.
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    from scan_worker.model_tiers import _openai_free_tier_token_key

    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("boom")
    with (
        patch("aletheore.adapters.openai_compatible.OpenAI", return_value=mock_client),
        patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"),
    ):
        with pytest.raises(Exception):
            openai_adapter.simple_completion("system", "user", cwd="/repo")

    assert redis_conn.data[_openai_free_tier_token_key()] == 0


# ── cascading fallback tests ────────────────────────────────────────────


def test_run_with_free_tier_fallback_uses_first_succeeding_adapter():
    call_log = []

    class FakeAdapter:
        def __init__(self, name, succeeds):
            self.name = name
            self._succeeds = succeeds

    adapters = [
        FakeAdapter("failing-1", False),
        FakeAdapter("failing-2", False),
        FakeAdapter("succeeding", True),
    ]

    def fn(adapter):
        call_log.append(adapter.name)
        if not adapter._succeeds:
            raise RuntimeError(f"{adapter.name} is down")
        return f"result from {adapter.name}"

    result = run_with_free_tier_fallback(adapters, fn)
    assert result == "result from succeeding"
    assert call_log == ["failing-1", "failing-2", "succeeding"]


def test_run_with_free_tier_fallback_raises_when_all_fail():
    class AlwaysFails:
        def __init__(self, name):
            self.name = name

    adapters = [AlwaysFails("prov-a"), AlwaysFails("prov-b")]

    def fn(adapter):
        raise ConnectionError(f"{adapter.name} timeout")

    try:
        run_with_free_tier_fallback(adapters, fn)
        assert False, "should have raised"
    except FreeTierFallbackExhausted as exc:
        assert len(exc.errors) == 2
        assert exc.errors[0][0] == "prov-a"
        assert exc.errors[1][0] == "prov-b"
        assert "prov-a" in str(exc)
        assert "prov-b" in str(exc)


def test_run_with_free_tier_fallback_logs_each_failure(monkeypatch, caplog):
    class AlwaysFails:
        def __init__(self, name):
            self.name = name

    adapters = [AlwaysFails("bad-1"), AlwaysFails("bad-2")]

    def fn(adapter):
        raise RuntimeError("nope")

    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        try:
            run_with_free_tier_fallback(adapters, fn)
        except FreeTierFallbackExhausted:
            pass

    assert "bad-1" in caplog.text
    assert "bad-2" in caplog.text
    assert "RuntimeError" in caplog.text


def test_run_with_free_tier_fallback_logs_successful_provider(monkeypatch, caplog):
    class FakeAdapter:
        def __init__(self, name):
            self.name = name

    adapters = [FakeAdapter("winner")]

    def fn(adapter):
        return "ok"

    with caplog.at_level(logging.INFO, logger="scan_worker.model_tiers"):
        result = run_with_free_tier_fallback(adapters, fn)

    assert result == "ok"
    assert "winner" in caplog.text
    assert "served request successfully" in caplog.text


def test_run_with_free_tier_fallback_single_adapter_succeeds():
    class SingleAdapter:
        name = "only-one"

    adapters = [SingleAdapter()]

    def fn(adapter):
        return "direct success"

    assert run_with_free_tier_fallback(adapters, fn) == "direct success"
