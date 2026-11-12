import sys
import pytest

from aletheore.council.capabilities import inspect_agents


def test_missing_binary_reports_unavailable_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    caps = {c.agent: c for c in inspect_agents()}
    assert caps["claude"].level == "unavailable" and not caps["claude"].installed
    assert caps["codex"].level == "unavailable"


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binaries")
def test_installed_verified_agents_are_workers_unverified_are_advisors(tmp_path, monkeypatch):
    for name in ("claude", "codex", "gemini"):
        p = tmp_path / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    caps = {c.agent: c for c in inspect_agents()}
    assert caps["claude"].level == "worker" and caps["codex"].level == "worker"
    assert caps["gemini"].level == "advisor"
    assert any("not verified" in n for n in caps["gemini"].notes)


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binaries")
def test_opencode_and_agy_are_workers_when_installed(tmp_path, monkeypatch):
    for name in ("opencode", "agy"):
        p = tmp_path / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    caps = {c.agent: c for c in inspect_agents()}
    assert caps["opencode"].level == "worker" and caps["agy"].level == "worker"
    assert any("Windows" in n for n in caps["opencode"].notes)
