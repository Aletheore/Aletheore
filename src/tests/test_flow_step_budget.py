"""The analysis budget counts interpreter steps, so a scan gives the same answer on a fast or a slow machine."""

import time

from aletheore.flow import budget
from aletheore.flow.discover import discover_repo

APP = """
const express = require('express');
const { exec } = require('child_process');
const app = express();
app.get('/a', (req, res) => { exec('ls ' + req.query.d); });
"""


def _repo(tmp_path):
    (tmp_path / "app.js").write_text(APP)
    return tmp_path


def _ids(findings):
    return sorted((f["path"], f["line"], f["rule_id"]) for f in findings)


def test_finding_survives_a_wall_clock_that_runs_slow(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _ids(discover_repo(repo))
    assert base
    clock = iter(range(0, 10**9, 20))  # each reading is 20 s later: far slower than the old 150 s wall-clock cap allowed
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    assert _ids(discover_repo(repo)) == base  # steps, not seconds, decide how much is analysed


def test_a_tiny_step_budget_stops_the_scan(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(budget, "STEPS_PER_SECOND", 1)
    assert discover_repo(repo, budget_seconds=0.0) == []


def test_step_tally_moves_with_work(tmp_path):
    before = budget.STEPS[0]
    discover_repo(_repo(tmp_path))
    assert budget.STEPS[0] > before
