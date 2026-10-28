import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.air_fixtures import minimal_air_evidence

from aletheore import watch
from aletheore.evidence_diff import new_risks
from aletheore.shield import guard_evidence, refresh, state
from aletheore.shield.guard_evidence import Finding, evidence_checks
from aletheore.tests_for import candidate_tests
from aletheore.tests_for import tests_for_report as render_tests_for

FUTURE = "2999-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"


def _mod(path, imports=(), imported_by=(), functions=()):
    return {
        "path": path,
        "imports": list(imports),
        "imported_by": list(imported_by),
        "symbols": {"functions": [{"name": n, "params": p} for n, p in functions]},
    }


def _evidence(modules, scanned_at=FUTURE):
    ev = minimal_air_evidence()
    ev["scanned_at"] = scanned_at
    ev["repository"]["modules"] = modules
    return ev


def _write_evidence(repo: Path, ev: dict) -> Path:
    path = repo / ".aletheore" / "air.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ev), encoding="utf-8")
    return path


def _core_modules(core_params="(x)"):
    return [
        _mod("app/core.py", imported_by=["app/api.py", "tests/test_core.py"], functions=[("run", core_params)]),
        _mod("app/api.py", imports=["app/core.py"], imported_by=["app/main.py"]),
        _mod("app/main.py", imports=["app/api.py"]),
        _mod("tests/test_core.py", imports=["app/core.py"]),
    ]


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "core.py").write_text("def run(x): pass\n")
    return tmp_path


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch):
    """No test may start a real child process."""
    calls = []
    monkeypatch.setattr(guard_evidence.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    return calls


# ---- candidate_tests / evidence_diff ----------------------------------------------------------------------------


def test_candidate_tests_matches_report_output(tmp_path):
    graph = {
        "a.py": ["b.py", "tests/test_a.py"],
        "b.py": ["tests/test_b.py"],
        "tests/test_a.py": [],
        "tests/test_b.py": [],
        "test_a.py": [],
    }
    ev = {"scanned_at": FUTURE, "repository": {"modules": [{"path": p, "imported_by": by} for p, by in graph.items()]}}
    high, low = candidate_tests(ev, "a.py")
    assert high == ["test_a.py", "tests/test_a.py"]
    assert low == ["tests/test_b.py"]
    report = render_tests_for(ev, tmp_path, "a.py", summary_only=True)
    assert all(p in report for p in high + low)
    assert candidate_tests(ev, "unknown.py") == ([], [])


def test_new_risks_reports_cycle_secret_and_skips_unchecked_vulnerabilities():
    old = _evidence([_mod("a.py", imports=["b.py"]), _mod("b.py")])
    new = _evidence([_mod("a.py", imports=["b.py"]), _mod("b.py", imports=["a.py"])])
    new["security"]["secrets"]["findings"] = [
        {"path": "k.py", "line": 1, "pattern": "aws", "match_preview": "AKIA...", "likely_placeholder": False},
        {"path": "p.py", "line": 2, "pattern": "aws", "match_preview": "AKIA...", "likely_placeholder": True},
    ]
    new["security"]["dependency_vulnerabilities"]["findings"] = [
        {"ecosystem": "pypi", "package": "x", "advisory_id": "A-1", "severity": "high"}
    ]
    risks = new_risks(old, new)
    assert set(risks) == {"secrets", "vulnerabilities", "layer_violations", "cycles"}
    assert [s["path"] for s in risks["secrets"]] == ["k.py"]
    assert risks["vulnerabilities"] == []  # check was off in both snapshots: toggling, not a real change
    assert len(risks["cycles"]) == 1 and set(risks["cycles"][0]["members"]) == {"a.py", "b.py"}
    # An existing cycle that merely grew is not a new cycle.
    assert new_risks(new, new)["cycles"] == []

    for ev in (old, new):
        ev["security"]["dependency_vulnerabilities"]["checked"] = True
    assert [v["advisory_id"] for v in new_risks(old, new)["vulnerabilities"]] == ["A-1"]


# ---- evidence_checks ---------------------------------------------------------------------------------------------


def test_affected_lists_dependents_and_a_test_command(repo):
    _write_evidence(repo, _evidence(_core_modules()))
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    findings, not_checked = evidence_checks(repo, ["app/core.py"], None)
    affected = [f for f in findings if f.check == "affected"]
    assert len(affected) == 1 and affected[0].severity == "info"
    msg = affected[0].message
    assert "2 file(s)" in msg and "app/api.py" in msg and "app/main.py" in msg
    assert "pytest tests/test_core.py" in msg
    assert isinstance(affected[0], Finding) and affected[0].evidence
    assert len(not_checked) == 1  # only the missing base evidence


def test_affected_without_pytest_config_lists_files(repo):
    _write_evidence(repo, _evidence(_core_modules()))
    findings, _ = evidence_checks(repo, ["app/core.py"], None)
    msg = next(f.message for f in findings if f.check == "affected")
    assert "pytest" not in msg and "tests/test_core.py" in msg


def test_affected_caps_long_lists(repo):
    mods = [_mod("app/core.py", imported_by=[f"m{i}.py" for i in range(9)])] + [_mod(f"m{i}.py") for i in range(9)]
    _write_evidence(repo, _evidence(mods))
    findings, _ = evidence_checks(repo, ["app/core.py"], None)
    msg = next(f.message for f in findings if f.check == "affected")
    assert "and 4 more" in msg and "m8.py" not in msg


def test_signature_change_uses_file_level_wording(repo):
    base = _evidence(_core_modules("(x)"))
    _write_evidence(repo, _evidence(_core_modules("(x, y)")))
    findings, not_checked = evidence_checks(repo, ["app/core.py"], base)
    sig = [f for f in findings if f.check == "signatures"]
    assert len(sig) == 1 and sig[0].severity == "warn"
    assert "run" in sig[0].message and "(x)" in sig[0].message and "(x, y)" in sig[0].message
    assert "imports the module" in sig[0].message
    assert "call" not in sig[0].message.lower()
    assert not_checked == []


def test_new_cycle_and_new_secret_are_reported(repo):
    base = _evidence([_mod("app/core.py"), _mod("app/api.py")])
    head = _evidence([_mod("app/core.py", imports=["app/api.py"], imported_by=["app/api.py"]),
                      _mod("app/api.py", imports=["app/core.py"], imported_by=["app/core.py"])])
    head["security"]["secrets"]["findings"] = [
        {"path": "app/core.py", "line": 3, "pattern": "aws", "match_preview": "AKIA...", "likely_placeholder": False}
    ]
    _write_evidence(repo, head)
    findings, _ = evidence_checks(repo, ["app/core.py"], base)
    risks = [f for f in findings if f.check == "risks"]
    text = " | ".join(f.message for f in risks)
    assert all(f.severity == "warn" for f in risks)
    assert "import cycle" in text and "app/api.py" in text
    assert "secret" in text and "app/core.py:3" in text


def test_stale_evidence_is_labelled_and_skips_dependent_checks(repo, monkeypatch):
    started = []
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda r, changed=None, scanned_at=None: started.append(1) or True)
    _write_evidence(repo, _evidence(_core_modules(), scanned_at=PAST))
    findings, not_checked = evidence_checks(repo, ["app/core.py"], _evidence(_core_modules("(a)")))
    assert findings == []  # no false "no dependents", no signature or risk claims from stale data
    assert len(not_checked) == 1 and "older than 1 changed file(s)" in not_checked[0]
    assert "refresh was started" in not_checked[0] and started == [1]


def test_stale_evidence_says_to_scan_when_no_refresh_started(repo, monkeypatch):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda r, changed=None, scanned_at=None: False)
    _write_evidence(repo, _evidence(_core_modules(), scanned_at=PAST))
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "aletheore scan" in not_checked[0] and "refresh was started" not in not_checked[0]


def test_missing_base_evidence_skips_signature_check_with_reason(repo):
    _write_evidence(repo, _evidence(_core_modules()))
    findings, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert not [f for f in findings if f.check in ("signatures", "risks")]
    assert len(not_checked) == 1 and "base evidence" in not_checked[0]


def test_no_evidence_file_gives_reason_and_creates_nothing(repo):
    findings, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert findings == [] and len(not_checked) == 1 and "aletheore scan" in not_checked[0]
    assert not (repo / ".aletheore").exists()


def test_corrupt_evidence_gives_reason(repo):
    path = _write_evidence(repo, _evidence(_core_modules()))
    path.write_text("{not json")
    findings, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert findings == [] and len(not_checked) == 1


def test_evidence_checks_never_rebuilds_inline(repo, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("rebuild must not run inline")

    monkeypatch.setattr(watch, "rebuild", boom)
    _write_evidence(repo, _evidence(_core_modules(), scanned_at=PAST))
    evidence_checks(repo, ["app/core.py"], None)  # stale: only a detached refresh may be started (Popen is stubbed)
    _write_evidence(repo, _evidence(_core_modules()))
    evidence_checks(repo, ["app/core.py"], None)


# ---- start_background_refresh ------------------------------------------------------------------------------------


def test_refresh_without_aletheore_dir_does_nothing(repo, no_spawn):
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is False
    assert not (repo / ".aletheore").exists() and no_spawn == []


def test_refresh_without_evidence_file_does_nothing(repo, no_spawn):
    (repo / ".aletheore").mkdir()
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is False
    assert no_spawn == []


def test_refresh_spawns_detached_child_once_per_minute(repo, no_spawn):
    air = _write_evidence(repo, _evidence(_core_modules()))
    old = time.time() - 100
    os.utime(air, (old, old))
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is True
    (args, kwargs), = no_spawn
    assert args[0][:3] == [sys.executable, "-m", "aletheore.shield.refresh"] and args[0][3] == str(repo)
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs["start_new_session"] is True
    # Spawn-storm guard: a second request inside 60 s starts nothing.
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is False
    assert len(no_spawn) == 1
    # A marker older than 60 s no longer blocks.
    marker = next(state.shield_dir(repo).glob("refresh*"))
    past = time.time() - 61
    os.utime(marker, (past, past))
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is True


def test_refresh_skipped_when_evidence_newer_than_every_changed_file(repo, no_spawn):
    air = _write_evidence(repo, _evidence(_core_modules()))
    older = time.time() - 500
    os.utime(repo / "app" / "core.py", (older, older))
    os.utime(air, (older + 100, older + 100))
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is False
    assert no_spawn == []


def test_refresh_spawn_failure_returns_false(repo, monkeypatch):
    _write_evidence(repo, _evidence(_core_modules()))

    def fail(*a, **k):
        raise OSError("no")

    monkeypatch.setattr(guard_evidence.subprocess, "Popen", fail)
    assert guard_evidence.start_background_refresh(repo) is False


def test_refresh_module_runs_rebuild(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(watch, "rebuild", lambda path, report, **kw: seen.append((path, kw)))
    assert refresh.main([str(repo)]) == 0
    assert seen and seen[0][0] in (repo, repo.resolve())
    assert refresh.main([]) == 2


# ---- base evidence -----------------------------------------------------------------------------------------------


def test_save_and_load_base_evidence_once_per_session(repo):
    first = _evidence(_core_modules("(x)"))
    air = _write_evidence(repo, first)
    saved = guard_evidence.save_base_evidence(repo, "sess-1")
    assert saved is not None and saved.parent == state.shield_dir(repo) / "sessions"
    air.write_text(json.dumps(_evidence(_core_modules("(x, y)"))))
    assert guard_evidence.save_base_evidence(repo, "sess-1") == saved  # not overwritten
    loaded = guard_evidence.load_base_evidence(repo, "sess-1")
    assert loaded["repository"]["modules"][0]["symbols"]["functions"][0]["params"] == "(x)"


def test_base_evidence_missing_or_corrupt_is_none(repo):
    assert guard_evidence.load_base_evidence(repo, "nope") is None
    assert guard_evidence.save_base_evidence(repo, "nope") is None  # no evidence to copy
    air = _write_evidence(repo, _evidence(_core_modules()))
    saved = guard_evidence.save_base_evidence(repo, "s2")
    saved.write_text("garbage")
    assert guard_evidence.load_base_evidence(repo, "s2") is None
    assert air.exists()


@pytest.mark.parametrize("sid", ["../../evil", "a/b", "..", "", "x\x00y", "s.base-air", "C:\\x"])
def test_hostile_session_id_never_escapes_sessions_dir(repo, sid):
    _write_evidence(repo, _evidence(_core_modules()))
    assert guard_evidence.save_base_evidence(repo, sid) is None
    assert guard_evidence.load_base_evidence(repo, sid) is None
    sessions = state.shield_dir(repo) / "sessions"
    written = [p for p in repo.rglob("*") if "base-air" in p.name]
    assert all(p.parent == sessions for p in written)


# ---- review fixes ------------------------------------------------------------------------------------------------


def _set_mtime(path: Path, epoch: float) -> None:
    os.utime(path, (epoch, epoch))


def test_file_edited_during_the_scan_counts_as_stale_but_one_well_before_does_not(repo, monkeypatch):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    scanned = time.time() - 1000
    stamp = datetime.fromtimestamp(scanned, tz=timezone.utc).isoformat()
    _write_evidence(repo, _evidence(_core_modules(), scanned_at=stamp))
    core = repo / "app" / "core.py"
    _set_mtime(core, scanned - 3)  # edited a few seconds before scanned_at: the scan may not have seen it
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]
    _set_mtime(core, scanned - guard_evidence.STALENESS_MARGIN_S - 60)
    findings, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert any(f.check == "affected" for f in findings)
    assert all("older than" not in r for r in not_checked)


def test_refresh_guard_uses_the_same_margin(repo, no_spawn):
    air = _write_evidence(repo, _evidence(_core_modules()))
    scanned = time.time() - 1000
    stamp = datetime.fromtimestamp(scanned, tz=timezone.utc).isoformat()
    core = repo / "app" / "core.py"
    _set_mtime(air, scanned + 5)
    _set_mtime(core, scanned - 3)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], stamp) is True
    marker = next(state.shield_dir(repo).glob("refresh*"))
    _set_mtime(marker, time.time() - 120)
    _set_mtime(core, scanned - guard_evidence.STALENESS_MARGIN_S - 60)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], stamp) is False


def test_refresh_marker_is_claimed_atomically(repo, no_spawn):
    _write_evidence(repo, _evidence(_core_modules()))
    marker = state.shield_dir(repo, create=True) / guard_evidence._MARKER_NAME
    assert guard_evidence._claim_marker(marker) is True
    assert guard_evidence._claim_marker(marker) is False  # a concurrent second caller loses
    _set_mtime(marker, time.time() - 120)
    assert guard_evidence._claim_marker(marker) is True  # stale marker replaced, exactly one winner
    assert guard_evidence._claim_marker(marker) is False


@pytest.mark.parametrize("rel", ["../outside.py", "/etc/passwd", "a/../../b.py", "..\\x.py", "C:\\x.py"])
def test_unsafe_changed_paths_are_ignored(repo, rel, monkeypatch):
    assert guard_evidence._safe_relative(rel) is None
    (repo.parent / "outside.py").write_text("x = 1\n")
    _write_evidence(repo, _evidence(_core_modules(), scanned_at=PAST))
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    _, not_checked = evidence_checks(repo, [rel], None)
    assert all("older than" not in r for r in not_checked)
    assert guard_evidence._safe_relative("app\\core.py") == "app/core.py"


def _ring(names):
    """Modules importing each other in a ring, starting the walk at names[0]."""
    return [_mod(n, imports=[names[(i + 1) % len(names)]]) for i, n in enumerate(names)]


def test_preexisting_cycles_beyond_the_listing_cap_are_not_reported_as_new():
    old_rings = [[f"g{g}a.py", f"g{g}b.py", f"g{g}c.py"][: 2 + (g % 2)] for g in range(8)]
    old = _evidence([m for ring in old_rings for m in _ring(ring)])
    new = _evidence([m for ring in old_rings for m in _ring(ring)] + _ring(["n1.py", "n2.py"]))
    cycles = new_risks(old, new)["cycles"]
    assert len(cycles) == 1 and set(cycles[0]["members"]) == {"n1.py", "n2.py"}


def test_rotated_cycle_start_is_not_new():
    old = _evidence(_ring(["a.py", "b.py", "c.py"]))
    new = _evidence(_ring(["b.py", "c.py", "a.py"]))
    assert new_risks(old, new)["cycles"] == []
    selfy = _evidence([_mod("s.py", imports=["s.py"])])
    assert [c["members"] for c in new_risks(_evidence([_mod("s.py")]), selfy)["cycles"]] == [["s.py"]]
    assert new_risks(selfy, selfy)["cycles"] == []
