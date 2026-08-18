import io
import json
import urllib.error

import pytest

from scan_worker.demo_scan import (
    DemoScanError,
    _run_sandboxed_scan,
    _summarize_for_public_display,
    run_demo_scan_job,
)


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_urlopen_returning(payload: dict):
    def _fake(request, timeout):
        return _FakeHTTPResponse(json.dumps(payload).encode())

    return _fake


def _fake_urlopen_raising_http_error(status: int, reason: str):
    def _fake(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, status, reason, {}, io.BytesIO(json.dumps({"error": reason}).encode())
        )

    return _fake


def _fake_evidence(**overrides) -> dict:
    base = {
        "repository": {
            "languages": [{"name": "Python", "loc": 1000}],
            "dead_code": {
                "unreachable_modules": [
                    {"path": f"module_{i}.py", "reason": "no other module imports this file"}
                    for i in range(8)
                ],
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


def test_summarize_dead_code_sample_is_plain_path_strings():
    # unreachable_modules entries are {"path": ..., "reason": ...} dicts
    # (dead_code.py) - the sample must extract the path string, not pass
    # the dict through, or it renders as "[object Object]" on the live
    # demo page.
    summary = _summarize_for_public_display(_fake_evidence())
    assert summary["dead_code"]["sample"][0] == "module_0.py"
    assert all(isinstance(path, str) for path in summary["dead_code"]["sample"])


def test_summarize_language_lines_reads_the_real_loc_field():
    # air.json's language entries use "loc", not "lines" - confirmed
    # against a real scan output, not assumed.
    summary = _summarize_for_public_display(_fake_evidence())
    assert summary["languages"][0]["lines"] == 1000


def test_summarize_handles_empty_evidence_gracefully():
    summary = _summarize_for_public_display({})
    assert summary["languages"] == []
    assert summary["dead_code"]["unreachable_module_count"] == 0
    assert summary["secrets"]["finding_count"] == 0


def test_summarize_notes_osv_is_held_back():
    summary = _summarize_for_public_display(_fake_evidence())
    assert "OSV" in summary["held_back"]["vulnerabilities"]


def test_run_sandboxed_scan_posts_repo_url_to_the_runner_and_returns_its_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _FakeHTTPResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr("scan_worker.demo_scan.urllib.request.urlopen", fake_urlopen)
    result = _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")

    assert result == {"ok": True}
    assert captured["url"].endswith("/run")
    assert captured["body"] == {"repo_url": "https://github.com/octocat/Hello-World.git"}
    assert captured["timeout"] > 90  # past the runner's own container timeout


def test_run_sandboxed_scan_raises_on_nonzero_exit_reason(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.demo_scan.urllib.request.urlopen", _fake_urlopen_raising_http_error(422, "nonzero_exit")
    )
    with pytest.raises(DemoScanError):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_sandboxed_scan_gives_a_specific_message_when_memory_limited(monkeypatch):
    # Confirmed directly: a full scan of the Linux kernel got OOM-killed
    # under the runner's 1GB container limit, and the CLI (via
    # GitAnalysisError) exits with GIT_ANALYSIS_RESOURCE_EXIT_CODE for
    # exactly this case - the demo should say so plainly rather than a
    # generic failure message.
    monkeypatch.setattr(
        "scan_worker.demo_scan.urllib.request.urlopen", _fake_urlopen_raising_http_error(422, "memory_limited")
    )
    with pytest.raises(DemoScanError, match="pip install aletheore"):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_sandboxed_scan_raises_on_non_json_output_reason(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.demo_scan.urllib.request.urlopen", _fake_urlopen_raising_http_error(422, "non_json_output")
    )
    with pytest.raises(DemoScanError):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_sandboxed_scan_raises_a_timed_out_message_on_timeout_reason(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.demo_scan.urllib.request.urlopen", _fake_urlopen_raising_http_error(422, "timeout")
    )
    with pytest.raises(DemoScanError, match="timed out"):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_sandboxed_scan_reports_the_runner_being_unreachable_distinctly(monkeypatch):
    # A network/connectivity failure to the runner itself is not a
    # repo-shaped problem - the message should say so, not blame the repo.
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("scan_worker.demo_scan.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(DemoScanError, match="temporarily unavailable"):
        _run_sandboxed_scan("https://github.com/octocat/Hello-World.git")


def test_run_demo_scan_job_returns_summarized_result(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.demo_scan.urllib.request.urlopen", _fake_urlopen_returning(_fake_evidence())
    )
    result = run_demo_scan_job("https://github.com/octocat/Hello-World.git")

    assert result["secrets"]["finding_count"] == 1
    assert "AKIA-super-secret-do-not-leak" not in json.dumps(result)
