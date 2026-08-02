import json
import subprocess
from unittest.mock import MagicMock

import pytest

from scan_worker.demo_sandbox_runner import SandboxRunError, run_sandboxed_scan


def test_run_sandboxed_scan_invokes_docker_with_gvisor_runtime(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=json.dumps({"ok": True}), stderr="")

    monkeypatch.setattr("scan_worker.demo_sandbox_runner.subprocess.run", fake_run)
    result = run_sandboxed_scan("https://github.com/octocat/Hello-World.git")

    assert result == {"ok": True}
    cmd = captured["cmd"]
    assert cmd[0:2] == ["docker", "run"]
    assert "--runtime=runsc" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--rm" in cmd
    assert cmd[-2] == "aletheore-demo-sandbox:latest"
    assert cmd[-1] == "https://github.com/octocat/Hello-World.git"
    assert captured["timeout"] == 90


def test_run_sandboxed_scan_rejects_a_url_that_is_not_a_github_repo(monkeypatch):
    # Defense in depth: app_server.demo_scan_validation already rejects this
    # before a job is ever enqueued, but this process is the one thing that
    # actually turns a string into a docker invocation, so it re-checks
    # rather than trusting the caller.
    called = []
    monkeypatch.setattr(
        "scan_worker.demo_sandbox_runner.subprocess.run", lambda *a, **k: called.append(1) or MagicMock()
    )

    with pytest.raises(SandboxRunError) as exc_info:
        run_sandboxed_scan("https://evil.example.com/pwn")

    assert exc_info.value.reason == "invalid_repo_url"
    assert not called


def test_run_sandboxed_scan_raises_on_nonzero_exit(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="clone failed")

    monkeypatch.setattr("scan_worker.demo_sandbox_runner.subprocess.run", fake_run)
    with pytest.raises(SandboxRunError) as exc_info:
        run_sandboxed_scan("https://github.com/octocat/Hello-World.git")
    assert exc_info.value.reason == "nonzero_exit"


def test_run_sandboxed_scan_reports_memory_limit_distinctly(monkeypatch):
    from scan_worker.demo_sandbox_runner import GIT_ANALYSIS_RESOURCE_EXIT_CODE

    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=GIT_ANALYSIS_RESOURCE_EXIT_CODE, stdout="", stderr="")

    monkeypatch.setattr("scan_worker.demo_sandbox_runner.subprocess.run", fake_run)
    with pytest.raises(SandboxRunError) as exc_info:
        run_sandboxed_scan("https://github.com/octocat/Hello-World.git")
    assert exc_info.value.reason == "memory_limited"


def test_run_sandboxed_scan_raises_on_non_json_stdout(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr("scan_worker.demo_sandbox_runner.subprocess.run", fake_run)
    with pytest.raises(SandboxRunError) as exc_info:
        run_sandboxed_scan("https://github.com/octocat/Hello-World.git")
    assert exc_info.value.reason == "non_json_output"


def test_run_sandboxed_scan_force_removes_container_on_timeout(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if args and args[0][0:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=90)
        return MagicMock(returncode=0)

    monkeypatch.setattr("scan_worker.demo_sandbox_runner.subprocess.run", fake_run)
    with pytest.raises(SandboxRunError) as exc_info:
        run_sandboxed_scan("https://github.com/octocat/Hello-World.git")
    assert exc_info.value.reason == "timeout"

    assert len(calls) == 2
    run_cmd = calls[0][0][0]
    cleanup_cmd = calls[1][0][0]
    assert cleanup_cmd[:3] == ["docker", "rm", "-f"]
    container_name_index = run_cmd.index("--name") + 1
    assert cleanup_cmd[3] == run_cmd[container_name_index]
