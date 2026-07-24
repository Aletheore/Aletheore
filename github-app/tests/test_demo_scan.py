import json
import subprocess
from unittest.mock import MagicMock

import pytest

from scan_worker.demo_scan import (
    DemoScanError,
    _run_sandboxed_scan,
    _summarize_for_public_display,
    run_demo_scan_job,
)


def _fake_evidence(**overrides) -> dict:
    base = {
        "repository": {
            "languages": [{"name": "Python", "lines": 1000}],
            "dead_code": {
                "unreachable_modules": [f"module_{i}.py" for i in range(8)],
                "unused_dependencies": ["requests"],
            },
            "api_endpoints": {"endpoints": [{"path": "/a"}, {"path": "/b"}]},
        },
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": "config.py",
                        "line": 12,
                        "pattern": "aws_access_key_id",
                        "match_preview": "AKIA-super-secret-do-not-leak",
                    }
                ]
            },
            "dependency_licenses": {"findings": [{"package": "left-pad", "category": "permissive"}]},
        },
        "architecture": {"clusters": [{"id": 1}, {"id": 2}]},
    }
    base.update(overrides)
    return base


def test_summarize_never_includes_secret_match_preview():
    summary = _summarize_for_public_display(_fake_evidence())
    assert "AKIA-super-secret-do-not-leak" not in json.dumps(summary)
    assert summary["secrets"]["finding_count"] == 1
    assert summary["secrets"]["sample"][0] == {"path": "config.py", "line": 12, "pattern": "aws_access_key_id"}


def test_summarize_caps_sample_list_size_but_keeps_true_count():
    summary = _summarize_for_public_display(_fake_evidence())
    assert summary["dead_code"]["unreachable_module_count"] == 8
    assert len(summary["dead_code"]["sample"]) == 5


def test_summarize_handles_empty_evidence_gracefully():
    summary = _summarize_for_public_display({})
    assert summary["languages"] == []
    assert summary["dead_code"]["unreachable_module_count"] == 0
    assert summary["secrets"]["finding_count"] == 0


def test_summarize_notes_osv_is_held_back():
    summary = _summarize_for_public_display(_fake_evidence())
    assert "OSV" in summary["held_back"]["vulnerabilities"]


def test_run_sandboxed_scan_invokes_docker_with_gvisor_runtime(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=json.dumps({"ok": True}), stderr="")

    monkeypatch.setattr("scan_worker.demo_scan.subprocess.run", fake_run)
    result = _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")

    assert result == {"ok": True}
    cmd = captured["cmd"]
    assert cmd[0:2] == ["docker", "run"]
    assert "--runtime=runsc" in cmd
    assert "--rm" in cmd
    assert cmd[-2] == "aletheore-demo-sandbox:latest"
    assert cmd[-1] == "https://github.com/octocat/Hello-World.git"
    assert captured["timeout"] == 90


def test_run_sandboxed_scan_raises_on_nonzero_exit(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="clone failed")

    monkeypatch.setattr("scan_worker.demo_scan.subprocess.run", fake_run)
    with pytest.raises(DemoScanError):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_sandboxed_scan_raises_on_non_json_stdout(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr("scan_worker.demo_scan.subprocess.run", fake_run)
    with pytest.raises(DemoScanError):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_sandboxed_scan_force_removes_container_on_timeout(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if args and args[0][0:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=90)
        return MagicMock(returncode=0)

    monkeypatch.setattr("scan_worker.demo_scan.subprocess.run", fake_run)
    with pytest.raises(DemoScanError, match="timed out"):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")

    # First call was the (timed-out) docker run, second must be the forced
    # cleanup - the container name from the failed call is what gets removed.
    assert len(calls) == 2
    run_cmd = calls[0][0][0]
    cleanup_cmd = calls[1][0][0]
    assert cleanup_cmd[:3] == ["docker", "rm", "-f"]
    container_name_index = run_cmd.index("--name") + 1
    assert cleanup_cmd[3] == run_cmd[container_name_index]


def test_run_demo_scan_job_returns_summarized_result(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=json.dumps(_fake_evidence()), stderr="")

    monkeypatch.setattr("scan_worker.demo_scan.subprocess.run", fake_run)
    result = run_demo_scan_job("https://github.com/octocat/Hello-World.git")

    assert result["secrets"]["finding_count"] == 1
    assert "AKIA-super-secret-do-not-leak" not in json.dumps(result)
