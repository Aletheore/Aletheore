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


# ---- the judge waits out a CLI that is briefly unavailable (Claude Code updates itself in place) -----------------------------------------------------
class FlakyRunner(FakeRunner):
    """Replies are (ok, text): a failed run (the CLI missing or erroring) is not the same as an answer that cannot be read."""

    def run(self, prompt, wt, mcp, env, timeout, read_only=False):
        from types import SimpleNamespace
        self.prompts.append(prompt)
        ok, text = self.replies.pop(0)
        return SimpleNamespace(final_text=text, ok=ok)


def test_the_judge_waits_and_retries_when_the_cli_itself_fails_then_succeeds():
    sleeps = []
    runner = FlakyRunner([(False, "claude is not installed or not on PATH"), (False, "agent exited with code 1"), (True, '{"M01": true}')])
    assert judge("alpha is wrong", [bug(1, fn="alpha")], runner, sleep=sleeps.append) == {"M01": True}
    assert len(runner.prompts) == 3 and len(sleeps) == 2 and all(s > 0 for s in sleeps)


def test_the_judge_gives_up_after_many_cli_failures_and_says_so():
    runner = FlakyRunner([(False, "claude is not installed or not on PATH")] * 8)
    with pytest.raises(JudgeFailed):
        judge("alpha is wrong", [bug(1, fn="alpha")], runner, sleep=lambda s: None)
    assert len(runner.prompts) <= 8


# ---- v3 safeguards: log what agents do, flag oracle-seeking, keep the answer key encrypted ------------------------------------------------------------
def test_normal_reading_is_not_flagged_and_oracle_seeking_is_():
    ok = ["sed -n '1,80p' funcy/colls.py", "cat parse.py", "rg -n 'if .* == ' funcy", "ls funcy", "python3 -c \"print(1)\"", "git log -3 --oneline"]
    bad = ["find / -name 'funcy' -type d", "cat /Users/x/dev/council-gain-data/v3/manifest.json", "ls /opt/homebrew/lib/python3.12/site-packages/funcy",
           "pip download funcy", "curl -s https://pypi.org/simple/funcy/", "rg -n review ~/.codex/memories/MEMORY.md", "git -C /somewhere/else log", "cat ../manifest.enc"]
    assert gainlib.audit_commands(ok) == []
    flagged = gainlib.audit_commands(bad)
    assert len(flagged) == len(bad)


def test_commands_are_pulled_from_codex_and_claude_events():
    codex = [{"type": "item.completed", "item": {"type": "command_execution", "command": "ls -la"}}, {"type": "item.completed", "item": {"type": "agent_message", "text": "x"}}]
    claude = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b.py"}},
                                                           {"type": "tool_use", "name": "Grep", "input": {"pattern": "foo", "path": "/a"}}, {"type": "text", "text": "hi"}]}}]
    assert gainlib.commands_of(codex) == ["ls -la"]
    assert gainlib.commands_of(claude) == ["Read /a/b.py", "Grep foo /a"]


def test_the_manifest_round_trips_encrypted_and_the_file_does_not_contain_it(tmp_path):
    data = {"bugs": [{"id": "M01", "file": "funcy/x.py", "line": 7, "function": "SECRET_FUNCTION_NAME"}]}
    key = gainlib.new_key()
    gainlib.save_manifest(tmp_path, data, key)
    assert b"SECRET_FUNCTION_NAME" not in (tmp_path / "manifest.enc").read_bytes() and not (tmp_path / "manifest.json").exists()
    assert gainlib.load_manifest(tmp_path, key) == data
    with pytest.raises(Exception):
        gainlib.load_manifest(tmp_path, gainlib.new_key())


def test_a_plain_manifest_is_still_read_when_there_is_no_encrypted_one(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"bugs": [1]}))
    assert gainlib.load_manifest(tmp_path, None) == {"bugs": [1]}


def test_a_recording_runner_keeps_every_commands_for_the_audit():
    from types import SimpleNamespace
    inner = SimpleNamespace(name="codex", run=lambda *a, **k: SimpleNamespace(ok=True, final_text="x", exit_code=0,
                                                                           raw_events=[{"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}}]))
    log = []
    rec = gainlib.Recording(inner, log, "alice-codex")
    rec.run("p", None, None, {}, 5)
    assert log == [("alice-codex", ["ls"])]


def test_the_cap_rate_is_a_setting():
    assert gainlib.cap_for(23, "per-file", rate=3) == 69 and gainlib.cap_for(6, "per-file", rate=3) == 18 and gainlib.cap_for(5, "per-file", rate=2.5) == 13
    assert gainlib.cap_for(23, "per-file") == 35          # the default is unchanged


def test_a_recording_runner_can_give_each_run_its_own_environment():
    from types import SimpleNamespace
    seen, counter = [], iter(range(10))
    inner = SimpleNamespace(name="codex", run=lambda inst, wt, mcp, env, t, **k: seen.append(dict(env)) or SimpleNamespace(ok=True, final_text="x", exit_code=0, raw_events=[]))
    rec = gainlib.Recording(inner, [], "alice-codex", env=lambda: {"CODEX_HOME": f"/fresh/{next(counter)}"})
    rec.run("p", None, None, {"KEEP": "1"}, 5)
    rec.run("p", None, None, {}, 5)
    assert seen[0] == {"KEEP": "1", "CODEX_HOME": "/fresh/0"} and seen[1] == {"CODEX_HOME": "/fresh/1"}


def test_a_fresh_codex_home_links_only_the_sign_in_file(tmp_path, monkeypatch):
    real = tmp_path / ".codex"
    real.mkdir()
    (real / "auth.json").write_text("{}")
    (real / "config.toml").write_text("model='x'")
    (real / "memories").mkdir()
    (real / "memories" / "MEMORY.md").write_text("notes")
    monkeypatch.setenv("HOME", str(tmp_path))
    home = gainlib.fresh_codex_home()
    names = sorted(p.name for p in home.iterdir())
    assert names == ["auth.json"] and (home / "auth.json").is_symlink() and home != real
    assert gainlib.fresh_codex_home() != home                     # a new one every time: nothing accumulates between runs


def test_claude_reading_back_its_own_saved_tool_output_is_not_oracle_seeking():
    own = "Read /Users/x/.claude/projects/-private-var-gain-claude-abc-repo/597d52dd-eb25-4ed5-8c92-720a15736f22/tool-results/bcn45.txt"
    assert gainlib.audit_commands([own]) == []
    # but its memory, settings and other projects' files still are
    for bad in ("Read /Users/x/.claude/projects/-private-var-gain-claude-abc-repo/memory/MEMORY.md", "Read /Users/x/.claude/CLAUDE.md",
                "Read /Users/x/.claude/projects/-Users-x-Aletheore/abc/notes.md", "cat ~/.claude/settings.json"):
        assert gainlib.audit_commands([bad]), bad


def test_git_remote_is_a_note_not_a_contamination_flag_because_the_clones_have_no_remote():
    cmd = "ls funcy parse; git remote -v; for f in funcy/*.py; do nl -ba $f; done"
    assert gainlib.audit_commands([cmd]) == []
    assert gainlib.audit_notes([cmd]) == [(cmd, "asks where the clone came from (the clones have no remote, so nothing is revealed)")]
    assert gainlib.audit_notes(["cat parse.py"]) == []
    assert gainlib.audit_commands(["git -C /elsewhere remote -v"]) != []            # looking at another repository is still a flag


# ---- the judge sometimes corrects itself after its first answer: the last valid JSON object is the answer -------------------------------------------------
def test_the_last_json_object_wins_when_the_judge_corrects_itself():
    bugs = [bug(1, fn="alpha"), bug(2, fn="beta")]
    reply = '{"M01": true, "M02": true}\n\nWait, correcting: beta is not really reported.\n\n{"M01": true, "M02": false}'
    assert judge("alpha and beta", bugs, FakeRunner([reply])) == {"M01": True, "M02": False}


def test_prose_with_braces_around_the_json_does_not_break_parsing():
    bugs = [bug(1, fn="alpha")]
    reply = 'Here is my answer {as requested}: {"M01": true} -- done {really}'
    assert judge("alpha is wrong", bugs, FakeRunner([reply])) == {"M01": True}


def test_json_that_is_not_an_object_of_ids_is_unreadable():
    bugs = [bug(1, fn="alpha")]
    with pytest.raises(JudgeFailed):
        judge("alpha is wrong", bugs, FakeRunner(["[1, 2, 3]", "{broken"]))


# ---- paths: only a path that actually leaves the corpus is oracle-seeking --------------------------------------------------------------------------------
def test_relative_paths_that_stay_inside_the_corpus_are_fine():
    ok = ["cd funcy/funcy && cat funcs.py objects.py; wc -l seqs.py ../../parse/parse/__init__.py", "cat funcy/../parse/parse/__init__.py", "cd pyrsistent && ls ../funcy",
          "rg -n foo funcy 2>/dev/null", "sed -n '1,40p' ./parse/parse/__init__.py"]
    assert gainlib.audit_commands(ok) == []


def test_paths_that_leave_the_corpus_are_flagged():
    for bad in ("cat ../manifest.enc", "ls ..", "cd funcy && cat ../../x", "cd funcy/funcy && ls ../../../", "ls ~/dev", "cat /Users/x/dev/council-gain-data/v3/manifest.enc",
                "cat /etc/hosts"):
        assert gainlib.audit_commands([bad]), bad


def test_absolute_paths_inside_the_corpus_folder_are_fine():
    assert gainlib.audit_commands(["cat /var/folders/ab/T/gain-claude-xyz-repo/funcy/funcy/seqs.py"], root="/var/folders/ab/T/gain-claude-xyz-repo") == []
    assert gainlib.audit_commands(["cat /var/folders/ab/T/gain-claude-other/x.py"], root="/var/folders/ab/T/gain-claude-xyz-repo") != []


def test_a_runs_own_temporary_worktree_counts_as_inside_the_corpus():
    inside = ["Read /private/var/folders/p2/4x5y/T/gain-claude-txgwa57a-repo/funcy/funcy/seqs.py", "cat /var/folders/p2/4x5y/T/gain-split-abc/repo0/parse/parse/__init__.py",
              "Grep foo /private/var/folders/p2/4x5y/T/gain-council-xyz/bob-repo"]
    assert gainlib.audit_commands(inside) == []
    assert gainlib.audit_commands(["Read /private/var/folders/p2/4x5y/T/something-else/x.py"]) != []
    assert gainlib.audit_commands(["Read /private/var/folders/p2/4x5y/T/gain-v3-work-abc/libs/funcy-2.1/funcy/seqs.py"]) != []      # the builder's workspace is not a run's worktree


# ---- the audit follows the working folder across a run's commands and ignores regex text inside quotes -----------------------------------------------------
def test_a_cd_in_an_earlier_command_carries_over_to_later_ones():
    assert gainlib.audit_commands(["Bash cd funcy/funcy", "Bash ls ../../parse/parse"]) == []
    assert gainlib.audit_commands(["Bash cd funcy", "Bash cd ../.."]) != []                   # climbing out of the corpus is still flagged
    assert gainlib.audit_commands(["Bash cd funcy/funcy && cat colls.py", "Bash cd ../../schema/schema && cat __init__.py"]) == []


def test_regex_text_inside_quotes_is_not_a_path():
    ok = ["sed -n '/def \\|=\\|return\\|if \\|for /p' parse/parse/__init__.py", "sed 's/^ *\\([0-9]*\\)/\\1+419/' f.py", "awk '{ if ($1+0 > 0) print }' x.py",
          "grep -n \"def validate\" -A135 schema/schema/__init__.py | sed -n 1,260p"]
    assert gainlib.audit_commands(ok) == []
    assert gainlib.audit_commands(['cat "/etc/hosts"']) != [] and gainlib.audit_commands(["cat '../manifest.enc'"]) != []


def test_an_absolute_cd_into_the_runs_own_worktree_sets_the_folder():
    run = "/private/var/folders/p2/4x5y/T/gain-claude-abc/repo"
    assert gainlib.audit_commands([f"Bash cd {run}/parse/parse; sed -n 1,40p __init__.py", "Bash ls ../../funcy"]) == []
    assert gainlib.audit_commands([f"Bash cd {run}/parse; ls ../../.."]) != []
