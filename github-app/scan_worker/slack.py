import httpx


def _has_new_findings(diff: dict) -> bool:
    return bool(
        diff.get("secrets", {}).get("new")
        or diff.get("history_secrets", {}).get("new")
        or diff.get("vulnerabilities", {}).get("new")
        or diff.get("layer_violations", {}).get("new")
    )


def format_slack_message(diff: dict, repo_full_name: str, pr_number: int) -> dict:
    lines = [f"*Aletheore*: new findings on `{repo_full_name}` PR #{pr_number}"]
    for finding in diff.get("secrets", {}).get("new", []):
        lines.append(
            f"- Secret: `{finding.get('path')}:{finding.get('line')}` ({finding.get('pattern')})"
        )
    for finding in diff.get("history_secrets", {}).get("new", []):
        lines.append(
            f"- History secret: `{finding.get('path')}` in {str(finding.get('commit'))[:8]}"
        )
    for finding in diff.get("vulnerabilities", {}).get("new", []):
        lines.append(
            f"- Vulnerability: {finding.get('package')} {finding.get('installed_version')} "
            f"({finding.get('advisory_id')})"
        )
    for finding in diff.get("layer_violations", {}).get("new", []):
        lines.append(f"- Layer violation: `{finding.get('from')}` -> `{finding.get('to')}`")
    return {"text": "\n".join(lines)}


def send_slack_alert(
    webhook_url: str,
    diff: dict,
    repo_full_name: str,
    pr_number: int,
    http_client: httpx.Client | None = None,
) -> None:
    if not _has_new_findings(diff):
        return
    client = http_client or httpx.Client()
    response = client.post(webhook_url, json=format_slack_message(diff, repo_full_name, pr_number))
    response.raise_for_status()
