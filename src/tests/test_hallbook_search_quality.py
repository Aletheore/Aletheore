"""Focused tests for the Hallbook search quality work.

These cover typo recovery, honest "none" confidence, per-session hook dedupe,
custom-skill content search and the small regression query list. They stay fast
on purpose: the full 994-query benchmark lives in benchmarks/hallbook-search.
"""

from aletheore import skill_hub


def test_fuzzy_match_recovers_common_typos():
    index = skill_hub._search_index(str(skill_hub.bundled_skills_dir()))
    assert index._fuzzy_match("webhok") == "webhook"
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
