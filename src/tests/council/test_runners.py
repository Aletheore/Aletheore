import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from aletheore.council.runners import AgentLaunchError, ClaudeRunner, CodexRunner, ScriptedRunner, RunResult

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake agent scripts are POSIX shebang scripts")


def fake_agent(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    p.chmod(0o755)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


@pytest.fixture
def mcp_config(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"council": {"command": "/py", "args": ["/srv.py"], "env": {"COUNCIL_AGENT": "a"}}}}))
    return p


def run_claude(tmp_path, mcp_config, **kw):
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    return ClaudeRunner().run("do it", wt, mcp_config, {"COUNCIL_AGENT": "a"}, timeout_s=kw.pop("timeout_s", 20))


def test_runner_missing_binary_returns_failed_result(tmp_path, monkeypatch, mcp_config):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    res = run_claude(tmp_path, mcp_config)
    assert not res.ok and "not installed" in res.final_text


@posix_only
def test_launch_oserror_returns_failed_result(bin_dir, tmp_path, mcp_config):
    p = bin_dir / "claude"
    p.write_text("#!/nonexistent/interpreter\n")
    p.chmod(0o755)
    res = run_claude(tmp_path, mcp_config)
    assert not res.ok and res.final_text


def test_windows_batch_shims_are_refused(tmp_path, monkeypatch, mcp_config):
    monkeypatch.setattr("aletheore.council.runners.shutil.which", lambda name: None if name.endswith(".exe") else "/x/codex.cmd")
    wt = tmp_path / "wt"
    wt.mkdir()
    res = CodexRunner().run("do it", wt, mcp_config, {}, timeout_s=5)
    assert not res.ok and "batch shim" in res.final_text


@posix_only
def test_claude_command_shape_and_final_text(bin_dir, tmp_path, mcp_config):
    out = tmp_path / "args.json"
    fake_agent(bin_dir, "claude", f"""
        import json, sys
        json.dump(sys.argv[1:], open({str(out)!r}, "w"))
        print(json.dumps({{"type": "system", "subtype": "init"}}))
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "ALL DONE"}}))
    """)
    res = run_claude(tmp_path, mcp_config)
    assert res.ok and res.final_text == "ALL DONE" and res.exit_code == 0
    assert [e["type"] for e in res.raw_events] == ["system", "result"]
    args = json.loads(out.read_text())
    assert args[0] == "-p" and args[1] == "do it"  # prompt before variadic flags
    assert "--strict-mcp-config" in args and str(mcp_config) in args
    assert "--output-format" in args and "stream-json" in args and "--verbose" in args
    assert "Bash" not in args


@posix_only
def test_nonzero_exit_gives_ok_false_with_stderr_note(bin_dir, tmp_path, mcp_config):
    fake_agent(bin_dir, "claude", """
        import sys
        sys.stderr.write("boom happened")
        sys.exit(3)
    """)
    res = run_claude(tmp_path, mcp_config)
    assert not res.ok and res.exit_code == 3 and "boom happened" in res.final_text


@posix_only
def test_error_result_event_gives_ok_false(bin_dir, tmp_path, mcp_config):
    fake_agent(bin_dir, "claude", """
        import json
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "ran out"}))
    """)
    res = run_claude(tmp_path, mcp_config)
    assert not res.ok and res.final_text == "ran out"


@posix_only
def test_timeout_gives_ok_false_and_kills_child_tree(bin_dir, tmp_path, mcp_config):
    pidfile = tmp_path / "child.pid"
    fake_agent(bin_dir, "claude", f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(str(child.pid))
        time.sleep(60)
    """)
    res = run_claude(tmp_path, mcp_config, timeout_s=2)
    assert not res.ok and "timed out" in res.final_text
    pid = int(pidfile.read_text())
    time.sleep(0.5)
    with pytest.raises(OSError):
        os.kill(pid, 0)


@posix_only
def test_env_does_not_leak_unlisted_variables(bin_dir, tmp_path, mcp_config, monkeypatch):
    monkeypatch.setenv("SECRET_TEST_VAR", "leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    dump = tmp_path / "env.json"
    fake_agent(bin_dir, "claude", f"""
        import json, os
        json.dump(dict(os.environ), open({str(dump)!r}, "w"))
        print(json.dumps({{"type": "result", "is_error": False, "result": "ok"}}))
    """)
    run_claude(tmp_path, mcp_config)
    env = json.loads(dump.read_text())
    assert "SECRET_TEST_VAR" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["COUNCIL_AGENT"] == "a" and "PATH" in env


@posix_only
def test_codex_command_shape_and_final_text(bin_dir, tmp_path, mcp_config):
    out = tmp_path / "args.json"
    fake_agent(bin_dir, "codex", f"""
        import json, sys
        json.dump(sys.argv[1:], open({str(out)!r}, "w"))
        print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "first"}}}}))
        print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "FINAL"}}}}))
        print(json.dumps({{"type": "turn.completed"}}))
    """)
    wt = tmp_path / "wt"
    wt.mkdir()
    res = CodexRunner().run("do it", wt, mcp_config, {"COUNCIL_AGENT": "a"}, timeout_s=20)
    assert res.ok and res.final_text == "FINAL"
    args = json.loads(out.read_text())
    assert args[:2] == ["exec", "--json"] and "--sandbox" in args and "workspace-write" in args
    assert str(wt) in args and args[-1] == "do it"
    joined = " ".join(args)
    assert 'mcp_servers.council.command="/py"' in joined
    assert 'mcp_servers.council.default_tools_approval_mode="approve"' in joined
    assert 'mcp_servers.council.env={COUNCIL_AGENT="a"}' in joined


def test_scripted_runner_writes_files_in_worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    runner = ScriptedRunner(final_text="done", writes={"redis/a.py": "x = 1\n"})
    res = runner.run("task", wt, tmp_path / "unused.json", {}, timeout_s=5)
    assert res == RunResult(ok=True, final_text="done", exit_code=0, raw_events=[])
    assert (wt / "redis" / "a.py").read_text() == "x = 1\n"


@posix_only
def test_codex_runner_works_with_no_council_mcp_server(bin_dir, tmp_path):
    out = tmp_path / "args.json"
    fake_agent(bin_dir, "codex", f"""
        import json, sys
        json.dump(sys.argv[1:], open({str(out)!r}, "w"))
        print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "fine"}}}}))
    """)
    cfg = tmp_path / "empty.json"
    cfg.write_text(json.dumps({"mcpServers": {}}))
    wt = tmp_path / "wt"
    wt.mkdir()
    res = CodexRunner().run("do it", wt, cfg, {}, timeout_s=20)
    assert res.ok and res.final_text == "fine"
    assert not any("mcp_servers" in a for a in json.loads(out.read_text()))


def test_codex_gets_a_writable_sandbox_on_windows_only_for_write_runs(tmp_path, monkeypatch):
    import aletheore.council.runners as runners
    seen = []
    monkeypatch.setattr(runners, "_resolve", lambda name: "codex")
    monkeypatch.setattr(runners, "_run_process", lambda argv, *a, **k: (seen.append(argv), (0, '{"type": "item.completed", "item": {"type": "agent_message", "text": "fine"}}', "", False))[1])
    cfg = tmp_path / "empty.json"
    cfg.write_text(json.dumps({"mcpServers": {}}))
    flag = 'windows.sandbox="unelevated"'
    for platform, read_only, expected in (("win32", False, True), ("win32", True, False), ("linux", False, False)):
        monkeypatch.setattr(runners.sys, "platform", platform)
        CodexRunner().run("do it", tmp_path, cfg, {}, timeout_s=20, read_only=read_only)
        assert (flag in seen[-1]) is expected, (platform, read_only)


from aletheore.council.runners import _resolve


def test_shim_resolves_to_real_exe_on_path(tmp_path, monkeypatch):
    real = tmp_path / "bin" / "claude.exe"
    real.parent.mkdir()
    real.write_text("x")
    shim = tmp_path / "npm" / "claude.CMD"
    shim.parent.mkdir()
    shim.write_text("x")
    monkeypatch.setattr("aletheore.council.runners.shutil.which",
                        lambda name: str(real) if name == "claude.exe" else str(shim))
    assert _resolve("claude") == str(real)


def test_shim_resolves_to_exe_inside_its_own_npm_tree(tmp_path, monkeypatch):
    npm = tmp_path / "npm"
    exe = npm / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "bin" / "codex.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    (npm / "codex.cmd").write_text("x")
    monkeypatch.setattr("aletheore.council.runners.shutil.which",
                        lambda name: None if name == "codex.exe" else str(npm / "codex.cmd"))
    assert _resolve("codex") == str(exe)


def test_shim_with_no_real_exe_still_refused(tmp_path, monkeypatch):
    (tmp_path / "codex.cmd").write_text("x")
    monkeypatch.setattr("aletheore.council.runners.shutil.which",
                        lambda name: None if name == "codex.exe" else str(tmp_path / "codex.cmd"))
    with pytest.raises(AgentLaunchError, match="batch shim"):
        _resolve("codex")


def test_read_only_runs_get_no_write_tools_and_a_read_only_sandbox(tmp_path, monkeypatch):
    import aletheore.council.runners as r
    seen = {}

    def fake(argv, cwd, env, timeout_s):
        seen["argv"] = argv
        return 0, '{"type":"result","result":"ok","is_error":false}\n{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}', "", False

    monkeypatch.setattr(r, "_run_process", fake)
    monkeypatch.setattr(r, "_resolve", lambda b: f"/bin/{b}")
    cfg = tmp_path / "m.json"
    cfg.write_text('{"mcpServers": {}}')
    ClaudeRunner().run("argue", tmp_path, cfg, {}, 5, read_only=True)
    tools = seen["argv"][seen["argv"].index("--allowedTools") + 1:]
    assert "Read" in tools and "Grep" in tools and "Write" not in tools and "Edit" not in tools
    ClaudeRunner().run("work", tmp_path, cfg, {}, 5)
    assert "Write" in seen["argv"]
    CodexRunner().run("argue", tmp_path, cfg, {}, 5, read_only=True)
    a = seen["argv"]
    assert a[a.index("--sandbox") + 1] == "read-only"
    CodexRunner().run("work", tmp_path, cfg, {}, 5)
    assert seen["argv"][seen["argv"].index("--sandbox") + 1] == "workspace-write"


def test_scripted_runner_writes_nothing_when_read_only(tmp_path):
    r = ScriptedRunner(final_text="ok", writes={"a.txt": "x"})
    r.run("i", tmp_path, tmp_path / "m.json", {}, 5, read_only=True)
    assert not (tmp_path / "a.txt").exists() and r.read_only_calls == [True]
    r.run("i", tmp_path, tmp_path / "m.json", {}, 5)
    assert (tmp_path / "a.txt").exists() and r.read_only_calls == [True, False]


# ---- a failed run says why: the CLI's own error, not just its exit code ----------------------------------------------------------------------------

def _run_with_output(tmp_path, monkeypatch, out, err="", code=1, kind="claude"):
    import aletheore.council.runners as r
    monkeypatch.setattr(r, "_run_process", lambda argv, cwd, env, timeout_s: (code, out, err, False))
    monkeypatch.setattr(r, "_resolve", lambda b: f"/bin/{b}")
    cfg = tmp_path / "m.json"
    cfg.write_text('{"mcpServers": {}}')
    runner = ClaudeRunner() if kind == "claude" else CodexRunner()
    return runner.run("go", tmp_path, cfg, {}, 5)


def test_a_claude_failure_with_nothing_on_stderr_reports_the_error_from_its_output(tmp_path, monkeypatch):
    out = '{"type":"system","subtype":"init"}\n{"type":"result","subtype":"error","is_error":true,"result":"You have reached your usage limit. It resets at 4pm."}'
    res = _run_with_output(tmp_path, monkeypatch, out)
    assert not res.ok and "usage limit" in res.final_text


def test_stderr_still_wins_when_the_agent_wrote_there(tmp_path, monkeypatch):
    out = '{"type":"result","is_error":true,"result":"from stdout"}'
    res = _run_with_output(tmp_path, monkeypatch, out, err="from stderr")
    assert "from stderr" in res.final_text and "from stdout" not in res.final_text


def test_with_no_message_anywhere_the_exit_code_is_reported(tmp_path, monkeypatch):
    res = _run_with_output(tmp_path, monkeypatch, "", code=3)
    assert res.final_text == "agent exited with code 3"


def test_a_codex_failure_reports_its_error_event(tmp_path, monkeypatch):
    out = '{"type":"error","message":"rate limit exceeded, try again later"}'
    res = _run_with_output(tmp_path, monkeypatch, out, kind="codex")
    assert not res.ok and "rate limit" in res.final_text


def test_an_error_message_longer_than_the_limit_is_kept_to_its_tail(tmp_path, monkeypatch):
    out = json.dumps({"type": "result", "is_error": True, "result": "x" * 5000 + " THE-REASON"})
    res = _run_with_output(tmp_path, monkeypatch, out)
    assert len(res.final_text) <= 2000 and res.final_text.endswith("THE-REASON")


def test_a_read_only_run_cannot_post_to_the_room_with_the_tools(tmp_path, monkeypatch, mcp_config):
    """A woken agent answers in text and the worker posts it. If the tool stayed available, a message could talk the agent into posting
    itself, with no hand-off depth, and two agents could then call each other past the depth limit."""
    import aletheore.council.runners as r
    seen = {}

    def fake(argv, cwd, env, timeout_s):
        seen["argv"] = argv
        return 0, '{"type":"result","result":"ok","is_error":false}\n{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}', "", False

    monkeypatch.setattr(r, "_run_process", fake)
    monkeypatch.setattr(r, "_resolve", lambda b: f"/bin/{b}")
    wt = tmp_path / "wt"
    wt.mkdir()
    ClaudeRunner().run("look", wt, mcp_config, {}, 5, read_only=True)
    tools = seen["argv"][seen["argv"].index("--allowedTools") + 1:]
    assert "mcp__council__council_post" not in tools and "Write" not in tools and "Edit" not in tools
    assert "mcp__council__council_read" in tools                        # it can still read the room
    ClaudeRunner().run("work", wt, mcp_config, {}, 5)
    assert "mcp__council__council_post" in seen["argv"]                 # a work run keeps it

    CodexRunner().run("look", wt, mcp_config, {}, 5, read_only=True)
    assert not any("mcp_servers.council" in a for a in seen["argv"])    # no council tools at all for a read-only Codex run
    CodexRunner().run("work", wt, mcp_config, {}, 5)
    assert any("mcp_servers.council" in a for a in seen["argv"])
