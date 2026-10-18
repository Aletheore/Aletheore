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
    assert (tmp_path / "my-review.md.bak").read_text(encoding="utf-8") == "first version"


def test_add_custom_skill_keeps_only_the_most_recent_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "v1")
    skill_hub.add_custom_skill("my-review", "v2")
    skill_hub.add_custom_skill("my-review", "v3")

    assert (tmp_path / "my-review.md.bak").read_text(encoding="utf-8") == "v2"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["my-review.md", "my-review.md.bak"]


def test_add_custom_skill_first_add_writes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "v1")

    assert not (tmp_path / "my-review.md.bak").exists()


def test_add_custom_skill_identical_content_does_not_clobber_the_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_hub, "custom_skills_dir", lambda: tmp_path)
    skill_hub.add_custom_skill("my-review", "v1")
    skill_hub.add_custom_skill("my-review", "v2")
    skill_hub.add_custom_skill("my-review", "v2")  # re-adding the same content

    assert (tmp_path / "my-review.md.bak").read_text(encoding="utf-8") == "v1"


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
    skill_hub.add_custom_skill("my-review", "v2")  # creates my-review.md.bak with "v1"
    skill_hub.remove_custom_skill("my-review")
    assert (tmp_path / "my-review.md.bak").read_text(encoding="utf-8") == "v1"


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
        ("review a pull request for security", "security/security-review"),
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
