import json
import os
import subprocess
import time
from contextlib import contextmanager

import pytest

from scan_worker.jobs import (
    FLASH_REVIEW_SPEND_RESERVE_USD,
    LIVE_DOCS_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS,
    LIVE_WIKI_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS,
    MAX_FREE_TIER_FLASH_REVIEWS_PER_MONTH,
    run_pr_scan_job,
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


@contextmanager
def _noop_spend_lock(*args, **kwargs):
    yield


@pytest.fixture(autouse=True)
def _noop_repo_checkout_lock(monkeypatch):
    # repo_checkout_lock (see scan_worker/db.py) opens a real psycopg
    # connection to settings.database_url - most tests here run against a
    # fake DSN (or no DSN at all), which would hang or fail slowly rather
    # than exercising the actual lock. The lock's own correctness has its
    # own real-Postgres tests in test_scan_worker_db.py; this file only
    # needs run_pr_scan_job/run_push_scan_job's wiring around it to be a
    # no-op, autoused so none of the 30+ existing tests need touching.
    monkeypatch.setattr("scan_worker.jobs.repo_checkout_lock", _noop_spend_lock)


@pytest.fixture(autouse=True)
def _pr_is_open_by_default(monkeypatch):
    # run_pr_scan_job now checks the PR is still open before attempting a
    # checkout that's doomed once its branch is gone (see
    # fetch_pr_is_open's docstring for the real production failure this
    # closes) - a real network call none of the 30+ existing tests here
    # expect. Default to "still open" so none of them need touching; the
    # skip-when-closed path gets its own dedicated test overriding this.
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_is_open", lambda *a, **k: True)


def _patch_no_spend_cap(monkeypatch) -> None:
    """AIRview/Docs build jobs now gate on the same installation monthly
    LLM spend cap managed audits and flash review already used - real
    DB-backed functions the rest of this file's tests never had to mock
    before this. Well under any cap, so the gate is always a no-op here;
    the cap-reached path gets its own dedicated tests."""
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    # _IncrementalSpendBudget.can_start_next_call() now reserves atomically
    # against the real DB per call instead of comparing an in-memory
    # snapshot - always-succeed here for the same "well under any cap"
    # reason as the mocks above.
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)


class _FakeCodeGraphStore:
    """Stands in for scan_worker.code_graph_store.CodeGraphStore so
    _sync_code_graph's wiring can be tested without a real database - the
    store's own persistence is already covered directly, against a real
    Postgres instance, in test_code_graph_store.py."""

    def __init__(self, dsn, installation_id, repo_full_name):
        self.installation_id = installation_id
        self.repo_full_name = repo_full_name
        self.content_hashes = {}
        self.endpoint_keys = {}
        self.applied_module_deltas = None
        self.applied_endpoint_deltas = None

    def load_content_hashes(self, branch):
        return self.content_hashes

    def load_endpoint_keys(self, branch):
        return self.endpoint_keys

    def apply_module_deltas(self, branch, changed_modules, deleted_paths, new_sync_sha, new_sync_at):
        self.applied_module_deltas = {
            "branch": branch, "changed_modules": changed_modules,
            "deleted_paths": deleted_paths, "new_sync_sha": new_sync_sha,
        }

    def apply_endpoint_deltas(self, branch, changed_endpoints, deleted_keys):
        self.applied_endpoint_deltas = {
            "branch": branch, "changed_endpoints": changed_endpoints, "deleted_keys": deleted_keys,
        }


def test_sync_code_graph_applies_module_and_endpoint_deltas(monkeypatch):
    from scan_worker.jobs import _sync_code_graph

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    fake_store = _FakeCodeGraphStore("dsn", 1, "octocat/hello-world")
    monkeypatch.setattr("scan_worker.jobs.CodeGraphStore", lambda *a, **k: fake_store)

    evidence = {
        "repository": {
            "modules": [
                {"path": "a.py", "language": "python", "imports": [], "symbols": {"functions": [], "classes": []}}
            ],
            "api_endpoints": {"endpoints": [{"method": "GET", "path": "/x", "file": "a.py", "line": 1}]},
        }
    }

    _sync_code_graph(1, "octocat/hello-world", "sha1", evidence)

    assert fake_store.applied_module_deltas["changed_modules"][0]["path"] == "a.py"
    assert fake_store.applied_module_deltas["new_sync_sha"] == "sha1"
    assert fake_store.applied_endpoint_deltas["changed_endpoints"] == [
        {"method": "GET", "path": "/x", "file": "a.py", "line": 1}
    ]


def test_sync_code_graph_skips_unchanged_modules(monkeypatch):
    from scan_worker.jobs import _sync_code_graph
    from aletheore.code_graph_diff import module_content_hash

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    module = {"path": "a.py", "language": "python", "imports": [], "symbols": {"functions": [], "classes": []}}
    fake_store = _FakeCodeGraphStore("dsn", 1, "octocat/hello-world")
    fake_store.content_hashes = {"a.py": module_content_hash(module)}
    monkeypatch.setattr("scan_worker.jobs.CodeGraphStore", lambda *a, **k: fake_store)

    evidence = {"repository": {"modules": [module]}}

    _sync_code_graph(1, "octocat/hello-world", "sha1", evidence)

    assert fake_store.applied_module_deltas["changed_modules"] == []
    assert fake_store.applied_module_deltas["deleted_paths"] == []


def test_sync_code_graph_degrades_gracefully_on_store_failure(monkeypatch):
    from scan_worker.jobs import _sync_code_graph

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    def _broken_store(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr("scan_worker.jobs.CodeGraphStore", _broken_store)

    # Must not raise - this is a persistence enhancement, never allowed to
    # break the scan job itself.
    _sync_code_graph(1, "octocat/hello-world", "sha1", {"repository": {"modules": []}})


def _make_local_repo(path, files: dict) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    for name, content in files.items():
        (path / name).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_build_unchanged_scan_cache_excludes_changed_files_includes_unchanged(pool, tmp_path, monkeypatch):
    from scan_worker.jobs import _build_unchanged_scan_cache
    from scan_worker.code_graph_store import CodeGraphStore

    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)", 950, "org"
    )
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    checkout_dir = tmp_path / "checkout"
    sha1 = _make_local_repo(checkout_dir, {"a.py": "def old():\n    pass\n", "b.py": "def stable():\n    pass\n"})
    (checkout_dir / "a.py").write_text("def new():\n    pass\n")
    subprocess.run(["git", "add", "-A"], cwd=checkout_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change a.py only"], cwd=checkout_dir, check=True)
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout_dir, check=True, capture_output=True, text=True
    ).stdout.strip()

    store = CodeGraphStore(TEST_DATABASE_URL, 950, "org/repo")
    from datetime import datetime as dt

    store.apply_module_deltas(
        "default",
        [
            {"path": "a.py", "language": "python", "imports": [], "content_hash": "old-a-hash",
             "symbols": {"functions": [{"name": "old", "start_line": 1, "end_line": 2}], "classes": []}},
            {"path": "b.py", "language": "python", "imports": [], "content_hash": "b-hash",
             "symbols": {"functions": [{"name": "stable", "start_line": 1, "end_line": 2}], "classes": []}},
        ],
        deleted_paths=[],
        new_sync_sha=sha1,
        new_sync_at=dt(2026, 7, 27),
    )

    cache_path = _build_unchanged_scan_cache(950, "org/repo", checkout_dir, sha1, sha2, tmp_path / "cache.json")

    assert cache_path is not None
    cache_data = json.loads(cache_path.read_text())
    assert "b.py" in cache_data["modules"]
    assert "a.py" not in cache_data["modules"]
    assert cache_data["modules"]["b.py"]["symbols"]["functions"][0]["name"] == "stable"


def test_build_unchanged_scan_cache_returns_none_without_a_previous_sync(tmp_path):
    from scan_worker.jobs import _build_unchanged_scan_cache

    checkout_dir = tmp_path / "checkout"
    _make_local_repo(checkout_dir, {"a.py": "pass\n"})

    result = _build_unchanged_scan_cache(1, "org/repo", checkout_dir, None, "somesha", tmp_path / "cache.json")

    assert result is None


def test_url_without_credentials_strips_embedded_token():
    from scan_worker.jobs import _url_without_credentials

    assert (
        _url_without_credentials("https://x-access-token:sometoken@github.com/org/repo.git")
        == "https://github.com/org/repo.git"
    )


def test_url_without_credentials_leaves_a_plain_local_path_unchanged():
    from scan_worker.jobs import _url_without_credentials

    assert _url_without_credentials("/tmp/some/bare-repo") == "/tmp/some/bare-repo"


def test_ensure_persistent_checkout_does_not_leave_a_live_token_on_disk(tmp_path, monkeypatch):
    # This checkout directory is a mounted, reused-across-scans volume in
    # production (see _persistent_checkout_dir) - unlike the ephemeral
    # per-job clones that get deleted within minutes, a token embedded in
    # its .git/config would sit at rest on disk for as long as the
    # checkout exists. _ensure_persistent_checkout must reset the remote
    # back to a credential-free URL before returning, on both the
    # fresh-clone and reused-checkout paths.
    from scan_worker.jobs import _ensure_persistent_checkout

    calls = []

    def fake_run(args, cwd=None, check=None):
        calls.append(args)
        if args[:2] == ["git", "clone"]:
            dest = args[-1]
            os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("scan_worker.jobs.subprocess.run", fake_run)

    checkout_dir = tmp_path / "fresh"
    credentialed_url = "https://x-access-token:livetoken@github.com/org/repo.git"
    _ensure_persistent_checkout(credentialed_url, "somesha", checkout_dir)

    set_url_calls = [c for c in calls if c[:3] == ["git", "remote", "set-url"]]
    assert set_url_calls, "expected at least one 'git remote set-url' call"
    # The LAST set-url call is what .git/config is left holding when this
    # function returns - it must never be the credentialed URL.
    assert set_url_calls[-1][-1] == "https://github.com/org/repo.git"
    assert "livetoken" not in set_url_calls[-1][-1]


def test_ensure_persistent_checkout_strips_credentials_on_reuse_path_too(tmp_path, monkeypatch):
    from scan_worker.jobs import _ensure_persistent_checkout

    checkout_dir = tmp_path / "existing"
    (checkout_dir / ".git").mkdir(parents=True)

    calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.subprocess.run",
        lambda args, cwd=None, check=None: calls.append(args) or subprocess.CompletedProcess(args, 0),
    )

    credentialed_url = "https://x-access-token:livetoken@github.com/org/repo.git"
    _ensure_persistent_checkout(credentialed_url, "somesha", checkout_dir)

    set_url_calls = [c for c in calls if c[:3] == ["git", "remote", "set-url"]]
    assert len(set_url_calls) == 2  # once with the live token to fetch, once to strip it
    assert set_url_calls[0][-1] == credentialed_url
    assert set_url_calls[-1][-1] == "https://github.com/org/repo.git"


def test_run_pr_scan_job_uses_persistent_checkout_and_unchanged_cache_for_head(
    bare_repo_with_two_commits, monkeypatch
):
    # Proves run_pr_scan_job actually wires the persistent-checkout +
    # incremental-scan-cache path for the HEAD scan specifically (not
    # base, which stays an ephemeral clone - see _build_unchanged_scan_cache's
    # module docstring for why only head feeds the durable code graph).
    bare_path, base_sha, head_sha = bare_repo_with_two_commits

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    prepare_head_calls = []
    real_prepare_head_checkout = None
    from scan_worker import jobs as jobs_module

    real_prepare_head_checkout = jobs_module._prepare_head_checkout

    def spy_prepare_head_checkout(clone_url, head_sha_arg, installation_id, repo_full_name, fallback_dir):
        prepare_head_calls.append(
            {"clone_url": clone_url, "head_sha": head_sha_arg, "installation_id": installation_id}
        )
        return real_prepare_head_checkout(clone_url, head_sha_arg, installation_id, repo_full_name, fallback_dir)

    monkeypatch.setattr("scan_worker.jobs._prepare_head_checkout", spy_prepare_head_checkout)

    cache_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._build_unchanged_scan_cache",
        lambda *a, **k: cache_calls.append(a) or None,
    )

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert len(prepare_head_calls) == 1
    assert prepare_head_calls[0]["head_sha"] == head_sha
    assert prepare_head_calls[0]["installation_id"] == 1
    assert len(cache_calls) == 1
    assert cache_calls[0][0] == 1  # installation_id
    assert cache_calls[0][4] == head_sha  # current_sha


def test_run_pr_scan_job_never_syncs_the_pr_head_checkout_into_the_persistent_git_graph(
    bare_repo_with_two_commits, monkeypatch
):
    # head_dir is checked out at this PR's head_sha, which may sit on a
    # feature branch that never merges. _sync_persistent_git_graph always
    # persists under the fixed GRAPH_BRANCH="default" key that
    # run_push_scan_job/run_initial_scan_job use for the repo's real
    # default branch, so calling it here would permanently fold unmerged,
    # possibly-rejected PR commits into the persisted "default" branch
    # ownership/churn/cadence graph (confirmed directly: see
    # test_jobs_git_graph_sync.py's
    # test_sync_persistent_git_graph_does_not_fold_unmerged_pr_commits_into_default_branch_stats).
    # run_pr_scan_job must never call it.
    bare_path, base_sha, head_sha = bare_repo_with_two_commits

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._sync_code_graph", lambda *a, **k: None)

    sync_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._sync_persistent_git_graph",
        lambda *a, **k: sync_calls.append(a) or (a[3] if len(a) > 3 else k.get("evidence")),
    )

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert sync_calls == []


def test_run_pr_scan_job_never_syncs_the_pr_head_checkout_into_the_persistent_code_graph(
    bare_repo_with_two_commits, monkeypatch
):
    # Direct sibling of the git-graph bug fixed above: _sync_code_graph is
    # its own docstring's "counterpart to _sync_persistent_git_graph...for
    # the code model rather than git history" - it too writes unconditionally
    # under the fixed GRAPH_BRANCH="default" key (apply_module_deltas/
    # apply_endpoint_deltas), the same key run_push_scan_job/
    # run_initial_scan_job use for the repo's real default branch. Calling it
    # here with this PR's own head_sha/evidence would permanently fold that
    # PR's file/symbol/dependency-edge/endpoint deltas into the durable code
    # graph several MCP tools and future incremental syncs read from, even
    # for PRs closed without merging. run_pr_scan_job must never call it.
    bare_path, base_sha, head_sha = bare_repo_with_two_commits

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    sync_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._sync_code_graph",
        lambda *a, **k: sync_calls.append(a),
    )

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert sync_calls == []


def test_happy_path_posts_comment_and_writes_history(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body
        posted["repo_full_name"] = repo_full_name
        posted["pr_number"] = pr_number

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert "Secrets" in posted["body"]
    assert posted["repo_full_name"] == "octocat/hello-world"
    assert posted["pr_number"] == 7


def test_run_pr_scan_job_excludes_a_dismissed_secret_from_the_pr_comment(
    bare_repo_with_two_commits, monkeypatch
):
    # Same fixture and setup as test_happy_path_posts_comment_and_writes_history
    # above (which confirms "Secrets" IS present when nothing is dismissed) -
    # this test only changes get_dismissed_identity_keys to report the
    # planted secret finding as already dismissed, and confirms it no
    # longer reaches the posted PR comment. filter_dismissed/
    # finding_identity_key's own correctness is covered directly in
    # test_dismissed_findings.py - this test is only about the wiring: that
    # run_pr_scan_job actually applies the filter before posting.
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": {"dismiss-everything"}, "vulnerability": set()},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.filter_dismissed",
        lambda findings, finding_type, dismissed_keys: (
            [] if finding_type == "secret" and dismissed_keys == {"dismiss-everything"} else findings
        ),
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert "Secrets" not in posted["body"]


def test_check_run_failure_does_not_overwrite_diff_comment(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    def raise_error(*a, **k):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", raise_error)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert "Secrets" in posted["body"]
    assert "couldn't complete this scan" not in posted["body"]


def test_temp_dir_cleaned_up_on_success(bare_repo_with_two_commits, monkeypatch):
    import scan_worker.jobs as jobs_module

    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    seen_job_dirs = []
    original_mkdtemp = jobs_module._job_temp_dir

    def spy():
        path = original_mkdtemp()
        seen_job_dirs.append(path)
        return path

    monkeypatch.setattr("scan_worker.jobs._job_temp_dir", spy)

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert len(seen_job_dirs) == 1
    assert not seen_job_dirs[0].exists()


def test_run_job_temp_dir_cleanup_job_removes_only_old_job_dirs(tmp_path, monkeypatch):
    from scan_worker.jobs import JOB_TEMP_DIR_MAX_AGE_SECONDS, run_job_temp_dir_cleanup_job

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "repo.py").write_text("source")
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    marker_file = tmp_path / "not-a-dir"
    marker_file.write_text("ignore me")

    now = time.time()
    old_mtime = now - JOB_TEMP_DIR_MAX_AGE_SECONDS - 60
    os.utime(old_dir, (old_mtime, old_mtime))

    monkeypatch.setattr("scan_worker.jobs.JOBS_ROOT", tmp_path)

    run_job_temp_dir_cleanup_job()

    assert not old_dir.exists()
    assert fresh_dir.exists()
    assert marker_file.exists()


def test_run_endpoint_health_cleanup_job_removes_only_old_rows(monkeypatch):
    from scan_worker.jobs import ENDPOINT_HEALTH_RETENTION_DAYS, run_endpoint_health_cleanup_job

    deleted = []
    monkeypatch.setattr(
        "scan_worker.jobs.get_settings",
        lambda: type("Settings", (), {"database_url": "dsn"})(),
    )
    monkeypatch.setattr(
        "scan_worker.jobs.delete_expired_endpoint_health",
        lambda dsn, retention_days: deleted.append((dsn, retention_days)) or 7,
    )

    run_endpoint_health_cleanup_job()

    assert deleted == [("dsn", ENDPOINT_HEALTH_RETENTION_DAYS)]


def test_run_flash_review_cache_cleanup_job_removes_only_old_rows(monkeypatch):
    from scan_worker.jobs import FLASH_REVIEW_CACHE_RETENTION_DAYS, run_flash_review_cache_cleanup_job

    deleted = []
    monkeypatch.setattr(
        "scan_worker.jobs.get_settings",
        lambda: type("Settings", (), {"database_url": "dsn"})(),
    )
    monkeypatch.setattr(
        "scan_worker.jobs.delete_expired_flash_review_cache",
        lambda dsn, retention_days: deleted.append((dsn, retention_days)) or 7,
    )

    run_flash_review_cache_cleanup_job()

    assert deleted == [("dsn", FLASH_REVIEW_CACHE_RETENTION_DAYS)]


def test_clone_failure_posts_failure_comment_and_cleans_up(monkeypatch):
    import scan_worker.jobs as jobs_module

    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: "/not-a-repo")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")

    seen_job_dirs = []
    original = jobs_module._job_temp_dir

    def spy():
        path = original()
        seen_job_dirs.append(path)
        return path

    monkeypatch.setattr("scan_worker.jobs._job_temp_dir", spy)

    with pytest.raises(subprocess.CalledProcessError):
        run_pr_scan_job(
            installation_id=1,
            repo_full_name="octocat/hello-world",
            pr_number=7,
            base_sha="deadbeef",
            head_sha="deadbeef",
        )

    assert "couldn't complete this scan" in posted["body"]
    assert not seen_job_dirs[0].exists()


def test_slack_alert_fires_on_paid_install_with_webhook_url_and_new_secret(
    bare_repo_with_two_commits, monkeypatch
):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "air", "webhook_url": "https://hooks.slack.com/x"},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    sent = {}
    monkeypatch.setattr(
        "scan_worker.jobs.send_slack_alert",
        lambda webhook_url, diff, repo_full_name, pr_number: sent.update(
            webhook_url=webhook_url, repo_full_name=repo_full_name
        ),
    )

    run_pr_scan_job(1, "octocat/hello-world", 7, base_sha, head_sha)

    assert sent["webhook_url"] == "https://hooks.slack.com/x"
    assert sent["repo_full_name"] == "octocat/hello-world"


def test_check_run_failure_on_new_secret(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    created = {}
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo_full_name, head_sha, conclusion, summary: created.update(
            conclusion=conclusion, head_sha=head_sha
        ),
    )

    run_pr_scan_job(1, "octocat/hello-world", 7, base_sha, head_sha)

    assert created["conclusion"] == "failure"
    assert created["head_sha"] == head_sha


def test_vulnerability_check_run_fails_on_real_known_cve_bump(
    bare_repo_with_dependency_bump, monkeypatch, tmp_path
):
    """Real end-to-end: bumps requirements.txt to pyyaml==5.3.1 (a real,
    live-queried OSV.dev advisory - see the fixture) and asserts the new
    vulnerability check run fires failure. Makes a real network call to
    OSV.dev, same as production; not mocked, so this only proves the
    wiring if OSV.dev is reachable when the suite runs.

    Real flakiness this fixed: check_vulnerabilities' default cache_path
    (aletheore.vulnerabilities.DEFAULT_VULNERABILITY_CACHE_PATH) is
    ~/.cache/aletheore/vulnerability-cache.json - a real file, shared and
    persistent across every test run and every branch/PR on the same
    machine, with a 24-hour TTL. Without isolating it, a single spurious-
    but-200 OSV.dev response (not even a real outage - just one
    momentarily incomplete/empty result) gets cached as "no vulnerabilities
    found" for pyyaml==5.3.1 and silently poisons every subsequent test run
    for up to a day, on any branch. Confirmed as the real root cause of a
    live CI failure on an unrelated PR (#463) whose only connection to this
    test was running on the same CI runner. The module's own comment on
    check_vulnerabilities ("resolved inside the function body so a test
    monkeypatching DEFAULT_VULNERABILITY_CACHE_PATH actually takes effect")
    already anticipated exactly this - this test just never did it."""
    monkeypatch.setattr(
        "aletheore.vulnerabilities.DEFAULT_VULNERABILITY_CACHE_PATH",
        tmp_path / "vulnerability-cache.json",
    )
    bare_path, base_sha, head_sha = bare_repo_with_dependency_bump
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    created_runs = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo_full_name, head_sha, conclusion, summary, name="": created_runs.append(
            {"name": name, "conclusion": conclusion, "summary": summary}
        ),
    )

    run_pr_scan_job(1, "octocat/hello-world", 7, base_sha, head_sha)

    vuln_runs = [r for r in created_runs if r["name"] == "Aletheore dependency vulnerability check"]
    assert len(vuln_runs) == 1
    assert vuln_runs[0]["conclusion"] == "failure"
    assert "pyyaml" in vuln_runs[0]["summary"]
    assert "GHSA-8q59-q68h-6hv4" in vuln_runs[0]["summary"] or "PYSEC-2021-142" in vuln_runs[0]["summary"]


def test_vulnerability_check_run_succeeds_when_no_new_vulnerability(
    bare_repo_with_two_commits, monkeypatch
):
    """Same real OSV.dev network path as the failure test above, but the
    fixture's only change is a hardcoded secret in app.py - no dependency
    manifest touched at all, so no vulnerability check should fire
    failure. Confirms the new check run doesn't false-positive on an
    unrelated PR."""
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    created_runs = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo_full_name, head_sha, conclusion, summary, name="": created_runs.append(
            {"name": name, "conclusion": conclusion, "summary": summary}
        ),
    )

    run_pr_scan_job(1, "octocat/hello-world", 7, base_sha, head_sha)

    vuln_runs = [r for r in created_runs if r["name"] == "Aletheore dependency vulnerability check"]
    assert len(vuln_runs) == 1
    assert vuln_runs[0]["conclusion"] == "success"


def test_maybe_create_regression_risk_check_run_creates_neutral_check_run(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.list_recent_endpoint_incidents",
        lambda *a, **k: [
            {
                "endpoint_method": "GET",
                "endpoint_path": "/x",
                "incident_count": 3,
                "last_incident_at": "2026-07-20T00:00:00Z",
            }
        ],
    )
    created = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo, sha, conclusion, summary, name="Aletheore secrets check": created.append(
            (conclusion, name, summary)
        ),
    )
    evidence = {
        "repository": {
            "api_endpoints": {
                "endpoints": [{"method": "GET", "path": "/x", "file": "app.py", "line": 10}]
            }
        }
    }

    from scan_worker.jobs import _maybe_create_regression_risk_check_run

    _maybe_create_regression_risk_check_run(
        client=None,
        token="tok",
        repo_full_name="octocat/hello-world",
        head_sha="sha1",
        installation_id=1,
        evidence=evidence,
        changed_files=["app.py"],
    )

    assert len(created) == 1
    assert created[0][0] == "neutral"
    assert created[0][1] == "Aletheore regression risk"
    assert "GET /x" in created[0][2]


def test_maybe_create_regression_risk_check_run_skips_when_no_incidents(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_recent_endpoint_incidents", lambda *a, **k: [])
    created = []
    monkeypatch.setattr("scan_worker.jobs.create_check_run", lambda *a, **k: created.append(True))

    from scan_worker.jobs import _maybe_create_regression_risk_check_run

    _maybe_create_regression_risk_check_run(
        client=None,
        token="tok",
        repo_full_name="octocat/hello-world",
        head_sha="sha1",
        installation_id=1,
        evidence={"repository": {"api_endpoints": {"endpoints": []}}},
        changed_files=["app.py"],
    )

    assert created == []


def test_maybe_create_regression_risk_check_run_skips_free_plan(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    touched_incidents = []
    monkeypatch.setattr(
        "scan_worker.jobs.list_recent_endpoint_incidents",
        lambda *a, **k: touched_incidents.append(True),
    )

    from scan_worker.jobs import _maybe_create_regression_risk_check_run

    _maybe_create_regression_risk_check_run(
        client=None,
        token="tok",
        repo_full_name="octocat/hello-world",
        head_sha="sha1",
        installation_id=1,
        evidence={"repository": {}},
        changed_files=[],
    )

    assert touched_incidents == []


def test_maybe_create_regression_fence_check_run_creates_neutral_check_run(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    created = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo, sha, conclusion, summary, name="Aletheore secrets check": created.append(
            (conclusion, name, summary)
        ),
    )
    old_evidence = {
        "repository": {
            "modules": [
                {"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}},
                {"path": "reports/export.py", "symbols": {"functions": []}},
            ]
        }
    }
    new_evidence = {
        "repository": {
            "modules": [
                {
                    "path": "billing.py",
                    "symbols": {
                        "functions": [{"name": "get_billing", "params": "(user_id, include_history)"}]
                    },
                    "imported_by": ["reports/export.py"],
                },
                {"path": "reports/export.py", "symbols": {"functions": []}},
            ]
        }
    }

    from scan_worker.jobs import _maybe_create_regression_fence_check_run

    _maybe_create_regression_fence_check_run(
        client=None,
        token="tok",
        repo_full_name="octocat/hello-world",
        head_sha="sha1",
        installation_id=1,
        old_evidence=old_evidence,
        new_evidence=new_evidence,
        changed_files=["billing.py"],
    )

    assert len(created) == 1
    assert created[0][0] == "neutral"
    assert created[0][1] == "Aletheore Regression Fence"
    assert "get_billing" in created[0][2]
    assert "reports/export.py" in created[0][2]


def test_maybe_create_regression_fence_check_run_skips_when_no_violations(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    created = []
    monkeypatch.setattr("scan_worker.jobs.create_check_run", lambda *a, **k: created.append(True))
    evidence = {
        "repository": {
            "modules": [
                {"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}
            ]
        }
    }

    from scan_worker.jobs import _maybe_create_regression_fence_check_run

    _maybe_create_regression_fence_check_run(
        client=None,
        token="tok",
        repo_full_name="octocat/hello-world",
        head_sha="sha1",
        installation_id=1,
        old_evidence=evidence,
        new_evidence=evidence,
        changed_files=["billing.py"],
    )

    assert created == []


def test_maybe_create_regression_fence_check_run_skips_free_plan(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    created = []
    monkeypatch.setattr("scan_worker.jobs.create_check_run", lambda *a, **k: created.append(True))
    old_evidence = {
        "repository": {
            "modules": [
                {"path": "billing.py", "symbols": {"functions": [{"name": "get_billing", "params": "(user_id)"}]}}
            ]
        }
    }
    new_evidence = {
        "repository": {
            "modules": [
                {
                    "path": "billing.py",
                    "symbols": {"functions": [{"name": "get_billing", "params": "(user_id, x)"}]},
                    "imported_by": ["reports/export.py"],
                }
            ]
        }
    }

    from scan_worker.jobs import _maybe_create_regression_fence_check_run

    _maybe_create_regression_fence_check_run(
        client=None,
        token="tok",
        repo_full_name="octocat/hello-world",
        head_sha="sha1",
        installation_id=1,
        old_evidence=old_evidence,
        new_evidence=new_evidence,
        changed_files=["billing.py"],
    )

    assert created == []


def test_managed_audit_api_job_returns_report_text(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100,
        evidence={"scanned_at": "2026-01-01"},
        repo_full_name="octocat/widgets",
    )

    assert "API Report" in result


def test_managed_audit_api_job_releases_lock_during_audit(monkeypatch):
    lock_state = {"held": False, "observed_during_audit": None}

    @contextmanager
    def _tracking_spend_lock(*args, **kwargs):
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _tracking_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: (
        lock_state.update(observed_during_audit=lock_state["held"]) or "# API Report"
    ))
    monkeypatch.setattr("scan_worker.jobs._sign_and_persist_audit_report", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100,
        evidence={"scanned_at": "2026-01-01"},
        repo_full_name="octocat/widgets",
    )

    assert "API Report" in result
    assert lock_state["observed_during_audit"] is False
    assert lock_state["held"] is False


def test_managed_audit_api_job_signs_and_persists_the_report(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    stored = {}
    monkeypatch.setattr(
        "scan_worker.jobs.insert_audit_report",
        lambda dsn, iid, repo, token, text, chash, sig, pubkey: stored.update(
            installation_id=iid,
            repo_full_name=repo,
            token=token,
            text=text,
            signing_public_key=pubkey,
        ),
    )

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100,
        evidence={"scanned_at": "2026-01-01"},
        repo_full_name="octocat/widgets",
    )

    assert "API Report" in result
    assert stored["installation_id"] == 100
    assert stored["repo_full_name"] == "octocat/widgets"
    assert stored["text"] == "# API Report"
    assert len(stored["token"]) == 64


def test_managed_audit_api_job_still_returns_report_when_signing_fails(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", _raise)

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100,
        evidence={"scanned_at": "2026-01-01"},
        repo_full_name="octocat/widgets",
    )

    assert "API Report" in result


def test_managed_audit_api_job_raises_when_spend_cap_reached(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_managed_audit", lambda *a, **k: llm_called.append(True)
    )
    from scan_worker.jobs import run_managed_audit_api_job

    with pytest.raises(Exception, match="spend cap"):
        run_managed_audit_api_job(
            installation_id=100,
            evidence={"scanned_at": "2026-01-01"},
            repo_full_name="octocat/widgets",
        )
    assert llm_called == []


def test_managed_audit_api_job_records_each_call_and_exposes_budget_stop(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.monthly_cap_for_installation", lambda *a, **k: 0.0012)
    monkeypatch.setattr("scan_worker.jobs.cost_for_usage", lambda *a, **k: 0.0006)
    # In-memory stand-in for the real atomic reserve_llm_spend/record_llm_spend
    # pair, sharing running-total state the same way the real DB row does -
    # reserve_llm_spend reserves next_call_reserve_usd up front (atomic
    # check-and-add), record_llm_spend's delta then trues it up to the real
    # cost. `cost_for_usage` mocked to 0.0006 < DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    # (0.001), so the true-up delta is negative: -0.0004.
    spend_state = {"total": 0.0}
    recorded_deltas = []

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        if spend_state["total"] + reserve_usd <= monthly_cap:
            spend_state["total"] += reserve_usd
            return True
        return False

    def _record_llm_spend(dsn, iid, delta, **k):
        spend_state["total"] += delta
        recorded_deltas.append(delta)

    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", _record_llm_spend)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", lambda *a, **k: None)

    budget_checks = []

    def fake_run_managed_audit(repo_dir, *, on_usage, before_llm_call, allow_partial_report, **kwargs):
        budget_checks.append(before_llm_call())
        on_usage(1, 1)
        budget_checks.append(before_llm_call())
        assert allow_partial_report is True
        return "# Partial managed audit"

    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", fake_run_managed_audit)

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100,
        evidence={"scanned_at": "2026-01-01"},
        repo_full_name="octocat/widgets",
    )

    assert "Partial managed audit" in result
    assert recorded_deltas == [pytest.approx(-0.0004)]
    assert budget_checks == [True, False]


def test_managed_audit_pr_job_clones_pr_head_runs_audit_and_replies(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha],
        check=True,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# Managed Audit")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            marker=kwargs.get("marker"),
        ),
    )
    from scan_worker.jobs import AUDIT_COMMENT_MARKER, run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert "Managed Audit" in posted["body"]
    assert posted["repo_full_name"] == "octocat/hello-world"
    assert posted["marker"] == AUDIT_COMMENT_MARKER


def test_managed_audit_pr_job_records_each_call_and_stops_mid_run_when_cap_reached(monkeypatch, tmp_path):
    # Mirrors test_managed_audit_api_job_records_each_call_and_exposes_budget_stop:
    # run_managed_audit_pr_job must gate on the same atomic per-call
    # reservation (_IncrementalSpendBudget/reserve_llm_spend) that
    # run_managed_audit_api_job, run_flash_review_job, and AIRview/Docs
    # builds already use - not the old check-once-then-record-once pattern
    # around installation_spend_lock, which left the entire (possibly
    # multi-call) audit run, and any other job racing the same
    # installation's cap concurrently, completely ungated between the one
    # check and the one record.
    def _clone_pr_head(url, pr_number, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "app.py").write_text("print('hello')\n")

    def _run_scan(repo_dir):
        evidence_path = repo_dir / "evidence.json"
        evidence_path.write_text(json.dumps({"repository": {"loc": 1}}))
        return evidence_path

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs._clone_pr_head", _clone_pr_head)
    monkeypatch.setattr("scan_worker.jobs._run_scan", _run_scan)
    monkeypatch.setattr("scan_worker.jobs.get_github_api_client", lambda: object())
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.monthly_cap_for_installation", lambda *a, **k: 0.0012)
    monkeypatch.setattr("scan_worker.jobs.cost_for_usage", lambda *a, **k: 0.0006)
    monkeypatch.setattr("scan_worker.jobs._sign_and_persist_audit_report", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)

    # In-memory stand-in for the real atomic reserve_llm_spend/record_llm_spend
    # pair, sharing running-total state the same way the real DB row does -
    # same shape as the API-job test this mirrors.
    spend_state = {"total": 0.0}
    recorded_deltas = []

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        if spend_state["total"] + reserve_usd <= monthly_cap:
            spend_state["total"] += reserve_usd
            return True
        return False

    def _record_llm_spend(dsn, iid, delta, **k):
        spend_state["total"] += delta
        recorded_deltas.append(delta)

    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", _record_llm_spend)

    budget_checks = []

    def fake_run_managed_audit(repo_dir, *, on_usage, before_llm_call=None, **kwargs):
        assert before_llm_call is not None, (
            "run_managed_audit_pr_job must thread an atomic before_llm_call gate into "
            "run_managed_audit, not just a single check before the whole run"
        )
        budget_checks.append(before_llm_call())
        on_usage(1, 1)
        budget_checks.append(before_llm_call())
        return "# Managed Audit"

    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", fake_run_managed_audit)

    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert recorded_deltas == [pytest.approx(-0.0004)]
    assert budget_checks == [True, False]


def test_managed_audit_pr_job_persists_and_signs_the_report(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha],
        check=True,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "the audit findings")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    stored = {}
    monkeypatch.setattr(
        "scan_worker.jobs.insert_audit_report",
        lambda dsn, iid, repo, token, text, chash, sig, pubkey: stored.update(
            installation_id=iid,
            repo_full_name=repo,
            token=token,
            text=text,
            hash=chash,
            sig=sig,
            signing_public_key=pubkey,
        ),
    )
    check_runs = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo, sha, conclusion, summary, name="Aletheore secrets check": check_runs.append(
            {"repo": repo, "sha": sha, "conclusion": conclusion, "summary": summary, "name": name}
        ),
    )

    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert stored["installation_id"] == 1
    assert stored["repo_full_name"] == "octocat/hello-world"
    assert stored["text"] == "the audit findings"
    assert len(stored["token"]) == 64
    assert stored["token"] in posted["body"]

    assert len(check_runs) == 1
    assert check_runs[0]["name"] == "Aletheore Audit Certificate"
    assert check_runs[0]["repo"] == "octocat/hello-world"
    assert check_runs[0]["sha"] == head_sha
    assert check_runs[0]["conclusion"] == "success"
    assert stored["token"] in check_runs[0]["summary"]


def test_managed_audit_pr_job_skips_check_run_when_signing_fails(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha],
        check=True,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "the audit findings")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", _raise)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    check_runs = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run", lambda *a, **k: check_runs.append(True)
    )

    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    # No certificate to point to if signing itself failed.
    assert check_runs == []


def test_managed_audit_pr_job_still_posts_report_when_signing_fails(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha],
        check=True,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "the audit findings")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", _raise)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body,
            marker=kwargs.get("marker"),
        ),
    )

    from scan_worker.jobs import AUDIT_COMMENT_MARKER, run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert "the audit findings" in posted["body"]
    assert posted["marker"] == AUDIT_COMMENT_MARKER
    assert "Verify this report" not in posted["body"]


def test_managed_audit_pr_job_skips_llm_call_when_spend_cap_reached(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha],
        check=True,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_managed_audit", lambda *a, **k: llm_called.append(True)
    )
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
    from scan_worker.jobs import AUDIT_COMMENT_MARKER, run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert llm_called == []
    assert "spend cap" in posted["body"].lower()
    assert posted["marker"] == AUDIT_COMMENT_MARKER


def test_managed_audit_pr_job_skips_llm_call_when_rate_limited(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha],
        check=True,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_managed_audit", lambda *a, **k: llm_called.append(True)
    )
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
    from scan_worker.jobs import AUDIT_COMMENT_MARKER, run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert llm_called == []
    assert "rate limit" in posted["body"].lower()
    assert posted["marker"] == AUDIT_COMMENT_MARKER


def test_flash_review_job_routes_free_tier_to_free_tier_path(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr(
        "scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0
    )
    # Mock the free-tier adapter chain to have one working adapter
    from unittest.mock import MagicMock
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    monkeypatch.setattr(
        "scan_worker.model_tiers.writing_adapter_chain_for_free_tier",
        lambda *a, **k: [mock_adapter],
    )
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr("scan_worker.jobs.resolve_model", lambda *a: "gpt-5.6-luna")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- a.py ---\n+real change\n")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["a.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_context", lambda *a, **k: "")
    # Deliberately False (not the True this test used to hardcode) - True
    # short-circuits _run_flash_review before it ever builds the adapter
    # chain or calls review_diff, which would silently pass this test
    # while exercising none of the free-tier code it's named for.
    monkeypatch.setattr("scan_worker.jobs.is_non_substantive_diff", lambda *a: False)
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr("scan_worker.jobs.files_missing_from_review_context", lambda *a: [])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a: None)
    monkeypatch.setattr("scan_worker.jobs.build_code_evidence_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_dependency_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_change_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_blast_radius_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.build_referenced_symbol_context", lambda *a: "")

    cost_for_usage_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.cost_for_usage",
        lambda *a: cost_for_usage_calls.append(a) or 999.0,  # loud, obviously-wrong value if ever called
    )
    cache_lookup_calls = []
    cache_write_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.lookup_cached_flash_review_result",
        lambda *a: cache_lookup_calls.append(a) or None,
    )
    monkeypatch.setattr(
        "scan_worker.jobs.store_flash_review_result",
        lambda *a, **k: cache_write_calls.append(a),
    )
    record_llm_spend_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_llm_spend",
        lambda *a, **k: record_llm_spend_calls.append(a),
    )
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.installation_spend_lock", _noop_spend_lock
    )

    from scan_worker.jobs import run_flash_review_job
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    # The free-tier chain's adapter was really called - proves the free-tier
    # path was actually exercised, not short-circuited before it started.
    mock_adapter.simple_completion.assert_called_once()

    # Fix for Issue A: free-tier tokens must never get priced at the paid
    # (Luna/DeepSeek) rate - cost_for_usage should never be called at all
    # for a free-tier review.
    assert cost_for_usage_calls == []
    # The spend actually recorded must be $0, not whatever cost_for_usage
    # would have produced if it had (wrongly) been called.
    assert record_llm_spend_calls == [("postgresql://unused", 1, 0.0)]

    # Fix for Issue B: free-tier reviews must never read or write the
    # shared similarity cache, so a paid customer who upgraded from free
    # can never be served a free-tier-model cached result.
    assert cache_lookup_calls == []
    assert cache_write_calls == []


def test_flash_review_job_reserves_the_free_tier_monthly_count_atomically(monkeypatch):
    # Regression guard for a TOCTOU race: the old check-then-later-increment
    # design under installation_spend_lock let two concurrent free-tier
    # reviews on the same installation both read "under cap" before either
    # recorded an attempt. The fix is reserve_flash_review_count - a single
    # atomic UPSERT...WHERE...RETURNING, not a lock (see
    # test_scan_worker_db.py's real-concurrency test for proof it holds
    # under actual concurrent load). This just confirms jobs.py calls it
    # with the right cap.
    reserve_calls = []

    def _reserve_flash_review_count(dsn, installation_id, limit):
        reserve_calls.append((installation_id, limit))
        return True

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", _reserve_flash_review_count)
    # Short-circuit before the review body runs - this test only cares
    # whether the cap was reserved, not the rest of the job.
    monkeypatch.setattr("scan_worker.jobs._run_flash_review", lambda *a, **k: True)

    from scan_worker.jobs import run_flash_review_job
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert reserve_calls == [(1, MAX_FREE_TIER_FLASH_REVIEWS_PER_MONTH)]


def test_flash_review_job_skips_when_over_the_free_tier_monthly_cap(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    # False = the atomic reservation itself found the cap already reached -
    # nothing left to release, since nothing was ever reserved.
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: False)
    run_called = []
    monkeypatch.setattr(
        "scan_worker.jobs._run_flash_review", lambda *a, **k: run_called.append(True)
    )

    from scan_worker.jobs import run_flash_review_job
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert run_called == []


def test_flash_review_job_alerts_ops_when_all_free_tier_providers_fail(monkeypatch):
    # The failed-review-comment path is deliberately not used here (see
    # flash_review.review_diff's FreeTierFallbackExhausted handling - "no
    # findings, not a crash" is the intended degradation for a free user).
    # But a total outage across all four providers still needs to reach a
    # human, or a rotated/expired key could silently blackhole free-tier
    # review indefinitely with nothing but an unwatched log line. This
    # confirms the on_free_tier_exhausted callback jobs.py wires into
    # review_diff actually reaches _send_ops_alert.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr(
        "scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0
    )
    from unittest.mock import MagicMock
    failing_adapter = MagicMock()
    failing_adapter.name = "Groq"
    failing_adapter.simple_completion.side_effect = RuntimeError("rate limited")
    monkeypatch.setattr(
        "scan_worker.model_tiers.writing_adapter_chain_for_free_tier",
        lambda *a, **k: [failing_adapter],
    )
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr("scan_worker.jobs.resolve_model", lambda *a: "gpt-5.6-luna")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- a.py ---\n+real change\n")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["a.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.is_non_substantive_diff", lambda *a: False)
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr("scan_worker.jobs.files_missing_from_review_context", lambda *a: [])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a: None)
    monkeypatch.setattr("scan_worker.jobs.build_code_evidence_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_dependency_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_change_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_blast_radius_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.build_referenced_symbol_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.cost_for_usage", lambda *a: 999.0)
    monkeypatch.setattr("scan_worker.jobs.lookup_cached_flash_review_result", lambda *a: None)
    monkeypatch.setattr("scan_worker.jobs.store_flash_review_result", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)

    ops_alert_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._send_ops_alert",
        lambda *a, **k: ops_alert_calls.append(a),
    )

    from scan_worker.jobs import run_flash_review_job
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert len(ops_alert_calls) == 1
    assert ops_alert_calls[0][1] == "flash_review.free_tier_exhausted"


def test_flash_review_does_not_post_or_advance_sha_when_free_tier_exhausted(monkeypatch):
    # Same all-providers-failed scenario as
    # test_flash_review_job_alerts_ops_when_all_free_tier_providers_fail,
    # but checking the other half of the contract: a review that never
    # actually ran must not tell the user their PR is clean, and must not
    # advance last_reviewed_sha - doing either would silently and
    # permanently skip reviewing the diff that failed. The
    # no-free-tier-keys-configured branch a few lines up in
    # _run_flash_review already bails before touching either; this
    # confirms the mid-review-exhaustion branch does the same.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr(
        "scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0
    )
    from unittest.mock import MagicMock
    failing_adapter = MagicMock()
    failing_adapter.name = "Groq"
    failing_adapter.simple_completion.side_effect = RuntimeError("rate limited")
    monkeypatch.setattr(
        "scan_worker.model_tiers.writing_adapter_chain_for_free_tier",
        lambda *a, **k: [failing_adapter],
    )
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr("scan_worker.jobs.resolve_model", lambda *a: "gpt-5.6-luna")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- a.py ---\n+real change\n")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["a.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.is_non_substantive_diff", lambda *a: False)
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr("scan_worker.jobs.files_missing_from_review_context", lambda *a: [])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a: None)
    monkeypatch.setattr("scan_worker.jobs.build_code_evidence_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_dependency_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_change_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_blast_radius_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.build_referenced_symbol_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.cost_for_usage", lambda *a: 999.0)
    monkeypatch.setattr("scan_worker.jobs.lookup_cached_flash_review_result", lambda *a: None)
    monkeypatch.setattr("scan_worker.jobs.store_flash_review_result", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._send_ops_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)

    comment_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda *a, **k: comment_calls.append(a),
    )
    sha_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_last_reviewed_sha",
        lambda *a, **k: sha_calls.append(a),
    )

    from scan_worker.jobs import run_flash_review_job
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert comment_calls == []
    assert sha_calls == []


def test_flash_review_job_skips_when_debounced(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: False
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_skips_when_spend_cap_reached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    # The count reservation succeeds (a slot exists), but the dollar
    # reservation is the one that finds the cap already reached - jobs.py
    # must then release the count reservation it just took, since the
    # review never actually gets to run.
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: False)
    released = []
    monkeypatch.setattr(
        "scan_worker.jobs.release_flash_review_count_reservation",
        lambda *a, **k: released.append(True),
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []
    assert released == [True]


def test_flash_review_job_skips_when_monthly_review_count_reached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    # False = the atomic reservation itself found the count cap already
    # reached - reserve_llm_spend must never even be attempted, since
    # there's nothing to reserve it for.
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: False)
    spend_reserve_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.reserve_llm_spend", lambda *a, **k: spend_reserve_called.append(True)
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []
    assert spend_reserve_called == []


def test_flash_review_job_skips_model_call_for_lockfile_only_diff(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- package-lock.json ---\n+huge lockfile diff"
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["package-lock.json"])
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []
    assert "no issues found" in posted["body"].lower()


def test_flash_review_job_posts_findings_and_updates_state(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [
            {"file": "app.py", "line": 1, "issue": "real problem", "source": "llm"}
        ],
    )
    recorded_spend = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_llm_spend",
        lambda dsn, iid, cost, **kwargs: recorded_spend.append(cost),
    )
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    set_sha_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_last_reviewed_sha",
        lambda dsn, iid, repo, pr, sha: set_sha_calls.append(sha),
    )
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
    from scan_worker.jobs import FLASH_REVIEW_MARKER, run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    inline_comments = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda client, token, repo, pr, commit_id, path, line, body: inline_comments.append(
            (path, line, body)
        )
        or {"id": 999001},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    # The finding itself now posts as its own inline review comment
    # (create_pr_review_comment) anchored to app.py:1, not listed inside
    # the summary issue-comment (upsert_pr_comment) - see
    # _post_flash_review_finding_comments.
    assert len(inline_comments) == 1
    assert inline_comments[0][0] == "app.py"
    assert inline_comments[0][1] == 1
    assert "real problem" in inline_comments[0][2]
    assert "1 finding(s) posted as inline review comment(s) below" in posted["body"]
    assert posted["marker"] == FLASH_REVIEW_MARKER
    assert set_sha_calls == ["bbb"]
    # True-up delta, not the raw total: real cost (0.0 - review_diff is
    # mocked, no on_usage ever fires) minus the FLASH_REVIEW_SPEND_RESERVE_USD
    # (0.5) reserved up front - record_llm_spend's additive upsert applies
    # this negative delta to give back the unused portion of the reservation.
    assert recorded_spend == [-FLASH_REVIEW_SPEND_RESERVE_USD]


def test_flash_review_comment_body_prefixes_the_symbol_when_present():
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body(
        {"file": "app.py", "line": 12, "issue": "real problem", "symbol": "handle_request"}
    )
    assert body.startswith("**`handle_request`**")
    assert "real problem" in body


def test_flash_review_comment_body_omits_the_symbol_line_when_none():
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body({"file": "app.py", "line": 12, "issue": "real problem"})
    assert "**`" not in body
    assert body.startswith("real problem")


def test_flash_review_job_attaches_symbol_attribution_from_deterministic_evidence(monkeypatch):
    # Build B: the symbol shown in the posted comment must come from the
    # same deterministic module-graph evidence every other blast-radius/
    # dependency-impact context already reads (find_symbol_at_location),
    # never a field the LLM itself generated - see find_symbol_at_location's
    # docstring in flash_review.py.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr(
        "scan_worker.jobs._latest_evidence_or_none",
        lambda *a, **k: {
            "repository": {
                "modules": [
                    {
                        "path": "app.py",
                        "symbols": {
                            "functions": [
                                {"name": "handle_request", "start_line": 1, "end_line": 5}
                            ],
                            "classes": [],
                        },
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [
            {"file": "app.py", "line": 2, "issue": "real problem", "source": "llm"}
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    inline_comments = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda client, token, repo, pr, commit_id, path, line, body: inline_comments.append(
            (path, line, body)
        )
        or {"id": 999002},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert len(inline_comments) == 1
    assert "**`handle_request`**" in inline_comments[0][2]


def test_flash_review_job_reserves_the_cap_before_running_the_review(monkeypatch):
    # F25/atomic-reservation redesign: run_flash_review_job used to check the
    # cap inside a lock, release it, run the (multi-minute) review unlocked,
    # then re-acquire the lock just to record spend/count - a real window
    # where two concurrent reviews for the same installation could both pass
    # the check before either recorded anything. The fix reserves both caps
    # atomically (reserve_flash_review_count/reserve_llm_spend) BEFORE the
    # review starts, not after - see test_scan_worker_db.py's real-concurrency
    # tests for proof the reservation itself is atomic under actual
    # concurrent load. This test verifies the call ORDER: by the time
    # review_diff runs, the reservation has already happened.
    call_order = []

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))

    def _reserve_flash_review_count(dsn, iid, limit):
        call_order.append("reserve_count")
        return True

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        call_order.append("reserve_spend")
        return True

    def _review_diff(diff_text, file_context="", **kwargs):
        call_order.append("review_diff")
        return [{"file": "app.py", "line": 1, "issue": "x", "source": "llm"}]

    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", _reserve_flash_review_count)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.review_diff", _review_diff)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)

    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert call_order == ["reserve_count", "reserve_spend", "review_diff"]


def test_flash_review_job_releases_reservation_when_the_review_never_runs(monkeypatch):
    # A reservation that never became a real review (every free-tier
    # provider failed, or no provider keys were configured) must not
    # permanently consume a slot/dollar the installation never actually
    # used - see run_flash_review_job's finally block.
    released = {"count": False, "spend": False}

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr(
        "scan_worker.jobs.release_flash_review_count_reservation",
        lambda *a, **k: released.__setitem__("count", True),
    )
    monkeypatch.setattr(
        "scan_worker.jobs.release_llm_spend_reservation",
        lambda *a, **k: released.__setitem__("spend", True),
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    # Free tier, no adapter chain built (no provider keys) - _run_flash_review
    # returns False before ever calling review_diff.
    monkeypatch.setattr("scan_worker.model_tiers.writing_adapter_chain_for_free_tier", lambda *a, **k: [])

    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    # Free tier has no dollar reservation (reserved_spend stays 0.0), so
    # only the count reservation should be released.
    assert released == {"count": True, "spend": False}


def test_flash_review_job_does_not_release_reservation_after_a_successful_review(monkeypatch):
    released = {"count": False, "spend": False}

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr(
        "scan_worker.jobs.release_flash_review_count_reservation",
        lambda *a, **k: released.__setitem__("count", True),
    )
    monkeypatch.setattr(
        "scan_worker.jobs.release_llm_spend_reservation",
        lambda *a, **k: released.__setitem__("spend", True),
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [{"file": "app.py", "line": 1, "issue": "x", "source": "llm"}],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)

    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert released == {"count": False, "spend": False}


def test_flash_review_job_posts_grounding_note_when_some_findings_are_dropped(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))

    def fake_review_diff(diff_text, file_context="", **kwargs):
        kwargs["on_grounding_result"]({"proposed": 2, "kept": 1})
        return [{"file": "app.py", "line": 1, "issue": "real problem", "source": "llm"}]

    monkeypatch.setattr("scan_worker.jobs.review_diff", fake_review_diff)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "Grounding: 1 of 2 proposed finding(s) held up" in posted["body"]


def test_flash_review_job_reports_zero_grounded_distinctly_from_no_issues_found(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))

    def fake_review_diff(diff_text, file_context="", **kwargs):
        kwargs["on_grounding_result"]({"proposed": 3, "kept": 0})
        return []

    monkeypatch.setattr("scan_worker.jobs.review_diff", fake_review_diff)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "No issues held up under grounding (3 proposed, 0 grounded" in posted["body"]
    assert "No issues found in this diff." not in posted["body"]
    # The line above already states the 0-grounded fact - a second
    # "Grounding: 0 of 3..." footer would just repeat it.
    assert "Grounding:" not in posted["body"]


def test_flash_review_job_reports_zero_confirmed_distinctly_from_zero_grounded(monkeypatch):
    # Real regression this guards: grounding accepted findings (kept > 0),
    # but the second-model verification step rejected all of them, so
    # review_diff returns []. Before this test existed, that hit the
    # elif proposed: branch and printed "0 grounded" even though grounding
    # had actually succeeded - factually wrong, and confusing "verification"
    # (this message's pre-existing name for grounding) with the new,
    # distinct second-model verification step.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))

    def fake_review_diff(diff_text, file_context="", **kwargs):
        kwargs["on_grounding_result"]({"proposed": 3, "kept": 3})
        return []

    monkeypatch.setattr("scan_worker.jobs.review_diff", fake_review_diff)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "No issues held up under independent verification (3 grounded, 0 confirmed" in posted["body"]
    assert "0 grounded in this diff" not in posted["body"]
    assert "No issues found in this diff." not in posted["body"]


def test_flash_review_job_discloses_files_it_never_reviewed(monkeypatch):
    # "No issues found in this diff." over a PR where most files were never
    # read is the most damaging form of the silent-degradation problem:
    # silence reads as an all-clear. fetch_review_file_context stops at
    # MAX_CONTEXT_FILES, so this is reachable on any sufficiently large PR.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- a.py ---\n+x")
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["a.py", "huge.py", "later.py"]
    )
    # Only a.py's content came back - huge.py was over the size cap and
    # later.py fell past the file-count cap.
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {"a.py": "x"})
    )
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: [])
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "No issues found in this diff." in posted["body"]
    assert "2 of 3 changed file(s) were not included" in posted["body"]
    assert "`huge.py`" in posted["body"]
    assert "`later.py`" in posted["body"]


def test_flash_review_job_adds_no_coverage_note_when_every_file_was_read(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- a.py ---\n+x")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["a.py"])
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {"a.py": "x"})
    )
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: [])
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "not included in this review" not in posted["body"]


def test_flash_review_job_posts_failure_comment_instead_of_raising(monkeypatch):
    # Before this fix, any exception in the review body (LLM call, GitHub
    # API, cache lookup) propagated straight out of the RQ job with zero
    # customer-visible signal - the PR would just never get a comment, and
    # nothing would tell the customer flash review had failed.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    # The raised exception aborts the job before _run_flash_review reaches
    # its normal completion, so run_flash_review_job's finally block must
    # release both reservations - a review that never ran must not
    # permanently consume a slot/dollar the installation never used.
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)

    def _raise_diff_fetch(*a, **k):
        raise RuntimeError("GitHub API timed out")

    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", _raise_diff_fetch)

    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
    from scan_worker.jobs import FLASH_REVIEW_MARKER, run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert posted["marker"] == FLASH_REVIEW_MARKER
    assert "couldn't complete this flash review" in posted["body"]
    assert "GitHub API timed out" in posted["body"]


def test_flash_review_job_passes_referenced_symbol_context_to_review_diff(monkeypatch):
    # Real hallucination this exists to prevent: Flash Review claimed an
    # imported function needed `await`, citing "usage in admin.py", when
    # admin.py's real (synchronous) definition was never in its context.
    # Proves the job actually wires a changed file's imported-and-referenced
    # symbol's real source into review_diff, not just that the pure
    # function (already covered in test_flash_review.py) works in isolation.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff",
        lambda *a, **k: "--- dashboard.py ---\n@@ -1,1 +75,1 @@\n+_github_http_client()\n",
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["dashboard.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr(
        "scan_worker.jobs._latest_evidence_or_none",
        lambda *a, **k: {
            "repository": {
                "modules": [
                    {"path": "dashboard.py", "imports": ["admin.py"], "symbols": {"functions": [], "classes": []}},
                    {
                        "path": "admin.py",
                        "imports": [],
                        "symbols": {
                            "functions": [
                                {"name": "_github_http_client", "start_line": 2, "end_line": 3}
                            ],
                            "classes": [],
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_file_content",
        lambda client, token, repo_full_name, path, ref: (
            "line1\ndef _github_http_client() -> httpx.Client:\n    return httpx.Client()\nline4"
            if path == "admin.py"
            else None
        ),
    )
    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: captured.update(kwargs) or [],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "admin.py:_github_http_client" in captured["referenced_symbol_context"]
    assert "def _github_http_client() -> httpx.Client" in captured["referenced_symbol_context"]


def test_flash_review_job_passes_changed_file_contents_to_review_diff(monkeypatch):
    # Real production gap this closes: Flash Review can correctly quote a
    # buggy string verbatim while citing the wrong line for it (confirmed
    # on a real PR - see _line_citation_content_matches's docstring in
    # flash_review.py). review_diff can only catch that if it's actually
    # given the changed files' real content to check citations against.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff",
        lambda *a, **k: "--- app.py ---\n@@ -1,1 +1,1 @@\n+broken",
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_review_file_context",
        lambda *a, **k: ("", {"app.py": "real content of app.py"}),
    )
    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: captured.update(kwargs) or [],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert captured["file_contents"] == {"app.py": "real content of app.py"}


def test_flash_review_job_requests_second_model_verification_on_paid_plan(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n@@ -1,1 +1,1 @@\n+broken"
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: captured.update(kwargs) or [],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert captured["verify_with_second_model"] is True
    assert callable(captured["on_verification_usage"])

    # And that callback must price at the verification model's own rate
    # (deepseek-v4-flash), never flash_review_model's - wrong whenever
    # generation ran on Luna, which paid plan usually does.
    cost_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.cost_for_usage", lambda model, p, c: cost_calls.append(model) or 0.001
    )
    captured["on_verification_usage"](100, 50)
    assert cost_calls == ["deepseek-v4-flash"]


def test_flash_review_job_does_not_request_second_model_verification_on_flash_tier(monkeypatch):
    # The real bug this guards: verify_with_second_model used to be
    # `not is_free_tier`, which would have silently given the flash plan
    # dual-agent verification for free the moment it existed as a plan
    # value - flash's real cost/recall validation was run on solo
    # generation only, and has no room in its cap for that. Also confirms
    # flash gets its own, separately-validated review-count cap (800),
    # not AIR's 500.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "flash"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n@@ -1,1 +1,1 @@\n+broken"
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: captured.update(kwargs) or [],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    reserve_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.reserve_flash_review_count",
        lambda dsn, installation_id, cap: reserve_calls.append(cap) or True,
    )
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import MAX_FLASH_TIER_FLASH_REVIEWS_PER_MONTH, run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert captured["verify_with_second_model"] is False
    assert reserve_calls == [MAX_FLASH_TIER_FLASH_REVIEWS_PER_MONTH]
    assert MAX_FLASH_TIER_FLASH_REVIEWS_PER_MONTH == 800


def test_flash_review_job_does_not_request_second_model_verification_on_free_tier(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    monkeypatch.setattr(
        "scan_worker.model_tiers.writing_adapter_chain_for_free_tier", lambda *a, **k: [mock_adapter]
    )
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr("scan_worker.jobs.resolve_model", lambda *a: "gpt-5.6-luna")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- a.py ---\n+real change\n")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["a.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.is_non_substantive_diff", lambda *a: False)
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr("scan_worker.jobs.files_missing_from_review_context", lambda *a: [])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a: None)
    monkeypatch.setattr("scan_worker.jobs.build_code_evidence_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_dependency_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_change_impact_context", lambda *a: "")
    monkeypatch.setattr("scan_worker.jobs.build_blast_radius_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.build_referenced_symbol_context", lambda *a: "")
    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: captured.update(kwargs) or [],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert captured["verify_with_second_model"] is False


def test_flash_review_job_never_sends_the_raw_file_context_blob_to_review_diff(monkeypatch):
    # Compact is the shipped default (see the comment above the file_context
    # blanking in _run_flash_review): review_diff must never receive the raw
    # file-content blob, even when fetch_review_file_context returns one -
    # only file_contents (used for citation verification) should flow
    # through unchanged.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff",
        lambda *a, **k: "--- app.py ---\n@@ -1,1 +1,1 @@\n+broken",
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_review_file_context",
        lambda *a, **k: (
            "--- full content: app.py ---\ndef broken():\n    pass",
            {"app.py": "real content of app.py"},
        ),
    )
    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: captured.update(
            {"file_context": file_context, **kwargs}
        )
        or [],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert captured["file_context"] == ""
    assert captured["file_contents"] == {"app.py": "real content of app.py"}


def test_flash_review_job_renders_suggestion_as_plain_fence_not_github_suggestion_syntax(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [
            {"file": "app.py", "line": 1, "issue": "unclosed handle", "suggestion": "f.close()", "source": "llm"}
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    inline_comments = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda client, token, repo, pr, commit_id, path, line, body: inline_comments.append(body)
        or {"id": 999001},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    # The suggestion now lives in the finding's own inline review comment
    # (create_pr_review_comment), not the summary issue-comment
    # (upsert_pr_comment) - see _post_flash_review_finding_comments.
    assert len(inline_comments) == 1
    assert "f.close()" in inline_comments[0]
    assert "```suggestion" not in inline_comments[0]


def test_flash_review_job_posts_no_issues_found_when_findings_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+fine")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: ("", {}))
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda diff_text, file_context="", **kwargs: [])
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "no issues found" in posted["body"].lower()


def _wiki_evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {
                        "functions": [{"name": "do_login", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                }
            ],
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "architecture": {"clusters": [{"id": 0, "modules": ["auth/login.py"], "internal_edges": 0}]},
    }


def test_run_live_wiki_full_build_job_skips_model_call_on_cache_hit(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])
    monkeypatch.setattr(
        "scan_worker.jobs.lookup_cached_result",
        lambda *a, **k: ({"description": "Cached, verified description.", "files": []}, "deepseek-v4-pro"),
    )
    store_calls = []
    monkeypatch.setattr("scan_worker.jobs.store_result", lambda *a, **k: store_calls.append(True))
    monkeypatch.setattr("scan_worker.live_wiki.verify_citations", lambda *a, **k: {"all_verified": True})

    adapter_calls = []

    class _SpyAdapter:
        name = "DeepSeek"

        def simple_completion(self, *a, **k):
            adapter_calls.append(True)
            return json.dumps({"description": "should not be reached", "files": []})

    class _NamingAdapter:
        def simple_completion(self, *a, **k):
            return json.dumps({"0": "Auth"})

    monkeypatch.setattr(
        "scan_worker.jobs._live_wiki_full_build_writing_adapter",
        lambda on_usage=None, before_llm_call=None: _SpyAdapter(),
    )
    monkeypatch.setattr(
        "scan_worker.jobs._live_wiki_naming_adapter",
        lambda on_usage=None, before_llm_call=None: _NamingAdapter(),
    )
    monkeypatch.setattr("scan_worker.jobs._store_wiki_generation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_wiki_build_status", lambda *a, **k: None)

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert adapter_calls == []
    assert store_calls == []


def _patch_sweep(
    monkeypatch,
    *,
    threshold_ms=None,
    prior=None,
    result_entry=None,
    evidence=None,
    retry_result_entry=None,
    redis_conn=None,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.time.sleep", lambda *a, **k: None)
    # Real DNS resolution has no place in a unit test - SSRF re-validation
    # itself is covered by its own dedicated tests below.
    monkeypatch.setattr(
        "scan_worker.jobs.validate_and_pin_https_url", lambda url: (url, "93.184.216.34")
    )
    monkeypatch.setattr(
        "scan_worker.jobs.list_health_check_targets_all",
        lambda dsn: [
            {
                "target_id": 900,
                "installation_id": 1,
                "repo_full_name": "octocat/hello-world",
                "label": "Primary",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": threshold_ms,
                "webhook_url": "https://hooks.slack.com/health",
            }
        ],
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: evidence
        or {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    default_first = result_entry or {
        "method": "GET",
        "path": "/x",
        "reachable": True,
        "status_code": 200,
        "latency_ms": 90.0,
        "response_shape": None,
    }
    calls = {"count": 0}

    def fake_healthcheck(endpoints, base_url, pinned_ip=None):
        calls["count"] += 1
        if calls["count"] == 1 or retry_result_entry is None:
            return {"results": [default_first]}
        return {"results": [retry_result_entry]}

    monkeypatch.setattr("scan_worker.jobs.run_healthcheck", fake_healthcheck)
    monkeypatch.setattr("scan_worker.jobs._enqueue_health_down_retry", lambda *a, **k: False)
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health", lambda dsn, iid, repo, method, path, target_id=None: prior
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)
    active_redis_conn = redis_conn if redis_conn is not None else _FakeRedis()
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: active_redis_conn)
    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))
    return sent


def test_send_alerts_if_configured_sends_email_when_alert_email_set(monkeypatch):
    from scan_worker.jobs import _send_alerts_if_configured

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **k: enqueued.append(k),
    )

    _send_alerts_if_configured(
        {"installation_id": 1, "target_id": 900, "alert_email": "ops@example.com"},
        {"text": "*Aletheore*: endpoint down on `octocat/hello-world`"},
    )

    assert len(enqueued) == 1
    assert enqueued[0]["to_email"] == "ops@example.com"
    assert enqueued[0]["template_name"] == "health_alert"
    assert enqueued[0]["template_arg"] == "*Aletheore*: endpoint down on `octocat/hello-world`"
    assert enqueued[0]["installation_id"] == 1


def test_send_alerts_if_configured_sends_both_channels_when_both_set(monkeypatch):
    from scan_worker.jobs import _send_alerts_if_configured

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    slack_sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: slack_sent.append(msg))
    email_sent = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **k: email_sent.append(k),
    )

    _send_alerts_if_configured(
        {
            "installation_id": 1,
            "target_id": 900,
            "webhook_url": "https://hooks.slack.com/x",
            "alert_email": "ops@example.com",
        },
        {"text": "down"},
    )

    assert len(slack_sent) == 1
    assert len(email_sent) == 1


def test_send_alerts_if_configured_email_dedupe_key_collapses_within_the_same_second(monkeypatch):
    # Regression: the docstring says the dedupe_key includes wall-clock
    # time "down to the second" specifically so a genuine retry of the
    # outer job re-sending the same flip collapses into one email - but
    # time.time() carries microsecond precision, so two calls a
    # millisecond apart (an actual retry) each got their own unique key
    # and dedup never fired at all. A retried flip must produce the same
    # dedupe_key when it happens within the same second.
    from scan_worker.jobs import _send_alerts_if_configured

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    enqueued = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **k: enqueued.append(k),
    )

    installation = {"installation_id": 1, "target_id": 900, "alert_email": "ops@example.com"}
    _send_alerts_if_configured(installation, {"text": "down"})
    _send_alerts_if_configured(installation, {"text": "down"})

    assert len(enqueued) == 2
    assert enqueued[0]["dedupe_key"] == enqueued[1]["dedupe_key"]


def test_send_alerts_if_configured_sends_neither_when_unconfigured(monkeypatch):
    from scan_worker.jobs import _send_alerts_if_configured

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    slack_sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: slack_sent.append(msg))
    email_sent = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **k: email_sent.append(k),
    )
    pushover_sent = []
    monkeypatch.setattr(
        "scan_worker.jobs.send_pushover_alert",
        lambda *a, **k: pushover_sent.append(k),
    )

    _send_alerts_if_configured({"installation_id": 1, "target_id": 900}, {"text": "down"})

    assert slack_sent == []
    assert email_sent == []
    assert pushover_sent == []


def test_send_alerts_if_configured_sends_pushover_when_user_key_set(monkeypatch):
    from scan_worker.jobs import _send_alerts_if_configured

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "server-app-token")
    from app_server.config import get_settings

    get_settings.cache_clear()
    pushover_sent = []
    monkeypatch.setattr(
        "scan_worker.jobs.send_pushover_alert",
        lambda token, user_key, message, **k: pushover_sent.append((token, user_key, message)),
    )

    _send_alerts_if_configured(
        {"installation_id": 1, "target_id": 900, "pushover_user_key": "user-key-y"},
        {"text": "*Aletheore*: endpoint down on `octocat/hello-world`", "pushover_priority": 2},
    )

    assert len(pushover_sent) == 1
    token, user_key, message = pushover_sent[0]
    assert token == "server-app-token"
    assert user_key == "user-key-y"
    assert message["pushover_priority"] == 2


def test_send_alerts_if_configured_skips_pushover_when_server_token_unset(monkeypatch):
    # An installation can have pushover_user_key set (from before the
    # server-side token was ever configured, or after it was later
    # removed) - this must degrade silently, the same as the other two
    # channels degrade when their own config is missing, not raise into
    # the sweep loop.
    from scan_worker.jobs import _send_alerts_if_configured

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    from app_server.config import get_settings

    get_settings.cache_clear()
    pushover_sent = []
    monkeypatch.setattr(
        "scan_worker.jobs.send_pushover_alert",
        lambda *a, **k: pushover_sent.append(k),
    )

    _send_alerts_if_configured(
        {"installation_id": 1, "target_id": 900, "pushover_user_key": "user-key-y"},
        {"text": "down"},
    )

    assert pushover_sent == []


def test_sweep_sends_reachability_down_alert(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]


def test_sweep_retries_before_confirming_down_and_recovers_silently(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": False,
            "status_code": None,
            "latency_ms": 10.0,
            "response_shape": None,
        },
        retry_result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": True,
            "status_code": 200,
            "latency_ms": 95.0,
            "response_shape": None,
        },
    )
    enqueued = []
    monkeypatch.setattr(
        "scan_worker.jobs._enqueue_health_down_retry",
        lambda target, entry, attempt: enqueued.append((target, entry, attempt)) or True,
    )

    from scan_worker.jobs import run_health_check_down_retry_job, run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(enqueued) == 1
    assert sent == []

    target, entry, attempt = enqueued[0]
    run_health_check_down_retry_job(target, entry, attempt)

    assert sent == []


def test_sweep_confirms_down_after_retries_all_fail(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": False,
            "status_code": None,
            "latency_ms": 10.0,
            "response_shape": None,
        },
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]


def test_sweep_does_not_retry_a_recovery_flip(monkeypatch):
    healthcheck_calls = []
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": False, "latency_ms": None},
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": True,
            "status_code": 200,
            "latency_ms": 80.0,
            "response_shape": None,
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url, pinned_ip=None: healthcheck_calls.append(True)
        or {
            "results": [
                {
                    "method": "GET",
                    "path": "/x",
                    "reachable": True,
                    "status_code": 200,
                    "latency_ms": 80.0,
                    "response_shape": None,
                }
            ]
        },
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(healthcheck_calls) == 1
    assert len(sent) == 1
    assert "recovered" in sent[0]["text"]


def test_sweep_attaches_recent_commit_on_confirmed_down(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        evidence={
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/x",
                            "file": "controllers/user.controller.ts",
                            "line": 42,
                        }
                    ]
                }
            }
        },
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": False,
            "status_code": None,
            "latency_ms": 10.0,
            "response_shape": None,
        },
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs._commit_attachment_from_graph",
        lambda installation_id, repo_full_name, source_file: {
            "kind": "commit",
            "file": None,
            "line": None,
            "end_line": None,
            "symbol": None,
            "owner": None,
            "owner_status": "unavailable",
            "commit": {"sha": "abc123def456", "author_name": "Ada", "subject": "touched the handler"},
            "commit_status": "available",
            "dependency": None,
            "dependency_status": "unavailable",
            "risk": [],
            "risk_status": "unavailable",
            "confidence": "weak",
            "evidence_path": None,
            "evidence_status": "unavailable",
        },
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "Recent commit: `abc123de`" in sent[0]["text"]
    assert "touched the handler" in sent[0]["text"]


def test_sweep_skips_fix_suggestion_when_endpoint_was_recently_down(monkeypatch):
    # The cooldown itself, not just its plumbing: a flapping endpoint
    # already recorded down within HEALTH_FIX_SUGGESTION_COOLDOWN_SECONDS
    # must not pay for a second LLM fix-suggestion call on this flip - the
    # alert (and its deterministic commit/owner attachments) still fires,
    # only the one expensive call is skipped.
    from scan_worker.jobs import _health_fix_suggestion_cooldown_key

    redis_conn = _FakeRedis()
    # installation_id/repo_full_name/target_id here match _patch_sweep's own
    # fixed target fixture (installation_id=1, repo_full_name=
    # "octocat/hello-world", target_id=900) - pre-populating the exact
    # cooldown key a real prior suggestion would have set is what actually
    # exercises the Redis-backed cooldown, not a mocked function.
    redis_conn.set(
        _health_fix_suggestion_cooldown_key(1, "octocat/hello-world", "GET", "/x", 900), "1", ex=1800
    )
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        evidence={
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {"method": "GET", "path": "/x", "file": "controllers/user.controller.ts", "line": 42}
                    ]
                }
            }
        },
        result_entry={
            "method": "GET", "path": "/x", "reachable": False,
            "status_code": None, "latency_ms": 10.0, "response_shape": None,
        },
        redis_conn=redis_conn,
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs._commit_attachment_from_graph", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._owner_attachment_from_graph", lambda *a, **k: None)

    suggestion_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._fix_suggestion_attachment",
        lambda *a, **k: suggestion_calls.append(True),
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert suggestion_calls == []


def test_sweep_alerts_without_commit_when_correlation_fails(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        evidence={
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/x",
                            "file": "controllers/user.controller.ts",
                            "line": 42,
                        }
                    ]
                }
            }
        },
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": False,
            "status_code": None,
            "latency_ms": 10.0,
            "response_shape": None,
        },
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})

    def _raise(*a, **k):
        raise RuntimeError("github api unavailable")

    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", _raise)

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]
    assert "Recent commit" not in sent[0]["text"]


def test_run_runtime_event_job_sends_alert_with_resolved_chain(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "air", "webhook_url": "https://hooks.slack.com/runtime"},
    )
    monkeypatch.setattr(
        "scan_worker.jobs._latest_evidence_or_none",
        lambda *a, **k: {"repository": {"modules": []}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs._attach_recent_commit_for_failure",
        lambda installation_id, repo_full_name, source_file, evidence_resolution, evidence=None, **k: {
            "symbol": "handle_request",
            "owner": ["@api-team"],
            "commit": {"sha": "abc123def456", "subject": "touched the handler"},
        },
    )
    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append((url, msg)))

    from scan_worker.jobs import run_runtime_event_job

    run_runtime_event_job(
        1,
        "octocat/hello-world",
        "ZeroDivisionError",
        "division by zero",
        "app/handler.py",
        42,
        method="GET",
        path="/v1/users",
    )

    assert len(sent) == 1
    url, message = sent[0]
    assert url == "https://hooks.slack.com/runtime"
    assert "ZeroDivisionError" in message["text"]
    assert "app/handler.py:42" in message["text"]
    assert "handle_request" in message["text"]
    assert "@api-team" in message["text"]


def test_run_runtime_event_job_skips_free_plan(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    called = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda *a, **k: called.append(True))

    from scan_worker.jobs import run_runtime_event_job

    run_runtime_event_job(1, "octocat/hello-world", "Error", "x", "a.py", 1)

    assert called == []


def test_run_runtime_event_job_skips_when_no_webhook_configured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._attach_recent_commit_for_failure", lambda *a, **k: None)
    called = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda *a, **k: called.append(True))

    from scan_worker.jobs import run_runtime_event_job

    run_runtime_event_job(1, "octocat/hello-world", "Error", "x", "a.py", 1)

    assert called == []


def test_run_runtime_event_job_sends_via_email_when_no_webhook_configured(monkeypatch):
    # Regression: run_runtime_event_job used to call send_health_alert
    # directly and gate the whole job on webhook_url alone - predating
    # email/Pushover as alert channels (_send_alerts_if_configured) and
    # never updated when those landed. An installation with only
    # alert_email configured got every health-check alert correctly but
    # silently zero runtime-error alerts.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "air", "alert_email": "ops@example.com"},
    )
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._attach_recent_commit_for_failure", lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda *a, **k: enqueued.append(k),
    )

    from scan_worker.jobs import run_runtime_event_job

    run_runtime_event_job(1, "octocat/hello-world", "ZeroDivisionError", "division by zero", "a.py", 1)

    assert len(enqueued) == 1
    assert enqueued[0]["to_email"] == "ops@example.com"
    assert "ZeroDivisionError" in enqueued[0]["template_arg"]


def test_run_runtime_event_job_sends_via_pushover_when_no_webhook_configured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "server-app-token")
    from app_server.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "air", "pushover_user_key": "u1"},
    )
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._attach_recent_commit_for_failure", lambda *a, **k: None)
    pushover_sent = []
    monkeypatch.setattr(
        "scan_worker.jobs.send_pushover_alert", lambda *a, **k: pushover_sent.append(a)
    )

    from scan_worker.jobs import run_runtime_event_job

    run_runtime_event_job(1, "octocat/hello-world", "ZeroDivisionError", "division by zero", "a.py", 1)

    assert len(pushover_sent) == 1


def test_sweep_sends_shape_change_alert_while_still_reachable(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={
            "reachable": True,
            "latency_ms": 100.0,
            "response_shape": ["email", "id", "name"],
        },
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": True,
            "status_code": 200,
            "latency_ms": 90.0,
            "response_shape": ["id", "name"],
        },
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "response shape changed" in sent[0]["text"]
    assert "dropped keys: email" in sent[0]["text"]


def test_sweep_skips_shape_alert_when_prior_shape_unknown(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0, "response_shape": None},
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": True,
            "status_code": 200,
            "latency_ms": 90.0,
            "response_shape": ["id"],
        },
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []


def test_sweep_sends_nothing_when_reachable_stays_same(monkeypatch):
    sent = _patch_sweep(monkeypatch, prior={"reachable": True, "latency_ms": 95.0})

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []


def test_sweep_does_not_alert_on_first_reachable_check(monkeypatch):
    sent = _patch_sweep(monkeypatch, prior=None)

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []


def test_sweep_sends_down_alert_on_first_unreachable_check(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior=None,
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]


def test_sweep_isolates_one_targets_failure_from_others(monkeypatch):
    # One installation's broken webhook URL (or any other failure) must not
    # take down the sweep for every other installation - this job runs
    # every HEALTH_SWEEP_INTERVAL_SECONDS for the whole customer base.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.validate_and_pin_https_url", lambda url: (url, "93.184.216.34")
    )
    monkeypatch.setattr(
        "scan_worker.jobs.list_health_check_targets_all",
        lambda dsn: [
            {
                "target_id": 1,
                "installation_id": 1,
                "repo_full_name": "acme/broken",
                "label": "Primary",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": None,
                "webhook_url": "https://hooks.slack.com/broken",
            },
            {
                "target_id": 2,
                "installation_id": 2,
                "repo_full_name": "acme/healthy",
                "label": "Primary",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": None,
                "webhook_url": "https://hooks.slack.com/healthy",
            },
        ],
    )

    def fake_get_latest_evidence(dsn, installation_id, repo_full_name):
        if installation_id == 1:
            raise RuntimeError("simulated failure for installation 1")
        return {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}}

    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", fake_get_latest_evidence)
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url, pinned_ip=None: {
            "results": [{"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 90.0}]
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health", lambda dsn, iid, repo, method, path, target_id=None: None
    )
    inserted = []
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: inserted.append(a))
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: None)

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    # Installation 2's target was still processed despite installation 1's
    # get_latest_evidence blowing up first in iteration order.
    assert len(inserted) == 1
    assert inserted[0][1] == 2


def test_sweep_revalidates_target_url_before_every_fetch_and_skips_on_ssrf_failure(monkeypatch):
    # A target that passed SSRF validation when it was saved (admin.py)
    # must be re-checked on every sweep cycle, not trusted forever - DNS
    # can be repointed at an internal/cloud-metadata address any time
    # after that one-time save-time check.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_health_check_targets_all",
        lambda dsn: [
            {
                "target_id": 1,
                "installation_id": 1,
                "repo_full_name": "acme/rebound",
                "label": "Primary",
                "base_url": "https://rebound.example.com",
                "latency_threshold_ms": None,
                "webhook_url": "https://hooks.slack.com/health",
            }
        ],
    )

    from app_server.url_validation import UnsafeURLError

    def fake_validate(url):
        raise UnsafeURLError(f"'{url}' now resolves to a disallowed address")

    monkeypatch.setattr("scan_worker.jobs.validate_and_pin_https_url", fake_validate)

    evidence_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: evidence_calls.append(True)
        or {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    fetch_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url, pinned_ip=None: fetch_calls.append(True)
        or {"results": [{"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 1.0}]},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: None)

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    # Rejected before ever touching evidence or making the actual request.
    assert evidence_calls == []
    assert fetch_calls == []


def test_sweep_proceeds_when_target_url_still_passes_validation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_health_check_targets_all",
        lambda dsn: [
            {
                "target_id": 1,
                "installation_id": 1,
                "repo_full_name": "acme/fine",
                "label": "Primary",
                "base_url": "https://fine.example.com",
                "latency_threshold_ms": None,
                "webhook_url": "https://hooks.slack.com/health",
            }
        ],
    )

    validated_urls = []
    monkeypatch.setattr(
        "scan_worker.jobs.validate_and_pin_https_url",
        lambda url: validated_urls.append(url) or (url, "93.184.216.34"),
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url, pinned_ip=None: {
            "results": [{"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 1.0}]
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health", lambda dsn, iid, repo, method, path, target_id=None: None
    )
    inserted = []
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: inserted.append(a))
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: None)

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert validated_urls == ["https://fine.example.com"]
    assert len(inserted) == 1


def test_sweep_threads_endpoint_source_location_into_alert(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        evidence={
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/x",
                            "file": "controllers/user.controller.ts",
                            "line": 42,
                        }
                    ]
                }
            }
        },
        result_entry={
            "method": "GET",
            "path": "/x",
            "reachable": False,
            "status_code": None,
            "latency_ms": 10.0,
        },
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "controllers/user.controller.ts:42" in sent[0]["text"]


def test_sweep_sends_latency_over_alert(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        threshold_ms=3000,
        prior={"reachable": True, "latency_ms": 1000.0},
        result_entry={"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 4200.0},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "slow" in sent[0]["text"]


def test_sweep_skips_latency_when_unreachable(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        threshold_ms=3000,
        prior={"reachable": False, "latency_ms": 5000.0},
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 5000.0},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []


def test_sweep_checks_every_target_independently(monkeypatch):
    # Two targets on the same repo (e.g. staging and production) - one down,
    # one up - must each be checked and alerted on their own, not merged or
    # short-circuited after the first.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.validate_and_pin_https_url", lambda url: (url, "93.184.216.34")
    )
    monkeypatch.setattr(
        "scan_worker.jobs.list_health_check_targets_all",
        lambda dsn: [
            {
                "target_id": 1,
                "installation_id": 1,
                "repo_full_name": "octocat/hello-world",
                "label": "Staging",
                "base_url": "https://staging.example.com",
                "latency_threshold_ms": None,
                "webhook_url": "https://hooks.slack.com/health",
            },
            {
                "target_id": 2,
                "installation_id": 1,
                "repo_full_name": "octocat/hello-world",
                "label": "Production",
                "base_url": "https://prod.example.com",
                "latency_threshold_ms": None,
                "webhook_url": "https://hooks.slack.com/health",
            },
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )

    def fake_healthcheck(endpoints, base_url, pinned_ip=None):
        reachable = base_url == "https://staging.example.com"
        return {
            "results": [
                {
                    "method": "GET",
                    "path": "/x",
                    "reachable": reachable,
                    "status_code": 200 if reachable else None,
                    "latency_ms": 50.0,
                    "response_shape": None,
                }
            ]
        }

    monkeypatch.setattr("scan_worker.jobs.run_healthcheck", fake_healthcheck)
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path, target_id=None: {"reachable": True, "latency_ms": 50.0},
    )
    # F28: a fresh down-flip is now deferred to a follow-up job instead of
    # being recorded/alerted synchronously (see
    # test_sweep_schedules_down_retries_without_blocking_later_targets for
    # that path). Force the graceful-degradation fallback here (as if
    # enqueueing the retry failed) so this test can keep asserting the
    # thing it's actually about: target 1 and target 2 are each checked on
    # their own, not merged or short-circuited after the first.
    monkeypatch.setattr("scan_worker.jobs._enqueue_health_down_retry", lambda *a, **k: False)
    recorded = []
    monkeypatch.setattr(
        "scan_worker.jobs.insert_endpoint_health",
        lambda dsn, iid, repo, method, path, reachable, status_code, latency_ms, response_shape=None, target_id=None, keep=20: recorded.append(
            (target_id, reachable)
        ),
    )
    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert set(recorded) == {(1, True), (2, False)}
    assert len(sent) == 1
    assert "down" in sent[0]["text"]


def test_sweep_schedules_down_retries_without_blocking_later_targets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    targets = [
        {
            "target_id": 1,
            "installation_id": 1,
            "repo_full_name": "octocat/slow",
            "label": "Primary",
            "base_url": "https://slow.example.com",
            "latency_threshold_ms": None,
            "webhook_url": "https://hooks.slack.com/health",
        },
        {
            "target_id": 2,
            "installation_id": 2,
            "repo_full_name": "octocat/later",
            "label": "Primary",
            "base_url": "https://later.example.com",
            "latency_threshold_ms": None,
            "webhook_url": "https://hooks.slack.com/health",
        },
    ]
    monkeypatch.setattr("scan_worker.jobs._rotated_health_check_targets", lambda dsn: targets)
    monkeypatch.setattr(
        "scan_worker.jobs.validate_and_pin_https_url", lambda url: (url, "93.184.216.34")
    )
    monkeypatch.setattr("scan_worker.jobs.time.sleep", lambda *a, **k: pytest.fail("sweep blocked on retry sleep"))

    def fake_evidence(dsn, installation_id, repo_full_name):
        endpoint_count = 12 if installation_id == 1 else 1
        return {
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {"method": "GET", "path": f"/endpoint-{index}"}
                        for index in range(endpoint_count)
                    ]
                }
            }
        }

    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", fake_evidence)

    def fake_healthcheck(endpoints, base_url, pinned_ip=None):
        if base_url == "https://slow.example.com":
            return {
                "results": [
                    {
                        "method": "GET",
                        "path": endpoint["path"],
                        "reachable": index >= 6,
                        "status_code": 200 if index >= 6 else None,
                        "latency_ms": 50.0,
                        "response_shape": None,
                    }
                    for index, endpoint in enumerate(endpoints)
                ]
            }
        return {
            "results": [
                {
                    "method": "GET",
                    "path": "/endpoint-0",
                    "reachable": True,
                    "status_code": 200,
                    "latency_ms": 50.0,
                    "response_shape": None,
                }
            ]
        }

    monkeypatch.setattr("scan_worker.jobs.run_healthcheck", fake_healthcheck)
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path, target_id=None: {"reachable": True, "latency_ms": 50.0},
    )
    enqueued_retries = []
    monkeypatch.setattr(
        "scan_worker.jobs._enqueue_health_down_retry",
        lambda target, entry, attempt: enqueued_retries.append((target["target_id"], entry["path"], attempt))
        or True,
    )
    recorded = []
    monkeypatch.setattr(
        "scan_worker.jobs.insert_endpoint_health",
        lambda dsn, iid, repo, method, path, reachable, status_code, latency_ms, response_shape=None, target_id=None, keep=20: recorded.append(
            (target_id, path, reachable)
        ),
    )
    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))

    from scan_worker.jobs import run_health_check_sweep_job

    start = time.monotonic()
    run_health_check_sweep_job()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert len(enqueued_retries) == 6
    assert any(target_id == 2 for target_id, _path, _reachable in recorded)
    assert sent == []


def test_health_sweep_rotates_target_order_between_ticks(monkeypatch):
    targets = [{"target_id": 1}, {"target_id": 2}, {"target_id": 3}]
    monkeypatch.setattr("scan_worker.jobs.list_health_check_targets_all", lambda dsn: targets)

    class FakeRedis:
        def __init__(self):
            self.count = 0

        def incr(self, key):
            self.count += 1
            return self.count

    redis_conn = FakeRedis()
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)

    from scan_worker.jobs import _rotated_health_check_targets

    first = _rotated_health_check_targets("postgresql://unused")
    second = _rotated_health_check_targets("postgresql://unused")

    assert [target["target_id"] for target in first] == [1, 2, 3]
    assert [target["target_id"] for target in second] == [2, 3, 1]


@pytest.mark.asyncio
async def test_sweep_end_to_end_against_real_postgres_redis_and_a_live_http_target(
    pool, redis_conn, monkeypatch
):
    """Every other sweep test in this file mocks list_health_check_targets_all,
    get_last_endpoint_health, insert_endpoint_health, and _enqueue_health_down_retry
    away entirely - real coverage of the *decision logic* (when to alert, when to
    retry), zero coverage of whether that logic is actually wired correctly to the
    real DB functions and a real RQ/Redis enqueue. This test enqueues the down-retry
    onto a real Redis-backed queue and runs it via Job.perform() the way RQ's own
    worker does (same discipline as test_pull_request_webhook_to_pr_comment_end_to_end
    in test_pr_scan_e2e.py), against a real local HTTP server that starts reachable
    and then goes down - so an argument-name mismatch between jobs.py and
    scan_worker/db.py, or a broken RQ serialization round-trip, would fail here
    instead of only in production.
    """
    import http.server
    import threading
    from datetime import datetime, timezone

    from rq import Queue
    from rq.registry import ScheduledJobRegistry

    class _OKHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        await pool.execute(
            "INSERT INTO installations (installation_id, account_login, plan, webhook_url) "
            "VALUES ($1, $2, $3, $4)",
            9001, "integration-test-org", "air", "https://hooks.slack.com/integration-test",
        )
        target_id = await pool.fetchval(
            "INSERT INTO health_check_targets (installation_id, repo_full_name, label, base_url) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            9001, "integration-test-org/repo", "Primary", base_url,
        )

        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        from aletheore.evidence import EVIDENCE_VERSION
        from scan_worker.db import get_last_endpoint_health, insert_repo_history

        test_evidence = {
            "aletheore_version": EVIDENCE_VERSION,
            "repository": {
                "api_endpoints": {"endpoints": [{"method": "GET", "path": "/health"}]}
            },
        }
        insert_repo_history(
            TEST_DATABASE_URL, 9001, "integration-test-org/repo", datetime.now(timezone.utc),
            test_evidence,
        )

        monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
        # The one deliberate bypass: real customers can never point a target at
        # 127.0.0.1 (validate_and_pin_https_url would reject it for real, as its
        # own dedicated tests cover) - this test's whole point is a real local
        # server, so only this gate is faked. Everything downstream is real.
        monkeypatch.setattr(
            "scan_worker.jobs.validate_and_pin_https_url", lambda url: (url, "127.0.0.1")
        )
        alerts_sent = []
        monkeypatch.setattr(
            "scan_worker.jobs.send_health_alert", lambda url, msg, **k: alerts_sent.append(msg)
        )

        from scan_worker.jobs import run_health_check_sweep_job

        # Sweep 1: server is up, this is the first-ever check for this
        # endpoint - reachable, recorded, no alert (nothing to compare against).
        run_health_check_sweep_job()

        row = get_last_endpoint_health(
            TEST_DATABASE_URL, 9001, "integration-test-org/repo", "GET", "/health", target_id=target_id
        )
        assert row is not None
        assert row["reachable"] is True
        assert alerts_sent == []

        # Take the server down for real.
        server.shutdown()
        thread.join(timeout=5)

        # Sweep 2: server is down - a reachability flip. The sweep must defer
        # to a real down-retry job instead of alerting immediately.
        run_health_check_sweep_job()

        assert alerts_sent == []
        health_queue = Queue("health", connection=redis_conn)
        registry = ScheduledJobRegistry(queue=health_queue)
        scheduled_ids = registry.get_job_ids()
        assert len(scheduled_ids) == 1
        job = health_queue.fetch_job(scheduled_ids[0])
        assert job.func_name == "scan_worker.jobs.run_health_check_down_retry_job"

        # The main sweep must not have written a "down" row yet - that's the
        # retry chain's job once it confirms, not this sweep's.
        row_after_flip = get_last_endpoint_health(
            TEST_DATABASE_URL, 9001, "integration-test-org/repo", "GET", "/health", target_id=target_id
        )
        assert row_after_flip["reachable"] is True

        # Drive the real retry chain exactly as RQ's own worker (started
        # with_scheduler=True, see health_worker.py) would: the scheduler
        # removes a due job from the registry before handing it to a worker
        # to execute - Job.perform() alone doesn't do that removal, so it
        # has to happen here too or the same stale entry gets replayed
        # forever instead of the chain actually advancing.
        for _ in range(5):
            registry.remove(job)
            job.perform()
            remaining = registry.get_job_ids()
            if not remaining:
                break
            job = health_queue.fetch_job(remaining[0])
        else:
            pytest.fail("down-retry chain never resolved within 5 hops")

        assert len(alerts_sent) == 1
        assert "down" in alerts_sent[0]["text"]
        row_confirmed = get_last_endpoint_health(
            TEST_DATABASE_URL, 9001, "integration-test-org/repo", "GET", "/health", target_id=target_id
        )
        assert row_confirmed["reachable"] is False
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        thread.join(timeout=5)


def _wiki_evidence() -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {"functions": [], "classes": []},
                }
            ],
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "architecture": {"clusters": [{"id": 0, "modules": ["auth/login.py"], "internal_edges": 0}]},
    }


def test_attach_wiki_file_pages_scopes_planned_pages_to_changed_files(monkeypatch):
    from scan_worker import jobs

    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.select_file_page_paths",
        lambda evidence, **k: ["auth/login.py", "auth/tokens.py"],
    )
    captured = {}

    def _fake_generate_file_pages(evidence, writing_adapter, *, paths, subsystem_by_path, fetch_line_count):
        captured["paths"] = paths
        return {p: f"page for {p}" for p in paths}

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_file_pages", _fake_generate_file_pages)

    records = [
        {
            "subsystem_id": "0",
            "name": "Authentication",
            "files": [
                {"path": "auth/login.py", "role": "", "key_symbols": []},
                {
                    "path": "auth/tokens.py",
                    "role": "Issues tokens.",
                    "key_symbols": [],
                    "detail": "prior detail",
                },
            ],
        }
    ]

    result = jobs._attach_wiki_file_pages(
        {}, records, writing_adapter=None, fetch_line_count=None, changed_files=["auth/login.py"]
    )

    assert captured["paths"] == ["auth/login.py"]
    by_path = {f["path"]: f for f in result[0]["files"]}
    assert by_path["auth/login.py"]["detail"] == "page for auth/login.py"
    # Untouched file keeps whatever detail it already carries (spliced from
    # the prior stored record by generate_subsystems) rather than losing it -
    # attach_file_pages only overwrites paths present in `pages`.
    assert by_path["auth/tokens.py"]["detail"] == "prior detail"


def test_attach_wiki_file_pages_regenerates_every_page_when_changed_files_is_none(monkeypatch):
    # changed_files=None is the full-build default - must reproduce today's
    # behavior exactly, no narrowing.
    from scan_worker import jobs

    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.select_file_page_paths",
        lambda evidence, **k: ["auth/login.py", "auth/tokens.py"],
    )
    captured = {}

    def _fake_generate_file_pages(evidence, writing_adapter, *, paths, subsystem_by_path, fetch_line_count):
        captured["paths"] = paths
        return {}

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_file_pages", _fake_generate_file_pages)

    records = [
        {
            "subsystem_id": "0",
            "name": "Authentication",
            "files": [
                {"path": "auth/login.py", "role": "", "key_symbols": []},
                {"path": "auth/tokens.py", "role": "", "key_symbols": []},
            ],
        }
    ]

    jobs._attach_wiki_file_pages({}, records, writing_adapter=None, fetch_line_count=None)

    assert captured["paths"] == ["auth/login.py", "auth/tokens.py"]


def test_maybe_update_live_wiki_skips_for_free_plan(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    called = []
    monkeypatch.setattr(
        "scan_worker.live_wiki.generate_subsystems", lambda *a, **k: called.append(1)
    )

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert called == []


def test_maybe_update_live_wiki_skips_when_no_clusters_affected(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    called = []
    monkeypatch.setattr(
        "scan_worker.live_wiki.generate_subsystems", lambda *a, **k: called.append(1)
    )

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["unrelated/file.py"], "sha1")

    assert called == []


def test_maybe_update_live_wiki_generates_and_stores_for_affected_clusters(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])

    fake_record = {
        "subsystem_id": "0",
        "name": "Authentication",
        "description": "Handles login.",
        "files": [],
        "diagram_mermaid": "flowchart TD",
    }
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems", lambda *a, **k: [fake_record]
    )

    stored = {}
    monkeypatch.setattr(
        "scan_worker.jobs._store_wiki_generation",
        lambda dsn, iid, repo, evidence, records, adapter, commit, **k: stored.update(
            records=records, commit=commit
        ),
    )
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error_message=None: status_calls.append(status),
    )

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert stored["records"] == [fake_record]
    assert stored["commit"] == "sha1"
    assert status_calls == ["ready"]


def test_maybe_update_live_wiki_fetches_and_threads_prior_records_through(monkeypatch):
    # prior_records must be read BEFORE generate_subsystems writes anything -
    # it's what an untouched file's content gets spliced from, so it has to
    # reflect the state as of before this push, not after.
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    stored_prior = [{"subsystem_id": "0", "files": [{"path": "auth/login.py", "role": "Old.", "key_symbols": []}]}]
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: stored_prior)

    captured = {}

    def _fake_generate_subsystems(evidence, naming_adapter, writing_adapter, **kwargs):
        captured["changed_files"] = kwargs.get("changed_files")
        captured["prior_records"] = kwargs.get("prior_records")
        return []

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_subsystems", _fake_generate_subsystems)
    monkeypatch.setattr("scan_worker.jobs._store_wiki_generation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_wiki_build_status", lambda *a, **k: None)

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert captured["changed_files"] == ["auth/login.py"]
    assert captured["prior_records"] == {"0": stored_prior[0]}


def test_maybe_update_live_wiki_records_failure_status_on_exception(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])

    def _boom(*a, **k):
        raise RuntimeError("LLM API unavailable")

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_subsystems", _boom)

    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error_message=None: status_calls.append((status, error_message)),
    )

    # Must not raise - a failed incremental update is a recorded status,
    # not a crash that would take down the rest of run_pr_scan_job.
    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert status_calls == [("failed", "LLM API unavailable")]


def test_maybe_update_live_wiki_skips_llm_call_when_spend_cap_reached(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems", lambda *a, **k: llm_called.append(True)
    )
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error_message=None: status_calls.append((status, error_message)),
    )

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert llm_called == []
    assert status_calls[0][0] == "failed"
    assert "spend cap" in status_calls[0][1]


def test_maybe_update_live_wiki_reserves_spend_atomically_against_concurrent_pushes(monkeypatch):
    # Same regression as test_run_live_wiki_full_build_job_reserves_spend_atomically_against_concurrent_repos,
    # for the incremental-update path: _maybe_update_live_wiki had the
    # identical two-separate-lock-acquisitions shape. Two pushes landing
    # close together for two different repos under the same paid
    # installation used to be able to both pass the cap check before either
    # recorded spend.
    import threading

    from scan_worker.jobs import DEFAULT_LLM_NEXT_CALL_RESERVE_USD, _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])
    monkeypatch.setattr("scan_worker.jobs._store_wiki_generation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._real_line_count_fetcher", lambda *a, **k: (lambda path: None))

    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr(
        "scan_worker.jobs.monthly_cap_for_installation", lambda *a, **k: DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    )

    spend_state = {"total": 0.0}
    state_lock = threading.Lock()
    cap_check_barrier = threading.Barrier(2)

    def _get_llm_spend_this_month(dsn, iid):
        cap_check_barrier.wait(timeout=5)
        with state_lock:
            value = spend_state["total"]
        cap_check_barrier.wait(timeout=5)
        return value

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        with state_lock:
            if spend_state["total"] + reserve_usd <= monthly_cap:
                spend_state["total"] += reserve_usd
                return True
            return False

    def _record_llm_spend(dsn, iid, delta, **k):
        with state_lock:
            spend_state["total"] += delta

    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", _get_llm_spend_this_month)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", _record_llm_spend)
    monkeypatch.setattr(
        "scan_worker.jobs.cost_for_usage", lambda *a, **k: DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    )

    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error_message=None: status_calls.append((repo, status, error_message)),
    )

    def _fake_generate_subsystems(evidence, naming_adapter, writing_adapter, **kwargs):
        writing_adapter.simple_completion("system", "user", cwd=".")
        return [{"subsystem_id": "0", "name": "Auth", "description": "d", "files": []}]

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_subsystems", _fake_generate_subsystems)
    monkeypatch.setattr("scan_worker.jobs._attach_wiki_file_pages", lambda *a, **k: a[1])

    class _FakeWikiAdapter:
        def __init__(self, on_usage=None, before_llm_call=None, **k):
            self._on_usage = on_usage
            self._before_llm_call = before_llm_call

        def simple_completion(self, *a, **k):
            if self._before_llm_call is not None and not self._before_llm_call():
                raise RuntimeError("monthly LLM spend cap would be exceeded")
            if self._on_usage:
                self._on_usage(10, 10)
            return "some subsystem prose"

    monkeypatch.setattr(
        "scan_worker.jobs._live_wiki_naming_adapter",
        lambda on_usage=None, before_llm_call=None: _FakeWikiAdapter(on_usage, before_llm_call),
    )
    monkeypatch.setattr(
        "scan_worker.jobs._live_wiki_update_writing_adapter",
        lambda on_usage=None, before_llm_call=None: _FakeWikiAdapter(on_usage, before_llm_call),
    )

    def _call(repo):
        _maybe_update_live_wiki(1, repo, _wiki_evidence(), ["auth/login.py"], "sha1")

    threads = [
        threading.Thread(target=_call, args=(repo,)) for repo in ("octocat/repo-a", "octocat/repo-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    ready = [repo for repo, status, _ in status_calls if status == "ready"]
    assert len(ready) == 1


def test_run_live_wiki_incremental_update_job_reloads_evidence_and_delegates(monkeypatch):
    """run_live_wiki_incremental_update_job is the new, separately-timed job
    that run_pr_scan_job/run_push_scan_job now enqueue instead of calling
    _maybe_update_live_wiki inline. It doesn't receive evidence directly -
    the calling scan job already persisted it via _insert_history before
    enqueueing this job, so this reloads it from repo_history by the exact
    history_id that scan wrote (not get_latest_evidence's "whatever is
    newest right now" - a second scan for the same repo persisting before
    this job runs would otherwise combine that newer evidence with this
    job's older changed_files/head_sha, applying an incremental update
    against a mismatched revision)."""
    from scan_worker.jobs import run_live_wiki_incremental_update_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    evidence = _wiki_evidence()
    seen_args = {}

    def fake_get_evidence_by_id(dsn, iid, repo, history_id):
        seen_args.update(installation_id=iid, repo_full_name=repo, history_id=history_id)
        return evidence

    monkeypatch.setattr("scan_worker.jobs.get_evidence_by_id", fake_get_evidence_by_id)
    called = {}
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_wiki",
        lambda installation_id, repo_full_name, ev, changed_files, head_sha: called.update(
            installation_id=installation_id, repo_full_name=repo_full_name,
            evidence=ev, changed_files=changed_files, head_sha=head_sha,
        ),
    )

    run_live_wiki_incremental_update_job(
        installation_id=1, repo_full_name="octocat/hello-world",
        changed_files=["auth/login.py"], head_sha="sha1", history_id=99,
    )

    assert seen_args == {"installation_id": 1, "repo_full_name": "octocat/hello-world", "history_id": 99}
    assert called["installation_id"] == 1
    assert called["repo_full_name"] == "octocat/hello-world"
    assert called["evidence"] is evidence
    assert called["changed_files"] == ["auth/login.py"]
    assert called["head_sha"] == "sha1"


def test_run_live_wiki_incremental_update_job_noop_when_no_evidence_yet(monkeypatch):
    from scan_worker.jobs import run_live_wiki_incremental_update_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_evidence_by_id", lambda dsn, iid, repo, history_id: None)
    called = []
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: called.append(True))

    run_live_wiki_incremental_update_job(
        installation_id=1, repo_full_name="octocat/hello-world",
        changed_files=["auth/login.py"], head_sha="sha1", history_id=99,
    )

    assert called == []


def test_run_live_docs_incremental_update_job_reloads_evidence_and_delegates(monkeypatch):
    from scan_worker.jobs import run_live_docs_incremental_update_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    evidence = _wiki_evidence()
    seen_args = {}

    def fake_get_evidence_by_id(dsn, iid, repo, history_id):
        seen_args.update(installation_id=iid, repo_full_name=repo, history_id=history_id)
        return evidence

    monkeypatch.setattr("scan_worker.jobs.get_evidence_by_id", fake_get_evidence_by_id)
    called = {}
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_docs",
        lambda installation_id, repo_full_name, ev, changed_files, head_sha: called.update(
            installation_id=installation_id, repo_full_name=repo_full_name,
            evidence=ev, changed_files=changed_files, head_sha=head_sha,
        ),
    )

    run_live_docs_incremental_update_job(
        installation_id=1, repo_full_name="octocat/hello-world",
        changed_files=["auth/login.py"], head_sha="sha1", history_id=99,
    )

    assert seen_args == {"installation_id": 1, "repo_full_name": "octocat/hello-world", "history_id": 99}
    assert called["installation_id"] == 1
    assert called["repo_full_name"] == "octocat/hello-world"
    assert called["evidence"] is evidence
    assert called["changed_files"] == ["auth/login.py"]
    assert called["head_sha"] == "sha1"


def test_run_live_docs_incremental_update_job_noop_when_no_evidence_yet(monkeypatch):
    from scan_worker.jobs import run_live_docs_incremental_update_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_evidence_by_id", lambda dsn, iid, repo, history_id: None)
    called = []
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_docs", lambda *a, **k: called.append(True))

    run_live_docs_incremental_update_job(
        installation_id=1, repo_full_name="octocat/hello-world",
        changed_files=["auth/login.py"], head_sha="sha1", history_id=99,
    )

    assert called == []


class _FakeScansQueue:
    """Records enqueue() calls instead of touching real Redis - see
    test_run_pr_scan_job_enqueues_live_wiki_and_docs_update_jobs for why
    this replaced monkeypatching _maybe_update_live_wiki directly."""

    def __init__(self):
        self.enqueued = []

    def enqueue(self, func_name, **kwargs):
        self.enqueued.append({"func_name": func_name, **kwargs})


def test_run_pr_scan_job_enqueues_live_wiki_and_docs_update_jobs(bare_repo_with_two_commits, monkeypatch):
    """Regression test for docs/audits history: run_pr_scan_job used to call
    _maybe_update_live_wiki/_maybe_update_live_docs inline, sharing the
    scan job's own 300s job_timeout - real production incidents showed
    AIRview's real LLM calls (with retries) on a large repo pushing total
    time past that budget, and RQ killing the whole job mid-flight
    ("Work-horse terminated unexpectedly"), losing the wiki/docs update
    entirely with no partial result and no signal to the customer. Now
    enqueued as their own jobs with their own, more generous timeout,
    decoupled from the scan job's critical path (which has already posted
    the PR diff comment - its primary deliverable - by this point)."""
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: 42)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"]
    )
    direct_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: direct_calls.append("wiki")
    )
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_docs", lambda *a, **k: direct_calls.append("docs")
    )
    fake_queue = _FakeScansQueue()
    monkeypatch.setattr("scan_worker.jobs._scans_queue", lambda redis_url: fake_queue)

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    # Never called inline - only as separately-enqueued jobs.
    assert direct_calls == []

    wiki_job = next(e for e in fake_queue.enqueued if "live_wiki_incremental" in e["func_name"])
    assert wiki_job["func_name"] == "scan_worker.jobs.run_live_wiki_incremental_update_job"
    assert wiki_job["installation_id"] == 1
    assert wiki_job["repo_full_name"] == "octocat/hello-world"
    assert wiki_job["changed_files"] == ["app.py"]
    assert wiki_job["head_sha"] == head_sha
    # The exact history row this scan persisted, not "whatever's latest" -
    # see get_evidence_by_id's docstring for the mismatched-revision race
    # this closes.
    assert wiki_job["history_id"] == 42
    assert wiki_job["job_timeout"] == LIVE_WIKI_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS
    assert wiki_job["job_timeout"] > 300  # strictly more headroom than the scan job's own budget

    docs_job = next(e for e in fake_queue.enqueued if "live_docs_incremental" in e["func_name"])
    assert docs_job["func_name"] == "scan_worker.jobs.run_live_docs_incremental_update_job"
    assert docs_job["installation_id"] == 1
    assert docs_job["repo_full_name"] == "octocat/hello-world"
    assert docs_job["changed_files"] == ["app.py"]
    assert docs_job["head_sha"] == head_sha
    assert docs_job["history_id"] == 42
    assert docs_job["job_timeout"] == LIVE_DOCS_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS
    assert docs_job["job_timeout"] > 300


def test_run_pr_scan_job_logs_slack_alert_failure_instead_of_swallowing_it(
    bare_repo_with_two_commits, monkeypatch, caplog
):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)

    def _boom(*a, **k):
        raise RuntimeError("Slack API error: invalid_token")

    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", _boom)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: None)

    with caplog.at_level("WARNING", logger="scan_worker.jobs"):
        # Must not raise - a dead/wrong webhook must never take down the
        # rest of the PR scan (the diff comment is already posted by now).
        run_pr_scan_job(
            installation_id=1,
            repo_full_name="octocat/hello-world",
            pr_number=7,
            base_sha=base_sha,
            head_sha=head_sha,
        )

    assert any(
        "alert webhook send failed" in record.message and "octocat/hello-world" in record.message
        for record in caplog.records
    )


def test_run_push_scan_job_enqueues_live_wiki_and_docs_update_jobs(bare_repo_with_two_commits, monkeypatch):
    """See test_run_pr_scan_job_enqueues_live_wiki_and_docs_update_jobs -
    same fix, same reasoning, the push-scan path."""
    from scan_worker.jobs import run_push_scan_job

    bare_path, _base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: 42)
    direct_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: direct_calls.append("wiki")
    )
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_docs", lambda *a, **k: direct_calls.append("docs")
    )
    fake_queue = _FakeScansQueue()
    monkeypatch.setattr("scan_worker.jobs._scans_queue", lambda redis_url: fake_queue)

    run_push_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        head_sha=head_sha,
        changed_files=["app.py"],
    )

    assert direct_calls == []

    wiki_job = next(e for e in fake_queue.enqueued if "live_wiki_incremental" in e["func_name"])
    assert wiki_job["installation_id"] == 1
    assert wiki_job["repo_full_name"] == "octocat/hello-world"
    assert wiki_job["changed_files"] == ["app.py"]
    assert wiki_job["head_sha"] == head_sha
    assert wiki_job["history_id"] == 42
    assert wiki_job["job_timeout"] == LIVE_WIKI_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS

    docs_job = next(e for e in fake_queue.enqueued if "live_docs_incremental" in e["func_name"])
    assert docs_job["installation_id"] == 1
    assert docs_job["repo_full_name"] == "octocat/hello-world"
    assert docs_job["changed_files"] == ["app.py"]
    assert docs_job["head_sha"] == head_sha
    assert docs_job["history_id"] == 42
    assert docs_job["job_timeout"] == LIVE_DOCS_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS


def test_run_push_scan_job_skips_wiki_update_for_free_plan(bare_repo_with_two_commits, monkeypatch):
    from scan_worker.jobs import run_push_scan_job

    bare_path, _base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    fake_queue = _FakeScansQueue()
    monkeypatch.setattr("scan_worker.jobs._scans_queue", lambda redis_url: fake_queue)

    run_push_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        head_sha=head_sha,
        changed_files=["app.py"],
    )

    assert fake_queue.enqueued == []


def test_run_push_scan_job_skips_paid_repo_past_monthly_scan_cap(bare_repo_with_two_commits, monkeypatch):
    from scan_worker.jobs import run_push_scan_job

    bare_path, _base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: False)
    cloned = []
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: cloned.append(True))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")

    run_push_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        head_sha=head_sha,
        changed_files=["app.py"],
    )

    assert cloned == []


def test_run_initial_scan_job_logs_and_reraises_on_inner_failure(monkeypatch, caplog):
    from scan_worker.jobs import run_initial_scan_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_github_api_client", lambda *a, **k: object())
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_default_branch_head_sha",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("default branch unavailable")),
    )

    with caplog.at_level("WARNING", logger="scan_worker.jobs"):
        with pytest.raises(RuntimeError, match="default branch unavailable"):
            run_initial_scan_job(1, "octocat/hello-world")

    assert any("initial scan job failed" in record.message for record in caplog.records)


def test_run_initial_scan_job_skips_silently_for_a_repo_with_no_commits_yet(monkeypatch, caplog):
    from scan_worker.jobs import run_initial_scan_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_github_api_client", lambda *a, **k: object())
    # A genuinely empty repo (no commits) - fetch_default_branch_head_sha
    # returns None for this rather than raising (see test_github_api.py's
    # 409 test); run_initial_scan_job's own docstring already says it's
    # "best-effort and silent on failure" for exactly this kind of case.
    monkeypatch.setattr("scan_worker.jobs.fetch_default_branch_head_sha", lambda *a, **k: None)
    clone_calls = []
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda *a, **k: clone_calls.append(1))

    with caplog.at_level("WARNING", logger="scan_worker.jobs"):
        run_initial_scan_job(1, "octocat/hello-world")

    assert clone_calls == []
    assert not any("initial scan job failed" in record.message for record in caplog.records)


def test_run_push_scan_job_logs_and_reraises_on_scan_failure(bare_repo_with_two_commits, monkeypatch, caplog):
    from scan_worker.jobs import run_push_scan_job

    _bare_path, _base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("clone failed")

    monkeypatch.setattr("scan_worker.jobs._clone_url", _boom)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")

    with caplog.at_level("WARNING", logger="scan_worker.jobs"):
        with pytest.raises(RuntimeError, match="clone failed"):
            run_push_scan_job(
                installation_id=1,
                repo_full_name="octocat/hello-world",
                head_sha=head_sha,
                changed_files=["app.py"],
            )

    assert any("push scan job failed" in record.message for record in caplog.records)


def test_run_pr_scan_job_skips_paid_repo_past_monthly_scan_cap(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: False)
    cloned = []
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: cloned.append(True))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    posted = []
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: posted.append(True))

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert cloned == []
    assert posted == []


def test_run_pr_scan_job_skips_cleanly_when_pr_already_closed(bare_repo_with_two_commits, monkeypatch):
    # Real production failure this closes: a PR merged (squash-merge-and-
    # delete-branch, a completely normal fast workflow) between this job
    # being queued and actually running left head_sha unfetchable by any
    # git checkout - "unable to read tree", not a real scan failure, and
    # not something a retry could ever fix. Checking PR state up front
    # means this is a clean no-op instead of a failed job (a bot comment
    # on an already-merged PR, and an ops "failed_jobs" alert for a PR
    # that already finished successfully).
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_is_open", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    cloned = []
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: cloned.append(True))
    posted = []
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: posted.append(True))

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert cloned == []
    assert posted == []


def test_run_pr_scan_job_free_plan_is_not_subject_to_monthly_scan_cap(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called for free plan")),
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert "Secrets" in posted["body"]


def test_flash_review_job_skips_paid_repo_past_monthly_scan_cap(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: False)
    attempted = []
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: attempted.append(True)
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda *a, **k: {"id": 999000 + len(a)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False
    )
    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert attempted == []
    assert llm_called == []


def test_managed_audit_pr_job_skips_paid_repo_past_monthly_scan_cap(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: False)
    cloned = []
    monkeypatch.setattr("scan_worker.jobs._clone_pr_head", lambda *a, **k: cloned.append(True))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    posted = []
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: posted.append(True))
    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert cloned == []
    assert posted == []


def test_managed_audit_pr_job_posts_failure_comment_and_reraises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    monkeypatch.setattr("scan_worker.jobs.managed_audit_definitely_still_cooling_down", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr(
        "scan_worker.jobs._clone_pr_head",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("clone failed")),
    )
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body: posted.update(body=body),
    )

    from scan_worker.jobs import run_managed_audit_pr_job

    with pytest.raises(RuntimeError, match="clone failed"):
        run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert "couldn't complete this scan" in posted["body"]


def test_run_live_wiki_full_build_job_skips_without_evidence(monkeypatch):
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems", lambda *a, **k: called.append(1)
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert called == []


def test_run_live_wiki_full_build_job_generates_and_stores(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"}
    )
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])

    fake_record = {
        "subsystem_id": "0",
        "name": "Authentication",
        "description": "Handles login.",
        "files": [],
        "diagram_mermaid": "flowchart TD",
    }
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems", lambda *a, **k: [fake_record]
    )

    stored = {}
    monkeypatch.setattr(
        "scan_worker.jobs._store_wiki_generation",
        lambda dsn, iid, repo, evidence, records, adapter, commit, **k: stored.update(
            records=records, commit=commit
        ),
    )
    build_status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error=None: build_status_calls.append((status, error)),
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert stored["records"] == [fake_record]
    assert stored["commit"] is None
    assert build_status_calls == [("ready", None)]


def test_run_live_wiki_full_build_job_records_failed_status_on_error(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])

    def _raise(*a, **k):
        raise RuntimeError("model provider unavailable")

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_subsystems", _raise)
    build_status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error=None: build_status_calls.append((status, error)),
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert build_status_calls == [("failed", "model provider unavailable")]


def test_run_live_wiki_full_build_job_skips_llm_call_when_spend_cap_reached(monkeypatch):
    # H-4: AIRview/Docs builds had no dollar spend cap at all, unlike
    # managed audits and flash review - this is the same gate those
    # already had, now closing that gap.
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    llm_called = []
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems", lambda *a, **k: llm_called.append(True)
    )
    build_status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error=None: build_status_calls.append((status, error)),
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert llm_called == []
    assert build_status_calls[0][0] == "failed"
    assert "spend cap" in build_status_calls[0][1]


def test_run_live_wiki_full_build_job_reserves_spend_atomically_against_concurrent_repos(monkeypatch):
    # Regression test for a check-then-act race: run_live_wiki_full_build_job
    # used to check the cap and record spend under two SEPARATE
    # installation_spend_lock acquisitions, with the real (potentially
    # many-call) generate_subsystems/_attach_wiki_file_pages work happening
    # fully unlocked in between - so two full builds for different repos
    # under the SAME paid installation, landing close together (e.g. both
    # due for the 48h catch-up sweep at the same tick), could each pass the
    # cap check before either had recorded anything, both proceeding. The
    # double-barrier below forces both threads' cap-check reads to land at
    # the same instant - the exact window the old two-lock shape left open -
    # so this only passes if the real gate is _IncrementalSpendBudget's
    # can_start_next_call(), reserving atomically per call rather than
    # reading a value that can go stale before it's acted on.
    import threading

    from scan_worker.jobs import DEFAULT_LLM_NEXT_CALL_RESERVE_USD, run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])
    monkeypatch.setattr("scan_worker.jobs._store_wiki_generation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._real_line_count_fetcher", lambda *a, **k: (lambda path: None))

    # Only one reservation of DEFAULT_LLM_NEXT_CALL_RESERVE_USD fits under
    # this cap - the second concurrent repo's build must be rejected.
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr(
        "scan_worker.jobs.monthly_cap_for_installation", lambda *a, **k: DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    )

    # In-memory stand-in for the real atomic llm_spend row, shared across
    # both threads the same way concurrent transactions against the same DB
    # row would be.
    spend_state = {"total": 0.0}
    state_lock = threading.Lock()
    cap_check_barrier = threading.Barrier(2)

    def _get_llm_spend_this_month(dsn, iid):
        # Two waits on the same (cyclic) barrier: the first forces both
        # threads to arrive together, the second forces both to finish
        # reading before either can return and proceed - see the identical
        # technique in test_fix_suggestion_attachment_reserves_spend_atomically_against_concurrent_calls.
        cap_check_barrier.wait(timeout=5)
        with state_lock:
            value = spend_state["total"]
        cap_check_barrier.wait(timeout=5)
        return value

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        with state_lock:
            if spend_state["total"] + reserve_usd <= monthly_cap:
                spend_state["total"] += reserve_usd
                return True
            return False

    def _record_llm_spend(dsn, iid, delta, **k):
        with state_lock:
            spend_state["total"] += delta

    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", _get_llm_spend_this_month)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", _record_llm_spend)
    # Real cost equal to the flat reservation, so record_usage's true-up
    # delta is exactly 0 (a no-op) - isolates this test to the reservation
    # race itself, same reasoning as the fix-suggestion regression test.
    monkeypatch.setattr(
        "scan_worker.jobs.cost_for_usage", lambda *a, **k: DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    )

    build_status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error=None: build_status_calls.append((repo, status, error)),
    )

    def _fake_generate_subsystems(evidence, naming_adapter, writing_adapter, **kwargs):
        writing_adapter.simple_completion("system", "user", cwd=".")
        return [{"subsystem_id": "0", "name": "Auth", "description": "d", "files": []}]

    monkeypatch.setattr("scan_worker.jobs.live_wiki.generate_subsystems", _fake_generate_subsystems)
    monkeypatch.setattr("scan_worker.jobs._attach_wiki_file_pages", lambda *a, **k: a[1])

    class _FakeWikiAdapter:
        def __init__(self, on_usage=None, before_llm_call=None, **k):
            self._on_usage = on_usage
            self._before_llm_call = before_llm_call

        def simple_completion(self, *a, **k):
            if self._before_llm_call is not None and not self._before_llm_call():
                raise RuntimeError("monthly LLM spend cap would be exceeded")
            if self._on_usage:
                self._on_usage(10, 10)
            return "some subsystem prose"

    monkeypatch.setattr(
        "scan_worker.jobs._live_wiki_naming_adapter",
        lambda on_usage=None, before_llm_call=None: _FakeWikiAdapter(on_usage, before_llm_call),
    )
    monkeypatch.setattr(
        "scan_worker.jobs._live_wiki_full_build_writing_adapter",
        lambda on_usage=None, before_llm_call=None: _FakeWikiAdapter(on_usage, before_llm_call),
    )

    threads = [
        threading.Thread(target=run_live_wiki_full_build_job, args=(1, repo))
        for repo in ("octocat/repo-a", "octocat/repo-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    ready = [repo for repo, status, _ in build_status_calls if status == "ready"]
    assert len(ready) == 1


def test_real_line_count_fetcher_returns_none_when_token_setup_fails(monkeypatch):
    from scan_worker.jobs import _real_line_count_fetcher

    monkeypatch.setattr(
        "scan_worker.jobs.generate_app_jwt",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key configured")),
    )

    assert _real_line_count_fetcher(1, "octocat/hello-world", None) is None


def test_real_line_count_fetcher_returns_real_line_count(monkeypatch):
    from scan_worker.jobs import _real_line_count_fetcher

    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_file_content",
        lambda client, token, repo, path, ref: "one\ntwo\nthree" if path == "app.py" else None,
    )

    fetch_line_count = _real_line_count_fetcher(1, "octocat/hello-world", "sha1")

    assert fetch_line_count is not None
    assert fetch_line_count("app.py") == 3
    assert fetch_line_count("missing.py") is None


def test_run_live_wiki_full_build_job_passes_fetch_line_count_through(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])
    sentinel = lambda path: 42  # noqa: E731
    monkeypatch.setattr("scan_worker.jobs._real_line_count_fetcher", lambda *a, **k: sentinel)

    captured_subsystems = {}
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems",
        lambda *a, **k: captured_subsystems.update(k) or [],
    )
    captured_store = {}
    monkeypatch.setattr(
        "scan_worker.jobs._store_wiki_generation",
        lambda *a, **k: captured_store.update(k),
    )
    monkeypatch.setattr("scan_worker.jobs.set_wiki_build_status", lambda *a, **k: None)

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert captured_subsystems["fetch_line_count"] is sentinel
    assert captured_store["fetch_line_count"] is sentinel


def _multi_cluster_wiki_evidence(cluster_ids: list[int]) -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": f"pkg{cid}/mod.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {"functions": [], "classes": []},
                }
                for cid in cluster_ids
            ],
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "architecture": {
            "clusters": [
                {"id": cid, "modules": [f"pkg{cid}/mod.py"], "internal_edges": 0}
                for cid in cluster_ids
            ]
        },
    }


def test_clusters_with_uncovered_wiki_work_filters_covered_clusters():
    from scan_worker.jobs import _clusters_with_uncovered_wiki_work

    evidence = _multi_cluster_wiki_evidence([0, 1, 2])

    result = _clusters_with_uncovered_wiki_work(evidence, covered_cluster_ids={"0"}, limit=10)

    assert result == {1, 2}


def test_clusters_with_uncovered_wiki_work_respects_limit():
    from scan_worker.jobs import _clusters_with_uncovered_wiki_work

    evidence = _multi_cluster_wiki_evidence([0, 1, 2, 3, 4])

    result = _clusters_with_uncovered_wiki_work(evidence, covered_cluster_ids=set(), limit=2)

    assert len(result) == 2


def test_clusters_with_uncovered_wiki_work_empty_when_everything_covered():
    from scan_worker.jobs import _clusters_with_uncovered_wiki_work

    evidence = _multi_cluster_wiki_evidence([0, 1])

    result = _clusters_with_uncovered_wiki_work(evidence, covered_cluster_ids={"0", "1"}, limit=10)

    assert result == set()


def test_run_live_wiki_full_build_job_only_requests_uncovered_clusters(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    evidence = _multi_cluster_wiki_evidence([0, 1, 2])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: evidence)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [{"subsystem_id": "0"}]
    )

    captured = {}
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems",
        lambda *a, **k: captured.update(k) or [],
    )
    monkeypatch.setattr("scan_worker.jobs._store_wiki_generation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_wiki_build_status", lambda *a, **k: None)

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert captured["cluster_ids"] == {1, 2}


def test_run_live_wiki_full_build_job_is_noop_when_every_cluster_already_covered(monkeypatch):
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    evidence = _multi_cluster_wiki_evidence([0, 1])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: evidence)
    monkeypatch.setattr(
        "scan_worker.jobs.list_wiki_subsystems",
        lambda *a, **k: [{"subsystem_id": "0"}, {"subsystem_id": "1"}],
    )
    generate_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems",
        lambda *a, **k: generate_calls.append(1),
    )
    build_status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_wiki_build_status",
        lambda dsn, iid, repo, status, error=None: build_status_calls.append((status, error)),
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert generate_calls == []
    assert build_status_calls == [("ready", None)]


def test_live_wiki_catchup_sweep_job_rebuilds_each_due_repo(monkeypatch):
    from scan_worker.jobs import run_live_wiki_catchup_sweep_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_paid_repos_due_for_wiki_catchup",
        lambda *a, **k: [(1, "octocat/hello-world"), (2, "octocat/other-repo")],
    )
    built = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_live_wiki_full_build_job",
        lambda iid, repo: built.append((iid, repo)),
    )
    swept = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_wiki_catchup_swept",
        lambda dsn, iid, repo: swept.append((iid, repo)),
    )

    run_live_wiki_catchup_sweep_job()

    assert built == [(1, "octocat/hello-world"), (2, "octocat/other-repo")]
    assert swept == built


def test_live_wiki_catchup_sweep_job_survives_one_repo_failing(monkeypatch):
    from scan_worker.jobs import run_live_wiki_catchup_sweep_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_paid_repos_due_for_wiki_catchup",
        lambda *a, **k: [(1, "octocat/broken-repo"), (2, "octocat/fine-repo")],
    )

    def _maybe_raise(iid, repo):
        if repo == "octocat/broken-repo":
            raise RuntimeError("boom")

    monkeypatch.setattr("scan_worker.jobs.run_live_wiki_full_build_job", _maybe_raise)
    swept = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_wiki_catchup_swept",
        lambda dsn, iid, repo: swept.append((iid, repo)),
    )

    run_live_wiki_catchup_sweep_job()

    # Both repos recorded as swept - including the one that failed, so it
    # isn't retried every tick for the same repeated failure - and the
    # second repo's build still happened despite the first one raising.
    assert swept == [(1, "octocat/broken-repo"), (2, "octocat/fine-repo")]


def test_maybe_update_live_wiki_passes_fetch_line_count_through(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_wiki_subsystems", lambda *a, **k: [])
    sentinel = lambda path: 42  # noqa: E731
    monkeypatch.setattr("scan_worker.jobs._real_line_count_fetcher", lambda *a, **k: sentinel)

    captured_subsystems = {}
    monkeypatch.setattr(
        "scan_worker.jobs.live_wiki.generate_subsystems",
        lambda *a, **k: captured_subsystems.update(k) or [],
    )
    captured_store = {}
    monkeypatch.setattr(
        "scan_worker.jobs._store_wiki_generation",
        lambda *a, **k: captured_store.update(k),
    )
    monkeypatch.setattr("scan_worker.jobs.set_wiki_build_status", lambda *a, **k: None)

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert captured_subsystems["fetch_line_count"] is sentinel
    assert captured_store["fetch_line_count"] is sentinel


def test_run_live_wiki_full_build_for_installation_job_enqueues_per_repo(monkeypatch):
    from unittest.mock import MagicMock

    from scan_worker.jobs import run_live_wiki_full_build_for_installation_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_repos_for_installation",
        lambda *a, **k: ["octocat/repo1", "octocat/repo2"],
    )
    fake_queue = MagicMock()
    monkeypatch.setattr("scan_worker.jobs._scans_queue", lambda redis_url: fake_queue)

    run_live_wiki_full_build_for_installation_job(1)

    assert fake_queue.enqueue.call_count == 2
    repo_names = {call.kwargs["repo_full_name"] for call in fake_queue.enqueue.call_args_list}
    assert repo_names == {"octocat/repo1", "octocat/repo2"}


def test_full_build_writing_adapter_always_uses_deepseek_flash_even_with_openai_key_configured(monkeypatch):
    # AIRview's own comprehension benchmark (aletheore-benchmarks,
    # AIRVIEW_GAP.md, re-measured 2026-08-22) found deepseek-v4-flash tied
    # RepoWise here while gpt-5.6-luna lost decisively - see
    # writing_adapter_for_airview's docstring. No longer plan-dependent
    # (was Luna falling back to deepseek-v4-pro per plan).
    from scan_worker.jobs import _live_wiki_full_build_writing_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    adapter = _live_wiki_full_build_writing_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.FLASH_MODEL


def test_full_build_writing_adapter_uses_deepseek_flash_without_openai_key_too(monkeypatch):
    from scan_worker.jobs import _live_wiki_full_build_writing_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)

    adapter = _live_wiki_full_build_writing_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.FLASH_MODEL


def _docs_module(path="a.py", functions=None, classes=None) -> dict:
    return {
        "path": path,
        "language": "python",
        "symbols": {"functions": functions or [], "classes": classes or []},
    }


def _docs_evidence(modules: list[dict]) -> dict:
    return {"repository": {"modules": modules}}


def test_module_has_uncovered_docs_work_true_when_symbol_never_covered():
    from scan_worker.jobs import _module_has_uncovered_docs_work

    module = _docs_module(functions=[{"name": "foo", "is_public": True, "docstring": None}])

    assert _module_has_uncovered_docs_work(module, already_covered_names=set()) is True


def test_module_has_uncovered_docs_work_false_when_every_symbol_already_covered():
    from scan_worker.jobs import _module_has_uncovered_docs_work

    module = _docs_module(functions=[{"name": "foo", "is_public": True, "docstring": None}])

    assert _module_has_uncovered_docs_work(module, already_covered_names={"foo"}) is False


def test_module_has_uncovered_docs_work_false_when_nothing_needs_work_at_all():
    from scan_worker.jobs import _module_has_uncovered_docs_work

    # Private, or already has a real developer-written docstring - neither
    # generate nor polish mode would ever ask about this one.
    module = _docs_module(functions=[{"name": "_private", "is_public": False, "docstring": None}])

    assert _module_has_uncovered_docs_work(module, already_covered_names=set()) is False


def test_modules_with_uncovered_docs_work_filters_and_caps():
    from scan_worker.jobs import _modules_with_uncovered_docs_work

    done = _docs_module("done.py", functions=[{"name": "f", "is_public": True, "docstring": None}])
    partial = _docs_module(
        "partial.py",
        functions=[
            {"name": "covered", "is_public": True, "docstring": None},
            {"name": "new", "is_public": True, "docstring": None},
        ],
    )
    untouched = _docs_module("untouched.py", functions=[{"name": "g", "is_public": True, "docstring": None}])
    covered_by_module = {"done.py": {"f"}, "partial.py": {"covered"}}

    result = _modules_with_uncovered_docs_work(
        [done, partial, untouched], covered_by_module, limit=10
    )

    paths = [m["path"] for m in result]
    assert "done.py" not in paths
    assert set(paths) == {"partial.py", "untouched.py"}
    # untouched.py has zero existing coverage, partial.py has some -
    # fully-untouched files come first so a capped run can't get crowded
    # out by files that are already mostly done.
    assert paths[0] == "untouched.py"


def test_modules_with_uncovered_docs_work_respects_limit():
    from scan_worker.jobs import _modules_with_uncovered_docs_work

    modules = [
        _docs_module(f"m{i}.py", functions=[{"name": "f", "is_public": True, "docstring": None}])
        for i in range(5)
    ]

    result = _modules_with_uncovered_docs_work(modules, covered_by_module={}, limit=2)

    assert len(result) == 2


def test_store_docs_generation_skips_llm_call_for_an_unchanged_symbol_but_keeps_its_row(monkeypatch):
    # "add" already has a stored description whose hash matches its current
    # source - unchanged, so no LLM call for it. "sub" has no stored hash
    # (new), so it does need one. The unchanged symbol's existing row must
    # survive the module's prune-stale-symbols step, not get deleted just
    # because this run's LLM response never mentioned it.
    from unittest.mock import MagicMock

    from scan_worker.jobs import _store_docs_generation_for_module
    from scan_worker.live_docs import _content_hash, _symbol_snippet

    source_lines = [
        "def add(a, b):", "    return a + b",
        "def sub(a, b):", "    return a - b",
    ]
    add_symbol = {
        "name": "add", "start_line": 1, "end_line": 2, "params": "(a, b)",
        "docstring": None, "is_public": True,
    }
    sub_symbol = {
        "name": "sub", "start_line": 3, "end_line": 4, "params": "(a, b)",
        "docstring": None, "is_public": True,
    }
    module = _docs_module("a.py", functions=[add_symbol, sub_symbol])
    add_hash = _content_hash(_symbol_snippet(source_lines, add_symbol))

    monkeypatch.setattr(
        "scan_worker.jobs.get_docs_symbol_hashes", lambda *a, **k: {"add": add_hash}
    )
    upserted = []
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_docs_symbol",
        lambda dsn, iid, repo, path, name, desc, mode, commit, content_hash: upserted.append(name),
    )
    pruned_keep_lists = []
    monkeypatch.setattr(
        "scan_worker.jobs.delete_docs_symbols_not_in",
        lambda dsn, iid, repo, path, keep: pruned_keep_lists.append(set(keep)),
    )

    adapter = MagicMock()
    adapter.simple_completion.return_value = json.dumps({"sub": {"description": "Subtracts b from a."}})

    _store_docs_generation_for_module(
        "postgresql://unused", 1, "octocat/hello-world", module, adapter, source_lines, "sha123",
    )

    # Only "sub" triggered an LLM call and a write - "add" was skipped.
    assert adapter.simple_completion.call_count == 1
    sent_items = json.loads(adapter.simple_completion.call_args[0][1])
    assert {item["name"] for item in sent_items} == {"sub"}
    assert upserted == ["sub"]
    # But "add" is still in the keep-list, so its existing row isn't pruned.
    assert pruned_keep_lists == [{"add", "sub"}]


def test_run_live_docs_full_build_job_skips_without_evidence(monkeypatch):
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: None)
    called = []
    monkeypatch.setattr("scan_worker.jobs._github_client_and_token", lambda *a, **k: called.append(1))

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert called == []


def test_run_live_docs_full_build_job_skips_llm_setup_when_nothing_new(monkeypatch):
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    module = _docs_module(functions=[{"name": "f", "is_public": True, "docstring": None}])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs.list_docs_symbols",
        lambda *a, **k: [{"module_path": "a.py", "symbol_name": "f"}],
    )
    client_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: client_calls.append(1)
    )
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert client_calls == []  # no GitHub/LLM setup for zero real work
    assert status_calls == [("ready", None)]


def test_run_live_docs_full_build_job_excludes_test_files_from_candidate_modules(monkeypatch):
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    # A test function is module-level and unprefixed, so is_public sees it
    # as ordinary public API - dogfooding-confirmed real symptom: test_*.py
    # functions were showing up as "generated" Docs entries. Only a test
    # module exists here, so if it isn't excluded there's real work to do.
    module = _docs_module(
        "tests/test_a.py", functions=[{"name": "test_f_does_the_thing", "is_public": True, "docstring": None}]
    )
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])
    client_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: client_calls.append(1)
    )
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert client_calls == []  # no GitHub/LLM setup - a test file is not real work
    assert status_calls == [("ready", None)]


def test_run_live_docs_full_build_job_skips_llm_call_when_spend_cap_reached(monkeypatch):
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    module = _docs_module(functions=[{"name": "f", "is_public": True, "docstring": None}])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    adapter_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._live_docs_full_build_writing_adapter",
        lambda plan, on_usage=None: adapter_calls.append(True),
    )
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert adapter_calls == []
    assert status_calls[0][0] == "failed"
    assert "spend cap" in status_calls[0][1]


def test_run_live_docs_full_build_job_survives_one_module_failing(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    good = _docs_module("good.py", functions=[{"name": "f", "is_public": True, "docstring": None}])
    bad = _docs_module("bad.py", functions=[{"name": "g", "is_public": True, "docstring": None}])
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([bad, good])
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr(
        "scan_worker.jobs._live_docs_full_build_writing_adapter", lambda plan, on_usage=None: object()
    )

    def fake_fetch(client, token, repo, path, ref):
        return "source" if path == "good.py" else "source"

    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", fake_fetch)

    stored_for = []

    def fake_store(dsn, iid, repo, module, adapter, source_lines, commit):
        if module["path"] == "bad.py":
            raise RuntimeError("model provider unavailable")
        stored_for.append(module["path"])

    monkeypatch.setattr("scan_worker.jobs._store_docs_generation_for_module", fake_store)
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)

    run_live_docs_full_build_job(1, "octocat/hello-world")

    # good.py's progress survives bad.py's failure - not discarded because
    # a later (or earlier, depending on iteration order) module failed.
    assert stored_for == ["good.py"]
    assert status_calls[0][0] == "ready"
    assert "1/2 files processed" in status_calls[0][1]
    assert "model provider unavailable" in status_calls[0][1]


def test_run_live_docs_full_build_job_stops_midway_at_remaining_spend_budget(monkeypatch):
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    modules = [
        _docs_module(f"m{i}.py", functions=[{"name": f"f{i}", "is_public": True, "docstring": None}])
        for i in range(3)
    ]
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence(modules))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.monthly_cap_for_installation", lambda *a, **k: 0.0012)
    monkeypatch.setattr("scan_worker.jobs.cost_for_usage", lambda *a, **k: 0.0006)
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: "source")

    class FakeAdapter:
        def __init__(self, on_usage):
            self.on_usage = on_usage

    monkeypatch.setattr(
        "scan_worker.jobs._live_docs_full_build_writing_adapter",
        lambda plan, on_usage=None: FakeAdapter(on_usage),
    )
    stored_for = []

    def fake_store(dsn, iid, repo, module, adapter, source_lines, commit):
        stored_for.append(module["path"])
        adapter.on_usage(1, 1)

    monkeypatch.setattr("scan_worker.jobs._store_docs_generation_for_module", fake_store)
    # In-memory stand-in for the real atomic reserve_llm_spend/record_llm_spend
    # pair - see test_managed_audit_api_job_records_each_call_and_exposes_budget_stop
    # for the full explanation of the shared running-total state and why the
    # true-up delta (cost - next_call_reserve_usd) is negative here.
    spend_state = {"total": 0.0}
    recorded_deltas = []

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        if spend_state["total"] + reserve_usd <= monthly_cap:
            spend_state["total"] += reserve_usd
            return True
        return False

    def _record_llm_spend(dsn, iid, delta, **k):
        spend_state["total"] += delta
        recorded_deltas.append(delta)

    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", _record_llm_spend)
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert stored_for == ["m0.py"]
    assert recorded_deltas == [pytest.approx(-0.0004)]
    assert status_calls[0][0] == "ready"
    assert "1/3 files processed" in status_calls[0][1]
    assert "spend cap" in status_calls[0][1]


def test_run_live_docs_full_build_job_reports_failed_when_every_module_fails(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    module = _docs_module(functions=[{"name": "f", "is_public": True, "docstring": None}])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr(
        "scan_worker.jobs._live_docs_full_build_writing_adapter", lambda plan, on_usage=None: object()
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: "source")

    def _raise(*a, **k):
        raise RuntimeError("model provider unavailable")

    monkeypatch.setattr("scan_worker.jobs._store_docs_generation_for_module", _raise)
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert status_calls == [("failed", "model provider unavailable")]


def test_run_live_docs_full_build_job_reports_failed_when_every_fetch_returns_none(monkeypatch):
    # Regression: fetch_file_content returning None (a 404, or a malformed
    # content response) is a real failure, not "nothing to do" - but
    # _run_docs_build_for_modules used to `continue` without recording it
    # as last_error. If every module in the batch hit this (e.g. GitHub's
    # Contents API lagging right after the push that triggered this job),
    # succeeded stayed 0 and last_error stayed None, so the caller's
    # `succeeded == 0 and last_error is not None` failed-status check never
    # fired - a build that did nothing got reported "ready" with no detail.
    # Same shape of bug as #405 (free-tier Flash Review claiming a diff was
    # clean when it never ran).
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import run_live_docs_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    module = _docs_module(functions=[{"name": "f", "is_public": True, "docstring": None}])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr(
        "scan_worker.jobs._live_docs_full_build_writing_adapter", lambda plan, on_usage=None: object()
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: None)
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )

    run_live_docs_full_build_job(1, "octocat/hello-world")

    assert status_calls[0][0] == "failed"
    assert status_calls[0][1] is not None


def test_maybe_update_live_docs_skips_llm_call_when_spend_cap_reached(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_docs

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)

    adapter_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._live_docs_update_writing_adapter",
        lambda on_usage=None: adapter_calls.append(True),
    )
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )

    evidence = _docs_evidence([_docs_module("good.py")])

    _maybe_update_live_docs(1, "octocat/hello-world", evidence, ["good.py"], "sha1")

    assert adapter_calls == []
    assert status_calls[0][0] == "failed"
    assert "spend cap" in status_calls[0][1]


def test_maybe_update_live_docs_survives_one_module_failing(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import _maybe_update_live_docs

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs._live_docs_update_writing_adapter", lambda on_usage=None: object())
    monkeypatch.setattr("scan_worker.jobs.fetch_file_content", lambda *a, **k: "source")

    def fake_store(dsn, iid, repo, module, adapter, source_lines, commit):
        if module["path"] == "bad.py":
            raise RuntimeError("rate limited")

    monkeypatch.setattr("scan_worker.jobs._store_docs_generation_for_module", fake_store)
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)

    good = _docs_module("good.py")
    bad = _docs_module("bad.py")
    evidence = _docs_evidence([good, bad])

    _maybe_update_live_docs(1, "octocat/hello-world", evidence, ["good.py", "bad.py"], "sha1")

    assert status_calls[0][0] == "ready"
    assert "1/2 files processed" in status_calls[0][1]
    assert "rate limited" in status_calls[0][1]


def test_maybe_update_live_docs_excludes_test_files_from_changed_modules(monkeypatch):
    _patch_no_spend_cap(monkeypatch)
    from scan_worker.jobs import _maybe_update_live_docs

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs._live_docs_update_writing_adapter", lambda on_usage=None: object())

    fetched_for = []
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_file_content",
        lambda client, token, repo, path, ref: fetched_for.append(path) or "source",
    )
    monkeypatch.setattr("scan_worker.jobs._store_docs_generation_for_module", lambda *a, **k: None)
    status_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_docs_build_status",
        lambda dsn, iid, repo, status, error=None: status_calls.append((status, error)),
    )
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)

    good = _docs_module("good.py")
    test_module = _docs_module("tests/test_a.py")
    evidence = _docs_evidence([good, test_module])

    _maybe_update_live_docs(1, "octocat/hello-world", evidence, ["good.py", "tests/test_a.py"], "sha1")

    # Only the real source file was ever fetched - the changed test file
    # never reached the LLM at all, same as a full build's candidate filter.
    assert fetched_for == ["good.py"]
    assert status_calls == [("ready", None)]


def test_maybe_sync_docs_to_repo_noop_when_settings_missing(monkeypatch):
    from scan_worker.jobs import _maybe_sync_docs_to_repo

    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: None)
    client_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: client_calls.append(1)
    )

    _maybe_sync_docs_to_repo("dsn", 1, "octocat/hello-world")

    assert client_calls == []  # never even checks GitHub auth when not opted in


def test_maybe_sync_docs_to_repo_noop_when_disabled(monkeypatch):
    from scan_worker.jobs import _maybe_sync_docs_to_repo

    monkeypatch.setattr(
        "scan_worker.jobs.get_docs_repo_commit_settings",
        lambda *a, **k: {"enabled": False, "last_content_hash": None, "pr_number": None},
    )
    client_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: client_calls.append(1)
    )

    _maybe_sync_docs_to_repo("dsn", 1, "octocat/hello-world")

    assert client_calls == []


def test_maybe_sync_docs_to_repo_pushes_and_records_when_enabled(monkeypatch):
    from scan_worker.jobs import _maybe_sync_docs_to_repo

    settings = {"enabled": True, "last_content_hash": None, "pr_number": None}
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: settings)
    monkeypatch.setattr("scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok"))
    module = _docs_module(functions=[{
        "name": "f", "is_public": True, "docstring": "Does a thing.", "start_line": 1, "end_line": 2,
    }])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])

    sync_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.sync_docs_to_repo",
        lambda client, token, repo, modules, s, bot_login: sync_calls.append((repo, modules, s, bot_login))
        or ("hash123", 7),
    )
    record_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_docs_repo_commit",
        lambda dsn, iid, repo, content_hash, pr_number: record_calls.append((iid, repo, content_hash, pr_number)),
    )

    _maybe_sync_docs_to_repo("dsn", 1, "octocat/hello-world")

    assert len(sync_calls) == 1
    repo, modules, s, bot_login = sync_calls[0]
    assert repo == "octocat/hello-world"
    assert "a.py" in modules
    assert s is settings
    # bot_login gates the force-push ownership check in ensure_branch_at -
    # must be derived from our own app slug, not left for the caller to guess.
    assert bot_login == "aletheore[bot]"
    assert record_calls == [(1, "octocat/hello-world", "hash123", 7)]


def test_maybe_sync_docs_to_repo_swallows_github_api_errors(monkeypatch):
    from scan_worker.jobs import _maybe_sync_docs_to_repo

    settings = {"enabled": True, "last_content_hash": None, "pr_number": None}
    monkeypatch.setattr("scan_worker.jobs.get_docs_repo_commit_settings", lambda *a, **k: settings)
    monkeypatch.setattr("scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok"))
    module = _docs_module(functions=[{"name": "f", "is_public": True, "docstring": "Does a thing."}])
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _docs_evidence([module]))
    monkeypatch.setattr("scan_worker.jobs.list_docs_symbols", lambda *a, **k: [])

    def _raise(*a, **k):
        raise RuntimeError("403 missing contents:write permission")

    monkeypatch.setattr("scan_worker.jobs.sync_docs_to_repo", _raise)

    # Should not raise - a repo-commit failure must not fail the Docs build job.
    _maybe_sync_docs_to_repo("dsn", 1, "octocat/hello-world")


def test_fix_suggestion_attachment_reserves_spend_atomically_against_concurrent_calls(monkeypatch):
    # Regression test for a check-then-act race: _fix_suggestion_attachment
    # used to check the cap and record spend under two SEPARATE
    # installation_spend_lock acquisitions, with the real GitHub fetch and
    # LLM call happening fully unlocked in between - so two concurrent
    # runtime events for the same installation (this path is reachable up
    # to RUNTIME_EVENT_RATE_LIMIT times/hour via POST /v1/runtime-events)
    # could each pass the cap check before either had recorded anything,
    # both proceeding and overshooting the cap. The barrier below forces
    # both threads to complete their cap-check read at the same instant -
    # the exact window the old two-lock shape left open - so this only
    # passes if the real gate is _IncrementalSpendBudget's
    # can_start_next_call(), which reserves atomically in one statement
    # rather than reading a value that can go stale before it's acted on.
    import threading

    from scan_worker.jobs import DEFAULT_LLM_NEXT_CALL_RESERVE_USD, _fix_suggestion_attachment

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "database_url": "postgresql://unused",
                "github_app_id": "1",
                "github_app_private_key": "fake-key",
            },
        )(),
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._token_sync", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.get_github_api_client", lambda: object())
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_file_content", lambda *a, **k: "def handler():\n    pass\n"
    )
    monkeypatch.setattr("scan_worker.jobs.model_for_plan", lambda *a, **k: "gpt-5.6-luna")

    # Only one reservation of DEFAULT_LLM_NEXT_CALL_RESERVE_USD fits under
    # this cap - the second concurrent call must be rejected.
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr(
        "scan_worker.jobs.monthly_cap_for_installation", lambda *a, **k: DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    )

    # In-memory stand-in for the real atomic llm_spend row, sharing running-
    # total state the same way concurrent transactions against the same DB
    # row would - reserve_llm_spend's own lock models the atomicity a real
    # UPSERT gets from Postgres row-level locking.
    spend_state = {"total": 0.0}
    state_lock = threading.Lock()
    cap_check_barrier = threading.Barrier(2)

    def _get_llm_spend_this_month(dsn, iid):
        # Two waits on the same (cyclic) barrier: the first forces both
        # threads to arrive together, the second forces both to finish
        # reading before either can return and proceed - so neither thread
        # can complete a full check-then-record cycle before the other has
        # even done its read. That's the exact check-then-act window the
        # old two-lock shape left open; without it, GIL scheduling alone
        # tends to let one thread race through check-unlocked_work-record
        # before the other's read is even attempted, hiding the bug.
        cap_check_barrier.wait(timeout=5)
        with state_lock:
            value = spend_state["total"]
        cap_check_barrier.wait(timeout=5)
        return value

    def _reserve_llm_spend(dsn, iid, reserve_usd, monthly_cap):
        with state_lock:
            if spend_state["total"] + reserve_usd <= monthly_cap:
                spend_state["total"] += reserve_usd
                return True
            return False

    def _record_llm_spend(dsn, iid, delta, **k):
        with state_lock:
            spend_state["total"] += delta

    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", _get_llm_spend_this_month)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", _reserve_llm_spend)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", _record_llm_spend)
    # Real cost equal to the flat reservation, so record_usage's true-up
    # delta is exactly 0 (a no-op) - isolates this test to the reservation
    # race itself, instead of a coincidental true-up masking it.
    monkeypatch.setattr(
        "scan_worker.jobs.cost_for_usage", lambda *a, **k: DEFAULT_LLM_NEXT_CALL_RESERVE_USD
    )

    class _FakeAdapter:
        def __init__(self, on_usage):
            self._on_usage = on_usage

        def simple_completion(self, *a, **k):
            if self._on_usage:
                self._on_usage(10, 10)
            return "Wrap the call in a try/except and log the failure."

    monkeypatch.setattr(
        "scan_worker.jobs._health_fix_suggestion_adapter",
        lambda on_usage=None: _FakeAdapter(on_usage),
    )

    results: list[dict | None] = [None, None]

    def _call(idx):
        results[idx] = _fix_suggestion_attachment(
            1, "octocat/hello-world", "app.py", 10, "GET", "/x", 500, None,
        )

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    succeeded = [r for r in results if r is not None]
    assert len(succeeded) == 1


def test_health_fix_suggestion_adapter_uses_luna_when_openai_key_configured(monkeypatch):
    from scan_worker.jobs import _health_fix_suggestion_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    adapter = _health_fix_suggestion_adapter()

    assert adapter.name == "OpenAI"
    assert adapter._model == "gpt-5.6-luna"


def test_health_fix_suggestion_adapter_falls_back_to_deepseek_pro(monkeypatch):
    from scan_worker.jobs import _health_fix_suggestion_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)

    adapter = _health_fix_suggestion_adapter()

    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-pro"


def test_live_wiki_naming_adapter_never_uses_luna_even_when_openai_key_configured(monkeypatch):
    # AIRview is the one writing surface that must not prefer Luna - see
    # writing_adapter_for_airview's docstring for the benchmark that
    # justifies the exception (deepseek-v4-flash tied RepoWise, Luna lost).
    from scan_worker.jobs import _live_wiki_naming_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    adapter = _live_wiki_naming_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.FLASH_MODEL


def test_live_wiki_naming_adapter_uses_deepseek_flash_without_openai_key_too(monkeypatch):
    from scan_worker.jobs import _live_wiki_naming_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)

    adapter = _live_wiki_naming_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.FLASH_MODEL


def test_live_wiki_update_writing_adapter_never_uses_luna_even_when_openai_key_configured(monkeypatch):
    from scan_worker.jobs import _live_wiki_update_writing_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    adapter = _live_wiki_update_writing_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.UPDATE_MODEL


def test_live_wiki_update_writing_adapter_uses_deepseek_flash_without_openai_key_too(monkeypatch):
    from scan_worker.jobs import _live_wiki_update_writing_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)

    adapter = _live_wiki_update_writing_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.UPDATE_MODEL


def test_live_docs_update_writing_adapter_uses_luna_when_openai_key_configured(monkeypatch):
    from scan_worker.jobs import _live_docs_update_writing_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    assert _live_docs_update_writing_adapter().name == "OpenAI"


def test_live_docs_update_writing_adapter_falls_back_to_deepseek_flash(monkeypatch):
    from scan_worker.jobs import _live_docs_update_writing_adapter
    from scan_worker import live_docs

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)

    adapter = _live_docs_update_writing_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_docs.FLASH_MODEL


def test_run_health_sweep_staleness_check_job_alerts_when_stale(monkeypatch):
    from scan_worker.jobs import HEALTH_SWEEP_STALENESS_THRESHOLD_SECONDS, run_health_sweep_staleness_check_job

    monkeypatch.setattr(
        "scan_worker.jobs.get_seconds_since_last_health_check",
        lambda dsn: HEALTH_SWEEP_STALENESS_THRESHOLD_SECONDS + 1,
    )
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))

    run_health_sweep_staleness_check_job()

    assert len(alerts) == 1
    assert alerts[0][0][0] == "health_sweep"


def test_run_health_sweep_staleness_check_job_does_not_alert_when_fresh(monkeypatch):
    from scan_worker.jobs import run_health_sweep_staleness_check_job

    monkeypatch.setattr("scan_worker.jobs.get_seconds_since_last_health_check", lambda dsn: 30.0)
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))

    run_health_sweep_staleness_check_job()

    assert alerts == []


def test_run_health_sweep_staleness_check_job_does_not_alert_when_no_data_yet(monkeypatch):
    from scan_worker.jobs import run_health_sweep_staleness_check_job

    monkeypatch.setattr("scan_worker.jobs.get_seconds_since_last_health_check", lambda dsn: None)
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))

    run_health_sweep_staleness_check_job()

    assert alerts == []


class _FakeRedis:
    """now_fn defaults to real time.time so existing callers of this fake
    (none of which care about expiry) are unaffected; a test that does care
    - see test_run_ops_monitor_job_does_not_repeat_alert_within_cooldown -
    passes the same controllable time source it already uses to advance
    jobs.time.time, so `ex=` expiry is real for that test rather than
    silently ignored (which the old version of this fake did - `set`
    dropped `ex` on the floor entirely, so a cooldown key could never
    expire no matter how much simulated time passed)."""

    def __init__(self, now_fn=time.time):
        self.data = {}  # key -> (value, expire_at | None)
        self._now_fn = now_fn

    def _expire_if_due(self, key):
        entry = self.data.get(key)
        if entry is None:
            return
        _value, expire_at = entry
        if expire_at is not None and self._now_fn() >= expire_at:
            del self.data[key]

    def get(self, key):
        self._expire_if_due(key)
        entry = self.data.get(key)
        return entry[0] if entry else None

    def set(self, key, value, ex=None):
        expire_at = self._now_fn() + ex if ex is not None else None
        self.data[key] = (str(value), expire_at)

    def delete(self, key):
        self.data.pop(key, None)

    def incr(self, key):
        self._expire_if_due(key)
        value = int(self.data.get(key, ("0", None))[0]) + 1
        _prev_value, expire_at = self.data.get(key, ("0", None))
        self.data[key] = (str(value), expire_at)
        return value

    def expire(self, key, seconds):
        entry = self.data.get(key)
        if entry is not None:
            self.data[key] = (entry[0], self._now_fn() + seconds)
        return True


def test_run_ops_monitor_job_alerts_on_second_app_health_failure(monkeypatch):
    from scan_worker.jobs import run_ops_monitor_job

    redis_conn = _FakeRedis()
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
    monkeypatch.setattr("scan_worker.jobs._fetch_app_health", lambda url: (False, "broken"))
    monkeypatch.setattr("scan_worker.jobs._check_queue_alerts", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_backup_freshness", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_free_tier_provider_keys", lambda redis_conn: None)
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setenv("ALETHEORE_APP_HEALTH_URL", "http://bad-health.local/healthz")

    run_ops_monitor_job()

    assert alerts == []

    run_ops_monitor_job()

    assert len(alerts) == 1
    assert alerts[0][0][0] == "ops_monitor.app_health"
    assert "bad-health.local" in alerts[0][0][2]


def test_run_ops_monitor_job_broken_app_health_sends_ops_email(monkeypatch):
    from app_server import error_alerts
    from app_server.config import get_settings
    from scan_worker.jobs import run_ops_monitor_job

    redis_conn = _FakeRedis()
    sent = []
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_REPLY_TO_ADDRESS", "ops@example.com")
    get_settings.cache_clear()
    monkeypatch.setattr(error_alerts, "_last_alert_at", {})
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
    monkeypatch.setattr("scan_worker.jobs._fetch_app_health", lambda url: (False, "broken"))
    monkeypatch.setattr("scan_worker.jobs._check_queue_alerts", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_backup_freshness", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_free_tier_provider_keys", lambda redis_conn: None)
    monkeypatch.setattr(
        error_alerts,
        "send_transactional_email",
        lambda api_key, from_addr, reply_to, to, subject, html, text: sent.append(
            {"api_key": api_key, "reply_to": reply_to, "to": to, "subject": subject, "text": text}
        ),
    )

    run_ops_monitor_job()
    run_ops_monitor_job()

    assert len(sent) == 1
    assert sent[0]["api_key"] == "re_test_key"
    assert sent[0]["reply_to"] == "ops@example.com"
    assert sent[0]["to"] == "ops@example.com"
    assert "ops_monitor.app_health" in sent[0]["subject"]
    assert "broken" in sent[0]["text"]


def test_run_ops_monitor_job_alerts_when_queue_depth_stays_high(monkeypatch):
    from scan_worker import jobs
    from scan_worker.jobs import OPS_THRESHOLD_DURATION_SECONDS, run_ops_monitor_job

    redis_conn = _FakeRedis()
    alerts = []

    class FakeQueue:
        def __init__(self, name, connection):
            self.name = name
            self.count = 26 if name == "scans" else 0

    class FakeFailedRegistry:
        def __init__(self, queue):
            self.count = 0

    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
    monkeypatch.setattr("scan_worker.jobs._check_app_health", lambda redis_conn, url: None)
    monkeypatch.setattr("scan_worker.jobs._check_backup_freshness", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_free_tier_provider_keys", lambda redis_conn: None)
    monkeypatch.setattr("scan_worker.jobs.Queue", FakeQueue)
    monkeypatch.setattr("scan_worker.jobs.FailedJobRegistry", FakeFailedRegistry)
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setenv("ALETHEORE_OPS_QUEUE_DEPTH_THRESHOLD", "25")
    monkeypatch.setattr(jobs.time, "time", lambda: 1000.0)

    run_ops_monitor_job()

    assert alerts == []

    monkeypatch.setattr(jobs.time, "time", lambda: 1000.0 + OPS_THRESHOLD_DURATION_SECONDS + 1)

    run_ops_monitor_job()

    assert len(alerts) == 1
    assert alerts[0][0][0] == "ops_monitor.queue_depth.scans"
    assert "scans queue depth=26" in alerts[0][0][2]


def test_run_ops_monitor_job_does_not_repeat_alert_within_cooldown(monkeypatch):
    """Real incident this guards against: a condition that crossed its
    threshold once (a month-old, since-fixed failed-jobs count that was
    never cleared from the registry) kept re-alerting on every ~3-minute
    ops_monitor run indefinitely - 918 emails accumulated in production
    before this was caught. A persisting condition must alert once, then
    stay quiet until OPS_ALERT_COOLDOWN_SECONDS has passed, not on every
    single check."""
    from scan_worker import jobs
    from scan_worker.jobs import (
        OPS_ALERT_COOLDOWN_SECONDS,
        OPS_THRESHOLD_DURATION_SECONDS,
        run_ops_monitor_job,
    )

    t = 1000.0
    redis_conn = _FakeRedis(now_fn=lambda: t)
    alerts = []

    class FakeQueue:
        def __init__(self, name, connection):
            self.name = name
            self.count = 26 if name == "scans" else 0

    class FakeFailedRegistry:
        def __init__(self, queue):
            self.count = 0

    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
    monkeypatch.setattr("scan_worker.jobs._check_app_health", lambda redis_conn, url: None)
    monkeypatch.setattr("scan_worker.jobs._check_backup_freshness", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_free_tier_provider_keys", lambda redis_conn: None)
    monkeypatch.setattr("scan_worker.jobs.Queue", FakeQueue)
    monkeypatch.setattr("scan_worker.jobs.FailedJobRegistry", FakeFailedRegistry)
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setenv("ALETHEORE_OPS_QUEUE_DEPTH_THRESHOLD", "25")

    monkeypatch.setattr(jobs.time, "time", lambda: t)
    run_ops_monitor_job()
    assert alerts == []  # condition just started, not yet past OPS_THRESHOLD_DURATION_SECONDS

    alert_fired_at = 1000.0 + OPS_THRESHOLD_DURATION_SECONDS + 1
    t = alert_fired_at
    monkeypatch.setattr(jobs.time, "time", lambda: t)
    run_ops_monitor_job()
    assert len(alerts) == 1  # first alert, condition has now persisted past the duration threshold

    # Condition is still present (queue depth still 26) and only a little
    # time has passed - this is exactly the "every ~3 minutes" repeat-check
    # scenario that caused the real incident. Must NOT alert again.
    t = alert_fired_at + 180
    monkeypatch.setattr(jobs.time, "time", lambda: t)
    run_ops_monitor_job()
    assert len(alerts) == 1

    t = alert_fired_at + 360
    monkeypatch.setattr(jobs.time, "time", lambda: t)
    run_ops_monitor_job()
    assert len(alerts) == 1

    # Once the cooldown has genuinely elapsed (anchored to when it was
    # actually set - alert_fired_at - not to whatever t happens to be now),
    # a still-persisting condition should alert again as a "this is still
    # ongoing" reminder, not stay silent forever. OPS_ALERT_COOLDOWN_SECONDS
    # (6h) is now far longer than _check_threshold_duration's own
    # first_seen state-key TTL (OPS_THRESHOLD_DURATION_SECONDS*3=1800s), so
    # by the time the cooldown clears that state key has long since expired
    # - in production, continuous ~3-minute ops_monitor runs keep
    # re-seeding it the whole time, so a "seasoned" (>=600s old) first_seen
    # is already in place the moment the cooldown clears. This test only
    # jumps in time, so it reproduces that same two-step shape explicitly:
    # one run to re-seed first_seen after its old value expired, then one
    # more run past OPS_THRESHOLD_DURATION_SECONDS later to actually
    # re-cross the duration threshold with the cooldown now clear.
    t = alert_fired_at + OPS_ALERT_COOLDOWN_SECONDS + 1
    monkeypatch.setattr(jobs.time, "time", lambda: t)
    run_ops_monitor_job()
    assert len(alerts) == 1  # first_seen re-seeded, not yet past the duration threshold again

    t += OPS_THRESHOLD_DURATION_SECONDS + 1
    monkeypatch.setattr(jobs.time, "time", lambda: t)
    run_ops_monitor_job()
    assert len(alerts) == 2


def test_run_ops_monitor_job_alerts_when_backup_missing(monkeypatch, tmp_path):
    from scan_worker.jobs import run_ops_monitor_job

    redis_conn = _FakeRedis()
    alerts = []
    missing_dir = tmp_path / "backups"
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
    monkeypatch.setattr("scan_worker.jobs._check_app_health", lambda redis_conn, url: None)
    monkeypatch.setattr("scan_worker.jobs._check_queue_alerts", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_free_tier_provider_keys", lambda redis_conn: None)
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setenv("ALETHEORE_BACKUP_DIR", str(missing_dir))

    run_ops_monitor_job()

    assert len(alerts) == 1
    assert alerts[0][0][0] == "ops_monitor.backup_freshness.missing_dir"
    assert str(missing_dir) in alerts[0][0][2]


def test_check_backup_freshness_missing_dir_and_stale_backup_both_alert_within_cooldown(monkeypatch, tmp_path):
    # Real regression this guards: all three backup-freshness conditions
    # used to share one source ("ops_monitor.backup_freshness"), so
    # whichever fired first silently suppressed the other two for
    # OPS_ALERT_COOLDOWN_SECONDS - a stale-backup alert firing, then the
    # backup dir going fully unavailable minutes later (a worse condition),
    # with on-call never hearing about the second, worse one. Each
    # condition now has its own source suffix, so both alert.
    from scan_worker.jobs import OPS_BACKUP_STALE_SECONDS, _check_backup_freshness

    redis_conn = _FakeRedis()
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append(a[0]))

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    stale_dump = backup_dir / "aletheore_app_20260101.dump"
    stale_dump.write_text("x")
    now = OPS_BACKUP_STALE_SECONDS + 10_000
    os.utime(stale_dump, (0, 0))
    monkeypatch.setenv("ALETHEORE_BACKUP_DIR", str(backup_dir))

    _check_backup_freshness(redis_conn, now)  # stale backup: first alert

    stale_dump.unlink()
    backup_dir.rmdir()  # now the whole directory is gone: worse condition

    _check_backup_freshness(redis_conn, now)  # missing dir: must still alert

    assert alerts == ["ops_monitor.backup_freshness.stale", "ops_monitor.backup_freshness.missing_dir"]


def test_check_backup_freshness_tolerates_normal_cron_and_dump_duration_jitter(monkeypatch, tmp_path):
    # Real false positive from prod, 2026-08-25: the backup cron fires at a
    # fixed wall-clock time (0 3 * * * UTC) and pg_dump takes ~7-11s to
    # finish (mtime is only set once the dump completes and is renamed into
    # place - see backup-postgres.sh), while this check runs on its own
    # independent ~180s-interval loop (scan_worker/scheduler.py) with no
    # wall-clock anchoring at all. The two schedules aren't correlated, so
    # over enough days a sample eventually lands in the few-second gap
    # after yesterday's dump crosses exactly 24h old but before today's
    # fresh dump lands - exactly what happened: the real alert reported
    # age_seconds=86403, just 3 seconds past the old threshold, while every
    # single day's backup in the preceding week actually succeeded. A
    # threshold with zero tolerance for this structural (cron latency +
    # dump duration) jitter will keep re-triggering this false positive
    # indefinitely, regardless of which specific day it next lands on.
    from scan_worker.jobs import _check_backup_freshness

    redis_conn = _FakeRedis()
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append(a[0]))

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dump = backup_dir / "aletheore_app_20260101.dump"
    dump.write_text("x")
    os.utime(dump, (0, 0))
    monkeypatch.setenv("ALETHEORE_BACKUP_DIR", str(backup_dir))

    # The real incident's literal age_seconds from the alert body - a bare
    # 24h (86400s) constant, not derived from OPS_BACKUP_STALE_SECONDS
    # itself, so this test actually pins the real-world scenario rather
    # than trivially tracking whatever the threshold is currently set to.
    _check_backup_freshness(redis_conn, 86400 + 3)

    assert alerts == []


def test_run_ops_monitor_job_alerts_when_a_free_tier_provider_key_is_missing(monkeypatch):
    # Real incident this guards: writing_adapter_chain_for_free_tier silently
    # skips (info-log only) any provider whose key isn't set, so all four
    # free-tier keys sat unset in production for weeks with free-tier Flash
    # Review quietly no-op'ing the whole time - no error, no alert. This is
    # the check that would have caught it in minutes instead of weeks.
    from scan_worker.jobs import run_ops_monitor_job

    redis_conn = _FakeRedis()
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.get_redis_client", lambda: redis_conn)
    monkeypatch.setattr("scan_worker.jobs._check_app_health", lambda redis_conn, url: None)
    monkeypatch.setattr("scan_worker.jobs._check_queue_alerts", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs._check_backup_freshness", lambda redis_conn, now: None)
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append((a, k)))

    def fake_has_api_key(env_var, provider_name, **kwargs):
        return provider_name != "Groq"

    monkeypatch.setattr("scan_worker.jobs.has_api_key", fake_has_api_key)

    run_ops_monitor_job()

    assert len(alerts) == 1
    assert alerts[0][0][0] == "ops_monitor.free_tier_key.groq"
    assert "GROQ_API_KEY" in alerts[0][0][2]


def test_check_free_tier_provider_keys_alerts_separately_for_each_missing_provider(monkeypatch):
    # Same reasoning as the backup-freshness dual-condition test above: each
    # provider needs its own alert source, or two providers going missing at
    # once would have the second one silently suppressed by the first's
    # cooldown.
    from scan_worker.jobs import _check_free_tier_provider_keys

    redis_conn = _FakeRedis()
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append(a[0]))
    monkeypatch.setattr("scan_worker.jobs.has_api_key", lambda *a, **k: False)

    _check_free_tier_provider_keys(redis_conn)

    assert alerts == [
        "ops_monitor.free_tier_key.groq",
        "ops_monitor.free_tier_key.gemini",
        "ops_monitor.free_tier_key.openai-freetier",
        "ops_monitor.free_tier_key.openrouter",
    ]


def test_check_free_tier_provider_keys_sends_no_alert_when_all_keys_present(monkeypatch):
    from scan_worker.jobs import _check_free_tier_provider_keys

    redis_conn = _FakeRedis()
    alerts = []
    monkeypatch.setattr("scan_worker.jobs.send_error_alert", lambda *a, **k: alerts.append(a[0]))
    monkeypatch.setattr("scan_worker.jobs.has_api_key", lambda *a, **k: True)

    _check_free_tier_provider_keys(redis_conn)

    assert alerts == []


def test_run_git_scrubs_credentialed_url_from_a_failed_clone_error(tmp_path):
    from scan_worker.jobs import _run_git

    credentialed_url = "https://x-access-token:supersecrettoken@github.com/acme/does-not-exist.git"
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _run_git(["git", "clone", "-q", credentialed_url, str(tmp_path / "dest")])

    assert "supersecrettoken" not in str(exc_info.value)
    assert "https://github.com/acme/does-not-exist.git" in exc_info.value.cmd
