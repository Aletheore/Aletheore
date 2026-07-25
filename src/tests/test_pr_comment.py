from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment


def _empty_diff():
    return {
        "secrets": {"new": [], "resolved": []},
        "history_secrets": {"new": [], "resolved": []},
        "vulnerabilities": {"new": [], "resolved": []},
        "layer_violations": {"new": [], "resolved": []},
        "aggregate_deltas": {
            "module_count": 0,
            "dependency_graph_edge_count": 0,
            "total_commits": 0,
        },
        "caveats": [],
    }


def test_marker_is_first_line():
    body = format_diff_comment(_empty_diff())
    assert body.splitlines()[0] == COMMENT_MARKER


def test_empty_diff_says_nothing_to_report():
    body = format_diff_comment(_empty_diff())
    assert "No new secrets, vulnerabilities, or layer violations" in body


def test_new_secret_is_bulleted_with_path_and_line():
    diff = _empty_diff()
    diff["secrets"]["new"] = [{"path": "config.py", "line": 12, "pattern": "aws_key"}]
    body = format_diff_comment(diff)
    assert "`config.py:12`" in body
    assert "(aws_key)" in body


def test_resolved_secret_shows_resolved_marker():
    diff = _empty_diff()
    diff["secrets"]["resolved"] = [{"path": "old.py", "line": 3, "pattern": "token"}]
    body = format_diff_comment(diff)
    assert "resolved: `old.py:3`" in body


def test_placeholder_secret_gets_suffix():
    diff = _empty_diff()
    diff["secrets"]["new"] = [
        {"path": "a.py", "line": 1, "pattern": "key", "likely_placeholder": True}
    ]
    body = format_diff_comment(diff)
    assert "likely placeholder" in body


def test_accepted_secret_gets_baseline_suffix():
    diff = _empty_diff()
    diff["secrets"]["new"] = [{"path": "a.py", "line": 1, "pattern": "key", "accepted": True}]
    body = format_diff_comment(diff)
    assert "accepted (in .aletheore.json baseline)" in body


def test_history_secret_shows_short_commit():
    diff = _empty_diff()
    diff["history_secrets"]["new"] = [
        {"path": "a.py", "commit": "abcdef1234567890", "pattern": "key"}
    ]
    body = format_diff_comment(diff)
    assert "in abcdef12" in body


def test_new_vulnerability_is_bulleted():
    diff = _empty_diff()
    diff["vulnerabilities"]["new"] = [
        {
            "package": "requests",
            "installed_version": "2.0.0",
            "advisory_id": "GHSA-xxxx",
            "ecosystem": "PyPI",
        }
    ]
    body = format_diff_comment(diff)
    assert "requests 2.0.0 - GHSA-xxxx (PyPI)" in body


def test_new_layer_violation_is_bulleted():
    diff = _empty_diff()
    diff["layer_violations"]["new"] = [
        {"from": "ui", "to": "db", "reason": "UI must not import DB directly"}
    ]
    body = format_diff_comment(diff)
    assert "`ui` -> `db`: UI must not import DB directly" in body


def test_nonzero_aggregate_deltas_are_shown():
    diff = _empty_diff()
    diff["aggregate_deltas"] = {
        "module_count": 3,
        "dependency_graph_edge_count": -1,
        "total_commits": 5,
    }
    body = format_diff_comment(diff)
    assert "Modules: +3" in body
    assert "Dependency graph edges: -1" in body
    assert "Commits: 5" in body


def test_caveats_are_shown_as_blockquotes():
    diff = _empty_diff()
    diff["caveats"] = ["evidence.json schema version mismatch"]
    body = format_diff_comment(diff)
    assert "> ⚠️ evidence.json schema version mismatch" in body
