import subprocess
from pathlib import Path

import pytest

from scan_worker.jobs import run_pr_scan_job


def _make_git_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    for name, content in files.items():
        (path / name).write_text(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def bare_repo_with_two_commits(tmp_path):
    work = tmp_path / "work"
    base_sha = _make_git_repo(work, {"app.py": "print('hello')\n"})
    (work / "app.py").write_text("password = 'sk-abcdef1234567890abcdef1234567890'\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add secret"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return str(bare), base_sha, head_sha


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
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "pro", "webhook_url": "https://hooks.slack.com/x"},
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
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"})
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
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda repo_path: "# API Report")
    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(evidence={"scanned_at": "2026-01-01"})

    assert "API Report" in result


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
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda repo_path: "# Managed Audit")
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
