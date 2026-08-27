from pathlib import Path

from aletheore.scanner import graph as graph_module
from aletheore.scanner.graph import (
    PARALLEL_PARSE_MIN_FILES,
    _available_parallelism,
    _cgroup_v1_cpu_quota,
    _cgroup_v2_cpu_quota,
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


def test_a_single_available_core_never_invokes_the_process_pool(tmp_path, monkeypatch):
    # Regression: a repo well over PARALLEL_PARSE_MIN_FILES still must not
    # spawn a pool when _available_parallelism() says only one worker is
    # actually usable (confirmed against this project's own hosted
    # scan-worker containers: cpus: "1.0" in docker-compose.yml) - a pool
    # sized at 1 pays the ~150ms creation cost plus a second process
    # independently loading every tree-sitter grammar, for zero
    # parallelism benefit over staying sequential.
    monkeypatch.setattr(graph_module, "PARALLEL_PARSE_MIN_FILES", 0)
    monkeypatch.setattr(graph_module, "_available_parallelism", lambda: 1)

    repo = _make_multi_file_python_repo(tmp_path)

    calls = []
    monkeypatch.setattr(
        graph_module,
        "_parse_many_in_parallel",
        lambda *a, **k: calls.append(True) or [],
    )

    modules, _graph, _unparseable = build_module_graph(repo)

    assert calls == []
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


def test_cgroup_v2_cpu_quota_parses_a_real_restriction(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("200000 100000\n")
    assert _cgroup_v2_cpu_quota(cpu_max) == 2


def test_cgroup_v2_cpu_quota_rounds_a_fractional_quota_up(tmp_path):
    # 1.5 effective cores - a real shape (`docker run --cpus=1.5`), confirmed
    # directly. Rounds up rather than down: a container with 1.5 CPUs' worth
    # of quota can still usefully run 2 workers some of the time, and
    # truncating to 1 would waste half a core's worth of real parallelism.
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("150000 100000\n")
    assert _cgroup_v2_cpu_quota(cpu_max) == 2


def test_cgroup_v2_cpu_quota_returns_none_when_unrestricted(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("max 100000\n")
    assert _cgroup_v2_cpu_quota(cpu_max) is None


def test_cgroup_v2_cpu_quota_returns_none_when_file_is_absent(tmp_path):
    # Non-Linux (macOS, Windows) or a cgroup v1 host - both real, both
    # must degrade to "no v2 signal", not raise.
    assert _cgroup_v2_cpu_quota(tmp_path / "does-not-exist") is None


def test_cgroup_v1_cpu_quota_parses_a_real_restriction(tmp_path):
    quota_path = tmp_path / "cfs_quota_us"
    period_path = tmp_path / "cfs_period_us"
    quota_path.write_text("200000\n")
    period_path.write_text("100000\n")
    assert _cgroup_v1_cpu_quota(quota_path, period_path) == 2


def test_cgroup_v1_cpu_quota_returns_none_when_unrestricted():
    # cgroup v1's unrestricted marker is quota=-1, not a missing file.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        quota_path = Path(d) / "cfs_quota_us"
        period_path = Path(d) / "cfs_period_us"
        quota_path.write_text("-1\n")
        period_path.write_text("100000\n")
        assert _cgroup_v1_cpu_quota(quota_path, period_path) is None


def test_available_parallelism_env_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("ALETHEORE_PARALLEL_PARSE_JOBS", "3")
    monkeypatch.setattr(graph_module, "_cgroup_v2_cpu_quota", lambda: 1)
    monkeypatch.setattr(graph_module, "_cgroup_v1_cpu_quota", lambda: 1)
    assert _available_parallelism() == 3


def test_available_parallelism_ignores_a_malformed_env_override(monkeypatch):
    monkeypatch.setenv("ALETHEORE_PARALLEL_PARSE_JOBS", "not-a-number")
    monkeypatch.setattr(graph_module, "_cgroup_v2_cpu_quota", lambda: None)
    monkeypatch.setattr(graph_module, "_cgroup_v1_cpu_quota", lambda: None)
    monkeypatch.setattr(graph_module.os, "cpu_count", lambda: 8)
    assert _available_parallelism() == 8


def test_available_parallelism_takes_the_tightest_of_cpu_count_and_cgroup_quota(monkeypatch):
    monkeypatch.delenv("ALETHEORE_PARALLEL_PARSE_JOBS", raising=False)
    monkeypatch.setattr(graph_module.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(graph_module, "_cgroup_v2_cpu_quota", lambda: 2)
    monkeypatch.setattr(graph_module, "_cgroup_v1_cpu_quota", lambda: None)
    if hasattr(graph_module.os, "sched_getaffinity"):
        monkeypatch.setattr(graph_module.os, "sched_getaffinity", lambda pid: set(range(32)))
    assert _available_parallelism() == 2


def test_available_parallelism_falls_back_to_cpu_count_when_unrestricted(monkeypatch):
    monkeypatch.delenv("ALETHEORE_PARALLEL_PARSE_JOBS", raising=False)
    monkeypatch.setattr(graph_module.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(graph_module, "_cgroup_v2_cpu_quota", lambda: None)
    monkeypatch.setattr(graph_module, "_cgroup_v1_cpu_quota", lambda: None)
    if hasattr(graph_module.os, "sched_getaffinity"):
        monkeypatch.setattr(graph_module.os, "sched_getaffinity", lambda pid: set(range(8)))
    assert _available_parallelism() == 8


def test_available_parallelism_never_returns_less_than_one(monkeypatch):
    monkeypatch.delenv("ALETHEORE_PARALLEL_PARSE_JOBS", raising=False)
    monkeypatch.setattr(graph_module.os, "cpu_count", lambda: None)
    monkeypatch.setattr(graph_module, "_cgroup_v2_cpu_quota", lambda: None)
    monkeypatch.setattr(graph_module, "_cgroup_v1_cpu_quota", lambda: None)
    assert _available_parallelism() >= 1
