import io
import json
import os
import time

from aletheore import evidence_hook as eh


def _evidence(tmp_path):
    modules = [
        {"path": "app/core.py", "imports": [], "imported_by": ["app/api.py", "app/cli.py", "tests/test_api.py", "app/api.py"]},
        {"path": "app/api.py", "imports": ["app/core.py"], "imported_by": ["app/main.py"]},
        {"path": "app/cli.py", "imports": ["app/core.py"], "imported_by": []},
        {"path": "app/main.py", "imports": ["app/api.py"], "imported_by": []},
        {"path": "app/lonely.py", "imports": [], "imported_by": []},
        {"path": "tests/test_api.py", "imports": ["app/core.py"], "imported_by": []},
    ]
    return {
        "scanned_at": "2099-01-01T00:00:00+00:00",
        "repo_path": str(tmp_path),
        "repository": {"modules": modules, "dead_code": {"unreachable_modules": [{"path": "app/lonely.py"}]}},
        "git": {
            "file_ownership": {"app/core.py": [{"email": "a@x", "names": ["Ana"], "percent": 0.75}]},
            "hotspots": [
                {"path": "app/core.py", "churn_count": 40, "co_change_partners": [{"path": "app/api.py", "co_occurrences": 22}]}
            ],
        },
        "architecture": {
            "clusters": [{"id": 3, "modules": ["app/core.py", "app/api.py"]}],
            "layer_violations": {"violations": [{"from": "app/core.py", "to": "app/main.py", "reason": "x"}]},
        },
    }


def _repo(tmp_path, evidence=None):
    (tmp_path / ".aletheore").mkdir()
    (tmp_path / "app").mkdir()
    for name in ("core", "api", "cli", "main", "lonely"):
        (tmp_path / "app" / f"{name}.py").write_text("x = 1\n")
    (tmp_path / ".aletheore" / "air.json").write_text(json.dumps(evidence or _evidence(tmp_path)))
    return tmp_path


def _payload(tmp_path, tool="Edit", event="PreToolUse", path="app/core.py", session="s1"):
    return {
        "hook_event_name": event,
        "tool_name": tool,
        "session_id": session,
        "cwd": str(tmp_path),
        "tool_input": {"file_path": str(tmp_path / path)},
    }


def test_build_index_dedupes_importers_and_collects_the_facts(tmp_path):
    index = eh.build_index(_evidence(tmp_path))
    core = index["files"]["app/core.py"]
    assert core["importers_total"] == 3
    assert core["owners"] == [{"name": "Ana", "percent": 0.75}]
    assert core["churn"] == 40
    assert core["co_change"] == [{"path": "app/api.py", "n": 22}]
    assert core["layer_violations"] == 1
    assert core["cluster"] == 3 and core["cluster_size"] == 2
    assert index["files"]["app/lonely.py"]["unreachable"] is True


def test_edit_gets_an_evidence_block_before_it_happens(tmp_path):
    repo = _repo(tmp_path)
    out = eh.evidence_for_payload(_payload(repo))
    text = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "app/core.py" in text
    assert "3 direct importers" in text
    assert "Tests: test_api.py" in text
    assert "Owner: Ana (75% of commits)" in text
    assert "api.py (22x)" in text
    assert "1 existing violation" in text


def test_transitive_reach_counts_through_imports(tmp_path):
    repo = _repo(tmp_path)
    text = eh.evidence_for_payload(_payload(repo))["hookSpecificOutput"]["additionalContext"]
    assert "4 modules reached through imports" in text


def test_read_is_answered_after_not_before_and_edit_not_after(tmp_path):
    repo = _repo(tmp_path)
    assert eh.evidence_for_payload(_payload(repo, tool="Read", event="PreToolUse")) is None
    assert eh.evidence_for_payload(_payload(repo, tool="Edit", event="PostToolUse")) is None
    out = eh.evidence_for_payload(_payload(repo, tool="Read", event="PostToolUse"))
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_once_per_file_per_session_but_not_across_sessions(tmp_path):
    repo = _repo(tmp_path)
    assert eh.evidence_for_payload(_payload(repo, session="a")) is not None
    assert eh.evidence_for_payload(_payload(repo, session="a")) is None
    assert eh.evidence_for_payload(_payload(repo, session="b")) is not None


def test_silent_for_unknown_outside_or_empty_files(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app" / "new.py").write_text("y = 2\n")
    assert eh.evidence_for_payload(_payload(repo, path="app/new.py")) is None  # not in the scan
    assert eh.evidence_for_payload(_payload(repo, path="../../etc/hosts")) is None  # outside the repo
    # in the scan but nothing to say: no importers, no churn
    assert eh.evidence_for_payload(_payload(repo, path="app/cli.py", session="c")) is None


def test_ignores_other_tools_and_missing_fields(tmp_path):
    repo = _repo(tmp_path)
    assert eh.evidence_for_payload(_payload(repo, tool="Bash")) is None
    assert eh.evidence_for_payload({"tool_name": "Edit"}) is None
    assert eh.evidence_for_payload({}) is None


def test_pointer_mode_is_one_line(tmp_path):
    repo = _repo(tmp_path)
    text = eh.evidence_for_payload(_payload(repo), mode="pointer")["hookSpecificOutput"]["additionalContext"]
    assert "\n" not in text
    assert "3 importers" in text and "owner Ana" in text


def test_stale_file_is_flagged(tmp_path):
    evidence = _evidence(tmp_path)
    evidence["scanned_at"] = "2000-01-01T00:00:00+00:00"
    repo = _repo(tmp_path, evidence)
    text = eh.evidence_for_payload(_payload(repo))["hookSpecificOutput"]["additionalContext"]
    assert "changed after the last scan" in text


def test_index_is_rebuilt_when_the_scan_changes(tmp_path):
    repo = _repo(tmp_path)
    assert eh.load_index(repo)["files"]["app/core.py"]["churn"] == 40
    changed = _evidence(repo)
    changed["git"]["hotspots"][0]["churn_count"] = 99
    air = repo / ".aletheore" / "air.json"
    air.write_text(json.dumps(changed))
    os.utime(air, (time.time() + 5, time.time() + 5))
    assert eh.load_index(repo)["files"]["app/core.py"]["churn"] == 99


def test_a_huge_scan_defers_to_a_background_build_and_stays_silent(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    spawned = []
    monkeypatch.setattr(eh, "_INLINE_BUILD_MAX_BYTES", 10)
    monkeypatch.setattr(eh, "_spawn_background_build", lambda path: spawned.append(path))
    assert eh.load_index(repo) is None
    assert spawned == [repo]


def test_named_test_files_are_found_when_the_scan_skipped_the_test_folder(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_cli.py").write_text("pass\n")
    index = eh.build_index(_evidence(repo))
    text = eh.render(index, "app/cli.py", repo_root=repo)
    # app/cli.py has no importers/churn, so nothing notable: still silent
    assert text is None
    assert eh._fs_named_tests(repo, "app/cli.py") == ["tests/test_cli.py"]


def test_main_always_exits_zero_and_prints_json_only_with_evidence(tmp_path):
    repo = _repo(tmp_path)
    out = io.StringIO()
    assert eh.main([], stdin=io.StringIO(json.dumps(_payload(repo))), stdout=out) == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    silent = io.StringIO()
    assert eh.main([], stdin=io.StringIO("not json"), stdout=silent) == 0
    assert silent.getvalue() == ""
