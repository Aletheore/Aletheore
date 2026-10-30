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
    # The evidence was not rewritten since that refresh started, so it counts as running until it is older than
    # REFRESH_RUNNING_TIMEOUT_S; after that it is taken to have died and no longer blocks.
    _record_refresh(repo, time.time() - 61)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is False
    _record_refresh(repo, time.time() - guard_evidence.REFRESH_RUNNING_TIMEOUT_S - 1)
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
    assert seen[0][1]["edit_gitignore"] is False



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
    _record_refresh(repo, time.time() - 300)
    _set_mtime(core, scanned - guard_evidence.STALENESS_MARGIN_S - 60)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], stamp) is False


def test_refresh_marker_is_claimed_atomically(repo, no_spawn):
    _write_evidence(repo, _evidence(_core_modules()))
    marker = state.shield_dir(repo, create=True) / guard_evidence._MARKER_NAME
    assert guard_evidence._claim_marker(marker) is True
    assert guard_evidence._claim_marker(marker) is False  # a concurrent second caller loses
    _record_refresh(repo, time.time() - 120)
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


# ---- refresh start time (final review) ---------------------------------------------------------------------------
# The refresh stamps `scanned_at` when it ends, seconds after it began reading files; the 15 s margin alone made a
# file edited just before the refresh look newer than the evidence it produced. The marker records when the refresh
# started, and once the evidence is rewritten after that, everything modified before the start counts as covered.


def _marker(repo: Path) -> Path:
    return state.shield_dir(repo, create=True) / guard_evidence._MARKER_NAME


def _record_refresh(repo: Path, started: float) -> Path:
    marker = _marker(repo)
    marker.write_text(repr(started), encoding="utf-8")
    _set_mtime(marker, started)
    return marker


def _finish_refresh(repo: Path, started: float) -> None:
    """What Shield's refresh process records once its rebuild returned: its start and the evidence it wrote."""
    guard_evidence.record_refresh_finished(repo, started, guard_evidence.evidence_mtime_ns(repo))


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def test_refresh_records_its_start_time_in_the_marker(repo, no_spawn):
    air = _write_evidence(repo, _evidence(_core_modules()))
    _set_mtime(air, time.time() - 100)
    before = time.time()
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is True
    recorded = guard_evidence._refresh_started_at(_marker(repo))
    assert recorded is not None and before - 1 <= recorded <= time.time() + 1


def test_file_edited_before_the_refresh_started_is_fresh_once_it_finished(repo, monkeypatch):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    started = time.time() - 100
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(started + 2)))
    _set_mtime(air, started + 3)  # the refresh wrote its evidence after it started
    _record_refresh(repo, started)
    _finish_refresh(repo, started)  # ... and recorded that write as its own
    core = repo / "app" / "core.py"
    _set_mtime(core, started - 1)  # inside the old 15 s margin, but before the refresh began reading files
    findings, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert any(f.check == "affected" for f in findings)
    assert all("older than" not in r for r in not_checked)
    _set_mtime(core, started + 1)  # edited after the refresh started: it may not be in the evidence
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]


def test_unfinished_refresh_falls_back_to_the_margin(repo, monkeypatch):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    started = time.time() - 100
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(started - 5)))
    _set_mtime(air, started - 4)  # evidence predates the refresh start: that refresh has not finished
    _record_refresh(repo, started)
    _set_mtime(repo / "app" / "core.py", started - 6)
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]


@pytest.mark.parametrize("content", [None, "", "garbage", "nan", "inf"])
def test_no_usable_marker_falls_back_to_the_margin(repo, monkeypatch, content):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    scanned = time.time() - 1000
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(scanned)))
    _set_mtime(air, scanned + 1)
    if content is not None:
        marker = _marker(repo)
        marker.write_text(content, encoding="utf-8")
        _set_mtime(marker, scanned - 50)
    core = repo / "app" / "core.py"
    _set_mtime(core, scanned - 3)
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]
    _set_mtime(core, scanned - guard_evidence.STALENESS_MARGIN_S - 60)
    findings, _ = evidence_checks(repo, ["app/core.py"], None)
    assert any(f.check == "affected" for f in findings)


def test_running_refresh_throttles_a_new_one(repo, no_spawn):
    now = time.time()
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(now - 100)))
    _set_mtime(air, now - 100)
    _record_refresh(repo, now - 10)  # started 10 s ago, evidence not rewritten since: still running
    _set_mtime(repo / "app" / "core.py", now - 5)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], _stamp(now - 100)) is False
    assert no_spawn == []


def test_crashed_refresh_does_not_block_forever(repo, no_spawn):
    now = time.time()
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(now - 1000)))
    _set_mtime(air, now - 1000)
    _record_refresh(repo, now - guard_evidence.REFRESH_RUNNING_TIMEOUT_S - 5)  # never finished
    _set_mtime(repo / "app" / "core.py", now - 5)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], _stamp(now - 1000)) is True
    assert len(no_spawn) == 1
    assert guard_evidence._refresh_started_at(_marker(repo)) >= now - 1


def test_finished_refresh_lets_a_newer_edit_start_another_inside_the_minute(repo, no_spawn):
    now = time.time()
    started = now - 20
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(started + 4)))
    _set_mtime(air, started + 5)  # finished
    _record_refresh(repo, started)
    _finish_refresh(repo, started)
    _set_mtime(repo / "app" / "core.py", started + 10)  # edited after that refresh started
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], _stamp(started + 4)) is True
    assert len(no_spawn) == 1


def test_finished_refresh_still_throttles_when_nothing_is_newer_than_its_start(repo, no_spawn):
    now = time.time()
    started = now - 20
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(started + 4)))
    _set_mtime(air, started + 5)
    _record_refresh(repo, started)
    _finish_refresh(repo, started)
    _set_mtime(repo / "app" / "core.py", started - 1)  # covered by that refresh
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], _stamp(started + 4)) is False
    assert guard_evidence.start_background_refresh(repo, None) is False  # no files given: 60 s throttle
    assert no_spawn == []


# ---- refresh completion is tied to Shield's own evidence write (re-review F2) ---------------------------------------
# Another writer (MCP watcher, `aletheore watch`, a manual scan) can rewrite air.json after a Shield refresh started
# although its scan read the files earlier; only the completion record Shield's own refresh writes may move the
# coverage boundary to the refresh start.


def test_another_writer_finishing_after_the_start_does_not_count_as_the_refresh(repo, monkeypatch):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    now = time.time()
    _record_refresh(repo, now - 10)  # Shield's refresh started and is still running
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(now - 9)))
    _set_mtime(air, now - 9)  # a concurrent scan, which read the files around now - 15, wrote this
    _set_mtime(repo / "app" / "core.py", now - 12)  # edited after that scan read it, before the refresh started
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]


def test_completion_record_for_other_evidence_falls_back_to_the_margin(repo, monkeypatch):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    started = time.time() - 100
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(started + 2)))
    _set_mtime(air, started + 3)
    _record_refresh(repo, started)
    _finish_refresh(repo, started)
    core = repo / "app" / "core.py"
    _set_mtime(core, started - 1)
    findings, _ = evidence_checks(repo, ["app/core.py"], None)
    assert any(f.check == "affected" for f in findings)  # Shield's own write: fresh
    _set_mtime(air, started + 4)  # another writer rewrote air.json since
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]


@pytest.mark.parametrize(
    "content",
    [None, "", "garbage", "[]", '{"started": "x", "evidence_mtime_ns": 1}', '{"started": NaN, "evidence_mtime_ns": 1}',
     '{"started": 1.0}', '{"started": 1.0, "evidence_mtime_ns": "1"}', '{"started": 1.0, "evidence_mtime_ns": true}',
     "FUTURE"],
)
def test_unusable_completion_record_falls_back_to_the_margin(repo, monkeypatch, content):
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    started = time.time() - 100
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(started + 2)))
    _set_mtime(air, started + 3)
    _record_refresh(repo, started)
    done = state.shield_dir(repo) / guard_evidence._DONE_NAME
    if content == "FUTURE":
        content = json.dumps({"started": time.time() + 3600, "evidence_mtime_ns": air.stat().st_mtime_ns})
    if content is not None:
        done.write_text(content, encoding="utf-8")
    _set_mtime(repo / "app" / "core.py", started - 1)
    _, not_checked = evidence_checks(repo, ["app/core.py"], None)
    assert "older than 1 changed file(s)" in not_checked[0]


def test_completion_record_without_evidence_is_ignored(repo):
    _record_refresh(repo, time.time() - 10)
    _finish_refresh(repo, time.time() - 10)  # no air.json: nothing is recorded, nothing raises
    assert guard_evidence._finished_refresh_start(repo) is None


def test_crashed_refresh_is_not_finished_and_stops_blocking_after_the_timeout(repo, no_spawn, monkeypatch):
    now = time.time()
    air = _write_evidence(repo, _evidence(_core_modules(), scanned_at=_stamp(now - 25)))
    _set_mtime(air, now - 20)  # some other writer, after the refresh started
    _record_refresh(repo, now - 30)  # started, never recorded a completion
    _set_mtime(repo / "app" / "core.py", now - 5)
    assert guard_evidence._finished_refresh_start(repo) is None
    # Not finished, so the "finished, newer edit" shortcut does not apply: it still counts as running.
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], _stamp(now - 25)) is False
    _record_refresh(repo, now - guard_evidence.REFRESH_RUNNING_TIMEOUT_S - 5)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"], _stamp(now - 25)) is True
    assert len(no_spawn) == 1


def test_refresh_child_gets_its_start_time(repo, no_spawn):
    air = _write_evidence(repo, _evidence(_core_modules()))
    _set_mtime(air, time.time() - 100)
    assert guard_evidence.start_background_refresh(repo, ["app/core.py"]) is True
    (args, _kwargs), = no_spawn
    assert float(args[0][4]) == guard_evidence._refresh_started_at(_marker(repo))


def test_refresh_module_records_completion_only_after_a_successful_rebuild(repo, monkeypatch):
    air = _write_evidence(repo, _evidence(_core_modules()))
    started = time.time() - 5
    state.shield_dir(repo, create=True)
    def rebuild(path, report, **kw):
        _set_mtime(air, started + 1)
        report("evidence updated")

    monkeypatch.setattr(watch, "rebuild", rebuild)
    assert refresh.main([str(repo), repr(started)]) == 0
    assert guard_evidence._finished_refresh_start(repo) == started

    (state.shield_dir(repo) / guard_evidence._DONE_NAME).unlink()

    def boom(*a, **k):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(watch, "rebuild", boom)
    assert refresh.main([str(repo), repr(started)]) == 1
    assert not (state.shield_dir(repo) / guard_evidence._DONE_NAME).exists()
    monkeypatch.setattr(watch, "rebuild", lambda path, report, **kw: None)
    assert refresh.main([str(repo), "nan"]) == 0  # an unusable start records nothing
    assert not (state.shield_dir(repo) / guard_evidence._DONE_NAME).exists()



def _real_rebuild_with_stubbed_scan(monkeypatch, repo, started, *, foreign_write_during_index: bool):
    """watch.rebuild for real, with the scan, the evidence write and the index build stubbed: the write stamps
    air.json just after `started`; the index build (which runs after "evidence updated") may let another writer
    replace air.json, as a manual scan or the MCP watcher could."""
    from aletheore import evidence, search_index

    air = repo / ".aletheore" / "air.json"
    (repo / ".aletheore" / "index.lancedb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(evidence, "scan_repository", lambda *a, **k: {"repository": {"modules": []}})

    def write(ev, path, edit_gitignore=True):
        air.write_text(json.dumps(_evidence(_core_modules())), encoding="utf-8")
        _set_mtime(air, started + 1)
        return air

    def build(*a, **k):
        if foreign_write_during_index:
            air.write_text(json.dumps(_evidence(_core_modules())), encoding="utf-8")
            _set_mtime(air, started + 2)  # another writer, whose scan may have read files before `started`
        return 0

    monkeypatch.setattr(evidence, "write_evidence", write)
    monkeypatch.setattr(search_index, "build_index", build)


def test_completion_records_the_mtime_of_shields_own_write_not_a_later_one(repo, monkeypatch):
    _write_evidence(repo, _evidence(_core_modules()))
    started = time.time() - 50
    state.shield_dir(repo, create=True)
    _real_rebuild_with_stubbed_scan(monkeypatch, repo, started, foreign_write_during_index=True)
    assert refresh.main([str(repo), repr(started)]) == 0
    done = json.loads((state.shield_dir(repo) / guard_evidence._DONE_NAME).read_text(encoding="utf-8"))
    assert done["evidence_mtime_ns"] != (repo / ".aletheore" / "air.json").stat().st_mtime_ns
    assert guard_evidence._finished_refresh_start(repo) is None  # margin fallback


def test_completion_is_trusted_when_nothing_else_wrote(repo, monkeypatch):
    _write_evidence(repo, _evidence(_core_modules()))
    started = time.time() - 50
    state.shield_dir(repo, create=True)
    _real_rebuild_with_stubbed_scan(monkeypatch, repo, started, foreign_write_during_index=False)
    assert refresh.main([str(repo), repr(started)]) == 0
    assert guard_evidence._finished_refresh_start(repo) == started


def test_no_completion_record_when_the_evidence_was_never_updated(repo, monkeypatch):
    _write_evidence(repo, _evidence(_core_modules()))
    state.shield_dir(repo, create=True)
    monkeypatch.setattr(watch, "rebuild", lambda path, report, **kw: None)  # "evidence updated" never reported
    assert refresh.main([str(repo), repr(time.time() - 5)]) == 0
    assert not (state.shield_dir(repo) / guard_evidence._DONE_NAME).exists()


@pytest.mark.parametrize(
    "content",
    ['{"started": ' + "9" * 400 + ', "evidence_mtime_ns": 1}', "[" * 100_000 + "]" * 100_000],
    ids=["huge-int", "deep-json"],
)
def test_hostile_completion_record_falls_back_without_raising(repo, content):
    _write_evidence(repo, _evidence(_core_modules()))
    (state.shield_dir(repo, create=True) / guard_evidence._DONE_NAME).write_text(content, encoding="utf-8")
    assert guard_evidence._finished_refresh_start(repo) is None
    assert guard_evidence._covered_until(repo, 1000.0) == 1000.0 - guard_evidence.STALENESS_MARGIN_S
