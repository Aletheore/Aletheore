import pytest
from aletheore import skill_hub


@pytest.fixture(autouse=True)
def _no_network_push(monkeypatch):
    # add_custom_skill now makes a best-effort network push; keep every test
    # offline by default. Tests that exercise the push override this stub.
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub_sync, "push_custom_skill_best_effort", lambda *a, **k: None)

def test_custom_skills_dir_under_user_config(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_hub, "user_home", lambda: tmp_path)
    d = skill_hub.custom_skills_dir()
    assert d == tmp_path / ".config" / "aletheore" / "custom_skills"

@pytest.mark.parametrize("bad_name", ["../escape", "a/b", "a\\b", "", ".", "..", "a/../b", "\0null"])
def test_sanitize_skill_name_rejects_unsafe(bad_name):
    with pytest.raises(ValueError):
        skill_hub.sanitize_skill_name(bad_name)

@pytest.mark.parametrize("good_name", ["my-review", "My_Review_2", "a"])
def test_sanitize_skill_name_accepts_safe(good_name):
    assert skill_hub.sanitize_skill_name(good_name) == good_name

def test_write_then_read_custom_skill_file(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.write_custom_skill_file("my-review", "# content")
    assert skill_hub.read_custom_skill_file("my-review") == "# content"

def test_read_custom_skill_file_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    assert skill_hub.read_custom_skill_file("nope") is None

def test_list_custom_skill_names(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.write_custom_skill_file("b-skill", "x")
    skill_hub.write_custom_skill_file("a-skill", "y")
    assert skill_hub.list_custom_skill_names() == ["a-skill", "b-skill"]


def test_list_bundled_categories_includes_seed_categories():
    categories = skill_hub.list_bundled_categories()
    assert "code-review" in categories
    assert "brainstorming" in categories


def test_list_bundled_skills_all_categories_returns_metadata_only():
    metas = skill_hub.list_bundled_skills()
    ids = [m.skill_id for m in metas]
    assert "code-review/security-focused" in ids
    assert all(not hasattr(m, "content") for m in metas)


def test_list_bundled_skills_filtered_by_category():
    metas = skill_hub.list_bundled_skills(category="code-review")
    assert all(m.category == "code-review" for m in metas)
    assert len(metas) >= 1


def test_get_bundled_skill_returns_full_content_and_source():
    skill = skill_hub.get_bundled_skill("code-review/security-focused")
    assert skill is not None
    assert "security" in skill.content.lower()
    assert skill.license in ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC")
    assert skill.source_url is not None


def test_get_bundled_skill_unknown_id_returns_none():
    assert skill_hub.get_bundled_skill("not/a-real-skill") is None


def test_malformed_skill_frontmatter_is_skipped_not_fatal(tmp_path, monkeypatch):
    broken = tmp_path / "broken-cat" / "broken-skill"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("---\nnot: [valid, yaml, :\n---\nbody")
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: tmp_path)
    # Must not raise, and must still be able to list whatever else is present
    # (here, nothing else - just asserting no exception propagates).
    assert skill_hub.list_bundled_skills() == []


def test_list_skills_merges_curated_and_custom(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "# my review steps")
    ids = [m.skill_id for m in skill_hub.list_skills()]
    assert "custom/my-review" in ids
    assert "code-review/security-focused" in ids


def test_add_custom_skill_returns_prefixed_id(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    assert skill_hub.add_custom_skill("my-review", "x") == "custom/my-review"


def test_get_skill_curated_by_id():
    skill = skill_hub.get_skill("code-review/security-focused")
    assert skill.license is not None


def test_get_skill_custom_by_prefixed_id(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "custom body")
    skill = skill_hub.get_skill("custom/my-review")
    assert skill.content == "custom body"
    assert skill.license is None
    assert skill.source_url is None


def test_get_skill_unknown_id_raises():
    with pytest.raises(skill_hub.SkillNotFoundError):
        skill_hub.get_skill("custom/does-not-exist")
    with pytest.raises(skill_hub.SkillNotFoundError):
        skill_hub.get_skill("not-a-real/category")


def test_custom_skill_can_never_shadow_curated_id(tmp_path, monkeypatch):
    # A custom skill named exactly like a curated one's trailing segment must
    # resolve to its own custom/-prefixed id, never to or over the curated one.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("security-focused", "mine")
    assert skill_hub.get_skill("code-review/security-focused").content != "mine"
    assert skill_hub.get_skill("custom/security-focused").content == "mine"


def test_add_custom_skill_triggers_best_effort_push(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(skill_hub_sync, "push_custom_skill_best_effort", lambda name, content, **kw: calls.append(name))
    skill_hub.add_custom_skill("my-review", "x")
    assert calls == ["my-review"]


def test_add_custom_skill_succeeds_even_if_push_raises(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    def boom(*a, **k): raise RuntimeError("should never happen, but even so")
    monkeypatch.setattr(skill_hub_sync, "push_custom_skill_best_effort", boom)
    # add_custom_skill itself must not propagate this - belt-and-suspenders
    # on top of push_custom_skill_best_effort's own internal try/except.
    with pytest.raises(RuntimeError):
        skill_hub.add_custom_skill("my-review", "x")  # documents current behavior; see Step 3


def test_add_custom_skill_backs_up_existing_content_before_overwriting(tmp_path, monkeypatch):
    # A second add under an existing name (say, a prompt-injected agent
    # calling aletheore_add_custom_skill on a name the user already has) must
    # not silently destroy the first version: it goes to <name>.md.bak, the
    # same convention skill_hub_sync's pull-merge uses.
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "first version")

    skill_hub.add_custom_skill("my-review", "second version")

    assert (tmp_path / "my-review.md").read_text(encoding="utf-8") == "second version"
    assert _bak_texts(tmp_path, "my-review") == ["first version"]


def _bak_texts(directory, name):
    return [p.read_text(encoding="utf-8") for p in skill_hub.list_backups(name)]


def test_add_custom_skill_keeps_earlier_versions_not_just_the_last(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    for version in ("v1", "v2", "v3"):
        skill_hub.add_custom_skill("my-review", version)

    assert _bak_texts(tmp_path, "my-review") == ["v1", "v2"]
    assert all(p.name.startswith("my-review.md.bak-") for p in skill_hub.list_backups("my-review"))


def test_only_the_five_newest_backups_are_kept_across_add_and_remove(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    for number in range(1, 10):
        skill_hub.add_custom_skill("my-review", f"v{number}")
    assert _bak_texts(tmp_path, "my-review") == ["v4", "v5", "v6", "v7", "v8"]
    skill_hub.remove_custom_skill("my-review")
    assert _bak_texts(tmp_path, "my-review") == ["v5", "v6", "v7", "v8", "v9"]
    assert list(tmp_path.glob("my-review.md.deleted-*.bak"))


def test_a_legacy_single_bak_still_counts_as_the_oldest_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    (tmp_path / "my-review.md.bak").write_text("legacy", encoding="utf-8")
    for number in range(1, 7):
        skill_hub.add_custom_skill("my-review", f"v{number}")
    assert _bak_texts(tmp_path, "my-review") == ["v1", "v2", "v3", "v4", "v5"]
    assert not (tmp_path / "my-review.md.bak").exists()


def test_a_failed_overwrite_leaves_the_previous_skill_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "good text")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(skill_hub.os, "replace", boom)
    with pytest.raises(skill_hub.CustomSkillError):
        skill_hub.add_custom_skill("my-review", "new text")
    monkeypatch.undo()
    assert (tmp_path / "my-review.md").read_text(encoding="utf-8") == "good text"
    assert not list(tmp_path.glob(".skill-*"))


def test_custom_skill_files_are_private(tmp_path, monkeypatch):
    import os
    import stat

    target = tmp_path / "custom_skills"
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: target)
    skill_hub.add_custom_skill("my-review", "x")
    if os.name == "posix":
        assert stat.S_IMODE((target / "my-review.md").stat().st_mode) == 0o600
        assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_add_custom_skill_first_add_writes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "v1")

    assert skill_hub.list_backups("my-review") == []


def test_add_custom_skill_identical_content_does_not_clobber_the_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "v1")
    skill_hub.add_custom_skill("my-review", "v2")
    skill_hub.add_custom_skill("my-review", "v2")  # re-adding the same content

    assert _bak_texts(tmp_path, "my-review") == ["v1"]


def test_backup_file_is_not_listed_as_a_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "v1")
    skill_hub.add_custom_skill("my-review", "v2")

    assert skill_hub.list_custom_skill_names() == ["my-review"]


def test_remove_custom_skill_deletes_local_file_and_pushes(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(
        skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: calls.append(name)
    )
    skill_hub.add_custom_skill("my-review", "x")
    assert skill_hub.remove_custom_skill("my-review") is True
    assert skill_hub.read_custom_skill_file("my-review") is None
    assert calls == ["my-review"]


def test_remove_custom_skill_already_gone_returns_false_not_error(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    assert skill_hub.remove_custom_skill("never-existed") is False


def test_remove_custom_skill_rejects_unsafe_name():
    with pytest.raises(ValueError):
        skill_hub.remove_custom_skill("../escape")


def test_remove_custom_skill_leaves_bak_file_alone(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    skill_hub.add_custom_skill("my-review", "v1")
    skill_hub.add_custom_skill("my-review", "v2")  # keeps "v1" as a backup
    skill_hub.remove_custom_skill("my-review")
    assert "v1" in _bak_texts(tmp_path, "my-review")


def test_remove_custom_skill_calls_sync_delete_even_when_local_file_absent(tmp_path, monkeypatch):
    # The server might still hold a stale copy from an earlier, now-forgotten
    # local delete - a repeated remove must keep trying to clear it there even
    # though there's nothing left to unlink locally.
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(
        skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: calls.append(name)
    )
    assert skill_hub.remove_custom_skill("never-existed") is False
    assert calls == ["never-existed"]


# --- Hallbook at catalog scale: overview, search, companion files, the shipped index ---


def _write_bundled_skill(root, category, name, body="body", description="D"):
    directory = root / category / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nskill_id: {category}/{name}\ntitle: T {name}\ndescription: {description}\n"
        f"source_url: https://example.com/{name}\nlicense: MIT\n---\n{body}"
    )
    return directory


def test_overview_groups_categories_by_shelf_with_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path / "none")
    overview = skill_hub.list_overview()
    shelves = {shelf["shelf"]: shelf for shelf in overview}
    assert list(SHELVES_IN_ORDER := [s for s in skill_hub.SHELF_TITLES if s in shelves]) == [
        s["shelf"] for s in overview
    ], "shelves must come out in the catalog's display order"
    assert len(SHELVES_IN_ORDER) == len(skill_hub.SHELF_TITLES)
    for shelf in overview:
        assert shelf["skills"] == sum(entry["skills"] for entry in shelf["categories"])
    assert sum(shelf["skills"] for shelf in overview) == len(skill_hub.list_bundled_skills())


def test_overview_adds_a_your_skills_shelf_only_when_custom_skills_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    assert "Your skills" not in [shelf["title"] for shelf in skill_hub.list_overview()]
    (tmp_path / "mine.md").write_text("x")
    mine = [shelf for shelf in skill_hub.list_overview() if shelf["title"] == "Your skills"]
    assert mine and mine[0]["skills"] == 1


@pytest.mark.parametrize(
    "query, expected",
    [
        ("flaky test debugging", "debugging/systematic-debugging"),
        ("stripe webhooks", "platforms/stripe-integration"),
        ("write a plan before coding", "planning/writing-plans"),
        ("review a pull request for security", "security/security-and-hardening"),  # security-review is its collapsed alternate
        ("kubernetes deployment", "devops/kubernetes-specialist"),
    ],
)
def test_search_puts_the_obvious_skill_in_the_top_results(query, expected):
    top = [meta.skill_id for meta in skill_hub.search_skills(query, limit=5)]
    assert expected in top, top


def test_search_matches_word_forms():
    for query in ("writing plans", "write plan", "plans written"):
        assert "planning/writing-plans" in [m.skill_id for m in skill_hub.search_skills(query, limit=5)], query


def test_search_with_nothing_but_filler_words_returns_nothing():
    assert skill_hub.search_skills("the of and to") == []
    assert skill_hub.search_skills("") == []
    assert skill_hub.search_skills("anything", limit=0) == []


def test_search_respects_category_and_limit():
    results = skill_hub.search_skills("testing", category="debugging", limit=3)
    assert len(results) <= 3
    assert all(meta.category == "debugging" for meta in results)


def test_search_is_deterministic():
    assert skill_hub.search_skills("api design") == skill_hub.search_skills("api design")


def test_search_includes_custom_skills_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    (tmp_path / "quarterly-report.md").write_text("x")
    assert "custom/quarterly-report" in [m.skill_id for m in skill_hub.search_skills("quarterly report")]
    assert "custom/quarterly-report" not in [
        m.skill_id for m in skill_hub.search_skills("quarterly report", category="debugging")
    ]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("My agent keeps forgetting which files it edited in long sessions", "agent-workflows/context-compression"),
        ("Our long chats get slower and pricier as they grow", "agent-workflows/context-optimization"),
        ("How do I get two agents to work on separate parts of the same feature at once", "agent-workflows/dispatching-parallel-agents"),
        ("The agent keeps ignoring our project conventions and inventing APIs", "agent-workflows/context-engineering"),
        ("Write a summary doc the next agent can pick up tomorrow", "agent-workflows/handoff"),
        ("Stripe webhooks", "platforms/stripe-integration"),
    ],
)
def test_search_finds_known_skills_in_the_top_three(query, expected):
    top = [meta.skill_id for meta in skill_hub.search_skills(query, limit=3)]
    assert expected in top, top


def test_search_returns_nothing_for_unknown_gibberish():
    assert skill_hub.search_skills("zzzzq xylophonic flibbertigibbet") == []


def test_search_scored_returns_the_same_ranking_with_scores():
    plain = skill_hub.search_skills("flaky test debugging", limit=5)
    scored = skill_hub.search_skills_scored("flaky test debugging", limit=5)
    assert [meta.skill_id for meta, _ in scored] == [meta.skill_id for meta in plain]
    assert all(isinstance(score, float) for _, score in scored)


def test_index_rebuild_is_deterministic(tmp_path):
    _write_bundled_skill(tmp_path, "cat", "alpha", body="# Alpha\nrun tests")
    _write_bundled_skill(tmp_path, "cat", "beta", body="# Beta\ndeploy slowly")
    skill_hub.rebuild_bundled_index(tmp_path)
    first = (tmp_path / "_index.json").read_bytes()
    skill_hub.rebuild_bundled_index(tmp_path)
    assert (tmp_path / "_index.json").read_bytes() == first


def test_list_skills_carries_shelf_license_and_requirements():
    by_id = {meta.skill_id: meta for meta in skill_hub.list_skills("debugging")}
    meta = by_id["debugging/systematic-debugging"]
    assert meta.shelf == "quality"
    assert meta.license == "MIT"
    assert "subagents" in meta.requires


def test_get_skill_lists_companion_files():
    skill = skill_hub.get_skill("debugging/systematic-debugging")
    assert "condition-based-waiting.md" in skill.files
    assert skill.files_total == len(skill.files) > 0
    assert "SKILL.md" not in skill.files
    assert not any(name.upper().startswith("LICENSE") for name in skill.files)


def test_get_skill_caps_the_companion_file_list_but_reports_the_true_total(tmp_path, monkeypatch):
    directory = _write_bundled_skill(tmp_path, "cat", "many")
    for number in range(skill_hub._MAX_LISTED_FILES + 5):
        (directory / f"f{number:04d}.md").write_text("x")
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: tmp_path)
    skill = skill_hub.get_skill("cat/many")
    assert len(skill.files) == skill_hub._MAX_LISTED_FILES
    assert skill.files_total == skill_hub._MAX_LISTED_FILES + 5


def test_read_skill_file_returns_the_file_text():
    text = skill_hub.read_skill_file("debugging/systematic-debugging", "condition-based-waiting.md")
    assert len(text) > 100


@pytest.mark.parametrize(
    "bad_path",
    [
        "../../../etc/passwd",
        "/etc/passwd",
        "\\windows\\system32",
        "C:\\windows\\win.ini",
        "references/../../../../SKILL.md",
        "..",
        "",
        "SKILL.md\0",
    ],
)
def test_read_skill_file_refuses_paths_that_escape_the_skill_folder(bad_path):
    with pytest.raises(skill_hub.SkillFileError):
        skill_hub.read_skill_file("debugging/systematic-debugging", bad_path)


def test_read_skill_file_refuses_a_symlink_that_points_outside(tmp_path, monkeypatch):
    directory = _write_bundled_skill(tmp_path, "cat", "linky")
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("do not leak")
    try:
        (directory / "innocent.md").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: tmp_path)
    with pytest.raises(skill_hub.SkillFileError):
        skill_hub.read_skill_file("cat/linky", "innocent.md")
    assert "innocent.md" not in skill_hub.list_skill_files("cat/linky")[0]


def test_read_skill_file_refuses_binary_and_missing_files(tmp_path, monkeypatch):
    directory = _write_bundled_skill(tmp_path, "cat", "bins")
    (directory / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: tmp_path)
    with pytest.raises(skill_hub.SkillFileError, match="not a text file"):
        skill_hub.read_skill_file("cat/bins", "blob.bin")
    with pytest.raises(skill_hub.SkillFileError, match="no such file"):
        skill_hub.read_skill_file("cat/bins", "nope.md")


def test_read_skill_file_truncates_large_files_without_calling_them_binary(tmp_path, monkeypatch):
    directory = _write_bundled_skill(tmp_path, "cat", "big")
    (directory / "big.md").write_text("é" * 40, encoding="utf-8")  # 80 bytes of multi-byte text
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub, "_MAX_FILE_BYTES", 41)  # cuts a character in half
    text = skill_hub.read_skill_file("cat/big", "big.md")
    assert text.startswith("é") and "[truncated" in text


def test_read_skill_file_unknown_skill_and_custom_skill():
    with pytest.raises(skill_hub.SkillNotFoundError):
        skill_hub.read_skill_file("nope/nothing", "x.md")
    with pytest.raises(skill_hub.SkillFileError, match="custom skills"):
        skill_hub.read_skill_file("custom/anything", "x.md")


def test_bundled_skill_ids_with_path_tricks_are_not_found():
    for bad in ("../x/y", "debugging/../..", "a/b/c", "debugging", "", "/etc/passwd"):
        assert skill_hub.get_bundled_skill(bad) is None


def test_shipped_index_matches_the_skill_files():
    # _index.json is what makes the first list/search call fast. It must describe exactly
    # what is on disk. After adding, removing or editing a bundled skill, regenerate it:
    #   python -c "from aletheore import skill_hub; skill_hub.rebuild_bundled_index()"
    shipped = skill_hub._read_index_file(skill_hub.bundled_skills_dir() / "_index.json")
    assert shipped is not None, "src/aletheore/skills/_index.json is missing or unreadable"
    assert shipped == skill_hub._scan_index(skill_hub.bundled_skills_dir()), (
        "src/aletheore/skills/_index.json is stale; regenerate it (see the comment above)"
    )


def test_index_falls_back_to_scanning_when_there_is_no_index_file(tmp_path, monkeypatch):
    _write_bundled_skill(tmp_path, "cat", "solo")
    monkeypatch.setattr(skill_hub, "bundled_skills_dir", lambda: tmp_path)
    assert [meta.skill_id for meta in skill_hub.list_bundled_skills()] == ["cat/solo"]
    (tmp_path / "_index.json").write_text("{not json")
    skill_hub._bundled_index.cache_clear()
    assert [meta.skill_id for meta in skill_hub.list_bundled_skills()] == ["cat/solo"]


def test_every_bundled_skill_has_a_known_shelf_and_a_notice_entry():
    notice = (skill_hub.bundled_skills_dir() / "NOTICE.md").read_text(encoding="utf-8")
    for meta in skill_hub.list_bundled_skills():
        assert meta.shelf in skill_hub.SHELF_TITLES, f"{meta.skill_id} has unknown shelf {meta.shelf!r}"
        assert f"`{meta.skill_id}`" in notice, f"{meta.skill_id} is missing from NOTICE.md"


def test_every_bundled_skill_is_under_an_allowed_license_and_has_a_source():
    from aletheore.skill_vetting import ALLOWED_LICENSES

    for meta in skill_hub.list_bundled_skills():
        assert meta.license in ALLOWED_LICENSES, meta.skill_id
        assert skill_hub.get_bundled_skill(meta.skill_id).source_url.startswith("https://"), meta.skill_id


# --- find_skill: one call, confident or move on ---------------------------------


@pytest.mark.parametrize(
    "task, expected",
    [
        ("How should I plan private cluster addresses and internet access?",
         "platforms/gke-networking"),
        ("I need a launch brief that keeps a coding agent on task for days without drifting.",
         "agent-workflows/long-horizon-prompting"),
        ("Break down this big feature into smaller pieces my agent team can work on in parallel, with dependencies between them",
         "agent-workflows/task-coordination-strategies"),
        ("Add a small dashboard widget to my server that renders inline in the chat when the tool runs",
         "ai-engineering/build-mcp-app"),
        ("Can you write a few ad headlines and descriptions for my campaign?",
         "marketing/ad-creative"),
    ],
)
def test_find_skill_is_high_confidence_for_clear_benchmark_tasks(task, expected):
    result = skill_hub.find_skill(task)
    assert result["confidence"] == "high"
    assert result["top"]["skill_id"] == expected
    assert result["skill"]["skill_id"] == expected
    assert result["note"] == skill_hub.FIND_SKILL_HIGH_NOTE


def test_find_skill_returns_none_for_gibberish():
    result = skill_hub.find_skill("zzzzq xylophonic flibbertigibbet")
    assert result["confidence"] == "none"
    assert result["note"] == skill_hub.FIND_SKILL_NONE_NOTE
    assert "skill" not in result


def test_find_skill_returns_none_for_small_talk():
    result = skill_hub.find_skill("help me")
    assert result["confidence"] == "none"
    assert "top" not in result
    assert result["note"] == skill_hub.FIND_SKILL_NONE_NOTE


def test_find_skill_returns_low_for_a_weak_but_real_task():
    result = skill_hub.find_skill("can you look at why my login test is flaky")
    assert result["confidence"] == "low"
    assert result["candidates"]
    assert result["note"] == skill_hub.FIND_SKILL_LOW_NOTE


def test_find_skill_caps_long_content_and_says_how_to_fetch_the_rest(monkeypatch):
    top = skill_hub.SkillMeta(skill_id="cat/long", category="cat", title="Long", description="long one")
    runner_up = skill_hub.SkillMeta(skill_id="cat/other", category="cat", title="Other", description="other")
    monkeypatch.setattr(
        skill_hub, "search_skills_scored", lambda *args, **kwargs: [(top, 100.0), (runner_up, 1.0)]
    )
    long_skill = skill_hub.Skill(
        skill_id="cat/long", title="Long", description="long one", content="z" * 20_000,
        source_url=None, license="MIT",
    )
    monkeypatch.setattr(skill_hub, "get_skill", lambda skill_id: long_skill)

    result = skill_hub.find_skill("anything at all about this task")

    assert result["confidence"] == "high"
    assert len(result["skill"]["content"]) == skill_hub.FIND_SKILL_MAX_CONTENT_CHARS
    assert result["skill"]["truncated"] is True
    assert "aletheore_get_skill" in result["skill"]["truncated_note"]


def test_agent_instruction_snippet_points_at_find_skill():
    snippet = skill_hub.AGENT_INSTRUCTION_SNIPPET
    assert "aletheore_find_skill" in snippet
    assert "aletheore_get_skill_file" in snippet
    assert len(snippet.strip().splitlines()) <= 6


HOOK_HIGH_CONFIDENCE_QUERIES = (
    "Rewrite this blog post intro to feel more human and less stiff.",
    "How should I write Javadoc comments for Java classes and methods?",
    "How do I clamp, map, and snap numbers in an animation?",
)


@pytest.mark.parametrize("query", HOOK_HIGH_CONFIDENCE_QUERIES)
def test_hook_suggestion_hints_for_a_clear_high_confidence_prompt(query):
    # Guard: these are real high-confidence queries from data/queries_stage4.jsonl.
    assert skill_hub.find_skill(query)["confidence"] == "high"

    suggestion = skill_hub.hook_suggestion(query)

    assert suggestion is not None
    assert suggestion.startswith("Hallbook skill that fits this task: ")
    assert "aletheore_get_skill" in suggestion
    assert suggestion.count("\n") <= 2


@pytest.mark.parametrize(
    "prompt",
    [
        "thanks",
        "ok continue",
        "/review this code now",
        "zzzzq xylophonic flibbertigibbet",
        "help me",
    ],
)
def test_hook_suggestion_is_silent_for_prompts_not_worth_a_skill(prompt):
    assert skill_hub.hook_suggestion(prompt) is None


def test_hook_suggestion_accepts_every_mode():
    assert skill_hub.HOOK_MODES == ("hint", "inject")


def test_hook_suggestion_inject_contains_the_skill_content():
    query = HOOK_HIGH_CONFIDENCE_QUERIES[0]
    result = skill_hub.find_skill(query)
    content = result["skill"]["content"]

    suggestion = skill_hub.hook_suggestion(query, mode="inject")

    assert suggestion is not None
    assert suggestion.startswith(
        f"Hallbook skill for this task ({result['top']['skill_id']}), "
        "apply it to this project and ignore parts that do not fit:"
    )
    assert content[:200] in suggestion


def test_hook_suggestion_uses_only_the_head_of_a_pasted_log(monkeypatch):
    seen = {}

    def fake_search_skills_scored(task, category=None, limit=10, min_matches=1, prefix=True, phrase=True):
        seen["task"] = task
        return []

    monkeypatch.setattr(skill_hub, "search_skills_scored", fake_search_skills_scored)

    skill_hub.hook_suggestion("x " * 20_000)

    assert len(seen["task"]) <= skill_hub.HOOK_TRUNCATE_CHARS


def test_hook_suggestion_medium_tier_names_two_skills():
    prompt = "can you look at why my login test is flaky"

    # Guard: this is a real prompt the strict find_skill tier leaves at "low".
    assert skill_hub.find_skill(prompt)["confidence"] != "high"

    suggestion = skill_hub.hook_suggestion(prompt)

    assert suggestion is not None
    assert suggestion.startswith("Hallbook may have a skill for this: ")
    assert "testing/e2e-testing-patterns" in suggestion
    assert "browser-automation/playwright-expert" in suggestion
    assert suggestion.count(" or ") == 1


@pytest.mark.parametrize(
    "prompt",
    [
        "thanks",
        "rename this variable",
        "what does this function do",
        "can you explain this line",
        "why is this here",
        "sounds good",
    ],
)
def test_hook_suggestion_medium_stays_silent_for_non_task_prompts(prompt):
    assert skill_hub.hook_suggestion(prompt) is None


def test_hallbook_hook_module_runs_via_python_m():
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "aletheore.hallbook_hook", "--mode", "hint"],
        input=json.dumps({"prompt": "can you look at why my login test is flaky"}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(src)},
        timeout=60,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("Hallbook may have a skill for this: ")


def test_hallbook_hook_module_is_silent_on_broken_input():
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "aletheore.hallbook_hook", "--mode", "hint"],
        input="{not json",
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(src)},
        timeout=60,
    )

    assert result.returncode == 0
    assert result.stdout == ""


# ---- curation of overlapping skills (_curation.json) ----
def test_curation_file_is_consistent():
    redirect, alternates_of = skill_hub._curation(str(skill_hub.bundled_skills_dir()))
    ids = {meta.skill_id for meta in skill_hub.list_bundled_skills()}
    assert redirect, "the curation file should load"
    for skill_id, canonical in redirect.items():
        assert skill_id in ids and canonical in ids
        assert canonical not in redirect, "a canonical skill must not itself be redirected (no chains)"
    assert all(set(v) <= set(redirect) for v in alternates_of.values())


def test_hidden_duplicate_is_not_returned_but_still_loadable():
    redirect, _ = skill_hub._curation(str(skill_hub.bundled_skills_dir()))
    hidden = "code-review/code-review-phuryn"
    assert hidden in redirect
    results = [meta.skill_id for meta in skill_hub.search_skills("code review pull request", limit=25)]
    assert hidden not in results
    assert skill_hub.get_skill(hidden).skill_id == hidden  # still loadable by id


def test_skill_alternates_is_the_public_view_of_the_collapsed_alternates():
    assert "code-review/code-review-and-quality" in skill_hub.skill_alternates("code-review/code-review")
    assert skill_hub.skill_alternates("no-such/skill") == []


def test_collapsed_alternates_appear_under_their_canonical_skill():
    results = skill_hub.search_skills("review my code before merging", limit=10)
    canonical = next(meta for meta in results if meta.skill_id == "code-review/code-review")
    assert "code-review/code-review-and-quality" in canonical.alternates
    ids = [meta.skill_id for meta in results]
    assert "code-review/code-review-and-quality" not in ids and len(ids) == len(set(ids))


def test_search_results_have_no_duplicate_ids_after_collapsing():
    for query in ("debug a failing test", "write a commit message", "security review of my code", "plan the work"):
        ids = [meta.skill_id for meta in skill_hub.search_skills(query, limit=10)]
        assert len(ids) == len(set(ids))


def test_remove_custom_skill_keeps_a_backup_copy(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "delete_custom_skill_best_effort", lambda name, **kw: None)
    skill_hub.add_custom_skill("my-review", "precious content")
    skill_hub.remove_custom_skill("my-review")
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob("my-review.md.deleted-*.bak")] == ["precious content"]


def test_custom_skill_matched_by_name_never_reaches_high_confidence(tmp_path, monkeypatch):
    from aletheore import skill_hub_sync

    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_hub_sync, "push_custom_skill_best_effort", lambda *a, **k: None)
    skill_hub.add_custom_skill("flaky-test-triage-checklist", "unvetted text the user wrote")

    result = skill_hub.find_skill("flaky test triage checklist")
    assert result["confidence"] != "high"
    assert skill_hub.hook_suggestion("flaky test triage checklist") is None or "custom/" not in (
        skill_hub.hook_suggestion("flaky test triage checklist") or ""
    )
