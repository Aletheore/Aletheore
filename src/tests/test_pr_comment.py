from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment, format_file_overview


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


def _row(path, status="modified", additions=0, deletions=0, previous_path=None,
         functions_added=(), functions_removed=(), classes_added=(), classes_removed=(),
         has_module_data=True, dependents_count=0):
    return {
        "path": path,
        "status": status,
        "additions": additions,
        "deletions": deletions,
        "previous_path": previous_path,
        "functions_added": list(functions_added),
        "functions_removed": list(functions_removed),
        "classes_added": list(classes_added),
        "classes_removed": list(classes_removed),
        "has_module_data": has_module_data,
        "dependents_count": dependents_count,
    }


def test_format_file_overview_empty_rows_returns_empty_string():
    assert format_file_overview([]) == ""


def test_format_file_overview_shows_path_status_and_line_counts():
    body = format_file_overview([_row("app.py", additions=5, deletions=2)])
    assert "`app.py`" in body
    assert "modified" in body
    assert "+5/-2" in body


def test_format_file_overview_shows_function_symbol_changes():
    body = format_file_overview([_row("app.py", functions_added=["c"], functions_removed=["b"])])
    assert "+1 function" in body
    assert "-1 function" in body


def test_format_file_overview_new_file_says_new_file_not_a_diff():
    body = format_file_overview([_row("new.py", status="added", functions_added=["f1", "f2"])])
    assert "new file, 2 symbols" in body


def test_format_file_overview_removed_file_says_removed():
    body = format_file_overview([_row("gone.py", status="removed", functions_removed=["f1"])])
    lines = [line for line in body.splitlines() if "gone.py" in line]
    assert lines and "removed" in lines[0]


def test_format_file_overview_removed_file_does_not_say_removed_twice():
    # Real gap found on final review: the phrase already says "removed";
    # appending the raw status word too produced "removed · removed".
    body = format_file_overview([_row("gone.py", status="removed", functions_removed=["f1"])])
    line = next(line for line in body.splitlines() if "gone.py" in line)
    assert line.count("removed") == 1


def test_format_file_overview_new_file_does_not_also_say_added():
    # Same gap: "new file, 2 symbols" already says it was added.
    body = format_file_overview([_row("new.py", status="added", functions_added=["f1", "f2"])])
    line = next(line for line in body.splitlines() if "new.py" in line)
    parts = [p.strip() for p in line.split("·")]
    assert "added" not in parts


def test_format_file_overview_new_non_code_file_still_says_added():
    # Regression guard: a newly-added file with no module data (has_module_data
    # False) gets an empty phrase from _symbol_change_phrase, so suppressing
    # the raw "added" status word there would drop the only signal that the
    # file is new at all.
    body = format_file_overview([_row("data.json", status="added", has_module_data=False)])
    line = next(line for line in body.splitlines() if "data.json" in line)
    parts = [p.strip() for p in line.split("·")]
    assert "added" in parts


def test_format_file_overview_shows_renamed_from():
    body = format_file_overview([_row("src/new_name.py", status="renamed", previous_path="src/old_name.py")])
    assert "renamed from `src/old_name.py`" in body


def test_format_file_overview_pure_rename_does_not_also_say_renamed():
    # The "(renamed from ...)" annotation already says it; a bare "renamed"
    # status token alongside it is redundant.
    body = format_file_overview(
        [_row("src/new_name.py", status="renamed", previous_path="src/old_name.py")]
    )
    line = next(line for line in body.splitlines() if "new_name.py" in line)
    parts = [p.strip() for p in line.split("·")]
    assert "renamed" not in parts


def test_format_file_overview_renamed_file_with_a_real_change_shows_both(
):
    body = format_file_overview([_row(
        "src/new_name.py", status="renamed", previous_path="src/old_name.py",
        functions_added=["f1"],
    )])
    assert "renamed from `src/old_name.py`" in body
    assert "+1 function" in body


def test_format_file_overview_shows_dependents_count():
    body = format_file_overview([_row("lib.py", dependents_count=3)])
    assert "3 other dependents" in body


def test_format_file_overview_singular_dependent_is_not_pluralized():
    body = format_file_overview([_row("lib.py", dependents_count=1)])
    assert "1 other dependent" in body
    assert "1 other dependents" not in body


def test_format_file_overview_escapes_a_backtick_not_at_the_boundary_without_padding():
    # Real gap found by Flash Review on the PR itself: GFM only requires
    # padding when the content starts or ends with a backtick. Padding
    # unconditionally (the original fix) introduces GFM's own space-
    # stripping rule (a leading/trailing space is silently removed) for
    # the common case - a backtick in the middle of a path, not at either
    # edge - where no padding was ever structurally required.
    body = format_file_overview([_row("odd`file.py")])
    line = next(line for line in body.splitlines() if "odd" in line)
    assert "``odd`file.py``" in line


def test_format_file_overview_pads_a_backtick_at_the_boundary():
    # A backtick genuinely at the start/end of the content DOES need
    # padding, or it would merge visually with the fence itself.
    body = format_file_overview([_row("`leading.py")])
    line = next(line for line in body.splitlines() if "leading.py" in line)
    assert "`` `leading.py ``" in line


def test_format_file_overview_truncates_past_20_files_honestly():
    rows = [_row(f"file_{i}.py") for i in range(25)]
    body = format_file_overview(rows)
    assert "file_19.py" in body
    assert "file_20.py" not in body
    assert "+5 more changed file(s)" in body


def test_format_file_overview_truncation_line_hedges_when_compare_may_be_capped():
    # Real gap found on final review: GitHub's compare API itself caps at
    # 300 files. Past that cap, "+N more" already understates the true
    # count, since files beyond it were never returned at all - the
    # renderer can't know the real total, so it must say so rather than
    # present a number that reads as exact.
    rows = [_row(f"file_{i}.py") for i in range(25)]
    body = format_file_overview(rows, possibly_capped=True)
    assert "+5 or more changed file(s)" in body


def test_format_file_overview_truncation_line_is_exact_when_not_capped():
    rows = [_row(f"file_{i}.py") for i in range(25)]
    body = format_file_overview(rows, possibly_capped=False)
    assert "+5 more changed file(s)" in body
    assert "or more" not in body


def test_format_file_overview_header_states_deterministic_and_always_posted():
    body = format_file_overview([_row("app.py")])
    assert "Deterministic" in body


def test_format_diff_comment_prepends_file_overview_right_after_the_header():
    diff = _empty_diff()
    overview = format_file_overview([_row("app.py")])
    body = format_diff_comment(diff, file_overview=overview)
    lines = body.splitlines()
    assert lines[0] == COMMENT_MARKER
    assert lines[1] == "### 🔍 Aletheore evidence diff"
    assert any("What changed" in line for line in lines[2:5])
    assert body.index("What changed") < body.index("No new secrets")


def test_format_diff_comment_still_says_nothing_to_report_alongside_a_file_overview():
    # The real bug this pins: prepending a non-empty file_overview must not
    # silently break the "nothing new" fallback message by changing what
    # body's length looks like at the point that check runs.
    diff = _empty_diff()
    overview = format_file_overview([_row("app.py")])
    body = format_diff_comment(diff, file_overview=overview)
    assert "No new secrets, vulnerabilities, or layer violations" in body


def test_format_diff_comment_with_no_file_overview_is_unchanged():
    body = format_diff_comment(_empty_diff())
    assert "What changed" not in body
