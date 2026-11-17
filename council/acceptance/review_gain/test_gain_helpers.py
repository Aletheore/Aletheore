"""Tests for the experiment's helpers (run with: pytest test_gain_helpers.py from this folder; they call no agents)."""
import json

import pytest

import gainlib
from score import JudgeFailed, judge


def bug(i, fn="f", file="networkx/a.py", line=10):
    return {"id": f"M{i:02d}", "file": file, "function": fn, "line": line, "original": "a < b", "mutated": "a <= b"}


class FakeRunner:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def run(self, prompt, wt, mcp, env, timeout, read_only=False):
        self.prompts.append(prompt)
        from types import SimpleNamespace
        return SimpleNamespace(final_text=self.replies.pop(0), ok=True)


# ---- the judge works in batches and never turns a failure into "found nothing" -------------------------------------------------------------------
def test_candidates_are_judged_in_batches_and_the_answers_are_combined():
    bugs = [bug(i, fn=f"func{i}") for i in range(1, 36)]
    review = " ".join(f"func{i} is wrong" for i in range(1, 36))
    # three calls (35 candidates, 15 per batch): each answers for the ids it was given
    runner = FakeRunner([])
    runner.replies = [json.dumps({f"M{i:02d}": True for i in range(1, 16)}), json.dumps({f"M{i:02d}": i % 2 == 0 for i in range(16, 31)}),
                      json.dumps({f"M{i:02d}": False for i in range(31, 36)})]
    got = judge(review, bugs, runner)
    assert len(runner.prompts) == 3 and len(got) == 35
    assert all(got[f"M{i:02d}"] for i in range(1, 16)) and got["M16"] is True and got["M17"] is False and got["M18"] is True and not any(got[f"M{i:02d}"] for i in range(31, 36))


def test_an_unreadable_judge_answer_is_retried_once_then_reported_not_scored_as_zero():
    bugs = [bug(1, fn="alpha")]
    ok = FakeRunner(["I am not sure", '{"M01": true}'])
    assert judge("alpha is wrong", bugs, ok) == {"M01": True} and len(ok.prompts) == 2
    bad = FakeRunner(["no json here", "still no json"])
    with pytest.raises(JudgeFailed):
        judge("alpha is wrong", bugs, bad)


def test_no_candidates_means_no_judge_call():
    runner = FakeRunner([])
    assert judge("nothing relevant", [bug(1, fn="alpha")], runner) == {} and runner.prompts == []


def test_an_id_the_judge_leaves_out_counts_as_not_found():
    bugs = [bug(1, fn="alpha"), bug(2, fn="beta")]
    assert judge("alpha and beta", bugs, FakeRunner(['{"M01": true}'])) == {"M01": True, "M02": False}


# ---- groups and caps ---------------------------------------------------------------------------------------------------------------------------------
def test_groups_balance_by_size_and_cover_every_file_once():
    files = [f"f{i}.py" for i in range(10)]
    lines = {f: 100 * (i + 1) for i, f in enumerate(files)}
    g = gainlib.groups(files, lines, 3)
    assert len(g) == 3 and sorted(x for grp in g for x in grp) == sorted(files)
    sizes = [sum(lines[f] for f in grp) for grp in g]
    assert max(sizes) - min(sizes) <= 100


def test_caps_scale_with_the_number_of_files_and_give_the_same_total_to_every_condition():
    assert gainlib.cap_for(12, "per-file") == 18 and gainlib.cap_for(60, "per-file") == 90 and gainlib.cap_for(4, "per-file") == 6
    assert gainlib.cap_for(12, "fixed", fixed=25) == 25
    sizes = [gainlib.cap_for(n, "per-file") for n in (10, 10, 10, 10, 10, 10)]
    assert sum(sizes) == gainlib.cap_for(60, "per-file")


# ---- resuming ----------------------------------------------------------------------------------------------------------------------------------------
def test_a_finished_condition_is_skipped_on_resume_and_a_failed_one_is_not(tmp_path):
    (tmp_path / "results.jsonl").write_text(json.dumps({"condition": "council", "ok": True, "judge_failed": False}) + "\n"
                                            + json.dumps({"condition": "split", "ok": True, "judge_failed": True}) + "\n"
                                            + json.dumps({"condition": "codex-alone", "ok": False, "judge_failed": False}) + "\n")
    assert gainlib.already_done(tmp_path, "council") is True
    assert gainlib.already_done(tmp_path, "split") is False            # the judge failed: score again (the review text is kept)
    assert gainlib.already_done(tmp_path, "codex-alone") is False      # the run itself failed
    assert gainlib.already_done(tmp_path, "claude-alone") is False
    assert gainlib.already_done(tmp_path / "missing", "council") is False
