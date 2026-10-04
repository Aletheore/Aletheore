import logging
import threading
from datetime import datetime, timezone

import pytest

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.model_tiers import (
    HEALTH_FIX_SUGGESTION_MODEL,
    INDIEROUTER_DEEPSEEK_MODEL,
    LUNA_MODEL,
    MANAGED_AUDIT_MODEL,
    MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS,
    OPENAI_FREE_TIER_DAILY_TOKEN_CAP,
    PRO_MODEL,
    FreeTierFallbackExhausted,
    airview_model_used,
    docs_model_used,
    health_fix_suggestion_model_used,
    managed_audit_model_used,
    model_for_plan,
    resolve_model,
    run_with_free_tier_fallback,
    writing_adapter_chain_for_free_tier,
    writing_adapter_for,
    writing_adapter_for_airview,
    writing_adapter_for_docs,
    writing_adapter_for_health_fix_suggestion,
    writing_adapter_for_managed_audit,
    writing_adapter_for_plan,
)


def _fake_has_api_key(openai: bool = False, indierouter: bool = False):
    """Key-aware has_api_key fake - a blanket `lambda *a, **k: True/False`
    would make _indierouter_available() (which calls the same has_api_key)
    agree with whatever OPENAI_API_KEY's fake value was, silently routing
    an AIRview/Docs/managed-audit/health-fix-suggestion test meant to
    exercise the DeepSeek-or-Luna fallback path into the new IndieRouter
    primary path instead. Mirrors the free-tier chain tests' own
    fake_has_api_key(env_var, name, **kwargs) shape below."""
    def fake(env_var, name, **kwargs):
        if env_var == "OPENAI_API_KEY":
            return openai
        if env_var == "INDIEROUTER_API_KEY":
            return indierouter
        return False
    return fake


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


def test_writing_adapter_for_json_output_turns_on_json_mode_for_the_openai_model(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    assert writing_adapter_for("some-fallback", json_output=True)._json_mode is True
    assert writing_adapter_for("some-fallback")._json_mode is False


def test_writing_adapter_for_json_output_leaves_the_deepseek_path_alone(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert writing_adapter_for("some-fallback", json_output=True)._json_mode is False
    # AIRview never turns on json_mode, on either path: not on IndieRouter
    # (writing_adapter_for_airview's own docstring - preserves real existing
    # behavior rather than changing provider and JSON mode at once), and not
    # on its DeepSeek-direct fallback even with OpenAI configured.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    assert writing_adapter_for_airview("some-fallback", json_output=True)._json_mode is False
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert writing_adapter_for_airview("some-fallback", json_output=True)._json_mode is False


def test_writing_adapter_for_plan_threads_json_output(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    assert writing_adapter_for_plan("air", json_output=True)._json_mode is True
    assert writing_adapter_for_plan("air")._json_mode is False


def test_docs_builders_ask_for_json(monkeypatch):
    # Docs parses every response as JSON - on IndieRouter (the real primary
    # path as of 2026-10-04) this is the first time json_output actually
    # reaches a provider's real response_format=json_object; on the Luna
    # fallback, a malformed long response drops the whole batch.
    from scan_worker.jobs import _live_docs_full_build_writing_adapter, _live_docs_update_writing_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert _live_docs_full_build_writing_adapter()._json_mode is True
    assert _live_docs_update_writing_adapter()._json_mode is True

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    assert _live_docs_full_build_writing_adapter()._json_mode is True
    assert _live_docs_update_writing_adapter()._json_mode is True


def test_every_prompt_sent_in_json_mode_mentions_json():
    # OpenAI rejects response_format=json_object (HTTP 400) when no message
    # contains the word "JSON", so each prompt must say it.
    from scan_worker import live_docs, live_wiki

    prompts = [
        live_wiki.NAMING_SYSTEM_PROMPT,
        live_wiki.SUBSYSTEM_WRITING_SYSTEM_PROMPT,
        live_wiki.BATCH_SUBSYSTEM_WRITING_SYSTEM_PROMPT,
        live_wiki.FILE_PAGE_WRITING_SYSTEM_PROMPT,
        live_wiki.BATCH_FILE_PAGE_WRITING_SYSTEM_PROMPT,
        live_wiki.OVERVIEW_WRITING_SYSTEM_PROMPT,
        live_docs.COMBINED_SYSTEM_PROMPT,
        live_docs.DESCRIBE_SYSTEM_PROMPT,
        live_docs.POLISH_SYSTEM_PROMPT,
    ]
    for prompt in prompts:
        assert "json" in prompt.lower()


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


def test_writing_adapter_for_airview_uses_indierouter_when_configured(monkeypatch):
    # AIRview's primary provider as of 2026-10-04 - deepseek-v4.1-flash via
    # IndieRouter, effort low, a 300s timeout, never json_mode (see
    # writing_adapter_for_airview's own docstring for the measured
    # settings). Takes this path regardless of OPENAI_API_KEY.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True, indierouter=True))
    adapter = writing_adapter_for_airview("deepseek-v4-flash", json_output=True)
    assert adapter.name == "IndieRouter"
    assert adapter._model == INDIEROUTER_DEEPSEEK_MODEL
    assert adapter._base_url == "https://api.indierouter.ai/v1"
    assert adapter._api_key_env_var == "INDIEROUTER_API_KEY"
    assert adapter._extra_body == {"reasoning_effort": "low"}
    assert adapter._request_timeout_seconds == 300
    assert adapter._json_mode is False


def test_writing_adapter_for_airview_never_uses_luna_when_indierouter_not_configured(monkeypatch):
    # AIRview's own comprehension benchmark (aletheore-benchmarks,
    # AIRVIEW_GAP.md, re-measured 2026-08-22, full 12-question architecture
    # set, 3 judge repeats) found deepseek-v4-flash tied RepoWise (1.88 vs
    # 1.99, inside the judge's own noise floor) while gpt-5.6-luna lost
    # decisively (1.53 vs 2.08, outside it) - same corpus, same day, same
    # rubric. Unlike writing_adapter_for, this must not switch to Luna just
    # because OPENAI_API_KEY is configured.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    adapter = writing_adapter_for_airview("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"
    assert adapter._base_url == "https://api.deepseek.com"
    assert adapter._api_key_env_var == "DEEPSEEK_API_KEY"


def test_writing_adapter_for_airview_still_uses_deepseek_when_nothing_is_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_airview("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"


def test_writing_adapter_for_airview_falls_back_and_logs_when_indierouter_not_configured(monkeypatch, caplog):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        adapter = writing_adapter_for_airview("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert "INDIEROUTER_API_KEY not configured" in caplog.text


def test_writing_adapter_for_airview_threads_on_usage_and_before_llm_call(monkeypatch):
    for kwargs in [{"indierouter": True}, {}]:
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(**kwargs))
        received = []
        calls_allowed = []
        adapter = writing_adapter_for_airview(
            "deepseek-v4-flash",
            on_usage=lambda p, c: received.append((p, c)),
            before_llm_call=lambda: calls_allowed.append(True) or True,
        )
        adapter._on_usage(7, 3)
        assert received == [(7, 3)], kwargs
        assert adapter._before_llm_call() is True
        assert calls_allowed == [True], kwargs


def test_airview_model_used_tracks_which_branch_will_run(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert airview_model_used("deepseek-v4-flash") == INDIEROUTER_DEEPSEEK_MODEL
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert airview_model_used("deepseek-v4-flash") == "deepseek-v4-flash"


def test_writing_adapter_for_managed_audit_uses_indierouter_when_configured(monkeypatch):
    # Managed audit's primary provider as of 2026-10-04 - deepseek-v4.1-flash
    # via IndieRouter, effort low (measured 9 rounds/132s/$0.16 against this
    # repository - see writing_adapter_for_managed_audit's own docstring).
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True, indierouter=True))
    adapter = writing_adapter_for_managed_audit()
    assert adapter.name == "IndieRouter"
    assert adapter._model == INDIEROUTER_DEEPSEEK_MODEL
    assert adapter._base_url == "https://api.indierouter.ai/v1"
    assert adapter._extra_body == {"reasoning_effort": "low"}
    # Default True on the IndieRouter branch - the False workaround below is
    # specific to deepseek-v4-pro's own thinking-mode quirk on the direct
    # DeepSeek API, not something seen against IndieRouter.
    assert adapter._supports_tool_choice is True
    # The hosted-only ceiling (openai_compatible.MANAGED_AUDIT_MAX_TOOL_ROUNDS)
    # - every other writing surface, including every local CLI adapter,
    # stays at the plain adapter default (20).
    from aletheore.adapters.openai_compatible import MANAGED_AUDIT_MAX_TOOL_ROUNDS

    assert adapter._max_tool_rounds == MANAGED_AUDIT_MAX_TOOL_ROUNDS == 40
    # Real gap found via a live smoke test (2026-10-04): one round hit a
    # 210,846-token prompt and a 14,971-token completion - ~118s of
    # generation alone at IndieRouter's own measured ~127 tokens/sec, 2s of
    # margin under the 120s default. Same AIRVIEW_REQUEST_TIMEOUT_SECONDS
    # mitigation, applied here too.
    assert adapter._request_timeout_seconds == MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS == 300


def test_writing_adapter_for_managed_audit_never_uses_luna_when_indierouter_not_configured(monkeypatch):
    # Measured directly against a real repo, three full audit runs: Luna
    # cost $0.15 (6 rounds) and missed a real circular import;
    # deepseek-v4-pro cost $1.15 (14 rounds) and caught it; deepseek-v4-flash
    # cost $0.40 (16 rounds) and also caught it - same accuracy as Pro for a
    # third of the cost. Unlike writing_adapter_for_plan, this must not
    # switch to Luna just because OPENAI_API_KEY is configured.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    adapter = writing_adapter_for_managed_audit()
    assert adapter.name == "DeepSeek"
    assert adapter._model == MANAGED_AUDIT_MODEL == "deepseek-v4-flash"
    assert adapter._base_url == "https://api.deepseek.com"
    assert adapter._supports_tool_choice is False
    # The hosted ceiling applies on the fallback branch too - the feature's
    # cost reserve is already sized for it regardless of which provider
    # actually answers. Same for the request timeout.
    from aletheore.adapters.openai_compatible import MANAGED_AUDIT_MAX_TOOL_ROUNDS

    assert adapter._max_tool_rounds == MANAGED_AUDIT_MAX_TOOL_ROUNDS == 40
    assert adapter._request_timeout_seconds == MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS == 300


def test_writing_adapter_for_managed_audit_still_uses_deepseek_when_nothing_is_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_managed_audit()
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"


def test_only_managed_audit_raises_max_tool_rounds_above_the_plain_default(monkeypatch):
    # Pins the intended scope of the hosted-only ceiling: AIRview, Docs, and
    # health-fix suggestions never pass max_tool_rounds (they use
    # simple_completion, not invoke(), so it would be inert for them anyway)
    # - only writing_adapter_for_managed_audit does, on both its branches.
    from aletheore.adapters.openai_compatible import MAX_TOOL_ROUNDS

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert writing_adapter_for_airview("deepseek-v4-flash")._max_tool_rounds == MAX_TOOL_ROUNDS
    assert writing_adapter_for_docs(PRO_MODEL)._max_tool_rounds == MAX_TOOL_ROUNDS
    assert writing_adapter_for_health_fix_suggestion()._max_tool_rounds == MAX_TOOL_ROUNDS


def test_only_airview_and_managed_audit_raise_the_request_timeout_above_the_plain_default(monkeypatch):
    # Same scoping pin as the max_tool_rounds test above, for the request
    # timeout: Docs and health-fix suggestions never saw a hang like
    # AIRview's or managed audit's, so they stay at the plain default (120)
    # rather than being bumped speculatively.
    from aletheore.adapters.openai_compatible import REQUEST_TIMEOUT_SECONDS

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert writing_adapter_for_docs(PRO_MODEL)._request_timeout_seconds == REQUEST_TIMEOUT_SECONDS == 120
    assert writing_adapter_for_health_fix_suggestion()._request_timeout_seconds == REQUEST_TIMEOUT_SECONDS == 120
    assert writing_adapter_for_airview("deepseek-v4-flash")._request_timeout_seconds == 300
    assert writing_adapter_for_managed_audit()._request_timeout_seconds == 300


def test_writing_adapter_for_managed_audit_falls_back_and_logs_when_indierouter_not_configured(
    monkeypatch, caplog
):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        adapter = writing_adapter_for_managed_audit()
    assert adapter.name == "DeepSeek"
    assert "INDIEROUTER_API_KEY not configured" in caplog.text


def test_writing_adapter_for_managed_audit_threads_on_usage_and_before_llm_call(monkeypatch):
    for kwargs in [{"indierouter": True}, {}]:
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(**kwargs))
        received = []
        calls_allowed = []
        adapter = writing_adapter_for_managed_audit(
            on_usage=lambda p, c: received.append((p, c)),
            before_llm_call=lambda: calls_allowed.append(True) or True,
        )
        adapter._on_usage(7, 3)
        assert received == [(7, 3)], kwargs
        assert adapter._before_llm_call() is True
        assert calls_allowed == [True], kwargs


def test_writing_adapter_for_managed_audit_threads_allow_partial_report(monkeypatch):
    for kwargs in [{"indierouter": True}, {}]:
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(**kwargs))
        adapter = writing_adapter_for_managed_audit(allow_partial_report=True)
        assert adapter._allow_partial_report is True, kwargs


def test_managed_audit_model_used_tracks_which_branch_will_run(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert managed_audit_model_used() == INDIEROUTER_DEEPSEEK_MODEL
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert managed_audit_model_used() == MANAGED_AUDIT_MODEL








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


# ── writing_adapter_for_docs tests ───────────────────────────────────────


def test_writing_adapter_for_docs_uses_indierouter_when_configured(monkeypatch):
    # Docs' primary provider as of 2026-10-04 - deepseek-v4.1-flash via
    # IndieRouter with JSON mode on (default reasoning - effort low and
    # per-file parallelism are untested for Docs specifically, see
    # writing_adapter_for_docs's own docstring).
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True, indierouter=True))
    adapter = writing_adapter_for_docs(PRO_MODEL, json_output=True)
    assert adapter.name == "IndieRouter"
    assert adapter._model == INDIEROUTER_DEEPSEEK_MODEL
    assert adapter._json_mode is True
    assert adapter._extra_body == {}


def test_writing_adapter_for_docs_falls_back_to_the_previous_resolution_when_indierouter_not_configured(
    monkeypatch,
):
    # Each existing call site's own _prefer_luna=True default is preserved
    # exactly on the fallback branch - Luna if OPENAI_API_KEY is configured,
    # else the given fallback_model direct.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    adapter = writing_adapter_for_docs(PRO_MODEL, json_output=True)
    assert adapter.name == "OpenAI"
    assert adapter._model == LUNA_MODEL
    assert adapter._json_mode is True

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_docs("deepseek-v4-flash", json_output=True)
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"
    # The DeepSeek-direct branch of writing_adapter_for drops json_output
    # entirely (see that function's own docstring) - unchanged, preexisting
    # behavior, not something this change alters for the fallback path.
    assert adapter._json_mode is False


def test_writing_adapter_for_docs_logs_when_indierouter_not_configured(monkeypatch, caplog):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        writing_adapter_for_docs("deepseek-v4-flash")
    assert "INDIEROUTER_API_KEY not configured" in caplog.text


def test_docs_model_used_tracks_which_branch_will_run(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert docs_model_used(PRO_MODEL) == INDIEROUTER_DEEPSEEK_MODEL
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    assert docs_model_used(PRO_MODEL) == LUNA_MODEL
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert docs_model_used("deepseek-v4-flash") == "deepseek-v4-flash"


# ── writing_adapter_for_health_fix_suggestion tests ──────────────────────


def test_writing_adapter_for_health_fix_suggestion_uses_indierouter_when_configured(monkeypatch):
    # Genuinely new surface as of 2026-10-04 - glm-5.3-flash via IndieRouter,
    # effort low, untested end to end (see this builder's own docstring).
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True, indierouter=True))
    adapter = writing_adapter_for_health_fix_suggestion()
    assert adapter.name == "IndieRouter"
    assert adapter._model == HEALTH_FIX_SUGGESTION_MODEL == "glm-5.3-flash"
    assert adapter._extra_body == {"reasoning_effort": "low"}


def test_writing_adapter_for_health_fix_suggestion_falls_back_to_luna_when_only_openai_is_configured(
    monkeypatch,
):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    adapter = writing_adapter_for_health_fix_suggestion()
    assert adapter.name == "OpenAI"
    assert adapter._model == LUNA_MODEL


def test_writing_adapter_for_health_fix_suggestion_falls_back_to_deepseek_pro_when_nothing_is_configured(
    monkeypatch,
):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_health_fix_suggestion()
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_MODEL


def test_writing_adapter_for_health_fix_suggestion_logs_when_indierouter_not_configured(monkeypatch, caplog):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        writing_adapter_for_health_fix_suggestion()
    assert "INDIEROUTER_API_KEY not configured" in caplog.text


def test_health_fix_suggestion_model_used_tracks_which_branch_will_run(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(indierouter=True))
    assert health_fix_suggestion_model_used("pro") == HEALTH_FIX_SUGGESTION_MODEL
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", _fake_has_api_key(openai=True))
    assert health_fix_suggestion_model_used("pro") == LUNA_MODEL
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert health_fix_suggestion_model_used("pro") == PRO_MODEL


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
    chain = writing_adapter_chain_for_free_tier(
        _FakeRedis(), on_usage=lambda p, c, cached=0: received.append((p, c))
    )
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


def test_reserve_openai_free_tier_budget_honors_an_explicit_empty_string_key(monkeypatch):
    # Flash Review finding: `key or _openai_free_tier_token_key()` treats
    # an explicitly-passed empty string the same as "no key given" and
    # silently reserves against a freshly computed key instead - this
    # function's own docstring promises "the caller may pass the exact
    # key to reserve against", which an empty string is a legal (if
    # unusual) instance of.
    from scan_worker.model_tiers import OPENAI_FREE_TIER_RESERVATION_TOKENS, _reserve_openai_free_tier_budget

    redis_conn = _FakeRedis()

    assert _reserve_openai_free_tier_budget(redis_conn, key="") is True

    assert "" in redis_conn.data
    assert redis_conn.data[""] == OPENAI_FREE_TIER_RESERVATION_TOKENS


def test_true_up_openai_free_tier_reservation_honors_an_explicit_empty_string_key(monkeypatch):
    from scan_worker.model_tiers import (
        OPENAI_FREE_TIER_RESERVATION_TOKENS,
        _true_up_openai_free_tier_reservation,
    )

    redis_conn = _FakeRedis({"": OPENAI_FREE_TIER_RESERVATION_TOKENS})

    _true_up_openai_free_tier_reservation(redis_conn, 500, key="")

    assert redis_conn.data[""] == 500


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


def test_openai_free_tier_true_up_corrects_the_same_day_the_reservation_used(monkeypatch):
    # Real bug found via audit: before_llm_call and on_usage/on_call_failed
    # each independently computed _openai_free_tier_token_key() from
    # datetime.now(timezone.utc) at the moment they were called - not a
    # value captured once per call. A call reserved at 23:59:59 UTC that
    # completes at 00:00:01 UTC the next day reserved against yesterday's
    # key but trued up against today's key, permanently over-reserving
    # the first day and leaking negative headroom into the second day's
    # real allowance (a brand-new day's counter starting negative means
    # more real tokens than the cap intends can be spent before
    # _reserve_openai_free_tier_budget starts rejecting new reservations).
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    import scan_worker.model_tiers as model_tiers_module

    class _FakeDatetime(datetime):
        _now = datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(model_tiers_module, "datetime", _FakeDatetime)
    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert openai_adapter._before_llm_call() is True  # reserves against 2026-01-01's key

    _FakeDatetime._now = datetime(2026, 1, 2, 0, 0, 1, tzinfo=timezone.utc)
    openai_adapter._on_usage(30_000, 20_000)  # completes just after midnight UTC

    assert redis_conn.data["free_tier:openai_tokens:2026-01-01"] == 50_000
    assert redis_conn.data.get("free_tier:openai_tokens:2026-01-02") is None


def test_openai_free_tier_on_call_failed_releases_against_the_same_day_the_reservation_used(monkeypatch):
    # Same real bug as the true-up test above, for the failure-release path.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    import scan_worker.model_tiers as model_tiers_module

    class _FakeDatetime(datetime):
        _now = datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(model_tiers_module, "datetime", _FakeDatetime)
    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    assert openai_adapter._before_llm_call() is True

    _FakeDatetime._now = datetime(2026, 1, 2, 0, 0, 1, tzinfo=timezone.utc)
    openai_adapter._on_call_failed()

    assert redis_conn.data["free_tier:openai_tokens:2026-01-01"] == 0
    assert redis_conn.data.get("free_tier:openai_tokens:2026-01-02") is None


def test_openai_free_tier_reserved_key_is_isolated_across_concurrent_threads(monkeypatch):
    # Flash Review finding: the reserved key used to live in one dict
    # shared by every call through this adapter. Two real, concurrent
    # calls whose reservations land on DIFFERENT keys (the UTC-midnight
    # case the sequential tests above already cover one at a time) used
    # to race: thread B's reservation could overwrite thread A's key in
    # the shared cell before A's true-up ran, so A's true-up/release
    # would silently correct B's key instead of its own. A Barrier forces
    # both threads to reserve before either trues up, so this only passes
    # if each thread's key is really isolated, not just usually not
    # clobbered by scheduling luck.
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    import scan_worker.model_tiers as model_tiers_module

    # Each thread computes its own key by name, standing in for two calls
    # whose reservations land on different real dates (this file's other
    # UTC-boundary tests already prove the date->key mapping itself; this
    # test is only about whether concurrent calls keep their keys apart).
    monkeypatch.setattr(
        model_tiers_module, "_openai_free_tier_token_key",
        lambda: f"free_tier:openai_tokens:{threading.current_thread().name}",
    )
    redis_conn = _FakeRedis()
    chain = writing_adapter_chain_for_free_tier(redis_conn)
    openai_adapter = next(a for a in chain if a.name == "OpenAI-FreeTier")

    reserve_barrier = threading.Barrier(2)
    true_up_barrier = threading.Barrier(2)

    def _run(true_up_amount: int) -> None:
        reserve_barrier.wait()  # both threads reserve before either trues up
        assert openai_adapter._before_llm_call() is True
        true_up_barrier.wait()
        openai_adapter._on_usage(true_up_amount, 0)

    thread_a = threading.Thread(target=_run, args=(11_000,), name="thread-a")
    thread_b = threading.Thread(target=_run, args=(22_000,), name="thread-b")
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    # Each thread's own key trued up to its own real total, not the other
    # thread's - the exact corruption the shared-dict version risked.
    assert redis_conn.data["free_tier:openai_tokens:thread-a"] == 11_000
    assert redis_conn.data["free_tier:openai_tokens:thread-b"] == 22_000


def test_openai_free_tier_usage_still_forwards_to_the_shared_on_usage(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    received = []
    chain = writing_adapter_chain_for_free_tier(
        _FakeRedis(), on_usage=lambda p, c, cached=0: received.append((p, c))
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
