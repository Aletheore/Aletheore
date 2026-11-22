"""The blast-radius answer names the direct dependents that are not test files when some of them are."""

from pathlib import Path

from aletheore.query import find_blast_radius


def _evidence(importers: list[str]) -> dict:
    target = {"path": "src/core.py", "imports": [], "imported_by": importers}
    others = [{"path": p, "imports": ["src/core.py"], "imported_by": []} for p in importers]
    return {"repository": {"modules": [target, *others]}, "architecture": {"layer_violations": {"violations": []}}}


def test_direct_dependents_that_are_not_tests_are_listed_separately(tmp_path: Path):
    importers = ["src/a.py", "tests/test_core.py", "src/b.py", "pkg/test_other.py"]
    result = find_blast_radius(_evidence(importers), tmp_path, "src/core.py")
    assert result["direct_dependents"] == importers  # unchanged: the full list
    assert result["direct_dependents_non_test"] == ["src/a.py", "src/b.py"]


def test_the_extra_list_is_left_out_when_no_direct_dependent_is_a_test(tmp_path: Path):
    result = find_blast_radius(_evidence(["src/a.py", "src/b.py"]), tmp_path, "src/core.py")
    assert "direct_dependents_non_test" not in result


def test_pascal_case_test_files_count_as_tests(tmp_path: Path):
    importers = ["src/Money.cs", "tests/MoneyTests.cs", "src/Money.Tests/Helpers.cs"]
    result = find_blast_radius(_evidence(importers), tmp_path, "src/core.py")
    assert result["direct_dependents_non_test"] == ["src/Money.cs"]
