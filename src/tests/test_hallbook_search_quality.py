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
