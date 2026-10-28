import io
import json
from pathlib import Path

import pytest

from aletheore import read_guard


def _scanned_repo(tmp_path: Path) -> Path:
    (tmp_path / ".aletheore").mkdir()
    return tmp_path


def test_allows_silently_when_no_prior_read_recorded(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    assert read_guard.pre_read_decision(payload) is None


def test_denies_a_full_reread_of_an_unchanged_file(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}

    read_guard.record_read(payload)
    out = read_guard.pre_read_decision(payload)

    assert out is not None
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "unchanged on disk" in spec["permissionDecisionReason"]
    assert str(target) in spec["permissionDecisionReason"]


@pytest.mark.parametrize(
    "content",
    ["x = 1\n" * 2001, "y" * 2001 + "\n"],
    ids=["too-many-lines", "too-long-line"],
)
def test_a_file_one_read_would_only_partly_show_is_never_denied(tmp_path: Path, content: str):
    repo = _scanned_repo(tmp_path)
    target = repo / "big.py"
    target.write_text(content)
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}

    read_guard.record_read(payload)

    assert read_guard.pre_read_decision(payload) is None


def test_a_file_of_exactly_the_read_limit_is_still_tracked(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "edge.py"
    target.write_text("x = 1\n" * 2000)
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}

    read_guard.record_read(payload)

    assert read_guard.pre_read_decision(payload) is not None


def test_allows_again_after_the_file_changes(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(payload)

    target.write_text("x = 2\n")

    assert read_guard.pre_read_decision(payload) is None


def test_a_ranged_read_is_never_denied_even_after_a_recorded_full_read(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    full_payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(full_payload)

    ranged_payload = {
        "session_id": "s1",
        "cwd": str(repo),
        "tool_input": {"file_path": str(target), "offset": 1, "limit": 10},
    }
    assert read_guard.pre_read_decision(ranged_payload) is None


def test_a_ranged_read_does_not_overwrite_full_read_state(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    full_payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(full_payload)

    ranged_payload = {
        "session_id": "s1",
        "cwd": str(repo),
        "tool_input": {"file_path": str(target), "offset": 1, "limit": 10},
    }
    read_guard.record_read(ranged_payload)

    # the full-read record must still be intact, so a later full read of the unchanged file
    # is still recognized as redundant
    assert read_guard.pre_read_decision(full_payload) is not None


def test_a_different_session_has_independent_state(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    read_guard.record_read({"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}})

    other_session_payload = {"session_id": "s2", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    assert read_guard.pre_read_decision(other_session_payload) is None


def test_silently_does_nothing_without_a_scanned_aletheore_dir(tmp_path: Path):
    # no .aletheore/ dir created - must never create one standalone, matching file_cache.py's rule
    target = tmp_path / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(tmp_path), "tool_input": {"file_path": str(target)}}

    read_guard.record_read(payload)

    assert not (tmp_path / ".aletheore").exists()
    assert read_guard.pre_read_decision(payload) is None


def test_a_missing_file_is_not_recorded_and_never_denied(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    missing = repo / "gone.py"
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(missing)}}

    read_guard.record_read(payload)

    assert read_guard.pre_read_decision(payload) is None


@pytest.mark.parametrize("bad_session_id", ["../escape", "a/b", "a\\b", ".", ".."])
def test_a_malicious_session_id_is_refused_not_written_outside_the_state_dir(tmp_path: Path, bad_session_id: str):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": bad_session_id, "cwd": str(repo), "tool_input": {"file_path": str(target)}}

    read_guard.record_read(payload)

    assert list((repo / ".aletheore").rglob("*.json")) == []


def test_main_pre_is_a_noop_when_the_env_var_is_unset(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(payload)

    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    code = read_guard.main_pre(stdin=stdin, stdout=stdout, environ={})

    assert code == 0
    assert stdout.getvalue() == ""


def test_main_pre_denies_end_to_end_when_opted_in(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(payload)

    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    code = read_guard.main_pre(stdin=stdin, stdout=stdout, environ={"ALETHEORE_READ_GUARD": "1"})

    assert code == 0
    out = json.loads(stdout.getvalue())
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_post_records_end_to_end_when_opted_in(tmp_path: Path):
    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}

    stdin = io.StringIO(json.dumps(payload))
    code = read_guard.main_post(stdin=stdin, stdout=io.StringIO(), environ={"ALETHEORE_READ_GUARD": "1"})

    assert code == 0
    assert read_guard.pre_read_decision(payload) is not None


def test_main_never_raises_on_garbage_stdin(tmp_path: Path):
    stdin = io.StringIO("not json")
    assert read_guard.main_pre(stdin=stdin, stdout=io.StringIO(), environ={"ALETHEORE_READ_GUARD": "1"}) == 0
    stdin2 = io.StringIO("not json")
    assert read_guard.main_post(stdin=stdin2, stdout=io.StringIO(), environ={"ALETHEORE_READ_GUARD": "1"}) == 0


def test_cli_hook_pre_read_end_to_end(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from aletheore.cli import app

    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(payload)

    monkeypatch.setenv("ALETHEORE_READ_GUARD", "1")
    result = CliRunner().invoke(app, ["hook", "pre-read"], input=json.dumps(payload))

    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_pre_records_exact_size_only_for_an_actual_denial(tmp_path: Path):
    from aletheore.token_meter import load_events

    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 123\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(payload)

    out = io.StringIO()
    assert read_guard.main_pre(stdin=io.StringIO(json.dumps(payload)), stdout=out,
                               environ={"ALETHEORE_READ_GUARD": "1"}) == 0
    events, corrupt = load_events(repo)
    assert corrupt == 0
    assert len(events) == 1
    assert events[0].source == "read_guard"
    assert events[0].size_bytes == len(target.read_bytes())


def test_meter_record_failure_never_breaks_or_changes_the_hook_decision(tmp_path: Path, monkeypatch):
    from aletheore import token_meter

    repo = _scanned_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 123\n")
    payload = {"session_id": "s1", "cwd": str(repo), "tool_input": {"file_path": str(target)}}
    read_guard.record_read(payload)
    monkeypatch.setattr(token_meter, "record_read_guard", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))

    out = io.StringIO()
    assert read_guard.main_pre(stdin=io.StringIO(json.dumps(payload)), stdout=out,
                               environ={"ALETHEORE_READ_GUARD": "1"}) == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"
