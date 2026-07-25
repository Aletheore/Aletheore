import os
from datetime import datetime

import pytest

from scan_worker.jobs import (
    GRAPH_BRANCH,
    _attach_recent_commit_for_failure,
    _dependency_context_attachment,
    _owner_attachment_from_graph,
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


def _fake_evidence_with_modules() -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "app/handler.py",
                    "imports": ["app/db.py", "app/config.py"],
                    "imported_by": ["app/main.py"],
                },
                {"path": "app/db.py", "imports": [], "imported_by": ["app/handler.py"]},
            ]
        }
    }


def test_dependency_context_attachment_finds_upstream_and_downstream():
    attachment = _dependency_context_attachment(_fake_evidence_with_modules(), "app/handler.py")

    assert attachment is not None
    assert attachment["kind"] == "dependency"
    assert attachment["dependency"]["upstream"] == ["app/config.py", "app/db.py"]
    assert attachment["dependency"]["downstream"] == ["app/main.py"]
    assert attachment["confidence"] == "exact"


def test_dependency_context_attachment_returns_none_for_unknown_file():
    assert _dependency_context_attachment(_fake_evidence_with_modules(), "nonexistent.py") is None


def test_dependency_context_attachment_returns_none_without_evidence():
    assert _dependency_context_attachment(None, "app/handler.py") is None


@pytest.mark.asyncio
async def test_owner_attachment_from_graph_uses_most_recent_committer(pool, monkeypatch):
    await _insert_installation(pool, 801, "org")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 801, "org/repo")

    from aletheore.git_intel.graph_store import CommitTouch

    store.apply_commits(
        "unused",
        GRAPH_BRANCH,
        [
            CommitTouch("s1", "Alice", "a@example.com", datetime(2026, 6, 1), ("app/handler.py",)),
            CommitTouch("s2", "Bob", "b@example.com", datetime(2026, 6, 8), ("app/handler.py",)),
        ],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 8),
        reset=True,
    )

    attachment = _owner_attachment_from_graph(801, "org/repo", "app/handler.py")

    assert attachment is not None
    assert attachment["kind"] == "owner"
    assert attachment["owner"] == "Bob"  # most recent committer, not most prolific


@pytest.mark.asyncio
async def test_owner_attachment_from_graph_returns_none_when_no_data(pool, monkeypatch):
    await _insert_installation(pool, 802, "org")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    assert _owner_attachment_from_graph(802, "org/repo", "never/scanned.py") is None


def test_owner_attachment_from_graph_degrades_gracefully_on_db_failure(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nonexistent-host-for-this-test/db")

    assert _owner_attachment_from_graph(803, "org/repo", "app/handler.py") is None


def test_attach_recent_commit_for_failure_combines_commit_owner_and_dependency_attachments(monkeypatch):
    # Proves all three sources merge into one resolution without any of
    # them depending on the others being present - the live commit lookup
    # keeps working exactly as before, and the two new attachments are
    # purely additive.
    class FakeSettings:
        github_app_id = "x"
        github_app_private_key = "y"

    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_recent_commits_for_path",
        lambda client, token, repo, path, limit=1: [
            {"sha": "abc123", "author": "Carol", "date": "2026-07-20T00:00:00Z", "subject": "fix"}
        ],
    )
    monkeypatch.setattr(
        "scan_worker.jobs._owner_attachment_from_graph",
        lambda installation_id, repo_full_name, source_file: {
            "kind": "owner",
            "file": None,
            "line": None,
            "end_line": None,
            "symbol": None,
            "owner": "Dave",
            "owner_status": "available",
            "commit": None,
            "commit_status": "unavailable",
            "dependency": None,
            "dependency_status": "unavailable",
            "risk": [],
            "risk_status": "unavailable",
            "confidence": "inferred",
            "evidence_path": None,
            "evidence_status": "unavailable",
        },
    )

    result = _attach_recent_commit_for_failure(
        FakeSettings(),
        901,
        "org/repo",
        "app/handler.py",
        None,
        _fake_evidence_with_modules(),
    )

    assert result["commit"]["sha"] == "abc123"
    assert result["owner"] == "Dave"
    assert result["dependency"]["upstream"] == ["app/config.py", "app/db.py"]


def test_attach_recent_commit_for_failure_still_returns_something_when_only_commit_available(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_recent_commits_for_path",
        lambda client, token, repo, path, limit=1: [
            {"sha": "abc123", "author": "Carol", "date": "2026-07-20T00:00:00Z", "subject": "fix"}
        ],
    )
    monkeypatch.setattr("scan_worker.jobs._owner_attachment_from_graph", lambda *a, **k: None)

    class FakeSettings:
        github_app_id = "x"
        github_app_private_key = "y"

    result = _attach_recent_commit_for_failure(
        FakeSettings(), 901, "org/repo", "unknown-file.py", None, None
    )

    assert result["commit"]["sha"] == "abc123"
    assert result["owner"] is None


def test_attach_recent_commit_for_failure_returns_original_resolution_when_nothing_found(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr("scan_worker.jobs.fetch_recent_commits_for_path", lambda *a, **k: [])
    monkeypatch.setattr("scan_worker.jobs._owner_attachment_from_graph", lambda *a, **k: None)

    class FakeSettings:
        github_app_id = "x"
        github_app_private_key = "y"

    original = {"kind": "endpoint"}
    result = _attach_recent_commit_for_failure(FakeSettings(), 901, "org/repo", "x.py", original, None)

    assert result is original
