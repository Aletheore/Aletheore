"""Adapters: every agent CLI Council can run beyond Claude and Codex (OpenCode, AGY, and any CLI the user describes in agents.json)."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from aletheore.council import runners
from aletheore.council.agent_adapters import AdapterError, AdapterRunner, adapter_runner, load_user_adapters, known_kinds
from aletheore.council.runners import worker_kind

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake agent scripts are POSIX shebang scripts")

RECORD = """
    import json, os, sys
    from pathlib import Path
    Path("record.json").write_text(json.dumps({"argv": sys.argv[1:], "cfg": os.environ.get("OPENCODE_CONFIG_CONTENT"),
                                               "env": sorted(os.environ)}))
"""


def fake_agent(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!{sys.executable}\n" + textwrap.dedent(RECORD) + textwrap.dedent(body))
    p.chmod(0o755)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    return d


@pytest.fixture
def wt(tmp_path):
    d = tmp_path / "wt"
    d.mkdir()
    return d


@pytest.fixture
def mcp_config(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"council": {"command": "/py", "args": ["-m", "srv"], "env": {"COUNCIL_ROOM": "r1"}}}}))
    return p


def record(wt):
    return json.loads((wt / "record.json").read_text())


def events(*items):
    return "\n".join(json.dumps(i) for i in items)


OC_OK = events({"type": "step_start"}, {"type": "text", "part": {"type": "text", "text": "first thought"}},
               {"type": "text", "part": {"type": "text", "text": "the answer"}}, {"type": "step_finish"})


# ---- OpenCode --------------------------------------------------------------------------------------------------------------------------------
@posix_only
def test_opencode_runs_headless_with_inline_config_locked_to_file_edits_and_the_room_tools(bin_dir, wt, mcp_config):
    fake_agent(bin_dir, "opencode", f"print({OC_OK!r})")
    res = adapter_runner("opencode").run("do the thing", wt, mcp_config, {"COUNCIL_TOKEN": "secret"}, timeout_s=20)
    assert res.ok and res.final_text == "the answer"
    rec = record(wt)
    assert rec["argv"][:3] == ["run", "--pure", "--format=json"] and rec["argv"][-2:] == ["--", "do the thing"]
    cfg = json.loads(rec["cfg"])
    assert cfg["permission"] == {"edit": "allow", "bash": "deny", "webfetch": "deny"}
    council = cfg["mcp"]["council"]
    assert council["type"] == "local" and council["command"] == ["/py", "-m", "srv"] and council["enabled"] is True
    assert council["environment"] == {"COUNCIL_ROOM": "r1", "COUNCIL_TOKEN": "secret"}
    assert "secret" not in " ".join(rec["argv"])  # tokens travel in the environment, never on the command line


@posix_only
def test_opencode_read_only_denies_edits_and_a_dash_led_instruction_cannot_become_a_flag(bin_dir, wt, mcp_config):
    fake_agent(bin_dir, "opencode", f"print({OC_OK!r})")
    adapter_runner("opencode").run("--dangerously-do-things; rm -rf /", wt, mcp_config, {}, timeout_s=20, read_only=True)
    rec = record(wt)
    assert json.loads(rec["cfg"])["permission"]["edit"] == "deny"
    assert rec["argv"][-2:] == ["--", "--dangerously-do-things; rm -rf /"]  # one argument after "--", never a shell line


@posix_only
def test_opencode_error_event_and_empty_output_are_failures_with_a_reason(bin_dir, wt, mcp_config):
    err = events({"type": "error", "error": {"name": "UnknownError", "data": {"message": "model unavailable"}}})
    fake_agent(bin_dir, "opencode", f"print({err!r}); sys.exit(1)")
    res = adapter_runner("opencode").run("x", wt, mcp_config, {}, timeout_s=20)
    assert not res.ok and "model unavailable" in res.final_text
    fake_agent(bin_dir, "opencode", "print('{\"type\": \"step_start\"}')")
    res = adapter_runner("opencode").run("x", wt, mcp_config, {}, timeout_s=20)
    assert not res.ok and "no final message" in res.final_text


@posix_only
def test_opencode_model_comes_from_the_environment_and_images_are_attached(bin_dir, wt, mcp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_OPENCODE_MODEL", "opencode/big-pickle")
    fake_agent(bin_dir, "opencode", f"print({OC_OK!r})")
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    adapter_runner("opencode").run("look", wt, mcp_config, {}, timeout_s=20, images=(img,))
    argv = record(wt)["argv"]
    assert argv[argv.index("-m") + 1] == "opencode/big-pickle" and argv[argv.index("-f") + 1] == str(img)
    assert argv.index("-f") < argv.index("--")


# ---- AGY -------------------------------------------------------------------------------------------------------------------------------------
AGY_OK = json.dumps({"conversation_id": "c", "status": "SUCCESS", "response": "agy answer"})


@posix_only
def test_agy_runs_with_shell_web_and_mcp_denied_and_edits_accepted(bin_dir, wt, mcp_config):
    fake_agent(bin_dir, "agy", f"print({AGY_OK!r})")
    res = adapter_runner("agy").run("--odd start", wt, mcp_config, {"COUNCIL_TOKEN": "secret"}, timeout_s=20)
    assert res.ok and res.final_text == "agy answer"
    argv = record(wt)["argv"]
    assert argv[argv.index("--mode") + 1] == "accept-edits" and argv[argv.index("--output-format") + 1] == "json"
    assert "--print=--odd start" in argv  # the prompt is attached to the flag, so it can never be taken for another flag
    settings = json.loads(next(a for a in argv if a.startswith("--settings="))[len("--settings="):])
    assert set(settings["permissions"]["deny"]) >= {"command(*)", "read_url(*)", "mcp(*)"}
    assert "secret" not in " ".join(argv)


@posix_only
def test_agy_read_only_uses_plan_mode_and_failures_carry_the_reason(bin_dir, wt, mcp_config):
    fake_agent(bin_dir, "agy", f"print({AGY_OK!r})")
    adapter_runner("agy").run("x", wt, mcp_config, {}, timeout_s=20, read_only=True)
    argv = record(wt)["argv"]
    assert argv[argv.index("--mode") + 1] == "plan"
    bad = json.dumps({"status": "ERROR", "error": "not signed in"})
    fake_agent(bin_dir, "agy", f"print({bad!r})")
    res = adapter_runner("agy").run("x", wt, mcp_config, {}, timeout_s=20)
    assert not res.ok and "not signed in" in res.final_text
    fake_agent(bin_dir, "agy", "print('Authentication required. Please visit the URL to log in:')")
    res = adapter_runner("agy").run("x", wt, mcp_config, {}, timeout_s=20)
    assert not res.ok and res.final_text


# ---- shared behaviour --------------------------------------------------------------------------------------------------------------------------
@posix_only
def test_child_environment_is_the_allowlist_not_the_parents_secrets(bin_dir, wt, mcp_config, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "leak-me")
    fake_agent(bin_dir, "opencode", f"print({OC_OK!r})")
    adapter_runner("opencode").run("x", wt, mcp_config, {}, timeout_s=20)
    assert "SOME_API_KEY" not in record(wt)["env"]


def test_missing_binary_is_a_failed_result_not_a_crash(bin_dir, wt, mcp_config):
    res = adapter_runner("opencode").run("x", wt, mcp_config, {}, timeout_s=5)
    assert not res.ok and "not installed" in res.final_text


# ---- agents the user describes ---------------------------------------------------------------------------------------------------------------
def write_agents(tmp_path, agents):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "agents.json").write_text(json.dumps({"agents": agents}))


@posix_only
def test_a_user_defined_cli_runs_with_placeholders_and_plain_text_output(bin_dir, wt, mcp_config, tmp_path):
    write_agents(tmp_path, {"aider": {"command": ["aider", "--yes", "--message", "{instruction}", "--mcp", "{mcp_config}"],
                                      "output": "text", "read_only_command": ["aider", "--dry-run", "--message", "{instruction}"]}})
    fake_agent(bin_dir, "aider", "print('  plain answer  ')")
    assert worker_kind("bob-aider") == "aider"
    res = adapter_runner("aider").run("fix it; echo pwned", wt, mcp_config, {}, timeout_s=20)
    assert res.ok and res.final_text == "plain answer"
    argv = record(wt)["argv"]
    assert argv == ["--yes", "--message", "fix it; echo pwned", "--mcp", str(mcp_config)]  # one argument each, no shell
    adapter_runner("aider").run("look only", wt, mcp_config, {}, timeout_s=20, read_only=True)
    assert record(wt)["argv"] == ["--dry-run", "--message", "look only"]


@posix_only
def test_a_user_defined_cli_without_a_read_only_command_is_refused_for_read_only_runs(bin_dir, wt, mcp_config, tmp_path):
    write_agents(tmp_path, {"aider": {"command": ["aider", "{instruction}"], "output": "text"}})
    fake_agent(bin_dir, "aider", "print('x')")
    res = adapter_runner("aider").run("plan this", wt, mcp_config, {}, timeout_s=20, read_only=True)
    assert not res.ok and "read-only" in res.final_text and not (wt / "record.json").exists()


@posix_only
def test_a_user_defined_cli_can_report_json_with_a_field_path_and_failures_surface(bin_dir, wt, mcp_config, tmp_path):
    write_agents(tmp_path, {"mycli": {"command": ["mycli", "{instruction}"], "output": "json", "field": "result.text"}})
    ok = json.dumps({"result": {"text": "deep answer"}})
    fake_agent(bin_dir, "mycli", f"print({ok!r})")
    assert adapter_runner("mycli").run("x", wt, mcp_config, {}, timeout_s=20).final_text == "deep answer"
    fake_agent(bin_dir, "mycli", "print('{\"result\": {}}')")
    res = adapter_runner("mycli").run("x", wt, mcp_config, {}, timeout_s=20)
    assert not res.ok and "mycli" in res.final_text
    fake_agent(bin_dir, "mycli", "print('boom', file=sys.stderr); sys.exit(3)")
    res = adapter_runner("mycli").run("x", wt, mcp_config, {}, timeout_s=20)
    assert not res.ok and "boom" in res.final_text


def test_bad_agent_definitions_are_refused_with_a_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    for name, spec, why in (
        ("claude", {"command": ["x", "{instruction}"], "output": "text"}, "built in"),
        ("Bad Name", {"command": ["x", "{instruction}"], "output": "text"}, "letters"),
        ("noinstr", {"command": ["x"], "output": "text"}, "{instruction}"),
        ("nocmd", {"command": [], "output": "text"}, "command"),
        ("badout", {"command": ["x", "{instruction}"], "output": "xml"}, "output"),
        ("jsonnofield", {"command": ["x", "{instruction}"], "output": "json"}, "field"),
        ("shellish", {"command": "x {instruction}", "output": "text"}, "list"),
    ):
        write_agents(tmp_path, {name: spec})
        with pytest.raises(AdapterError, match=why):
            load_user_adapters()


def test_a_missing_or_unreadable_agents_file_is_no_agents_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    assert load_user_adapters() == {}
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "agents.json").write_text("{not json")
    with pytest.raises(AdapterError, match="agents.json"):
        load_user_adapters()


# ---- the list of kinds -----------------------------------------------------------------------------------------------------------------------
def test_worker_kind_knows_every_builtin_and_prefers_the_longest_match(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    for kind in ("claude", "codex", "opencode", "agy"):
        assert worker_kind(f"bob-{kind}") == kind and worker_kind(kind) == kind
    assert worker_kind("bob") is None and worker_kind("bob-gemini") is None
    write_agents(tmp_path, {"mycodex": {"command": ["x", "{instruction}"], "output": "text"}})
    assert worker_kind("bob-mycodex") == "mycodex"  # not "codex"
    assert set(known_kinds()) >= {"claude", "codex", "opencode", "agy", "mycodex"}


def test_adapter_runner_for_an_unknown_kind_says_which_kinds_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    with pytest.raises(AdapterError, match="opencode"):
        adapter_runner("nonesuch")
    assert isinstance(adapter_runner("opencode"), AdapterRunner) and runners.WORKER_KINDS[:2] == ("claude", "codex")
