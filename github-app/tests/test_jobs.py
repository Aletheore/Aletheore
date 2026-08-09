import json
import os
import subprocess
from contextlib import contextmanager

import pytest

from scan_worker.jobs import MAX_FLASH_REVIEWS_PER_MONTH, run_pr_scan_job

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


@contextmanager
def _noop_spend_lock(*args, **kwargs):
    yield


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
        lambda dsn, iid, repo, token, text, chash, sig: stored.update(
            installation_id=iid,
            repo_full_name=repo,
            token=token,
            text=text,
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
        lambda dsn, iid, repo, token, text, chash, sig: stored.update(
            installation_id=iid,
            repo_full_name=repo,
            token=token,
            text=text,
            hash=chash,
            sig=sig,
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


def test_flash_review_job_skips_free_tier(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


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
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_skips_when_monthly_review_count_reached(monkeypatch):
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
    monkeypatch.setattr(
        "scan_worker.jobs.get_flash_review_count_this_month",
        lambda *a, **k: MAX_FLASH_REVIEWS_PER_MONTH,
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

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
            {"file": "app.py", "line": 1, "issue": "real problem"}
        ],
    )
    recorded_spend = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_llm_spend", lambda dsn, iid, cost: recorded_spend.append(cost)
    )
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
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

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "app.py:1" in posted["body"]
    assert "real problem" in posted["body"]
    assert posted["marker"] == FLASH_REVIEW_MARKER
    assert set_sha_calls == ["bbb"]
    assert recorded_spend == [0.0]


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
        return [{"file": "app.py", "line": 1, "issue": "real problem"}]

    monkeypatch.setattr("scan_worker.jobs.review_diff", fake_review_diff)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "No issues held up under verification (3 proposed, 0 grounded" in posted["body"]
    assert "No issues found in this diff." not in posted["body"]
    # The line above already states the 0-grounded fact - a second
    # "Grounding: 0 of 3..." footer would just repeat it.
    assert "Grounding:" not in posted["body"]


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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

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
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import run_flash_review_job

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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

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
            {"file": "app.py", "line": 1, "issue": "unclosed handle", "suggestion": "f.close()"}
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "f.close()" in posted["body"]
    assert "```suggestion" not in posted["body"]


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
    monkeypatch.setattr("scan_worker.jobs.increment_flash_review_count", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

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

    monkeypatch.setattr("scan_worker.jobs._live_wiki_full_build_writing_adapter", lambda plan: _SpyAdapter())
    monkeypatch.setattr("scan_worker.jobs._live_wiki_naming_adapter", lambda: _NamingAdapter())
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
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.time.sleep", lambda *a, **k: None)
    # Real DNS resolution has no place in a unit test - SSRF re-validation
    # itself is covered by its own dedicated tests below.
    monkeypatch.setattr("scan_worker.jobs.validate_external_https_url", lambda url: url)
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

    def fake_healthcheck(endpoints, base_url):
        calls["count"] += 1
        if calls["count"] == 1 or retry_result_entry is None:
            return {"results": [default_first]}
        return {"results": [retry_result_entry]}

    monkeypatch.setattr("scan_worker.jobs.run_healthcheck", fake_healthcheck)
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health", lambda dsn, iid, repo, method, path, target_id=None: prior
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))
    return sent


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

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

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
        lambda endpoints, base_url: healthcheck_calls.append(True)
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
    monkeypatch.setattr("scan_worker.jobs.validate_external_https_url", lambda url: url)
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
        lambda endpoints, base_url: {
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

    monkeypatch.setattr("scan_worker.jobs.validate_external_https_url", fake_validate)

    evidence_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: evidence_calls.append(True)
        or {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    fetch_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: fetch_calls.append(True)
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
        "scan_worker.jobs.validate_external_https_url",
        lambda url: validated_urls.append(url) or url,
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: {
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
    monkeypatch.setattr("scan_worker.jobs.validate_external_https_url", lambda url: url)
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

    def fake_healthcheck(endpoints, base_url):
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
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})

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


def test_maybe_update_live_wiki_records_failure_status_on_exception(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})

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


def test_run_pr_scan_job_wires_changed_files_into_live_wiki_update(bare_repo_with_two_commits, monkeypatch):
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
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"]
    )
    called = {}
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_wiki",
        lambda installation_id, repo_full_name, evidence, changed_files, head_sha: called.update(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            changed_files=changed_files,
            head_sha=head_sha,
        ),
    )

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert called["installation_id"] == 1
    assert called["repo_full_name"] == "octocat/hello-world"
    assert called["changed_files"] == ["app.py"]
    assert called["head_sha"] == head_sha


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


def test_run_push_scan_job_scans_and_reconciles_wiki(bare_repo_with_two_commits, monkeypatch):
    from scan_worker.jobs import run_push_scan_job

    bare_path, _base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)

    called = {}
    monkeypatch.setattr(
        "scan_worker.jobs._maybe_update_live_wiki",
        lambda installation_id, repo_full_name, evidence, changed_files, head_sha: called.update(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            changed_files=changed_files,
            head_sha=head_sha,
        ),
    )

    run_push_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        head_sha=head_sha,
        changed_files=["app.py"],
    )

    assert called["installation_id"] == 1
    assert called["repo_full_name"] == "octocat/hello-world"
    assert called["changed_files"] == ["app.py"]
    assert called["head_sha"] == head_sha


def test_run_push_scan_job_skips_wiki_update_for_free_plan(bare_repo_with_two_commits, monkeypatch):
    from scan_worker.jobs import run_push_scan_job

    bare_path, _base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    called = []
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: called.append(True))

    run_push_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        head_sha=head_sha,
        changed_files=["app.py"],
    )

    assert called == []


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


def test_run_push_scan_job_logs_and_does_not_raise_on_scan_failure(bare_repo_with_two_commits, monkeypatch, caplog):
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
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
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


def test_full_build_writing_adapter_uses_the_tier_model_for_the_plan(monkeypatch):
    # Model-selection logic itself is covered by test_model_tiers.py - this
    # just checks jobs.py's wrapper actually delegates plan through.
    from scan_worker.jobs import _live_wiki_full_build_writing_adapter

    adapter = _live_wiki_full_build_writing_adapter("pro")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-pro"


def test_full_build_writing_adapter_indie_stays_on_deepseek(monkeypatch):
    from scan_worker.jobs import _live_wiki_full_build_writing_adapter

    adapter = _live_wiki_full_build_writing_adapter("indie")
    assert adapter.name == "DeepSeek"


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


def test_run_live_docs_full_build_job_survives_one_module_failing(monkeypatch):
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
        "scan_worker.jobs._live_docs_full_build_writing_adapter", lambda plan: object()
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


def test_run_live_docs_full_build_job_reports_failed_when_every_module_fails(monkeypatch):
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
        "scan_worker.jobs._live_docs_full_build_writing_adapter", lambda plan: object()
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


def test_maybe_update_live_docs_survives_one_module_failing(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_docs

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs._live_docs_update_writing_adapter", lambda: object())
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
    from scan_worker.jobs import _maybe_update_live_docs

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr(
        "scan_worker.jobs._github_client_and_token", lambda *a, **k: (object(), "tok")
    )
    monkeypatch.setattr("scan_worker.jobs._live_docs_update_writing_adapter", lambda: object())

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
        lambda client, token, repo, modules, s: sync_calls.append((repo, modules, s)) or ("hash123", 7),
    )
    record_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_docs_repo_commit",
        lambda dsn, iid, repo, content_hash, pr_number: record_calls.append((iid, repo, content_hash, pr_number)),
    )

    _maybe_sync_docs_to_repo("dsn", 1, "octocat/hello-world")

    assert len(sync_calls) == 1
    repo, modules, s = sync_calls[0]
    assert repo == "octocat/hello-world"
    assert "a.py" in modules
    assert s is settings
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


def test_live_wiki_naming_adapter_uses_luna_when_openai_key_configured(monkeypatch):
    from scan_worker.jobs import _live_wiki_naming_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    assert _live_wiki_naming_adapter().name == "OpenAI"


def test_live_wiki_naming_adapter_falls_back_to_deepseek_flash(monkeypatch):
    from scan_worker.jobs import _live_wiki_naming_adapter
    from scan_worker import live_wiki

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)

    adapter = _live_wiki_naming_adapter()
    assert adapter.name == "DeepSeek"
    assert adapter._model == live_wiki.FLASH_MODEL


def test_live_wiki_update_writing_adapter_uses_luna_when_openai_key_configured(monkeypatch):
    from scan_worker.jobs import _live_wiki_update_writing_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)

    assert _live_wiki_update_writing_adapter().name == "OpenAI"


def test_live_wiki_update_writing_adapter_falls_back_to_deepseek_flash(monkeypatch):
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
