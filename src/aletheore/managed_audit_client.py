import time

import httpx

from aletheore.toon_encoding import ToonEncodingError, to_toon


class ManagedAuditError(Exception):
    pass


def _error_detail(response: httpx.Response) -> str:
    try:
        return response.json().get("detail", "managed audit request rejected")
    except ValueError:
        return response.text or "managed audit request rejected"


def run_managed_audit_request(
    evidence: dict,
    token: str,
    repo_full_name: str | None = None,
    api_base_url: str = "https://aletheore.com",
    http_client: httpx.Client | None = None,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> str:
    owns_client = http_client is None
    # Flash Review finding: `http_client or httpx.Client(...)` chooses by
    # truthiness while owns_client above checks identity against None - a
    # caller-supplied client-like object that's falsy (unusual, but not
    # impossible - a test double with a custom __bool__, an httpx.Client
    # subclass overriding it) would be silently discarded here in favor
    # of a freshly created one, while owns_client still says "not owned",
    # so that new client is never closed. Both checks now use the same
    # None comparison.
    client = http_client if http_client is not None else httpx.Client(base_url=api_base_url)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        try:
            encoded_evidence = to_toon(evidence)
        except ToonEncodingError as exc:
            raise ManagedAuditError(f"could not encode evidence for managed audit: {exc}") from exc

        response = client.post(
            "/v1/managed-audit",
            json={"evidence": encoded_evidence, "repo_full_name": repo_full_name},
            headers=headers,
        )
        if response.status_code in (401, 402, 429):
            raise ManagedAuditError(_error_detail(response))
        response.raise_for_status()
        job_id = response.json()["job_id"]

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status_response = client.get(f"/v1/managed-audit/{job_id}", headers=headers)
            status_response.raise_for_status()
            body = status_response.json()
            if body["status"] == "finished":
                result = body["result"]
                # Real bug found via audit: the server signs the report
                # and persists a verification_token whenever signing
                # succeeds (see jobs.py's run_managed_audit_api_job /
                # get_managed_audit_status), but this was the only real
                # consumer of that endpoint that never read it - the CLI
                # user got the raw report text with no indication the
                # report is a cryptographically signed, independently
                # verifiable certificate, or where to verify it. The
                # PR-comment path (run_managed_audit_pr_job) already
                # appends the identical "[Verify this report](url)" link
                # directly into the report text on success - matching
                # that same convention here instead of inventing a new
                # one, and keeping this function's return type a plain
                # str rather than changing its public contract.
                verification_token = body.get("verification_token")
                if verification_token:
                    verify_url = f"{api_base_url}/v1/audit/{verification_token}/verify"
                    result = f"{result}\n\n[Verify this report]({verify_url})"
                return result
            if body["status"] == "failed":
                raise ManagedAuditError("managed audit job failed on the server")
            if poll_interval:
                time.sleep(poll_interval)

        raise ManagedAuditError(f"managed audit timed out after {timeout}s waiting for job {job_id}")
    finally:
        # Only close a client we created ourselves - a caller-supplied
        # http_client is owned by the caller (e.g. reused across requests)
        # and must outlive this call.
        if owns_client:
            client.close()
