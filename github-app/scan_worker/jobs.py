import asyncio
import inspect
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aletheore.evidence import write_evidence
from aletheore.history import compute_diff
from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment
from app_server.config import get_settings
from app_server.github_auth import generate_app_jwt, get_installation_token
from scan_worker.db import get_installation as get_installation_row
from scan_worker.db import insert_repo_history
from scan_worker.github_api import create_check_run, upsert_pr_comment
from scan_worker.managed_audit import run_managed_audit
from scan_worker.slack import send_slack_alert

JOBS_ROOT = Path("/tmp/aletheore-jobs")
AUDIT_COMMENT_MARKER = "<!-- aletheore-audit -->"


def _job_temp_dir() -> Path:
    path = JOBS_ROOT / str(uuid.uuid4())
    path.mkdir(parents=True, exist_ok=False)
    return path


def _clone_url(repo_full_name: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"


def _clone_ref(url: str, ref: str, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(["git", "checkout", "-q", ref], cwd=dest, check=True)


def _run_scan(repo_dir: Path) -> Path:
    subprocess.run(["aletheore", "scan", str(repo_dir)], check=True)
    return repo_dir / ".aletheore" / "evidence.json"


def _insert_history(installation_id: int, repo_full_name: str, evidence: dict) -> None:
    settings = get_settings()
    insert_repo_history(
        settings.database_url,
        installation_id,
        repo_full_name,
        datetime.now(timezone.utc),
        evidence,
    )


def _maybe_send_slack_alert(
    installation_id: int, repo_full_name: str, pr_number: int, diff: dict
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return
    webhook_url = installation.get("webhook_url")
    if not webhook_url:
        return
    send_slack_alert(webhook_url, diff, repo_full_name, pr_number)


def _real_new_secrets(diff: dict) -> list[dict]:
    return [
        finding
        for finding in diff.get("secrets", {}).get("new", [])
        if not finding.get("likely_placeholder", False) and not finding.get("accepted", False)
    ]


def _maybe_create_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    diff: dict,
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    new_secrets = _real_new_secrets(diff)
    if new_secrets:
        summary = "\n".join(
            f"- `{finding.get('path')}:{finding.get('line')}` ({finding.get('pattern')})"
            for finding in new_secrets
        )
        create_check_run(client, token, repo_full_name, head_sha, "failure", summary)
    else:
        create_check_run(client, token, repo_full_name, head_sha, "success", "No new secrets found.")


async def _resolve_token(installation_id: int, app_jwt: str) -> str:
    result = get_installation_token(installation_id, app_jwt)
    if inspect.isawaitable(result):
        return await result
    return result


def _token_sync(installation_id: int, app_jwt: str) -> str:
    return asyncio.run(_resolve_token(installation_id, app_jwt))


def _failure_body(error: Exception) -> str:
    return f"{COMMENT_MARKER}\nAletheore couldn't complete this scan: {error}"


def _post_failure_comment(
    settings,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    error: Exception,
) -> None:
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = _token_sync(installation_id, app_jwt)
    client = httpx.Client(base_url="https://api.github.com")
    upsert_pr_comment(client, token, repo_full_name, pr_number, _failure_body(error))


def run_pr_scan_job(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)

        clone_url = _clone_url(repo_full_name, token)
        base_dir = job_dir / "base"
        head_dir = job_dir / "head"
        _clone_ref(clone_url, base_sha, base_dir)
        _clone_ref(clone_url, head_sha, head_dir)

        base_evidence_path = _run_scan(base_dir)
        head_evidence_path = _run_scan(head_dir)
        old = json.loads(base_evidence_path.read_text())
        new = json.loads(head_evidence_path.read_text())
        diff = compute_diff(old, new, full=False)

        client = httpx.Client(base_url="https://api.github.com")
        upsert_pr_comment(client, token, repo_full_name, pr_number, format_diff_comment(diff))
        _insert_history(installation_id, repo_full_name, new)
        _maybe_send_slack_alert(installation_id, repo_full_name, pr_number, diff)
        _maybe_create_check_run(client, token, repo_full_name, head_sha, installation_id, diff)
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _clone_pr_head(url: str, pr_number: int, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(
        ["git", "fetch", "-q", "origin", f"refs/pull/{pr_number}/head"],
        cwd=dest,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, check=True)


def run_managed_audit_pr_job(installation_id: int, repo_full_name: str, pr_number: int) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        repo_dir = job_dir / "head"
        _clone_pr_head(_clone_url(repo_full_name, token), pr_number, repo_dir)
        _run_scan(repo_dir)

        report_text = run_managed_audit(repo_dir)
        body = f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n{report_text}"
        client = httpx.Client(base_url="https://api.github.com")
        upsert_pr_comment(
            client,
            token,
            repo_full_name,
            pr_number,
            body,
            marker=AUDIT_COMMENT_MARKER,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def run_managed_audit_api_job(evidence: dict | str) -> str:
    job_dir = _job_temp_dir()
    try:
        if isinstance(evidence, dict):
            write_evidence(evidence, job_dir)
        else:
            aletheore_dir = job_dir / ".aletheore"
            aletheore_dir.mkdir(parents=True, exist_ok=True)
            (aletheore_dir / "evidence.toon").write_text(evidence)
            (aletheore_dir / "evidence.json").write_text(json.dumps({"managed_evidence": True}))
        return run_managed_audit(job_dir)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
