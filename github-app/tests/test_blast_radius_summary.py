import pytest

from scan_worker.blast_radius_summary import (
    blast_radius_summary,
    build_change_diagram,
    compute_blast_radius,
    count_direct_dependents,
)


def _evidence(edges: dict[str, list[str]]) -> dict:
    """edges maps a module to the modules that import it (its imported_by)."""
    paths = set(edges) | {p for deps in edges.values() for p in deps}
    return {
        "repository": {
            "modules": [{"path": p, "imports": [], "imported_by": edges.get(p, [])} for p in sorted(paths)]
        }
    }


def test_no_evidence_or_no_known_changed_module_returns_nothing():
    assert blast_radius_summary(None, ["a.py"]) == ""
    assert blast_radius_summary({}, ["a.py"]) == ""
    # a changed file the scan doesn't know (docs, config) must not produce a "nothing imports it" claim
    assert blast_radius_summary(_evidence({"lib.py": ["app.py"]}), ["README.md"]) == ""


def test_lists_direct_and_indirect_dependents_outside_the_pr():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"], "svc.py": ["api.py"]})
    out = blast_radius_summary(evidence, ["core.py"])
    assert "3 other file(s) depend on what this PR changes (2 directly, 1 indirectly)" in out
    assert "`core.py` is imported by `cli.py`, `svc.py`" in out
    assert "Indirectly affected: `api.py`" in out
    assert "not guessed by a model" in out
    assert out.count("<details>") == 1 and out.count("</details>") == 1


def test_files_already_in_the_pr_are_not_counted_as_affected():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"]})
    out = blast_radius_summary(evidence, ["core.py", "svc.py"])
    assert "1 other file(s)" in out and "`cli.py`" in out
    assert "`svc.py` is imported" not in out


def test_says_so_when_nothing_imports_the_changed_file():
    out = blast_radius_summary(_evidence({"leaf.py": []}), ["leaf.py"])
    assert "no other file in the repo imports the changed file(s)" in out
    assert "<details>" not in out


def test_lists_are_capped_and_the_overflow_is_counted():
    dependents = [f"d{i}.py" for i in range(9)]
    out = blast_radius_summary(_evidence({"core.py": dependents}), ["core.py"])
    assert "9 other file(s)" in out
    assert "(+5 more)" in out  # 4 shown per target


def test_many_changed_files_are_capped():
    edges = {f"m{i}.py": [f"user{i}.py"] for i in range(9)}
    out = blast_radius_summary(_evidence(edges), list(edges))
    assert "...and 3 more changed file(s) with dependents" in out


def test_a_malformed_module_entry_never_raises(monkeypatch):
    def boom(*a, **k):
        raise KeyError("imported_by")

    monkeypatch.setattr("scan_worker.blast_radius_summary.find_blast_radius", boom)
    assert blast_radius_summary(_evidence({"core.py": ["a.py"]}), ["core.py"]) == ""


def test_compute_blast_radius_exposes_per_file_direct_and_indirect_dependents():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"], "svc.py": ["api.py"]})
    result = compute_blast_radius(evidence, ["core.py"])

    assert result["analysed"] == 1
    assert result["per_target"] == {"core.py": ["cli.py", "svc.py"]}
    assert result["direct"] == {"cli.py", "svc.py"}
    assert result["indirect"] == {"api.py"}
    assert result["truncated"] is False


def test_compute_blast_radius_excludes_files_already_in_the_pr():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"]})
    result = compute_blast_radius(evidence, ["core.py", "svc.py"])

    assert result["per_target"] == {"core.py": ["cli.py"]}
    assert "svc.py" not in result["direct"]


def test_compute_blast_radius_skips_an_unknown_changed_file_and_reports_zero_analysed():
    evidence = _evidence({"lib.py": ["app.py"]})
    result = compute_blast_radius(evidence, ["README.md"])

    assert result["analysed"] == 0
    assert result["per_target"] == {}


def test_count_direct_dependents_is_not_capped_at_fifty():
    # Real gap found on final review: find_blast_radius (which
    # compute_blast_radius reads its per_target counts from) truncates
    # direct_dependents to _BLAST_RADIUS_MAX_DIRECT (50) BEFORE the
    # already-in-PR exclusion runs, so a hub file's true dependent count
    # silently reads as "50" or fewer once routed through per_target. This
    # function reads evidence's own `imported_by` adjacency directly - no
    # BFS, no cap - specifically so a per-file count can't undercount.
    hub_dependents = [f"user{i}.py" for i in range(60)]
    evidence = _evidence({"core.py": hub_dependents})

    counts = count_direct_dependents(evidence, ["core.py"])

    assert counts["core.py"] == 60


def test_count_direct_dependents_excludes_files_already_in_the_pr():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"]})
    counts = count_direct_dependents(evidence, ["core.py", "svc.py"])

    assert counts["core.py"] == 1


def test_count_direct_dependents_omits_a_file_with_no_dependents():
    evidence = _evidence({"leaf.py": []})
    counts = count_direct_dependents(evidence, ["leaf.py"])

    assert "leaf.py" not in counts


def test_count_direct_dependents_omits_an_unknown_changed_file():
    evidence = _evidence({"lib.py": ["app.py"]})
    counts = count_direct_dependents(evidence, ["README.md"])

    assert counts == {}


@pytest.mark.parametrize(
    "env_value,expect_call",
    [(None, True), ("on", True), ("off", False), ("0", False), ("False", False), (" no ", False)],
)
def test_summary_section_only_reads_exact_head_sha_evidence_and_honours_the_switch(
    monkeypatch, env_value, expect_call
):
    from scan_worker import jobs

    if env_value is None:
        monkeypatch.delenv("FLASH_REVIEW_BLAST_RADIUS", raising=False)
    else:
        monkeypatch.setenv("FLASH_REVIEW_BLAST_RADIUS", env_value)
    calls = []
    evidence = _evidence({"core.py": ["app.py"]})
    monkeypatch.setattr(
        "scan_worker.jobs._evidence_by_head_sha_or_none",
        lambda dsn, iid, repo, sha: calls.append(sha) or evidence,
    )
    # The "latest evidence" fallback describes possibly another branch, so it must never be consulted.
    monkeypatch.setattr(
        "scan_worker.jobs._latest_evidence_or_none",
        lambda *a, **k: pytest.fail("blast radius must not use the latest-evidence fallback"),
    )

    out = jobs._blast_radius_section_for("dsn", 1, "o/r", "abc123", ["core.py"])

    assert calls == (["abc123"] if expect_call else [])
    assert ("app.py" in out) is expect_call


def test_summary_section_is_empty_when_the_exact_scan_has_not_landed(monkeypatch):
    from scan_worker import jobs

    monkeypatch.delenv("FLASH_REVIEW_BLAST_RADIUS", raising=False)
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: None)
    assert jobs._blast_radius_section_for("dsn", 1, "o/r", "abc123", ["core.py"]) == ""


def test_summary_section_never_fails_the_review(monkeypatch):
    from scan_worker import jobs

    monkeypatch.delenv("FLASH_REVIEW_BLAST_RADIUS", raising=False)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", boom)
    assert jobs._blast_radius_section_for("dsn", 1, "o/r", "abc123", ["core.py"]) == ""


def test_change_diagram_is_empty_when_no_changed_file_has_dependents():
    evidence = _evidence({"leaf.py": []})
    assert build_change_diagram(evidence, ["leaf.py"]) == ""


def test_change_diagram_includes_changed_file_and_its_direct_dependents():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"]})
    out = build_change_diagram(evidence, ["core.py"])

    assert "```mermaid" in out and "```" in out.split("```mermaid", 1)[1]
    assert '"core.py"' in out
    assert '"svc.py"' in out
    assert '"cli.py"' in out
    assert out.count("-->") == 2


def test_change_diagram_omits_a_changed_file_with_no_dependents():
    evidence = _evidence({"core.py": ["svc.py"], "untouched.py": []})
    out = build_change_diagram(evidence, ["core.py", "untouched.py"])

    assert '"core.py"' in out
    assert "untouched.py" not in out


def test_change_diagram_marks_a_changed_file_that_has_a_new_finding():
    evidence = _evidence({"core.py": ["svc.py"]})
    out = build_change_diagram(evidence, ["core.py"], files_with_findings={"core.py"})

    assert "classDef changedFinding" in out
    assert "class n0 changedFinding" in out


def test_change_diagram_does_not_mark_a_changed_file_with_no_finding():
    evidence = _evidence({"core.py": ["svc.py"]})
    out = build_change_diagram(evidence, ["core.py"], files_with_findings={"other.py"})

    # classDef changedFinding is always pre-declared (harmless, unused-if-absent);
    # what matters is that it's never actually assigned to a node here.
    assert "class n0 changed\n" in out
    assert "class n0 changedFinding" not in out


def test_change_diagram_uses_basenames_not_full_paths_as_labels():
    evidence = _evidence({"src/core.py": ["src/svc.py"]})
    out = build_change_diagram(evidence, ["src/core.py"])

    assert '"core.py"' in out
    assert '"src/core.py"' not in out


def test_change_diagram_disambiguates_colliding_basenames_with_full_paths():
    # Flash Review finding on this PR: src/utils.py and tests/utils.py would
    # otherwise both render as two identically-labeled "utils.py" nodes with
    # no way to tell them apart - defeats the diagram's whole point.
    evidence = _evidence({"src/utils.py": ["tests/utils.py"]})
    out = build_change_diagram(evidence, ["src/utils.py"])

    assert '"src/utils.py"' in out
    assert '"tests/utils.py"' in out
    assert out.count('"utils.py"') == 0


def test_change_diagram_escapes_a_double_quote_in_a_filename():
    evidence = _evidence({'weird"file.py': ["svc.py"]})
    out = build_change_diagram(evidence, ['weird"file.py'])

    # a raw unescaped quote inside a quoted Mermaid label would break the node syntax
    assert 'weird"file.py"' not in out
    assert "weird" in out


def test_change_diagram_never_raises_on_a_malformed_module_entry(monkeypatch):
    def boom(*a, **k):
        raise KeyError("imported_by")

    monkeypatch.setattr("scan_worker.blast_radius_summary.find_blast_radius", boom)
    assert build_change_diagram(_evidence({"core.py": ["a.py"]}), ["core.py"]) == ""
