import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from aletheore.shield import checkpoint as cp
from aletheore.shield import deps as _deps
from aletheore.shield import guard, guard_evidence, hooks, receipt, registry
from aletheore.shield.guard import GuardReport
from aletheore.shield.guard_evidence import Finding

EVENTS = ("pre-edit", "post-edit", "turn-end", "session-end", "pre-commit")
STOP_NAMES = ("PRE_EDIT_STOP_S", "POST_EDIT_STOP_S", "TURN_END_STOP_S", "SESSION_END_STOP_S", "PRE_COMMIT_STOP_S")
CONTRACT_STOPS = {name: getattr(hooks, name) for name in STOP_NAMES}
SRC = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY_CACHE_PATH", tmp_path / "reg-cache.json")
    monkeypatch.setattr(_deps, "_installed_import_names", lambda: set())
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    monkeypatch.setenv("ALETHEORE_SHIELD_OFFLINE", "1")
    monkeypatch.delenv("ALETHEORE_HOOK_DEBUG", raising=False)
    # Real git on a slow CI runner (Windows) can pass the 2 s contract stop; the budget test shrinks them itself.
    for name in STOP_NAMES:
        monkeypatch.setattr(hooks, name, 60.0)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(path, *, aletheore=True):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    if aletheore:
        (path / ".aletheore").mkdir()
    return path


def _payload(repo, *, event="PreToolUse", session="s1", path="a.py", tool="Write", **extra):
    data = {
        "session_id": session,
        "cwd": str(repo),
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": {"file_path": str(Path(repo) / path) if not os.path.isabs(path) else path},
    }
    data.update(extra)
    return data


def _run(event, payload=None, *, raw=None, argv_extra=()):
    stdin = io.StringIO(raw if raw is not None else ("" if payload is None else json.dumps(payload)))
    out, err = io.StringIO(), io.StringIO()
    code = hooks.main([event, *argv_extra], stdin=stdin, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _report(*messages):
    return GuardReport(tuple(Finding("dependencies", "warn", m, "pyproject.toml") for m in messages), ())


def _fake_guard(monkeypatch, *reports):
    """run_guard returns the given reports in turn (the last one repeats); records each call's kwargs."""
    calls = []

    def fake(repo, **kwargs):
        calls.append(kwargs)
        return reports[min(len(calls) - 1, len(reports) - 1)] if reports else GuardReport((), ())

    monkeypatch.setattr(guard, "run_guard", fake)
    monkeypatch.setattr(receipt, "run_guard", fake)
    return calls


def _checkpoints(repo):
    return cp.list_checkpoints(repo, limit=100)


# ------------------------------------------------------------------------------------------- robustness


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize(
    "raw", ["", "not json", "[1, 2, 3]", "null", "42", json.dumps({"x": ["y" * 1000] * 5000})]
)
def test_every_subcommand_exits_zero_on_garbage_stdin(event, raw, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_guard(monkeypatch)
    code, out, err = _run(event, raw=raw)
    assert code == 0
    assert out == ""
    assert err == ""


def test_unknown_or_missing_event_exits_zero_silently():
    assert _run("nonsense", raw="{}")[:2] == (0, "")
    out = io.StringIO()
    assert hooks.main([], stdin=io.StringIO("{}"), stdout=out, stderr=io.StringIO()) == 0
    assert out.getvalue() == ""


def test_hook_internal_error_prints_nothing_and_exits_zero(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cp, "take_checkpoint", explode)
    code, out, err = _run("pre-edit", _payload(repo))
    assert (code, out, err) == (0, "", "")


def test_internal_error_traceback_goes_to_stderr_only_when_debugging(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    monkeypatch.setattr(cp, "take_checkpoint", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setenv("ALETHEORE_HOOK_DEBUG", "1")
    code, out, err = _run("pre-edit", _payload(repo))
    assert code == 0 and out == ""
    assert "RuntimeError: boom" in err


def test_even_base_exception_is_swallowed(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cp, "take_checkpoint", stop)
    assert _run("pre-edit", _payload(repo)) == (0, "", "")


# ------------------------------------------------------------------------------------------- pre-edit


def test_pre_edit_takes_a_checkpoint_and_prints_nothing(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert _run("pre-edit", _payload(repo)) == (0, "", "")
    made = _checkpoints(repo)
    assert len(made) == 1
    assert made[0].session == "s1"


def test_pre_edit_accepts_a_path_relative_to_cwd(tmp_path):
    repo = _init(tmp_path / "r")
    payload = _payload(repo)
    payload["tool_input"]["file_path"] = "a.py"
    assert _run("pre-edit", payload) == (0, "", "")
    assert len(_checkpoints(repo)) == 1


def test_pre_edit_from_a_subfolder_uses_the_repo_root(tmp_path):
    repo = _init(tmp_path / "r")
    (repo / "pkg").mkdir()
    payload = _payload(repo, path="pkg/m.py")
    payload["cwd"] = str(repo / "pkg")
    assert _run("pre-edit", payload) == (0, "", "")
    assert len(_checkpoints(repo)) == 1


def test_pre_edit_saves_base_evidence_once(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = []
    real = guard_evidence.save_base_evidence

    def counting(repo_arg, session_id):
        calls.append(session_id)
        return real(repo_arg, session_id)

    monkeypatch.setattr(guard_evidence, "save_base_evidence", counting)
    (repo / ".aletheore" / "air.json").write_text('{"first": true}', encoding="utf-8")
    _run("pre-edit", _payload(repo))
    (repo / ".aletheore" / "air.json").write_text('{"second": true}', encoding="utf-8")
    (repo / "a.py").write_text("changed\n", encoding="utf-8")
    _run("pre-edit", _payload(repo))
    assert calls and set(calls) == {"s1"}
    saved = repo / ".aletheore" / "shield" / "sessions" / "s1.base-air.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == {"first": True}


def test_pre_edit_ignores_a_file_outside_the_repo(tmp_path):
    repo = _init(tmp_path / "r")
    outside = tmp_path / "elsewhere" / "x.py"
    assert _run("pre-edit", _payload(repo, path=str(outside))) == (0, "", "")
    assert _checkpoints(repo) == []


def test_pre_edit_ignores_payload_without_a_file_path(tmp_path):
    repo = _init(tmp_path / "r")
    payload = _payload(repo)
    payload["tool_input"] = {}
    assert _run("pre-edit", payload) == (0, "", "")
    assert _checkpoints(repo) == []


def test_pre_edit_in_a_non_git_folder_does_nothing(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert _run("pre-edit", _payload(folder)) == (0, "", "")
    assert not (folder / ".aletheore").exists()


def test_pre_edit_does_not_create_aletheore_folder(tmp_path):
    repo = _init(tmp_path / "r", aletheore=False)
    assert _run("pre-edit", _payload(repo)) == (0, "", "")
    assert len(_checkpoints(repo)) == 1  # checkpoints live in git refs
    assert not (repo / ".aletheore").exists()


def test_pre_edit_with_invalid_session_id_still_checkpoints_but_writes_no_state(tmp_path):
    repo = _init(tmp_path / "r")
    assert _run("pre-edit", _payload(repo, session="../evil")) == (0, "", "")
    made = _checkpoints(repo)
    assert len(made) == 1 and made[0].session == ""
    assert not (repo / ".aletheore" / "shield").exists()


def test_prune_runs_at_most_once_per_hour_per_session(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    pruned = []
    monkeypatch.setattr(cp, "prune_checkpoints", lambda r, **k: pruned.append(Path(r)) or 0)
    clock = [1_000_000.0]
    monkeypatch.setattr(hooks, "_now", lambda: clock[0])
    _run("pre-edit", _payload(repo))
    _run("pre-edit", _payload(repo))
    clock[0] += 3599
    _run("pre-edit", _payload(repo))
    assert len(pruned) == 1
    _run("pre-edit", _payload(repo, session="s2"))  # another session keeps its own clock
    assert len(pruned) == 2
    clock[0] += 2
    _run("pre-edit", _payload(repo))
    assert len(pruned) == 3


def test_corrupt_session_state_file_is_ignored(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    sessions = repo / ".aletheore" / "shield" / "sessions"
    sessions.mkdir(parents=True)
    state_file = sessions / "s1.json"
    state_file.write_text("{not json", encoding="utf-8")
    pruned = []
    monkeypatch.setattr(cp, "prune_checkpoints", lambda r, **k: pruned.append(r) or 0)
    assert _run("pre-edit", _payload(repo)) == (0, "", "")
    assert len(pruned) == 1
    assert isinstance(json.loads(state_file.read_text(encoding="utf-8")), dict)
    state_file.write_text("[1, 2]", encoding="utf-8")
    _fake_guard(monkeypatch, _report("x does not exist on PyPI."))
    code, out, _ = _run("post-edit", _payload(repo, event="PostToolUse"))
    assert code == 0 and "x does not exist" in out


def test_concurrent_pre_edit_hooks_do_not_collide(tmp_path):
    repo = _init(tmp_path / "r")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    results = {}

    def go(session):
        results[session] = _run("pre-edit", _payload(repo, session=session))

    threads = [threading.Thread(target=go, args=(s,)) for s in ("s1", "s2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == {"s1": (0, "", ""), "s2": (0, "", "")}
    made = _checkpoints(repo)
    assert 1 <= len(made) <= 2
    assert {c.session for c in made} <= {"s1", "s2"}
    assert len({c.ref for c in made}) == len(made)
    for session in ("s1", "s2"):
        state_file = repo / ".aletheore" / "shield" / "sessions" / f"{session}.json"
        assert isinstance(json.loads(state_file.read_text(encoding="utf-8")), dict)


# ------------------------------------------------------------------------------------------- post-edit


def test_post_edit_prints_additional_context_only_when_text_is_new(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = _fake_guard(
        monkeypatch, _report("x does not exist on PyPI."), _report("x does not exist on PyPI."), GuardReport((), ())
    )
    payload = _payload(repo, event="PostToolUse")
    code, out, err = _run("post-edit", payload)
    assert code == 0 and err == ""
    data = json.loads(out)
    assert list(data) == ["hookSpecificOutput"]
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "x does not exist on PyPI." in data["hookSpecificOutput"]["additionalContext"]
    assert _run("post-edit", payload) == (0, "", "")  # identical text: said already
    assert _run("post-edit", payload) == (0, "", "")  # nothing to say
    assert calls[0]["budget_s"] == hooks.POST_EDIT_BUDGET_S
    assert calls[0]["lookups"] is False  # registry lookups never run per edit


def test_post_edit_repeats_findings_that_come_back_after_clearing(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    _fake_guard(monkeypatch, _report("a"), GuardReport((), ()), _report("a"))
    payload = _payload(repo, event="PostToolUse")
    assert _run("post-edit", payload)[1]
    assert _run("post-edit", payload)[1] == ""
    assert _run("post-edit", payload)[1]


def test_post_edit_passes_the_session_base_evidence(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = _fake_guard(monkeypatch)
    sentinel = {"base": True}
    monkeypatch.setattr(guard_evidence, "load_base_evidence", lambda r, s: sentinel if s == "s1" else None)
    _run("post-edit", _payload(repo, event="PostToolUse"))
    assert calls[0]["base_evidence"] is sentinel


def test_post_edit_ignores_a_file_outside_the_repo(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = _fake_guard(monkeypatch, _report("a"))
    payload = _payload(repo, event="PostToolUse", path=str(tmp_path / "x.py"))
    assert _run("post-edit", payload) == (0, "", "")
    assert calls == []


# ------------------------------------------------------------------------------------------- turn-end


def test_turn_end_prints_guard_text_only_when_there_is_something(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = _fake_guard(monkeypatch, GuardReport((), ()), _report("y is one edit from requests."))
    payload = _payload(repo, event="Stop", stop_hook_active=False)
    assert _run("turn-end", payload) == (0, "", "")
    code, out, err = _run("turn-end", payload)
    data = json.loads(out)
    assert list(data) == ["systemMessage"]
    assert "y is one edit from requests." in data["systemMessage"]
    assert "decision" not in out
    assert calls[0]["budget_s"] == hooks.TURN_END_BUDGET_S


def test_turn_end_is_silent_when_stop_hook_active(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = _fake_guard(monkeypatch, _report("a"))
    assert _run("turn-end", _payload(repo, event="Stop", stop_hook_active=True)) == (0, "", "")
    assert calls == []


def test_turn_end_does_not_repeat_text_post_edit_already_gave(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    _fake_guard(monkeypatch, _report("a"))
    assert _run("post-edit", _payload(repo, event="PostToolUse"))[1]
    assert _run("turn-end", _payload(repo, event="Stop", stop_hook_active=False)) == (0, "", "")


# ------------------------------------------------------------------------------------------- session-end


def test_session_end_writes_a_receipt(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    _fake_guard(monkeypatch)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _run("pre-edit", _payload(repo))
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert _run("session-end", _payload(repo, event="SessionEnd", reason="other")) == (0, "", "")
    written = list((repo / ".aletheore" / "shield" / "receipts").glob("*-s1.md"))
    assert len(written) == 1
    assert "a.py" in written[0].read_text(encoding="utf-8")


def test_session_end_without_a_checkpoint_writes_nothing(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    _fake_guard(monkeypatch)
    assert _run("session-end", _payload(repo, event="SessionEnd")) == (0, "", "")
    assert not (repo / ".aletheore" / "shield" / "receipts").exists()


# ------------------------------------------------------------------------------------------- pre-commit


def test_pre_commit_prints_to_stderr_and_exits_zero(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    calls = _fake_guard(monkeypatch, GuardReport(
        (Finding("dependencies", "warn", "zz does not exist on PyPI.", "pyproject.toml"),),
        ("registry lookup failed for 1 name: offline",),
    ))
    code, out, err = _run("pre-commit", argv_extra=("--repo", str(repo)), raw="")
    assert code == 0 and out == ""
    lines = err.splitlines()
    assert len(lines) == 2
    assert all(line.startswith("aletheore shield: ") for line in lines)
    assert "zz does not exist on PyPI." in err
    assert calls[0]["budget_s"] == hooks.PRE_COMMIT_BUDGET_S


def test_pre_commit_uses_cwd_and_ignores_stdin(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    _fake_guard(monkeypatch, _report("a"))
    monkeypatch.chdir(repo)

    class Unreadable(io.StringIO):
        def read(self, *args):
            raise AssertionError("pre-commit must not read stdin")

    err = io.StringIO()
    assert hooks.main(["pre-commit"], stdin=Unreadable(), stdout=io.StringIO(), stderr=err) == 0
    assert err.getvalue().startswith("aletheore shield: ")


def test_pre_commit_is_silent_when_clean(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    _fake_guard(monkeypatch)
    assert _run("pre-commit", argv_extra=("--repo", str(repo))) == (0, "", "")


def test_pre_commit_in_a_non_git_dir_never_raises(tmp_path, monkeypatch):
    folder = tmp_path / "plain"
    folder.mkdir()
    calls = _fake_guard(monkeypatch, _report("a"))
    monkeypatch.chdir(folder)
    assert _run("pre-commit") == (0, "", "")
    assert _run("pre-commit", argv_extra=("--repo", str(folder / "missing"))) == (0, "", "")
    assert calls == []


# ------------------------------------------------------------------------------------------- budget


def test_hook_stops_at_budget(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r")
    release = threading.Event()

    def slow(repo_arg, **kwargs):
        release.wait(10)
        return _report("too late")

    monkeypatch.setattr(guard, "run_guard", slow)
    monkeypatch.setattr(hooks, "POST_EDIT_STOP_S", 0.3)
    monkeypatch.setattr(hooks, "TURN_END_STOP_S", 0.3)
    try:
        start = time.monotonic()
        assert _run("post-edit", _payload(repo, event="PostToolUse")) == (0, "", "")
        assert _run("turn-end", _payload(repo, event="Stop")) == (0, "", "")
        assert time.monotonic() - start < 2.5
    finally:
        release.set()
    time.sleep(0.2)  # the abandoned workers finish now; they must still not write anything
    sessions = repo / ".aletheore" / "shield" / "sessions"
    assert not (sessions / "s1.json").exists() or "last_guard_sha" not in (sessions / "s1.json").read_text()


def test_stop_constants_match_the_contract():
    assert CONTRACT_STOPS == {
        "PRE_EDIT_STOP_S": 2.0,
        "POST_EDIT_STOP_S": 2.0,
        "TURN_END_STOP_S": 8.0,
        "SESSION_END_STOP_S": 8.0,
        "PRE_COMMIT_STOP_S": 8.0,
    }


# ------------------------------------------------------------------------------------------- agent_output


def test_agent_output_shapes():
    assert hooks.agent_output("post-edit", "warn: a") == {
        "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": hooks.HEADER + "\nwarn: a"}
    }
    assert hooks.agent_output("turn-end", "warn: a") == {"systemMessage": hooks.HEADER + "\nwarn: a"}
    for event in ("pre-edit", "session-end", "pre-commit", "other"):
        assert hooks.agent_output(event, "warn: a") is None
    assert hooks.agent_output("post-edit", "") is None
    assert hooks.agent_output("turn-end", "") is None


# ------------------------------------------------------------------------------------------- real process


def test_module_runs_as_a_real_subprocess(tmp_path):
    repo = _init(tmp_path / "r")
    env = {**os.environ, "PYTHONPATH": str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    env.pop("ALETHEORE_HOOK_DEBUG", None)
    start = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", "aletheore.shield.hooks", "pre-edit"],
        input=json.dumps(_payload(repo)).encode("utf-8"),
        capture_output=True,
        cwd=str(repo),
        env=env,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0
    assert done.stdout == b""
    assert time.monotonic() - start < 10
