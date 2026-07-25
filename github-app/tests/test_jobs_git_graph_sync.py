import os
import subprocess
from pathlib import Path

import pytest

from scan_worker.jobs import GRAPH_BRANCH, _sync_persistent_git_graph
from scan_worker.postgres_graph_store import PostgresRepoGraphStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


async def _insert_installation(pool, installation_id: int, account_login: str, **values) -> None:
    columns = ["installation_id", "account_login", *values.keys()]
    params = [installation_id, account_login, *values.values()]
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
    await pool.execute(
        f"INSERT INTO installations ({', '.join(columns)}) VALUES ({placeholders})",
        *params,
    )


def _run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "a@example.com")
    _run(repo, "config", "user.name", "Alice")
    (repo / "a.py").write_text("print('hi')\n")
    _run(repo, "add", "a.py")
    _run(repo, "commit", "-q", "-m", "initial")
    return repo


def _fake_evidence() -> dict:
    return {
        "git": {"available": True},
        "repository": {"modules": [{"path": "a.py", "imported_by": []}]},
    }


@pytest.mark.asyncio
async def test_sync_persistent_git_graph_overrides_git_data_with_postgres_backed_result(
    pool, tmp_path, monkeypatch
):
    await _insert_installation(pool, 701, "org")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    repo = _init_repo(tmp_path)

    result = _sync_persistent_git_graph(701, "org/repo", repo, _fake_evidence())

    assert result["git"]["available"] is True
    assert result["git"]["total_commits"] == 1
    assert result["git"]["ownership"][0]["email"] == "a@example.com"
    assert "hotspots" in result["git"]

    # Confirms this really landed in Postgres, not just returned in-memory -
    # a second sync of the same, unchanged repo should see prior state.
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 701, "org/repo")
    snapshot = store.load("unused", GRAPH_BRANCH)
    assert snapshot.last_synced_sha is not None


@pytest.mark.asyncio
async def test_sync_persistent_git_graph_degrades_gracefully_on_db_failure(tmp_path, monkeypatch):
    # A real production possibility (Postgres down, network blip) must
    # never break the PR scan this is a side effect of - the caller gets
    # back its own original evidence unchanged, not an exception.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nonexistent-host-for-this-test/db")
    repo = _init_repo(tmp_path)
    evidence = _fake_evidence()

    result = _sync_persistent_git_graph(702, "org/repo", repo, evidence)

    assert result is evidence
    assert result["git"] == {"available": True}


def test_sync_persistent_git_graph_skips_when_git_analysis_was_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    evidence = {"git": {"available": False}, "repository": {"modules": []}}

    result = _sync_persistent_git_graph(703, "org/repo", repo, evidence)

    assert result == evidence
