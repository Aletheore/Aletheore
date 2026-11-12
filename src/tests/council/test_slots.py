import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from aletheore.council import worktrees
from aletheore.council.runners import RunResult, ScriptedRunner
from aletheore.council.slots import AgentSlots, LimitedRunner


def test_only_limit_agents_run_at_once_and_the_rest_wait(tmp_path):
    slots = AgentSlots(tmp_path, limit=2)
    a, b = slots.acquire(), slots.acquire()
    got = []
    t = threading.Thread(target=lambda: got.append(slots.acquire(timeout_s=5)))
    t.start()
    time.sleep(0.4)
    assert got == []  # both slots taken: the third waits
    slots.release(a)
    t.join(3)
    assert len(got) == 1
    slots.release(b)
    slots.release(got[0])


def test_timeout_when_no_slot_frees_up(tmp_path):
    slots = AgentSlots(tmp_path, limit=1)
    held = slots.acquire()
    with pytest.raises(TimeoutError):
        slots.acquire(timeout_s=0.5)
    slots.release(held)


def test_slots_are_shared_across_processes_and_freed_when_a_holder_dies(tmp_path):
    code = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from aletheore.council.slots import AgentSlots
        s = AgentSlots(Path({str(tmp_path)!r}), limit=1)
        h = s.acquire()
        print("held", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(TimeoutError):
            AgentSlots(tmp_path, limit=1).acquire(timeout_s=0.5)
    finally:
        proc.kill()
        proc.wait()
    h = AgentSlots(tmp_path, limit=1).acquire(timeout_s=3)
    AgentSlots(tmp_path, limit=1).release(h)


def test_limited_runner_holds_a_slot_only_while_running_and_passes_images_through(tmp_path):
    inner = ScriptedRunner(final_text="ok")
    slots = AgentSlots(tmp_path, limit=1)
    r = LimitedRunner(inner, slots)
    res = r.run("do it", tmp_path, tmp_path / "m.json", {}, 5, images=(tmp_path / "a.png",))
    assert res.ok and inner.image_calls == [(tmp_path / "a.png",)]
    held = slots.acquire(timeout_s=1)  # the slot was released after the run
    slots.release(held)


def test_limited_runner_reports_a_failed_result_instead_of_waiting_forever(tmp_path):
    slots = AgentSlots(tmp_path, limit=1)
    held = slots.acquire()
    r = LimitedRunner(ScriptedRunner(final_text="ok"), slots, wait_s=0.4)
    res = r.run("x", tmp_path, tmp_path / "m.json", {}, 5)
    assert not res.ok and "free agent slot" in res.final_text
    slots.release(held)


def test_git_retries_when_another_process_holds_a_lock(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = subprocess.run

    def flaky(argv, **kw):
        if "rev-parse" in argv:
            calls["n"] += 1
            if calls["n"] <= 2:
                return subprocess.CompletedProcess(argv, 128, "", "fatal: Unable to create '/x/.git/index.lock': File exists.")
        return real(argv, **kw)

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(worktrees.subprocess, "run", flaky)
    monkeypatch.setattr(worktrees, "_LOCK_RETRY_DELAY_S", 0.01)
    assert worktrees._git(tmp_path, "rev-parse", "--git-dir").returncode == 0
    assert calls["n"] == 3
    calls["n"] = -100  # always locked: gives up with the real error after a few tries
    with pytest.raises(worktrees.GitError, match="index.lock"):
        worktrees._git(tmp_path, "rev-parse", "--git-dir")
