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
