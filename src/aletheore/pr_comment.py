"""Format Aletheore diff results as pull request comment bodies."""

COMMENT_MARKER = "<!-- aletheore-diff -->"

FILE_OVERVIEW_TRUNCATION_CAP = 20


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


def _symbol_change_phrase(row: dict) -> str:
    if row["status"] == "removed":
        return "removed"
    if not row["has_module_data"]:
        return ""
    if row["status"] == "added":
        added = len(row["functions_added"]) + len(row["classes_added"])
        if not added:
            return "new file"
        return f"new file, {added} symbol{'s' if added != 1 else ''}"

    parts = []
    if row["functions_added"]:
        n = len(row["functions_added"])
        parts.append(f"+{n} function{'s' if n != 1 else ''}")
    if row["functions_removed"]:
        n = len(row["functions_removed"])
        parts.append(f"-{n} function{'s' if n != 1 else ''}")
    if row["classes_added"]:
        n = len(row["classes_added"])
        parts.append(f"+{n} class{'es' if n != 1 else ''}")
    if row["classes_removed"]:
        n = len(row["classes_removed"])
        parts.append(f"-{n} class{'es' if n != 1 else ''}")
    return ", ".join(parts)


def format_file_overview(rows: list[dict]) -> str:
    """Render `history.summarize_file_changes`'s per-file rows (with a
    caller-merged "dependents_count" key - see `blast_radius_summary.
    compute_blast_radius`) as the leading section of the PR evidence-diff
    comment. Empty string when `rows` is empty (nothing GitHub reports as
    changed - `run_pr_scan_job` never calls this with an empty list in
    practice, but an empty result must never fabricate a section header
    over nothing). Otherwise always non-empty: this section is fully
    deterministic and posts on every run, regardless of tier or whether
    Flash Review ran at all.
    """
    if not rows:
        return ""
    lines = [
        "**What changed**",
        "_Deterministic, computed from this commit's real scan and import graph - "
        "posted on every run, whether or not Flash Review found anything._",
        "",
    ]
    shown = rows[:FILE_OVERVIEW_TRUNCATION_CAP]
    for row in shown:
        parts = [f"`{row['path']}`"]
        if row.get("previous_path"):
            parts.append(f"(renamed from `{row['previous_path']}`)")
        parts.append(row["status"])
        if row["additions"] or row["deletions"]:
            parts.append(f"+{row['additions']}/-{row['deletions']}")
        phrase = _symbol_change_phrase(row)
        if phrase:
            parts.append(phrase)
        dependents = row.get("dependents_count", 0)
        if dependents:
            parts.append(f"{dependents} dependent{'s' if dependents != 1 else ''}")
        lines.append("- " + " · ".join(parts))
    if len(rows) > FILE_OVERVIEW_TRUNCATION_CAP:
        lines.append(f"- +{len(rows) - FILE_OVERVIEW_TRUNCATION_CAP} more changed file(s)")
    lines.append("")
    return "\n".join(lines)


def format_diff_comment(diff: dict, file_overview: str = "") -> str:
    """Return the markdown body for an ``aletheore.history.compute_diff`` result.

    `file_overview` is Piece B's per-file "what changed" section (see
    `format_file_overview`) - prepended, when non-empty, right after the
    header and before everything else, per the PR-comment-presentation
    design's "Decided" note: one leading section on this same comment,
    not a new comment type.
    """

    body = [COMMENT_MARKER, "### 🔍 Aletheore evidence diff", ""]

    if file_overview:
        body.append(file_overview)

    for caveat in diff.get("caveats", []):
        body.append(f"> ⚠️ {caveat}")
    if diff.get("caveats"):
        body.append("")

    # Snapshot taken here, not a hardcoded "3" - the file-overview section
    # above (and caveats, just above this line) are real content that
    # existed before Piece B too, and must not count toward "nothing new
    # to report" below. A magic-number length check would silently stop
    # firing the "No new secrets..." fallback on every PR once the file
    # overview became unconditional.
    pre_findings_len = len(body)

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

    if len(body) <= pre_findings_len:
        body.append("No new secrets, vulnerabilities, or layer violations. ✅")

    return "\n".join(body)
