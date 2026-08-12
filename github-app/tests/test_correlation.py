import os
from contextlib import contextmanager
from datetime import datetime

import pytest

from aletheore.evidence_resolution import normalize_resolution
from scan_worker.jobs import (
    GRAPH_BRANCH,
    _attach_recent_commit_for_failure,
    _commit_attachment_from_graph,
    _dependency_context_attachment,
    _find_enclosing_symbol,
    _fix_suggestion_attachment,
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


@pytest.mark.asyncio
async def test_commit_attachment_from_graph_uses_most_recent_commit(pool, monkeypatch):
    # Replaces a live GitHub API round-trip (fetch_recent_commits_for_path)
    # with a read from the same persisted, incrementally-synced graph
    # _owner_attachment_from_graph already uses - evidence_git_file_churn
    # already has this exact data cached from the last scan; there's no
    # reason a failing-endpoint alert should hit the live API for it.
    await _insert_installation(pool, 804, "org")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 804, "org/repo")

    from aletheore.git_intel.graph_store import CommitTouch

    store.apply_commits(
        "unused",
        GRAPH_BRANCH,
        [
            CommitTouch("s1", "Alice", "a@example.com", datetime(2026, 6, 1), ("app/handler.py",), "initial handler"),
            CommitTouch("s2", "Bob", "b@example.com", datetime(2026, 6, 8), ("app/handler.py",), "fix null check"),
        ],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 8),
        reset=True,
    )

    attachment = _commit_attachment_from_graph(804, "org/repo", "app/handler.py")

    assert attachment is not None
    assert attachment["kind"] == "commit"
    assert attachment["commit"]["sha"] == "s2"
    assert attachment["commit"]["author_name"] == "Bob"
    assert attachment["commit"]["subject"] == "fix null check"


@pytest.mark.asyncio
async def test_commit_attachment_from_graph_returns_none_when_no_data(pool, monkeypatch):
    await _insert_installation(pool, 805, "org")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    assert _commit_attachment_from_graph(805, "org/repo", "never/scanned.py") is None


def test_commit_attachment_from_graph_degrades_gracefully_on_db_failure(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nonexistent-host-for-this-test/db")

    assert _commit_attachment_from_graph(806, "org/repo", "app/handler.py") is None


def test_attach_recent_commit_for_failure_combines_commit_owner_and_dependency_attachments(monkeypatch):
    # Proves all three sources merge into one resolution without any of
    # them depending on the others being present. The commit lookup now
    # reads the persisted graph (_commit_attachment_from_graph) instead of
    # a live API call - see test_commit_attachment_from_graph_* above for
    # that function's own coverage against a real database.
    monkeypatch.setattr(
        "scan_worker.jobs._commit_attachment_from_graph",
        lambda installation_id, repo_full_name, source_file: normalize_resolution(
            kind="commit",
            commit={"sha": "abc123", "author_name": "Carol", "author_email": "carol@example.com"},
            confidence="weak",
        ),
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
    monkeypatch.setattr("scan_worker.jobs._fix_suggestion_attachment", lambda *a, **k: None)

    result = _attach_recent_commit_for_failure(
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
    monkeypatch.setattr(
        "scan_worker.jobs._commit_attachment_from_graph",
        lambda installation_id, repo_full_name, source_file: normalize_resolution(
            kind="commit", commit={"sha": "abc123", "author_name": "Carol"}, confidence="weak"
        ),
    )
    monkeypatch.setattr("scan_worker.jobs._owner_attachment_from_graph", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._fix_suggestion_attachment", lambda *a, **k: None)

    result = _attach_recent_commit_for_failure(901, "org/repo", "unknown-file.py", None, None)

    assert result["commit"]["sha"] == "abc123"
    assert result["owner"] is None


def test_attach_recent_commit_for_failure_returns_original_resolution_when_nothing_found(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs._commit_attachment_from_graph", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._owner_attachment_from_graph", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._fix_suggestion_attachment", lambda *a, **k: None)

    original = {"kind": "endpoint"}
    result = _attach_recent_commit_for_failure(901, "org/repo", "x.py", original, None)

    assert result is original


def _module_with_symbol(path: str, name: str, start: int, end: int) -> dict:
    return {
        "path": path,
        "imports": [],
        "imported_by": [],
        "symbols": {"functions": [{"name": name, "start_line": start, "end_line": end}], "classes": []},
    }


def test_find_enclosing_symbol_finds_containing_function():
    evidence = {"repository": {"modules": [_module_with_symbol("app/handler.py", "do_login", 10, 25)]}}
    assert _find_enclosing_symbol(evidence, "app/handler.py", 15) == "do_login"


def test_find_enclosing_symbol_returns_none_outside_any_symbol_range():
    evidence = {"repository": {"modules": [_module_with_symbol("app/handler.py", "do_login", 10, 25)]}}
    assert _find_enclosing_symbol(evidence, "app/handler.py", 100) is None


def test_find_enclosing_symbol_returns_none_without_line_or_evidence():
    assert _find_enclosing_symbol(None, "app/handler.py", 15) is None
    evidence = {"repository": {"modules": [_module_with_symbol("app/handler.py", "do_login", 10, 25)]}}
    assert _find_enclosing_symbol(evidence, "app/handler.py", None) is None


class FakeHealthSettings:
    github_app_id = "x"
    github_app_private_key = "y"
    database_url = "postgresql://unused"


@contextmanager
def _noop_spend_lock(*args, **kwargs):
    yield


def _patch_fix_suggestion_spend_gate(monkeypatch, plan: str = "air") -> None:
    # F7: _fix_suggestion_attachment now checks and records against the
    # installation's monthly LLM spend cap like every other LLM call site -
    # these were previously the only mocks these tests needed.
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": plan})
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)


def test_fix_suggestion_attachment_returns_none_when_file_content_unavailable(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_settings", lambda: FakeHealthSettings())
    _patch_fix_suggestion_spend_gate(monkeypatch)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: None)

    result = _fix_suggestion_attachment(901, "org/repo", "app/handler.py", 15, "GET", "/v1/users", 500, None)

    assert result is None


def test_fix_suggestion_attachment_skips_the_llm_call_when_spend_cap_reached(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_settings", lambda: FakeHealthSettings())
    _patch_fix_suggestion_spend_gate(monkeypatch)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_file_content", lambda *a, **k: llm_called.append(True) or None
    )

    result = _fix_suggestion_attachment(901, "org/repo", "app/handler.py", 15, "GET", "/v1/users", 500, None)

    assert result is None
    assert llm_called == []  # never even reached the file fetch - blocked at the cap check


def test_fix_suggestion_attachment_returns_none_when_model_says_unknown(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_settings", lambda: FakeHealthSettings())
    _patch_fix_suggestion_spend_gate(monkeypatch)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: "def do_login():\n    pass\n")

    class FakeAdapter:
        def simple_completion(self, system_prompt, user_prompt, cwd):
            return "unknown"

    monkeypatch.setattr("scan_worker.jobs._health_fix_suggestion_adapter", lambda **k: FakeAdapter())

    result = _fix_suggestion_attachment(901, "org/repo", "app/handler.py", 15, "GET", "/v1/users", 500, None)

    assert result is None


def test_fix_suggestion_attachment_returns_suggestion_when_model_succeeds(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_settings", lambda: FakeHealthSettings())
    _patch_fix_suggestion_spend_gate(monkeypatch)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: "def do_login():\n    pass\n")

    class FakeAdapter:
        def simple_completion(self, system_prompt, user_prompt, cwd):
            return "  The DB connection pool is exhausted; increase max_connections.  "

    monkeypatch.setattr("scan_worker.jobs._health_fix_suggestion_adapter", lambda **k: FakeAdapter())

    result = _fix_suggestion_attachment(901, "org/repo", "app/handler.py", 15, "GET", "/v1/users", 500, None)

    assert result is not None
    assert result["kind"] == "suggestion"
    assert result["suggestion"] == "The DB connection pool is exhausted; increase max_connections."
    assert result["suggestion_status"] == "available"


def test_fix_suggestion_attachment_records_spend_when_model_succeeds(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_settings", lambda: FakeHealthSettings())
    _patch_fix_suggestion_spend_gate(monkeypatch)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "token")
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: "def do_login():\n    pass\n")
    monkeypatch.setattr("scan_worker.jobs.cost_for_usage", lambda model, prompt, completion: 0.0017)

    class FakeAdapter:
        def __init__(self, on_usage=None):
            self._on_usage = on_usage

        def simple_completion(self, system_prompt, user_prompt, cwd):
            if self._on_usage is not None:
                self._on_usage(80, 40)
            return "increase the connection pool size"

    monkeypatch.setattr(
        "scan_worker.jobs._health_fix_suggestion_adapter", lambda on_usage=None: FakeAdapter(on_usage)
    )
    recorded = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_llm_spend", lambda dsn, iid, cost, **k: recorded.append(cost)
    )

    result = _fix_suggestion_attachment(901, "org/repo", "app/handler.py", 15, "GET", "/v1/users", 500, None)

    assert result is not None
    assert recorded == [pytest.approx(0.0017)]


def test_fix_suggestion_attachment_degrades_gracefully_on_any_failure(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("github app auth broken")

    monkeypatch.setattr("scan_worker.jobs.get_settings", lambda: FakeHealthSettings())
    _patch_fix_suggestion_spend_gate(monkeypatch)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", _raise)

    result = _fix_suggestion_attachment(901, "org/repo", "app/handler.py", 15, "GET", "/v1/users", 500, None)

    assert result is None


def test_attach_recent_commit_for_failure_includes_suggestion_when_available(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs._commit_attachment_from_graph", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._owner_attachment_from_graph", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs._fix_suggestion_attachment",
        lambda *a, **k: {
            "kind": "suggestion",
            "file": None,
            "line": None,
            "end_line": None,
            "symbol": None,
            "owner": None,
            "owner_status": "unavailable",
            "commit": None,
            "commit_status": "unavailable",
            "dependency": None,
            "dependency_status": "unavailable",
            "risk": [],
            "risk_status": "unavailable",
            "suggestion": "increase the connection pool size",
            "suggestion_status": "available",
            "confidence": "inferred",
            "evidence_path": None,
            "evidence_status": "unavailable",
        },
    )

    result = _attach_recent_commit_for_failure(901, "org/repo", "app/handler.py", None, None)

    assert result["suggestion"] == "increase the connection pool size"
