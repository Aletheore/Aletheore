import pytest

from scan_worker.blast_radius_summary import blast_radius_summary


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
