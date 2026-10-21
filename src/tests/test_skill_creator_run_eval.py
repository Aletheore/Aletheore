"""skill-creator's run_eval.py ships adapted: it must never write into the user's own agent folders."""

import importlib
import os
import stat
import sys
from pathlib import Path

import pytest

from aletheore import skill_hub

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses a fake POSIX `claude` executable")


def test_trigger_eval_runs_in_a_scratch_project_not_the_users(tmp_path, monkeypatch):
    skill_dir = skill_hub.bundled_skills_dir() / "agent-workflows" / "skill-creator"
    monkeypatch.syspath_prepend(str(skill_dir))
    monkeypatch.delitem(sys.modules, "scripts", raising=False)
    run_eval = importlib.import_module("scripts.run_eval")
    for name in [m for m in sys.modules if m == "scripts" or m.startswith("scripts.")]:
        monkeypatch.setitem(sys.modules, name, sys.modules[name])  # restored (removed) when the test ends

    # A fake `claude` that records where it ran and which command files existed there.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "claude"
    fake.write_text(
        "#!/bin/sh\npwd > \"$FAKE_CWD_OUT\"\nls .claude/commands > \"$FAKE_CMDS_OUT\"\nprintf '{\"type\":\"result\"}\\n'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CWD_OUT", str(tmp_path / "cwd"))
    monkeypatch.setenv("FAKE_CMDS_OUT", str(tmp_path / "cmds"))

    user_project = tmp_path / "user-project"
    (user_project / ".claude").mkdir(parents=True)

    run_eval.run_single_query("a query", "demo", "a demo skill", 10, str(user_project))

    ran_in = Path((tmp_path / "cwd").read_text().strip())
    assert ran_in.resolve() != user_project.resolve()
    assert (tmp_path / "cmds").read_text().strip() != ""  # the command file existed while claude ran
    assert not (user_project / ".claude" / "commands").exists()  # and nothing was written to the user's project
    assert not ran_in.exists()  # the scratch project is removed afterwards
