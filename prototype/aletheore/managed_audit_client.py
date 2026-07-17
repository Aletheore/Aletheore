import time

import httpx

from aletheore.toon_encoding import to_toon


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
    api_base_url: str = "https://aletheore.com",
    http_client: httpx.Client | None = None,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> str:
    client = http_client or httpx.Client(base_url=api_base_url)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/v1/managed-audit",
        json={"evidence": to_toon(evidence)},
        headers=headers,
    )
    if response.status_code in (401, 402):
        raise ManagedAuditError(_error_detail(response))
    response.raise_for_status()
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_response = client.get(f"/v1/managed-audit/{job_id}", headers=headers)
        status_response.raise_for_status()
        body = status_response.json()
        if body["status"] == "finished":
            return body["result"]
        if body["status"] == "failed":
            raise ManagedAuditError("managed audit job failed on the server")
        if poll_interval:
            time.sleep(poll_interval)

    raise ManagedAuditError(f"managed audit timed out after {timeout}s waiting for job {job_id}")
