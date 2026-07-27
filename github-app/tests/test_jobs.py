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


def test_check_run_failure_does_not_overwrite_diff_comment(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    def raise_error(*a, **k):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
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

    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert stored["installation_id"] == 1
    assert stored["repo_full_name"] == "octocat/hello-world"
    assert stored["text"] == "the audit findings"
    assert len(stored["token"]) == 64
    assert stored["token"] in posted["body"]


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
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.fetch_changed_file_contents", lambda *a, **k: {})
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
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.fetch_changed_file_contents", lambda *a, **k: {})
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
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_changed_file_contents",
        lambda *a, **k: {"app.py": "real content of app.py"},
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
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.fetch_changed_file_contents", lambda *a, **k: {})
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
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.fetch_changed_file_contents", lambda *a, **k: {})
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

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert stored["records"] == [fake_record]
    assert stored["commit"] == "sha1"


def test_run_pr_scan_job_wires_changed_files_into_live_wiki_update(bare_repo_with_two_commits, monkeypatch):
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

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert stored["records"] == [fake_record]
    assert stored["commit"] is None


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

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert captured_subsystems["fetch_line_count"] is sentinel
    assert captured_store["fetch_line_count"] is sentinel


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
