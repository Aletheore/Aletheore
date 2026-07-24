import httpx


def _format_list(value) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value[:5])
    return str(value)


def _format_evidence_context(evidence_resolution: dict | None) -> str:
    if not evidence_resolution:
        return ""
    lines = []
    symbol = evidence_resolution.get("symbol")
    if symbol:
        lines.append(f"Symbol: `{symbol}`")
    owner = _format_list(evidence_resolution.get("owner"))
    if owner:
        lines.append(f"Owner: {owner}")
    commit = evidence_resolution.get("commit")
    if isinstance(commit, dict) and commit.get("sha"):
        subject = f" - {commit['subject']}" if commit.get("subject") else ""
        lines.append(f"Recent commit: `{commit['sha'][:8]}`{subject}")
    dependency = _format_list(evidence_resolution.get("dependency"))
    if dependency:
        lines.append(f"Dependencies: {dependency}")
    risks = evidence_resolution.get("risk") or []
    if risks:
        summaries = [
            risk.get("summary")
            for risk in risks[:3]
            if isinstance(risk, dict) and risk.get("summary")
        ]
        if summaries:
            lines.append(f"Risk: {'; '.join(summaries)}")
    return "" if not lines else "\n" + "\n".join(lines)


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


def format_reachability_alert(
    repo_full_name: str,
    method: str,
    path: str,
    source_file: str | None,
    source_line: int | None,
    now_reachable: bool,
    evidence_resolution: dict | None = None,
) -> dict:
    location = (
        f" - handled by {source_file}:{source_line}"
        if source_file and source_line is not None
        else ""
    )
    if now_reachable:
        text = (
            f"*Aletheore*: endpoint recovered on `{repo_full_name}`\n"
            f"`{method} {path}` is reachable again{location}"
            f"{_format_evidence_context(evidence_resolution)}"
        )
    else:
        text = (
            f"*Aletheore*: endpoint down on `{repo_full_name}`\n"
            f"`{method} {path}` is unreachable (was reachable as of the last check){location}"
            f"{_format_evidence_context(evidence_resolution)}"
        )
    return {"text": text}


def format_latency_alert(
    repo_full_name: str,
    method: str,
    path: str,
    source_file: str | None,
    source_line: int | None,
    latency_ms: float,
    threshold_ms: int,
    now_over: bool,
    evidence_resolution: dict | None = None,
) -> dict:
    location = (
        f" - handled by {source_file}:{source_line}"
        if source_file and source_line is not None
        else ""
    )
    if now_over:
        text = (
            f"*Aletheore*: endpoint slow on `{repo_full_name}`\n"
            f"`{method} {path}` took {latency_ms:.0f}ms (threshold: {threshold_ms}ms){location}"
            f"{_format_evidence_context(evidence_resolution)}"
        )
    else:
        text = (
            f"*Aletheore*: endpoint back under threshold on `{repo_full_name}`\n"
            f"`{method} {path}` took {latency_ms:.0f}ms (threshold: {threshold_ms}ms){location}"
            f"{_format_evidence_context(evidence_resolution)}"
        )
    return {"text": text}


def format_shape_change_alert(
    repo_full_name: str,
    method: str,
    path: str,
    source_file: str | None,
    source_line: int | None,
    prior_shape: list[str],
    current_shape: list[str],
    evidence_resolution: dict | None = None,
) -> dict:
    location = (
        f" - handled by {source_file}:{source_line}"
        if source_file and source_line is not None
        else ""
    )
    added = sorted(set(current_shape) - set(prior_shape))
    dropped = sorted(set(prior_shape) - set(current_shape))
    changes = []
    if added:
        changes.append(f"added keys: {', '.join(added)}")
    if dropped:
        changes.append(f"dropped keys: {', '.join(dropped)}")
    change_summary = "; ".join(changes) if changes else "key order changed"
    text = (
        f"*Aletheore*: response shape changed on `{repo_full_name}`\n"
        f"`{method} {path}` {change_summary}{location}"
        f"{_format_evidence_context(evidence_resolution)}"
    )
    return {"text": text}


def send_health_alert(
    webhook_url: str,
    message: dict,
    http_client: httpx.Client | None = None,
) -> None:
    client = http_client or httpx.Client()
    response = client.post(webhook_url, json=message)
    response.raise_for_status()
