"""Path scoping for `aletheore dependents`: `--under <dir>`, `--exclude <name>` and `--no-tests`."""

from __future__ import annotations

import re

_TEST_DIRS = {"test", "tests", "__tests__", "testdata", "spec", "specs", "e2e", "fixtures", "__mocks__"}
_TEST_NAME = re.compile(
    r"(^test_.*\.py$|_test\.(py|go)$|^conftest\.py$|\.(test|spec)\.[cm]?[jt]sx?$|(Test|Tests|IT)\.(java|kt)$|_spec\.rb$)"
)


def is_test_path(path: str) -> bool:
    """True for files that are tests by the common conventions of the supported languages: a tests/ style directory
    or a test-shaped file name (test_x.py, x_test.go, x.test.ts, x.spec.js, XTest.java, ...)."""
    parts = path.split("/")
    return bool(_TEST_DIRS & set(parts[:-1])) or bool(_TEST_NAME.search(parts[-1]))


def matches(path: str, pattern: str) -> bool:
    """`pattern` is a whole directory name or file name ("tests", "locale"), not a substring: "locale" must not drop
    a module called django/middleware/locale.py."""
    return pattern in path.split("/")


def normalize_under(under: list[str] | None) -> list[str]:
    """Directory prefixes ending in '/', with './' and trailing '/' removed; '' and '.' mean the whole repo."""
    cleaned = [u.removeprefix("./").strip("/") for u in (under or [])]
    return [u + "/" for u in cleaned if u not in ("", ".")]


def in_scope(path: str, exclude: list[str], under: list[str], no_tests: bool) -> bool:
    """`under` must already be normalized (see normalize_under)."""
    if any(matches(path, e) for e in exclude) or (no_tests and is_test_path(path)):
        return False
    return not under or path.startswith(tuple(under))

