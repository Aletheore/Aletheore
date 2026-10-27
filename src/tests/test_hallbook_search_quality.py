"""Focused tests for the Hallbook search quality work.

These cover typo recovery, honest "none" confidence, per-session hook dedupe,
custom-skill content search and the small regression query list. They stay fast
on purpose: the full 994-query benchmark lives in benchmarks/hallbook-search.
"""

import pytest

from aletheore import skill_hub


def test_fuzzy_match_recovers_common_typos():
    index = skill_hub._search_index(str(skill_hub.bundled_skills_dir()))
    assert index._fuzzy_match("webhok") == "webhook"
    assert index._fuzzy_match("bysql") == "mysql"
    assert index._fuzzy_match("kuberent") == "kubernet"
    assert index._fuzzy_match("postgress") == "postgr"
    assert index._fuzzy_match("zzzzq") is None


def test_typo_query_ranks_the_same_skills_as_the_clean_query():
    clean = [meta.skill_id for meta in skill_hub.search_skills("kubernetes deployment", limit=3)]
    typo = [meta.skill_id for meta in skill_hub.search_skills("kuberentes deployment", limit=3)]
    assert clean == typo


def test_fuzzy_matching_only_runs_for_words_with_no_exact_hit():
    # A clean query must keep exactly the ranking it had before fuzzy matching existed.
    for query in ("debug a failing test", "postgres migration"):
        with_fuzzy = [meta.skill_id for meta, _ in skill_hub.search_skills_scored(query, limit=5)]
        without_fuzzy = [
            meta.skill_id for meta, _ in skill_hub.search_skills_scored(query, limit=5, fuzzy=False)
        ]
        assert with_fuzzy == without_fuzzy

    clean = [meta.skill_id for meta in skill_hub.search_skills("postgres migration", limit=5)]
    typo = [meta.skill_id for meta in skill_hub.search_skills("postgress migrations", limit=5)]
    assert clean[0] == typo[0]


def test_small_talk_and_trivial_edits_are_none():
    for prompt in (
        "hi",
        "thanks",
        "what is 2+2",
        "rename this variable",
        "what does this function do",
        "can you explain this line",
        "yes go ahead",
    ):
        result = skill_hub.find_skill(prompt)
        assert result["confidence"] == "none", prompt
        assert "top" not in result
        assert "skill" not in result


def test_a_clear_task_is_never_none():
    for prompt in (
        "can you look at why my login test is flaky",
        "add pagination to the users list endpoint",
        "write a migration that adds an index on orders.created_at",
    ):
        assert skill_hub.find_skill(prompt)["confidence"] != "none", prompt


def test_weak_scattered_matches_are_none():
    # "with" and "the" carry no signal, so there is nothing to rank.
    assert skill_hub.find_skill("something with the")["confidence"] == "none"


def test_scattered_match_is_not_reported_as_high_confidence():
    # The top skill covers only a third of the task's content words.
    result = skill_hub.find_skill("Can you draw a diagram showing how my cloud services connect?")
    assert result["confidence"] == "low"


def test_a_fully_covered_strong_match_is_high_confidence():
    result = skill_hub.find_skill("How should I write Javadoc comments for Java classes and methods?")
    assert result["confidence"] == "high"
    assert result["top"]["skill_id"] == "documentation/java-docs"


def test_hook_stays_silent_for_follow_up_questions_about_the_chat():
    for prompt in (
        "can you explain what you meant by the previous paragraph",
        "what did you do in the last step",
        "please continue with what you were saying",
        "that output looks correct to me, thanks",
        "sorry, I meant the other file",
        "can you show me the diff for the last change",
        "thanks for taking a look at that earlier, it is working now",
        "add a stripe webhook handler",
    ):
        assert skill_hub.hook_suggestion(prompt) is None, prompt


def test_hook_still_hints_for_a_clear_task_with_a_short_polite_prefix():
    suggestion = skill_hub.hook_suggestion("please add a stripe webhook handler for payments")
    assert suggestion is not None
    assert "platforms/stripe-integration" in suggestion


def test_hook_suggests_a_skill_at_most_once_per_session(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    prompt = "can you look at why my login test is flaky"

    first = skill_hub.hook_suggestion(prompt, session_id="session-one")
    assert first is not None
    assert skill_hub.hook_suggestion(prompt, session_id="session-one") is None
    assert skill_hub.hook_suggestion(prompt, session_id="session-two") is not None

    state = tmp_path / ".config" / "aletheore" / "hallbook_sessions" / "session-one.json"
    assert state.is_file()
    assert "testing/e2e-testing-patterns" in state.read_text(encoding="utf-8")


def test_hook_dedupe_does_not_need_a_session_id(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    prompt = "can you look at why my login test is flaky"

    assert skill_hub.hook_suggestion(prompt) is not None
    assert skill_hub.hook_suggestion(prompt) is not None
    assert not (tmp_path / ".config").exists()


def test_hook_ignores_an_unsafe_session_id(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    prompt = "can you look at why my login test is flaky"

    assert skill_hub.hook_suggestion(prompt, session_id="../escape") is not None
    assert not (tmp_path / ".config").exists()


def test_hook_recovers_from_a_corrupt_session_file(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    state = tmp_path / ".config" / "aletheore" / "hallbook_sessions" / "s1.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")

    assert skill_hub.hook_suggestion("can you look at why my login test is flaky", session_id="s1") is not None


def test_hook_prunes_session_files_older_than_a_week(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    old = tmp_path / ".config" / "aletheore" / "hallbook_sessions" / "old.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"suggested": []}', encoding="utf-8")
    eight_days_ago = time.time() - 8 * 24 * 60 * 60
    os.utime(old, (eight_days_ago, eight_days_ago))

    assert skill_hub.hook_suggestion("can you look at why my login test is flaky", session_id="new") is not None
    assert not old.exists()


def test_hallbook_hook_reads_the_session_id_from_the_payload(monkeypatch):
    import io
    import json
    import sys
    from aletheore import hallbook_hook

    class FakeStdin:
        buffer = io.BytesIO(json.dumps({"prompt": "a task prompt", "session_id": "s1"}).encode("utf-8"))

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    assert hallbook_hook._read_payload() == ("a task prompt", "s1")


def test_hallbook_hook_accepts_plain_text_without_a_session_id(monkeypatch):
    import io
    import sys
    from aletheore import hallbook_hook

    class FakeStdin:
        buffer = io.BytesIO(b"a plain prompt")

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    assert hallbook_hook._read_payload() == ("a plain prompt", None)


def _use_custom_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub._CUSTOM_DOC_CACHE.clear()
    skill_hub._CUSTOM_INDEX_CACHE = ((), None)
    return tmp_path


def test_a_custom_skill_body_is_searchable(monkeypatch, tmp_path):
    _use_custom_dir(monkeypatch, tmp_path)
    (tmp_path / "release.md").write_text("# Release\n\nDeploy the canary and rollback with feature flags.", encoding="utf-8")

    ids = [meta.skill_id for meta in skill_hub.search_skills("canary rollback feature flags", limit=5)]

    assert "custom/release" in ids


def test_a_custom_skill_frontmatter_title_and_description_are_searchable(monkeypatch, tmp_path):
    _use_custom_dir(monkeypatch, tmp_path)
    (tmp_path / "costs.md").write_text(
        "---\ntitle: Azure Cost Review\ndescription: find anomaly spikes in cloud spend\n---\n# Steps\n",
        encoding="utf-8",
    )

    results = skill_hub.search_skills("azure anomaly spikes", limit=5)

    assert "custom/costs" in [meta.skill_id for meta in results]
    assert next(meta for meta in results if meta.skill_id == "custom/costs").description == "find anomaly spikes in cloud spend"


def test_custom_skill_search_updates_after_an_edit_and_a_removal(monkeypatch, tmp_path):
    import os

    _use_custom_dir(monkeypatch, tmp_path)
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nzebra crossing", encoding="utf-8")
    assert "custom/notes" in [meta.skill_id for meta in skill_hub.search_skills("zebra crossing", limit=5)]

    path.write_text("# Notes\n\nunicorn pasture", encoding="utf-8")
    newer = path.stat().st_mtime + 2
    os.utime(path, (newer, newer))

    assert "custom/notes" not in [meta.skill_id for meta in skill_hub.search_skills("zebra crossing", limit=5)]
    assert "custom/notes" in [meta.skill_id for meta in skill_hub.search_skills("unicorn pasture", limit=5)]

    path.unlink()
    assert "custom/notes" not in [meta.skill_id for meta in skill_hub.search_skills("unicorn pasture", limit=5)]


def test_a_custom_skill_content_match_can_never_be_high_confidence(monkeypatch, tmp_path):
    _use_custom_dir(monkeypatch, tmp_path)
    (tmp_path / "canary.md").write_text(
        "---\ndescription: deploy canary and rollback safely with feature flags\n---\n", encoding="utf-8"
    )

    result = skill_hub.find_skill("deploy canary rollback feature flags")

    assert result["confidence"] != "high"


def test_fuzzy_and_prefix_candidates_are_sorted_for_stable_results():
    index = skill_hub._search_index(str(skill_hub.bundled_skills_dir()))
    for by_length in index.vocab_by_first.values():
        for candidates in by_length.values():
            assert candidates == sorted(candidates)
    index._ensure_prefix_tokens()
    assert all(candidates == tuple(sorted(candidates)) for candidates in index.prefix_tokens.values())


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("postgres migration", "data/database-migration"),
        ("kubernetes deployment", "devops/kubernetes-specialist"),
        ("debug a failing test", "debugging/systematic-debugging"),
        ("react performance optimization", "performance/performance-optimization"),
        ("stripe webhook handler", "platforms/stripe-integration"),
        ("terraform aws infrastructure", "infrastructure/terraform-engineer"),
        ("oauth authentication security", "backend/auth-implementation-patterns"),
        ("sql query optimization", "data/sql-optimization-patterns"),
        ("docker multi-stage build", "devops/multi-stage-dockerfile"),
        ("github actions cache", "devops/github-actions-efficiency"),
        ("write technical documentation", "documentation/documentation-writer"),
        ("security focused code review", "code-review/security-focused"),
        ("flaky e2e tests", "testing/e2e-testing-patterns"),
        ("postgres index performance", "data/postgres-pro"),
        ("database schema change rollback", "data/database-migration"),
    ],
)
def test_hand_picked_queries_keep_the_expected_skill_in_the_top_three(query, expected):
    ids = [meta.skill_id for meta in skill_hub.search_skills(query, limit=3)]
    assert expected in ids, (query, ids)


def test_hook_suggests_at_most_one_hint_per_three_prompts_in_a_session(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    first = "can you look at why my login test is flaky"
    second = "please add a stripe webhook handler for payments"
    third = "trace the user input flows and flag anything exploitable"

    assert skill_hub.hook_suggestion(first, session_id="throttled") is not None
    assert skill_hub.hook_suggestion(second, session_id="throttled") is None
    assert skill_hub.hook_suggestion(third, session_id="throttled") is None
    assert skill_hub.hook_suggestion(second, session_id="throttled") is not None

    state = tmp_path / ".config" / "aletheore" / "hallbook_sessions" / "throttled.json"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["prompts"] == 4
    assert saved["last_suggest"] == 4


def test_hook_throttle_is_per_session(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    first = "can you look at why my login test is flaky"
    second = "please add a stripe webhook handler for payments"

    assert skill_hub.hook_suggestion(first, session_id="one") is not None
    assert skill_hub.hook_suggestion(second, session_id="one") is None
    assert skill_hub.hook_suggestion(second, session_id="two") is not None
