import json
import subprocess
from contextlib import contextmanager

import pytest

from scan_worker.jobs import run_pr_scan_job


@contextmanager
def _noop_spend_lock(*args, **kwargs):
    yield


def test_happy_path_posts_comment_and_writes_history(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body
        posted["repo_full_name"] = repo_full_name
        posted["pr_number"] = pr_number

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
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
        lambda *a, **k: {"plan": "indie", "webhook_url": "https://hooks.slack.com/x"},
    )
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
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
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


def test_managed_audit_api_job_returns_report_text(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(installation_id=100, evidence={"scanned_at": "2026-01-01"})

    assert "API Report" in result


def test_managed_audit_api_job_raises_when_spend_cap_reached(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
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
        run_managed_audit_api_job(installation_id=100, evidence={"scanned_at": "2026-01-01"})
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
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# Managed Audit")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
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
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
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
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
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
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: False
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_skips_when_spend_cap_reached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_posts_findings_and_updates_state(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", on_usage=None: [
            {"file": "app.py", "line": 1, "issue": "real problem"}
        ],
    )
    recorded_spend = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_llm_spend", lambda dsn, iid, cost: recorded_spend.append(cost)
    )
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


def test_flash_review_job_renders_suggestion_as_plain_fence_not_github_suggestion_syntax(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", on_usage=None: [
            {"file": "app.py", "line": 1, "issue": "unclosed handle", "suggestion": "f.close()"}
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
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
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+fine")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.gather_file_context", lambda *a, **k: "")
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda diff_text, file_context="", on_usage=None: [])
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
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
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
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
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
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
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: {
            "results": [
                result_entry
                or {"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 90.0}
            ]
        },
    )
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
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )

    def fake_healthcheck(endpoints, base_url):
        reachable = base_url == "https://staging.example.com"
        return {"results": [{"method": "GET", "path": "/x", "reachable": reachable, "status_code": 200 if reachable else None, "latency_ms": 50.0}]}

    monkeypatch.setattr("scan_worker.jobs.run_healthcheck", fake_healthcheck)
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path, target_id=None: {"reachable": True, "latency_ms": 50.0},
    )
    recorded = []
    monkeypatch.setattr(
        "scan_worker.jobs.insert_endpoint_health",
        lambda dsn, iid, repo, method, path, reachable, status_code, latency_ms, target_id=None, keep=20: recorded.append(
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
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
    called = []
    monkeypatch.setattr(
        "scan_worker.live_wiki.generate_subsystems", lambda *a, **k: called.append(1)
    )

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["unrelated/file.py"], "sha1")

    assert called == []


def test_maybe_update_live_wiki_generates_and_stores_for_affected_clusters(monkeypatch):
    from scan_worker.jobs import _maybe_update_live_wiki

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})

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
        lambda dsn, iid, repo, evidence, records, adapter, commit: stored.update(
            records=records, commit=commit
        ),
    )

    _maybe_update_live_wiki(1, "octocat/hello-world", _wiki_evidence(), ["auth/login.py"], "sha1")

    assert stored["records"] == [fake_record]
    assert stored["commit"] == "sha1"


def test_run_pr_scan_job_wires_changed_files_into_live_wiki_update(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
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
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
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
        lambda dsn, iid, repo, evidence, records, adapter, commit: stored.update(
            records=records, commit=commit
        ),
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert stored["records"] == [fake_record]
    assert stored["commit"] is None


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
    # Fallback/tier-selection logic itself is covered by test_model_tiers.py -
    # this just checks jobs.py's wrapper actually delegates plan through.
    from scan_worker.jobs import _live_wiki_full_build_writing_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = _live_wiki_full_build_writing_adapter("team")
    assert adapter.name == "OpenAI"
    assert adapter._model == "gpt-4o"


def test_full_build_writing_adapter_indie_stays_on_deepseek(monkeypatch):
    from scan_worker.jobs import _live_wiki_full_build_writing_adapter

    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = _live_wiki_full_build_writing_adapter("indie")
    assert adapter.name == "DeepSeek"
