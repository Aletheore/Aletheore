import os
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from aletheore.shield import checkpoint as cp
from aletheore.shield import deps as _deps
from aletheore.shield import guard_evidence, receipt, registry
from aletheore.shield.guard import GuardReport
from aletheore.shield.guard_evidence import Finding

T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY_CACHE_PATH", tmp_path / "reg-cache.json")
    monkeypatch.setattr(_deps, "_installed_import_names", lambda: set())
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    monkeypatch.delenv("ALETHEORE_SHIELD_OFFLINE", raising=False)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(path, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    if commit:
        (path / "base.txt").write_text("base\n", encoding="utf-8")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "init")
    return path


def _checkpoint(repo, session, minutes=0):
    made = cp.take_checkpoint(repo, session, force=True, now=T0 + timedelta(minutes=minutes))
    assert made is not None
    return made


def _empty_report():
    return GuardReport((), ())


def _fake_guard(monkeypatch, report=None):
    seen = {}

    def fake(repo, **kwargs):
        seen.update(kwargs)
        return report if report is not None else _empty_report()

    monkeypatch.setattr(receipt, "run_guard", fake)
    return seen


def test_receipt_lists_files_changed_since_first_session_checkpoint(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r")
    _checkpoint(repo, "s1", 0)
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    _checkpoint(repo, "s1", 1)
    (repo / "b.py").write_text("b\n", encoding="utf-8")
    built = receipt.build_receipt(repo, "s1", now=T0 + timedelta(minutes=2), lookups=False)
    assert built is not None
    assert built.files == ("a.py", "b.py")
    assert built.session == "s1"


def test_receipt_names_the_restore_checkpoint(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r")
    first = _checkpoint(repo, "s1", 0)
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    _checkpoint(repo, "s1", 1)
    built = receipt.build_receipt(repo, "s1", now=T0, lookups=False)
    assert built.first_checkpoint == first.ref
    text = receipt.render_markdown(built)
    assert first.ref in text
    assert "aletheore shield undo" in text


def test_idle_session_writes_nothing(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r")
    _checkpoint(repo, "other", 0)
    assert receipt.build_receipt(repo, "s1", now=T0, lookups=False) is None
    assert not (repo / ".aletheore" / "shield" / "receipts").exists()


def test_guard_runs_with_session_base_evidence_and_options(tmp_path, monkeypatch):
    seen = _fake_guard(monkeypatch)
    monkeypatch.setattr(receipt, "load_base_evidence", lambda repo, sid: {"sid": sid})
    repo = _init(tmp_path / "r")
    _checkpoint(repo, "s1", 0)
    opener = object()
    receipt.build_receipt(repo, "s1", now=T0, lookups=False, budget_s=3.0, opener=opener)
    assert seen["base"] == "HEAD"
    assert seen["lookups"] is False
    assert seen["budget_s"] == 3.0
    assert seen["base_evidence"] == {"sid": "s1"}
    assert seen["opener"] is opener


def test_markdown_is_deterministic_for_fixed_inputs():
    report = GuardReport(
        (Finding("signatures", "warn", "f() lost a parameter", "a.py:3"),),
        ("evidence unavailable",),
    )
    built = receipt.Receipt("s1", T0, ("a.py", "b.py"), report, "refs/aletheore/checkpoints/x/1")
    expected = (
        "# Shield receipt\n"
        "\n"
        "- Session: `s1`\n"
        "- Created: 2026-10-28T12:00:00Z\n"
        "- Guard verdict: 1 finding (1 warning); some checks did not run\n"
        "\n"
        "## Files touched (2)\n"
        "\n"
        "- `a.py`\n"
        "- `b.py`\n"
        "\n"
        "## Guard\n"
        "\n"
        "- warn: f() lost a parameter (a.py:3)\n"
        "- not checked: evidence unavailable\n"
        "\n"
        "## Undo\n"
        "\n"
        "To undo this session: `aletheore shield undo`\n"
        "\n"
        "Restore checkpoint: `refs/aletheore/checkpoints/x/1`\n"
    )
    assert receipt.render_markdown(built) == expected
    assert receipt.render_markdown(built) == expected


def test_clean_report_says_no_findings_only_when_nothing_skipped():
    clean = receipt.Receipt("s1", T0, (), _empty_report(), None)
    assert "No findings." in receipt.render_markdown(clean)
    skipped = receipt.Receipt("s1", T0, (), GuardReport((), ("registry lookups off",)), None)
    text = receipt.render_markdown(skipped)
    assert "No findings." not in text
    assert "not checked: registry lookups off" in text


def test_hostile_file_names_cannot_break_or_inject_markdown():
    names = ("evil\n## Injected\x1b[31m.py", "tick`name.py", "``double.py", "bidi‮exe.py")
    built = receipt.Receipt("s1", T0, names, _empty_report(), "refs/x\n# bad")
    text = receipt.render_markdown(built)
    assert "\x1b" not in text and "‮" not in text
    assert "\n## Injected" not in text
    assert "\n# bad" not in text
    for line in text.splitlines():
        assert not line.startswith("## Injected")
    assert text.count("\n## ") == 3  # Files touched, Guard, Undo


def test_files_are_capped_at_fifty_with_remainder():
    files = tuple(f"f{i:03}.py" for i in range(60))
    text = receipt.render_markdown(receipt.Receipt("s1", T0, files, _empty_report(), None))
    assert "`f049.py`" in text and "`f050.py`" not in text
    assert "and 10 more" in text


def test_invalid_session_id_falls_back_to_since_previous_receipt(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r")
    _checkpoint(repo, "old", 0)
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    old = receipt.build_receipt(repo, "old", now=T0 + timedelta(minutes=1), lookups=False)
    receipt.write_receipt(repo, old)
    later = _checkpoint(repo, "", 5)
    (repo / "b.py").write_text("b\n", encoding="utf-8")
    _checkpoint(repo, "", 6)
    built = receipt.build_receipt(repo, "", now=T0 + timedelta(minutes=7), lookups=False)
    assert built is not None
    assert built.first_checkpoint == later.ref
    assert "session id unavailable: covers changes since the previous receipt" in receipt.render_markdown(built)
    assert receipt.build_receipt(repo, "bad/id", now=T0, lookups=False).first_checkpoint == later.ref


def test_fallback_without_any_new_checkpoint_returns_none(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r")
    assert receipt.build_receipt(repo, "", now=T0, lookups=False) is None


def test_git_failure_yields_could_not_compute_note(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r")
    _checkpoint(repo, "s1", 0)

    def boom(*a, **k):
        raise cp.CheckpointError("git exploded")

    monkeypatch.setattr(receipt, "changed_since", boom)
    built = receipt.build_receipt(repo, "s1", now=T0, lookups=False)
    assert built is not None and built.files == ()
    assert any("could not compute" in n for n in built.report.not_checked)
    monkeypatch.setattr(receipt, "list_checkpoints", boom)
    assert receipt.build_receipt(repo, "s1", now=T0, lookups=False) is None


def test_works_when_repo_has_no_commits(tmp_path, monkeypatch):
    _fake_guard(monkeypatch)
    repo = _init(tmp_path / "r", commit=False)
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    made = cp.take_checkpoint(repo, "s1", force=True, now=T0)
    if made is None:
        assert receipt.build_receipt(repo, "s1", now=T0, lookups=False) is None
        return
    built = receipt.build_receipt(repo, "s1", now=T0, lookups=False)
    assert built is not None and built.first_checkpoint == made.ref


def test_non_git_folder_returns_none(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert receipt.build_receipt(folder, "s1", now=T0, lookups=False) is None


def test_write_receipt_path_and_atomic(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    built = receipt.Receipt("s1", T0, ("a.py",), _empty_report(), "ref")
    path = receipt.write_receipt(repo, built)
    assert path == repo / ".aletheore" / "shield" / "receipts" / "20261028T120000Z-s1.md"
    assert path.read_text(encoding="utf-8") == receipt.render_markdown(built)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_write_receipt_uses_unknown_for_invalid_session(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    path = receipt.write_receipt(repo, receipt.Receipt("../evil", T0, (), _empty_report(), None))
    assert path.name == "20261028T120000Z-unknown.md"
    assert path.parent.name == "receipts"


def test_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    built = receipt.Receipt("s1", T0, (), _empty_report(), None)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        receipt.write_receipt(repo, built)
    assert list((repo / ".aletheore" / "shield" / "receipts").iterdir()) == []


def test_latest_receipt_returns_newest(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    assert receipt.latest_receipt(repo) is None
    older = receipt.write_receipt(repo, receipt.Receipt("z", T0, (), _empty_report(), None))
    newer = receipt.write_receipt(
        repo, receipt.Receipt("a", T0 + timedelta(hours=1), (), _empty_report(), None)
    )
    os.utime(newer, (1, 1))  # file mtime must not matter
    assert receipt.latest_receipt(repo) == newer
    assert older != newer


def test_retention_removes_old_receipts(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    now = T0 + timedelta(days=100)
    for i in range(60):  # 60 receipts, all older than 7 days
        receipt.write_receipt(
            repo, receipt.Receipt(f"s{i:02}", T0 + timedelta(minutes=i), (), _empty_report(), None)
        )
    directory = repo / ".aletheore" / "shield" / "receipts"
    assert len(list(directory.iterdir())) == 60  # each recent at write time, so all kept
    receipt.write_receipt(repo, receipt.Receipt("fresh", now, (), _empty_report(), None))
    names = sorted(p.name for p in directory.iterdir())
    assert len(names) == 50  # the newest 50 (fresh + 49 newest old ones); none of the old ones is young
    assert any(n.endswith("-fresh.md") for n in names)
    assert not any(n.endswith("-s00.md") for n in names)
    assert any(n.endswith("-s59.md") for n in names)


def test_retention_keeps_receipts_younger_than_seven_days(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    for i in range(55):
        receipt.write_receipt(
            repo, receipt.Receipt(f"s{i:02}", T0 + timedelta(minutes=i), (), _empty_report(), None)
        )
    assert len(list((repo / ".aletheore" / "shield" / "receipts").iterdir())) == 55
