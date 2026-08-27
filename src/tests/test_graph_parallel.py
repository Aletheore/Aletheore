from pathlib import Path

from aletheore.scanner import graph as graph_module
from aletheore.scanner.graph import (
    PARALLEL_PARSE_MIN_FILES,
    _extract_module,
    _parse_and_extract_one,
    build_module_graph,
)


def _make_multi_file_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    app = repo / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "config.py").write_text("SETTING = 1\n\n\ndef load():\n    return SETTING\n")
    (app / "auth.py").write_text(
        "from app.config import load\n\n\ndef check():\n    return load()\n"
    )
    (app / "main.py").write_text(
        "from app import auth\nfrom app.config import SETTING\n\n\ndef run():\n    return auth.check() + SETTING\n"
    )
    return repo


def _normalize(modules, dependency_graph):
    # Order was never asserted anywhere in the existing suite (confirmed
    # directly against test_graph*.py/test_evidence.py before this change) -
    # this normalizes list order only, so the comparison is about content,
    # not incidental completion/iteration order.
    modules_sorted = sorted(modules, key=lambda m: m["path"])
    for m in modules_sorted:
        m["imports"] = sorted(m["imports"])
        m["imported_by"] = sorted(m["imported_by"])
    edges_sorted = sorted(dependency_graph["edges"])
    return modules_sorted, {"nodes": dependency_graph["nodes"], "edges": edges_sorted}


def test_parallel_and_sequential_paths_produce_identical_results(tmp_path, monkeypatch):
    # The real regression guard: forces the same multi-file, multi-import
    # repo through both code paths (parallel pool vs sequential fallback,
    # via the PARALLEL_PARSE_MIN_FILES threshold) and asserts they agree,
    # rather than trusting the two implementations stay in sync by
    # inspection alone.
    repo = _make_multi_file_python_repo(tmp_path)

    monkeypatch.setattr(graph_module, "PARALLEL_PARSE_MIN_FILES", 0)
    parallel_modules, parallel_graph, parallel_unparseable = build_module_graph(repo)

    monkeypatch.setattr(graph_module, "PARALLEL_PARSE_MIN_FILES", 10_000)
    sequential_modules, sequential_graph, sequential_unparseable = build_module_graph(repo)

    parallel_modules, parallel_graph = _normalize(parallel_modules, parallel_graph)
    sequential_modules, sequential_graph = _normalize(sequential_modules, sequential_graph)

    assert parallel_modules == sequential_modules
    assert parallel_graph == sequential_graph
    assert parallel_unparseable == sequential_unparseable == []


def test_small_repo_never_invokes_the_process_pool(tmp_path, monkeypatch):
    repo = _make_multi_file_python_repo(tmp_path)

    calls = []
    monkeypatch.setattr(
        graph_module,
        "_parse_many_in_parallel",
        lambda *a, **k: calls.append(True) or [],
    )

    modules, _graph, _unparseable = build_module_graph(repo)

    assert calls == []
    # Every file still got parsed via the sequential fallback - the pool
    # being skipped isn't silently dropping work.
    assert {m["path"] for m in modules} == {"app/__init__.py", "app/config.py", "app/auth.py", "app/main.py"}


def test_parallel_parse_min_files_default_is_reasonable():
    # Not asserting an exact number (the design doc calls this an
    # implementation-time empirical choice, not a fixed contract) - just
    # that it's a real, positive threshold, not accidentally 0 or negative
    # (which would force every scan, however small, through the pool).
    assert PARALLEL_PARSE_MIN_FILES > 0


def test_worker_pool_round_trip_is_actually_picklable(tmp_path):
    # The whole point of this feature: prove a real ProcessPoolExecutor
    # round-trip works end to end (path in, module dict out) - not just
    # that the code is structured plausibly. A mocked-out pool would never
    # catch a real pickling failure (e.g. accidentally trying to pass a
    # tree_sitter.Tree/Language across the process boundary).
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(5):
        (repo / f"mod{i}.py").write_text(f"VALUE_{i} = {i}\n\n\ndef get_{i}():\n    return VALUE_{i}\n")

    modules = graph_module._parse_many_in_parallel(
        paths=[repo / f"mod{i}.py" for i in range(5)],
        repo_path=repo,
        python_source_roots=[repo],
        go_module_prefix=None,
        has_rust_crate_root=False,
        php_psr4_map={},
    )

    assert {m["path"] for m in modules} == {f"mod{i}.py" for i in range(5)}
    by_path = {m["path"]: m for m in modules}
    for i in range(5):
        funcs = {f["name"] for f in by_path[f"mod{i}.py"]["symbols"]["functions"]}
        assert f"get_{i}" in funcs


def test_extract_module_dispatches_python_and_returns_expected_shape(tmp_path):
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser

    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "a.py"
    path.write_text("def foo():\n    pass\n")

    parser = Parser()
    parser.language = Language(tspython.language())
    source = path.read_bytes()
    tree = parser.parse(source)

    module = _extract_module(path, repo, tree, source, "python", python_source_roots=[repo])

    assert module["path"] == "a.py"
    assert module["language"] == "python"
    assert module["imports"] == []
    assert module["imported_by"] == []
    assert module["symbols"]["functions"][0]["name"] == "foo"


def test_parse_and_extract_one_matches_extract_module(tmp_path):
    from tree_sitter import Parser

    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "a.py"
    path.write_text("def foo():\n    pass\n")

    result = _parse_and_extract_one(
        path,
        repo,
        Parser(),
        python_source_roots=[repo],
        go_module_prefix=None,
        has_rust_crate_root=False,
        php_psr4_map={},
    )

    assert result["path"] == "a.py"
    assert result["language"] == "python"
    assert result["symbols"]["functions"][0]["name"] == "foo"
