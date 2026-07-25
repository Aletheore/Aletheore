"""Format Aletheore diff results as pull request comment bodies."""

COMMENT_MARKER = "<!-- aletheore-diff -->"


def _secret_suffix(finding: dict) -> str:
    if finding.get("accepted"):
        return " - accepted (in .aletheore.json baseline)"
    if finding.get("likely_placeholder"):
        return " - likely placeholder"
    return ""


def _bullets(title: str, entries: dict, formatter) -> list[str]:
    new = entries.get("new", [])
    resolved = entries.get("resolved", [])
    if not new and not resolved:
        return []

    lines = [f"**{title}**"]
    lines += [f"- 🆕 {formatter(item)}" for item in new]
    lines += [f"- ✅ resolved: {formatter(item)}" for item in resolved]
    lines.append("")
    return lines


def format_diff_comment(diff: dict) -> str:
    """Return the markdown body for an ``aletheore.history.compute_diff`` result."""

    body = [COMMENT_MARKER, "### 🔍 Aletheore evidence diff", ""]

    for caveat in diff.get("caveats", []):
        body.append(f"> ⚠️ {caveat}")
    if diff.get("caveats"):
        body.append("")

    body += _bullets(
        "Secrets",
        diff.get("secrets", {}),
        lambda f: f"`{f.get('path')}:{f.get('line')}` ({f.get('pattern')})"
        + _secret_suffix(f),
    )
    body += _bullets(
        "Secrets in git history",
        diff.get("history_secrets", {}),
        lambda f: f"`{f.get('path')}` in {str(f.get('commit'))[:8]} ({f.get('pattern')})"
        + _secret_suffix(f),
    )
    body += _bullets(
        "Dependency vulnerabilities",
        diff.get("vulnerabilities", {}),
        lambda f: (
            f"{f.get('package')} {f.get('installed_version')} - "
            f"{f.get('advisory_id')} ({f.get('ecosystem')})"
        ),
    )
    body += _bullets(
        "Layer violations",
        diff.get("layer_violations", {}),
        lambda f: f"`{f.get('from')}` -> `{f.get('to')}`: {f.get('reason')}",
    )

    deltas = diff.get("aggregate_deltas", {})
    if any(deltas.get(k, 0) for k in ("module_count", "dependency_graph_edge_count", "total_commits")):
        body.append("**Aggregate deltas**")
        body.append(f"- Modules: {deltas.get('module_count', 0):+d}")
        body.append(f"- Dependency graph edges: {deltas.get('dependency_graph_edge_count', 0):+d}")
        body.append(f"- Commits: {deltas.get('total_commits', 0)}")
        body.append("")

    if len(body) <= 3:
        body.append("No new secrets, vulnerabilities, or layer violations. ✅")

    return "\n".join(body)
