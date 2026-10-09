"""Every non-Python file the package reads at runtime must be declared as package data.

setuptools only puts .py files in a wheel by default; anything else (rules, queries,
schemas, images) must match a pattern in [tool.setuptools.package-data]. Missing one
does not fail the build or any editable-install test: it only breaks pip-installed
copies. That is exactly how semgrep_rules/*.yaml and joern_queries/*.sc were left out of
every wheel, which made the semgrep scan fail on every pip-installed copy, including
the hosted worker.
"""
import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "aletheore"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _declared_patterns() -> list[str]:
    config = tomllib.loads(PYPROJECT.read_text())
    return config["tool"]["setuptools"]["package-data"]["aletheore"]


def _data_files() -> list[Path]:
    return [
        path for path in PACKAGE_ROOT.rglob("*")
        if path.is_file()
        and path.suffix not in (".py", ".pyc")
        and "__pycache__" not in path.parts
        and not path.name.startswith(".")
    ]


def test_every_non_python_file_in_the_package_is_declared_as_package_data():
    covered = {
        match.resolve()
        for pattern in _declared_patterns()
        for match in PACKAGE_ROOT.glob(pattern)
    }
    missing = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix() for path in _data_files() if path.resolve() not in covered
    )

    assert not missing, (
        "these files exist in the package but would not be in the wheel; add a pattern to "
        f"[tool.setuptools.package-data] in pyproject.toml: {missing}"
    )


def test_every_declared_package_data_pattern_matches_something():
    # A typo'd or stale pattern would otherwise pass silently.
    dead = [pattern for pattern in _declared_patterns() if not list(PACKAGE_ROOT.glob(pattern))]

    assert not dead, f"package-data patterns that match no file: {dead}"
