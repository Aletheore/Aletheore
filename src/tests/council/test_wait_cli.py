import json
import subprocess
import threading
import time

import pytest
from typer.testing import CliRunner

from aletheore.council import cli
from aletheore.council.runners import ScriptedRunner

runner = CliRunner()


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def room(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "make_runner", lambda agent: ScriptedRunner(final_text="ok"))
    res = runner.invoke(cli.app, ["start", "--repo", str(repo), "--host", "alice"])
    assert res.exit_code == 0, res.output
    return res.output.strip().split()[-1]


def say(room, who, text):
    res = runner.invoke(cli.app, ["say", text, "--room", room, "--me", who])
    assert res.exit_code == 0, res.output


def test_wait_times_out_with_status_3(room):
    res = runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "0.2", "--poll", "0.05"])
    assert res.exit_code == 3 and "nothing addressed" in res.output


def test_wait_prints_a_mention_and_then_waits_for_the_next(room):
    runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "0.1", "--poll", "0.05"])   # sets the marker at the end
    say(room, "bob", "@alice are you there")
    res = runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "2", "--poll", "0.05"])
    assert res.exit_code == 0 and "bob" in res.output and "@alice are you there" in res.output
    again = runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "0.2", "--poll", "0.05"])
    assert again.exit_code == 3


def test_wait_json_is_machine_readable(room):
    runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "0.1", "--poll", "0.05"])
    say(room, "bob", "@alice json please")
    res = runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "2", "--poll", "0.05", "--json"])
    (event,) = json.loads(res.output)
    assert event["from"] == "bob" and event["body"]["text"] == "@alice json please"


def test_wait_wakes_while_a_message_arrives(room):
    runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "0.1", "--poll", "0.05"])
    threading.Timer(0.4, lambda: say(room, "bob", "@alice late news")).start()
    started = time.monotonic()
    res = runner.invoke(cli.app, ["wait", "--room", room, "--me", "alice", "--timeout", "10", "--poll", "0.05"])
    assert res.exit_code == 0 and "late news" in res.output and time.monotonic() - started < 5


def test_job_set_and_show(room):
    res = runner.invoke(cli.app, ["job", "set", "test every new revision", "--room", room, "--me", "alice", "--agent", "codex"])
    assert res.exit_code == 0, res.output
    shown = runner.invoke(cli.app, ["job", "show", "--room", room, "--me", "alice"])
    assert "codex: test every new revision" in shown.output
    runner.invoke(cli.app, ["job", "set", "", "--room", room, "--me", "alice", "--agent", "codex"])
    assert "no standing jobs" in runner.invoke(cli.app, ["job", "show", "--room", room, "--me", "alice"]).output


def test_a_stranger_cannot_set_a_job(room):
    res = runner.invoke(cli.app, ["job", "set", "hijack", "--room", room, "--me", "mallory", "--agent", "codex"])
    assert res.exit_code != 0
