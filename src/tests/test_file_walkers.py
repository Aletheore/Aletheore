"""The string-based file walkers must return exactly what the original
pathlib-based ones did - same files, same order - on the cases that trip
pathlib-vs-string rewrites: symlinks, sort order across '-' vs '/', nested
git repos, ignore patterns."""

import os
from pathlib import Path

import pytest

from aletheore.repo_config import is_ignored
from aletheore.scanner import detect, graph
from aletheore.scanner.detect import IGNORED_DIRS, _nested_git_roots
from aletheore.secrets import BINARY_EXTENSIONS, MAX_SCANNED_FILE_BYTES, iter_all_files


def _reference_walk(repo_path, ignored_paths, *, nested=True, secrets=False):
    """The pre-rewrite implementation, kept here as the oracle."""
    nested_git_roots = _nested_git_roots(repo_path) if nested else set()
    patterns = ignored_paths or []
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(repo_path).as_posix()
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not is_ignored(f"{rel_dir}/{d}" if rel_dir != "." else d, patterns)
        ]
        if nested and any(root in current_dir.parents or root == current_dir for root in nested_git_roots):
            dirnames[:] = []
            continue
        for filename in filenames:
            path = current_dir / filename
            if path.is_symlink() or not path.is_file():
                continue
            if secrets:
                if path.suffix in BINARY_EXTENSIONS:
                    continue
                try:
                    if path.stat().st_size > MAX_SCANNED_FILE_BYTES:
                        continue
                except OSError:
                    continue
            if is_ignored(path.relative_to(repo_path).as_posix(), patterns):
                continue
            yield path


@pytest.fixture
def tree(tmp_path):
    repo = (tmp_path / "repo").resolve()
    for rel in ["a/b.py", "a-b/x.py", "a.b/y.py", "ab/z.py", "A/up.py", "src/main.c", "src/util.h",
                "vendor/lib.go", "pkg/vendor/deep.go", "docs/readme.md", "img/logo.png", ".hidden.py",
                "node_modules/skip.js", "nested/.git/HEAD", "nested/inner.py", "space dir/f.py", "trailing."]:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    (repo / "big.py").write_bytes(b"x" * (MAX_SCANNED_FILE_BYTES + 1))
    os.symlink(repo / "a" / "b.py", repo / "link_to_file.py")
    os.symlink(repo / "a", repo / "link_to_dir")
    os.symlink(repo / "missing.py", repo / "dangling.py")
    os.mkfifo(repo / "fifo.py")
    return repo


@pytest.mark.parametrize("patterns", [None, ["vendor"], ["src/*.h", "docs/**"], ["a-b", "*.png"]])
def test_graph_walker_matches_reference(tree, patterns):
    assert list(graph._iter_source_files(tree, patterns)) == sorted(_reference_walk(tree, patterns))


@pytest.mark.parametrize("patterns", [None, ["vendor"], ["src/*.h", "docs/**"]])
def test_detect_walker_matches_reference(tree, patterns):
    assert list(detect._iter_source_files(tree, patterns)) == list(_reference_walk(tree, patterns))


@pytest.mark.parametrize("patterns", [None, ["vendor"], ["a-b"]])
def test_secrets_walker_matches_reference(tree, patterns):
    assert list(iter_all_files(tree, patterns)) == list(_reference_walk(tree, patterns, nested=False, secrets=True))


def test_sort_order_is_pathlibs_not_plain_string_order(tree):
    rels = [graph._rel(tree, p) for p in graph._iter_source_files(tree)]
    assert rels.index("a/b.py") < rels.index("a-b/x.py")  # "a" < "a-b" part by part
    assert "nested/inner.py" not in rels and "link_to_file.py" not in rels and "fifo.py" not in rels


def test_rel_matches_relative_to(tree):
    for path in [tree / "a" / "b.py", tree, tree / "a" / ".." / "b.py", Path("/etc/passwd"), tree.parent / "repo2" / "x"]:
        try:
            expected = path.relative_to(tree).as_posix()
        except ValueError:
            expected = None
        assert graph._rel(tree, path) == expected
