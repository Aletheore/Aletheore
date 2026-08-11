import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scan_worker.jobs import (
    GRAPH_BRANCH,
    GRAPH_COLD_SYNC_DEPTH_CAP,
    SECRETS_HISTORY_DEPTH_CAP,
    _run_scan,
    _sync_persistent_git_graph,
)
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


def test_run_scan_sets_git_history_depth_cap_env_var(tmp_path):
    # This subprocess call runs `aletheore scan`, which hits the exact same
    # cold-sync memory ceiling as the Postgres persistence sync - and runs
    # first, before that sync code ever executes - so it needs the same
    # depth cap passed through as an env var (see evidence.py's
    # ALETHEORE_GIT_HISTORY_DEPTH_CAP handling).
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("scan_worker.jobs.subprocess.run") as mock_run:
        _run_scan(repo_dir)
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["ALETHEORE_GIT_HISTORY_DEPTH_CAP"] == str(GRAPH_COLD_SYNC_DEPTH_CAP)
    assert kwargs["env"]["ALETHEORE_SECRETS_HISTORY_DEPTH_CAP"] == str(SECRETS_HISTORY_DEPTH_CAP)


def test_run_scan_does_not_leak_our_secrets_into_the_subprocess_env(tmp_path, monkeypatch):
    # This subprocess parses source files from an arbitrary, attacker-
    # controlled customer repo - it must not inherit DATABASE_URL,
    # GITHUB_APP_PRIVATE_KEY, or any other secret from the scan-worker
    # container's own environment.
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-leak")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----should-not-leak")
    monkeypatch.setenv("SESSION_SECRET", "should-not-leak")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("scan_worker.jobs.subprocess.run") as mock_run:
        _run_scan(repo_dir)
    _, kwargs = mock_run.call_args
    env = kwargs["env"]
    assert "DATABASE_URL" not in env
    assert "GITHUB_APP_PRIVATE_KEY" not in env
    assert "SESSION_SECRET" not in env


def test_run_scan_always_disables_the_local_scan_cache(tmp_path):
    # Every hosted scan clones an untrusted, attacker-controllable repo -
    # ALETHEORE_DISABLE_LOCAL_SCAN_CACHE must always be set regardless of
    # whether an explicit unchanged_scan_cache_path was passed, so the
    # scanner never trusts a .aletheore/scan-cache.json the repo owner
    # could have committed themselves (see evidence.py for why that cache
    # is unsafe to read from a checkout you don't control).
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("scan_worker.jobs.subprocess.run") as mock_run:
        _run_scan(repo_dir)
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["ALETHEORE_DISABLE_LOCAL_SCAN_CACHE"] == "1"


def test_run_scan_disables_the_local_scan_cache_even_with_an_unchanged_cache_path(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cache_path = tmp_path / "unchanged-cache.json"
    with patch("scan_worker.jobs.subprocess.run") as mock_run:
        _run_scan(repo_dir, unchanged_scan_cache_path=cache_path)
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["ALETHEORE_DISABLE_LOCAL_SCAN_CACHE"] == "1"
    assert kwargs["env"]["ALETHEORE_UNCHANGED_SCAN_CACHE"] == str(cache_path)


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
