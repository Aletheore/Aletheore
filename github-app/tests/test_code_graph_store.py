import os
from datetime import datetime

import pytest

from scan_worker.code_graph_store import CodeGraphStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


async def _insert_installation(pool, installation_id: int, account_login: str) -> None:
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        installation_id,
        account_login,
    )


def _module(path, content_hash, language="python", imports=None, functions=None, classes=None):
    return {
        "path": path,
        "language": language,
        "imports": imports or [],
        "symbols": {"functions": functions or [], "classes": classes or []},
        "content_hash": content_hash,
    }


def _endpoint(method, path, file, line):
    return {"method": method, "path": path, "file": file, "line": line}


@pytest.mark.asyncio
async def test_load_content_hashes_returns_empty_for_unknown_repo(pool):
    await _insert_installation(pool, 701, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 701, "org/repo")

    assert store.load_content_hashes("main") == {}


@pytest.mark.asyncio
async def test_apply_module_deltas_then_load_round_trips_content_hashes(pool):
    await _insert_installation(pool, 702, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 702, "org/repo")
    modules = [
        _module("a.py", "hash-a", imports=["b.py"], functions=[{"name": "f", "start_line": 1, "end_line": 2}]),
        _module("b.py", "hash-b"),
    ]

    store.apply_module_deltas(
        "main", modules, deleted_paths=[], new_sync_sha="s1", new_sync_at=datetime(2026, 7, 26)
    )

    assert store.load_content_hashes("main") == {"a.py": "hash-a", "b.py": "hash-b"}


@pytest.mark.asyncio
async def test_apply_module_deltas_persists_symbols_and_edges(pool):
    await _insert_installation(pool, 703, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 703, "org/repo")
    modules = [
        _module(
            "a.py", "hash-a", imports=["b.py"],
            functions=[{"name": "f", "start_line": 1, "end_line": 5}],
            classes=[{"name": "C", "start_line": 10, "end_line": 20}],
        ),
        _module("b.py", "hash-b"),
    ]

    store.apply_module_deltas(
        "main", modules, deleted_paths=[], new_sync_sha="s1", new_sync_at=datetime(2026, 7, 26)
    )

    symbols = store.load_symbols_for_path("main", "a.py")
    assert {"name": "f", "kind": "function", "start_line": 1, "end_line": 5} in symbols
    assert {"name": "C", "kind": "class", "start_line": 10, "end_line": 20} in symbols

    assert store.load_dependents("main", "b.py") == ["a.py"]


@pytest.mark.asyncio
async def test_apply_module_deltas_only_touches_changed_files(pool):
    # Core incrementality guarantee: re-applying with an unrelated new file
    # must not disturb an already-persisted, unchanged file's rows.
    await _insert_installation(pool, 704, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 704, "org/repo")
    store.apply_module_deltas(
        "main",
        [_module("a.py", "hash-a", functions=[{"name": "f", "start_line": 1, "end_line": 2}])],
        deleted_paths=[],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 7, 26),
    )

    store.apply_module_deltas(
        "main",
        [_module("c.py", "hash-c")],
        deleted_paths=[],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 7, 27),
    )

    assert store.load_content_hashes("main") == {"a.py": "hash-a", "c.py": "hash-c"}
    assert store.load_symbols_for_path("main", "a.py") == [
        {"name": "f", "kind": "function", "start_line": 1, "end_line": 2}
    ]


@pytest.mark.asyncio
async def test_apply_module_deltas_replaces_symbols_for_a_changed_file(pool):
    await _insert_installation(pool, 705, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 705, "org/repo")
    store.apply_module_deltas(
        "main",
        [_module("a.py", "hash-a", functions=[{"name": "old_fn", "start_line": 1, "end_line": 2}])],
        deleted_paths=[],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 7, 26),
    )

    store.apply_module_deltas(
        "main",
        [_module("a.py", "hash-a2", functions=[{"name": "new_fn", "start_line": 5, "end_line": 6}])],
        deleted_paths=[],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 7, 27),
    )

    symbols = store.load_symbols_for_path("main", "a.py")
    assert symbols == [{"name": "new_fn", "kind": "function", "start_line": 5, "end_line": 6}]


@pytest.mark.asyncio
async def test_apply_module_deltas_removes_deleted_file_and_its_edges(pool):
    await _insert_installation(pool, 706, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 706, "org/repo")
    store.apply_module_deltas(
        "main",
        [_module("a.py", "hash-a", imports=["b.py"]), _module("b.py", "hash-b")],
        deleted_paths=[],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 7, 26),
    )

    store.apply_module_deltas(
        "main", [], deleted_paths=["a.py"], new_sync_sha="s2", new_sync_at=datetime(2026, 7, 27)
    )

    assert "a.py" not in store.load_content_hashes("main")
    assert store.load_dependents("main", "b.py") == []


@pytest.mark.asyncio
async def test_load_endpoint_keys_returns_empty_for_unknown_repo(pool):
    await _insert_installation(pool, 707, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 707, "org/repo")

    assert store.load_endpoint_keys("main") == {}


@pytest.mark.asyncio
async def test_apply_endpoint_deltas_then_load_round_trips(pool):
    await _insert_installation(pool, 708, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 708, "org/repo")
    endpoints = [_endpoint("GET", "/users", "app.py", 10)]

    store.apply_endpoint_deltas("main", endpoints, deleted_keys=[])

    assert store.load_endpoint_keys("main") == {("GET", "/users"): {"file": "app.py", "line": 10}}


@pytest.mark.asyncio
async def test_apply_endpoint_deltas_removes_deleted_keys(pool):
    await _insert_installation(pool, 709, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 709, "org/repo")
    store.apply_endpoint_deltas("main", [_endpoint("GET", "/users", "app.py", 10)], deleted_keys=[])

    store.apply_endpoint_deltas("main", [], deleted_keys=[("GET", "/users")])

    assert store.load_endpoint_keys("main") == {}


@pytest.mark.asyncio
async def test_different_branches_are_isolated(pool):
    await _insert_installation(pool, 710, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 710, "org/repo")

    store.apply_module_deltas(
        "main", [_module("a.py", "hash-a")], deleted_paths=[], new_sync_sha="s1", new_sync_at=datetime(2026, 7, 26)
    )
    store.apply_module_deltas(
        "feature", [_module("b.py", "hash-b")], deleted_paths=[], new_sync_sha="s2", new_sync_at=datetime(2026, 7, 27)
    )

    assert store.load_content_hashes("main") == {"a.py": "hash-a"}
    assert store.load_content_hashes("feature") == {"b.py": "hash-b"}


@pytest.mark.asyncio
async def test_installation_deletion_cascades_to_code_graph_tables(pool):
    await _insert_installation(pool, 711, "org")
    store = CodeGraphStore(TEST_DATABASE_URL, 711, "org/repo")
    store.apply_module_deltas(
        "main", [_module("a.py", "hash-a")], deleted_paths=[], new_sync_sha="s1", new_sync_at=datetime(2026, 7, 26)
    )

    await pool.execute("DELETE FROM installations WHERE installation_id = 711")

    assert store.load_content_hashes("main") == {}
