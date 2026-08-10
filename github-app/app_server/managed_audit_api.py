import hashlib

import toon
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app_server.audit_signing import (
    LLM_SUGGESTION_HEADING,
    contains_non_evidence_backed_section,
    public_key_hex_from_private,
    verify_report,
)
from app_server.config import get_settings
from app_server.db import (
    MAX_SCANNED_REPOS_PER_MONTH,
    check_and_reserve_managed_audit,
    check_and_reserve_monthly_repo_scan_slot,
    get_audit_report_by_token,
    get_installation_by_token_hash,
    touch_api_token,
)
from app_server.evidence_limits import MAX_EVIDENCE_BYTES
from app_server.rate_limit import cooldown_seconds_for_loc, total_loc_from_evidence

managed_audit_router = APIRouter()


class StartManagedAuditRequest(BaseModel):
    repo_full_name: str | None = None
    # max_length is a character count, evidence is TOON text so this is a
    # close approximation of MAX_EVIDENCE_BYTES rather than an exact byte cap.
    evidence: str = Field(max_length=MAX_EVIDENCE_BYTES)


def _get_queue(redis_url: str):
    from rq import Queue

    from app_server.redis_client import get_redis_client

    return Queue("scans", connection=get_redis_client())


def _fetch_job(job_id: str, redis_url: str):
    from rq.job import Job

    from app_server.redis_client import get_redis_client

    return Job.fetch(job_id, connection=get_redis_client())


async def _authenticate_token(request: Request) -> tuple[dict, str]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    raw_token = auth_header.removeprefix("Bearer ")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    installation = await get_installation_by_token_hash(request.app.state.db_pool, token_hash)
    if installation is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    return installation, token_hash


@managed_audit_router.post("/v1/managed-audit")
async def start_managed_audit(request: Request, body: StartManagedAuditRequest):
    installation, token_hash = await _authenticate_token(request)
    pool = request.app.state.db_pool
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="managed audits require a paid plan")

    if not body.repo_full_name:
        raise HTTPException(status_code=400, detail="repo_full_name is required")

    try:
        decoded_evidence = toon.decode(body.evidence)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="evidence could not be decoded") from exc

    if not await check_and_reserve_monthly_repo_scan_slot(
        pool, installation["installation_id"], body.repo_full_name, MAX_SCANNED_REPOS_PER_MONTH
    ):
        raise HTTPException(
            status_code=429,
            detail=(
                f"this installation has already scanned {MAX_SCANNED_REPOS_PER_MONTH} different "
                "repos this month (across PR scans, Flash review, and managed audits) - try "
                "again next month, or re-run against a repo you've already scanned"
            ),
        )

    cooldown_seconds = cooldown_seconds_for_loc(total_loc_from_evidence(decoded_evidence))
    allowed = await check_and_reserve_managed_audit(
        pool, installation["installation_id"], body.repo_full_name, cooldown_seconds
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"managed audit rate limit: this repo can run one managed audit every "
                f"{cooldown_seconds // 3600} hours - try again later"
            ),
        )

    await touch_api_token(pool, token_hash)
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_managed_audit_api_job",
        job_timeout=900,
        installation_id=installation["installation_id"],
        evidence=body.evidence,
        repo_full_name=body.repo_full_name,
    )
    return JSONResponse(status_code=202, content={"job_id": job.id})


@managed_audit_router.get("/v1/whoami")
async def whoami(request: Request):
    installation, _ = await _authenticate_token(request)
    return {"account_login": installation["account_login"], "plan": installation["plan"]}


@managed_audit_router.get("/v1/audit/{verification_token}/verify")
async def verify_audit_report(verification_token: str, request: Request):
    report = await get_audit_report_by_token(request.app.state.db_pool, verification_token)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")

    settings = get_settings()
    # The key recorded on the report itself, not whichever key is current.
    # Deriving it from AUDIT_SIGNING_PRIVATE_KEY at request time meant a
    # rotation would report verified=false for every certificate ever issued -
    # so the only safe operation on the signing key was never to rotate it.
    # Rows written before migration 044 carry no key and were signed by the
    # current one; that fallback applies to them alone.
    current_public_key_hex = public_key_hex_from_private(settings.audit_signing_private_key)
    public_key_hex = report["signing_public_key"] or current_public_key_hex
    verified = verify_report(report["report_text"], report["signature"], public_key_hex)

    # A signature attests "Aletheore produced this exact text" - not "every
    # claim in it is backed by a citation". Those are different guarantees,
    # and a certificate that reports only `verified: true` invites a reader to
    # conflate them, extending the signature's authority to the one section
    # that is deliberately a model opinion. Stating it here means a consumer
    # of this endpoint can tell the two apart without parsing the markdown.
    has_opinion_section = contains_non_evidence_backed_section(report["report_text"])

    return {
        "repo_full_name": report["repo_full_name"],
        "content_hash": report["content_hash"],
        "signed_at": report["created_at"].isoformat(),
        "verified": verified,
        "fully_evidence_backed": not has_opinion_section,
        "non_evidence_backed_sections": [LLM_SUGGESTION_HEADING.lstrip("# ")]
        if has_opinion_section
        else [],
        # Included so this is an actual verifiable certificate, not just an
        # "our database says so" boolean - anyone can confirm signature over
        # content_hash using this public key, without trusting this endpoint's
        # own "verified" field.
        #
        # That independence is only real if the verifier obtained the key from
        # somewhere other than this response, so /v1/audit/signing-key serves
        # the current one at a stable URL to pin out-of-band, and is_current_key
        # says whether this report was signed by it. A false here is not a
        # problem - it means the report predates a rotation and was checked
        # against its own recorded key, exactly as intended.
        "algorithm": "Ed25519",
        "signature": report["signature"],
        "public_key": public_key_hex,
        "is_current_key": public_key_hex == current_public_key_hex,
    }


@managed_audit_router.get("/v1/audit/signing-key")
async def audit_signing_key():
    """The public half of the key currently signing audit reports.

    Exists so a consumer can pin the key out-of-band and verify a certificate
    without taking the verifying endpoint's word for either the key or the
    result. Public and unauthenticated by design: a public key is not a secret,
    and a verifier who has to authenticate to fetch it is back to trusting us.
    """
    settings = get_settings()
    return {
        "algorithm": "Ed25519",
        "public_key": public_key_hex_from_private(settings.audit_signing_private_key),
    }


@managed_audit_router.get("/v1/managed-audit/{job_id}")
async def get_managed_audit_status(job_id: str, request: Request):
    installation, _ = await _authenticate_token(request)

    from rq.exceptions import NoSuchJobError

    try:
        job = _fetch_job(job_id, get_settings().redis_url)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc

    if job.kwargs.get("installation_id") != installation["installation_id"]:
        raise HTTPException(status_code=404, detail="job not found")

    if job.is_failed:
        return {"status": "failed"}
    if job.is_finished:
        return {
            "status": "finished",
            "result": job.result,
            "verification_token": job.meta.get("verification_token"),
        }
    return {"status": "pending"}
