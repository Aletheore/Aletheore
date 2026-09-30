"""Format Aletheore diff results as pull request comment bodies.

Demo-only comment: exercising the new Mermaid change diagram (#881) end to
end on a real PR against this same repo.
"""

import re

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


def _code_span(text: str) -> str:
    """Wrap `text` in Markdown inline-code backticks, escaping a literal
    backtick in the content by widening the fence past the longest run of
    backticks the content itself contains (GFM's own escaping rule) -
    otherwise a filename containing a backtick would prematurely close
    the code span instead of being shown as part of it.

    Padding spaces around the content are added only when the content
    itself starts or ends with a backtick - GFM's actual rule, and the
    only case where the fence would otherwise visually merge with the
    content. Real gap found by Flash Review on this PR: padding
    unconditionally (whenever any backtick appears, even mid-content)
    triggers GFM's own space-stripping rule (one leading/trailing space
    silently removed) for the common case where the padding was never
    structurally required in the first place.
    """
    if "`" not in text:
        return f"`{text}`"
    runs = re.findall(r"`+", text)
    fence = "`" * (max(len(r) for r in runs) + 1)
    if text.startswith("`") or text.endswith("`"):
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def format_file_overview(rows: list[dict], possibly_capped: bool = False) -> str:
    """Render `history.summarize_file_changes`'s per-file rows (with a
    caller-merged "dependents_count" key - see `blast_radius_summary.
    count_direct_dependents`) as the leading section of the PR evidence-diff
    comment. Empty string when `rows` is empty (nothing GitHub reports as
    changed - `run_pr_scan_job` never calls this with an empty list in
    practice, but an empty result must never fabricate a section header
    over nothing). Otherwise always non-empty: this section is fully
    deterministic and posts on every run, regardless of tier or whether
    Flash Review ran at all.

    `possibly_capped` - True when `rows` came from a GitHub compare that
    itself hit its own file-count cap (see `github_api.
    GITHUB_COMPARE_FILES_HARD_CAP`) - past that cap, files beyond it were
    never returned at all, so "+N more" would understate the true total
    rather than merely truncate a known one; the truncation line hedges
    instead of presenting a number that reads as exact.
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
        parts = [_code_span(row["path"])]
        if row.get("previous_path"):
            parts.append(f"(renamed from {_code_span(row['previous_path'])})")
        phrase = _symbol_change_phrase(row)
        # The raw status word is skipped when the phrase (or the rename
        # annotation just above) already says the same thing - "removed ·
        # removed" and "renamed ... · renamed" are redundant on every PR
        # that deletes or renames a file. A status whose phrase came back
        # empty (e.g. an added non-code file, which has no module data to
        # phrase at all) keeps the raw word - it's the only signal left.
        status_redundant = (
            (row["status"] == "removed" and phrase == "removed")
            or (row["status"] == "added" and phrase.startswith("new file"))
            or (row["status"] == "renamed" and row.get("previous_path"))
        )
        if not status_redundant:
            parts.append(row["status"])
        if row["additions"] or row["deletions"]:
            parts.append(f"+{row['additions']}/-{row['deletions']}")
        if phrase:
            parts.append(phrase)
        dependents = row.get("dependents_count", 0)
        if dependents:
            parts.append(f"{dependents} other dependent{'s' if dependents != 1 else ''}")
        lines.append("- " + " · ".join(parts))
    if len(rows) > FILE_OVERVIEW_TRUNCATION_CAP:
        overflow = len(rows) - FILE_OVERVIEW_TRUNCATION_CAP
        hedge = "or more" if possibly_capped else "more"
        lines.append(f"- +{overflow} {hedge} changed file(s)")
    lines.append("")
    return "\n".join(lines)


def format_diff_comment(diff: dict, file_overview: str = "", change_diagram: str = "") -> str:
    """Return the markdown body for an ``aletheore.history.compute_diff`` result.

    `file_overview` is Piece B's per-file "what changed" section (see
    `format_file_overview`) - prepended, when non-empty, right after the
    header and before everything else, per the PR-comment-presentation
    design's "Decided" note: one leading section on this same comment,
    not a new comment type.

    `change_diagram` (see `scan_worker.blast_radius_summary.
    build_change_diagram`) is a fenced ```mermaid block GitHub renders
    natively - prepended, when non-empty, before `file_overview`: the
    diagram gives the shape of what changed, the file overview right
    below it gives the exact detail.
    """

    body = [COMMENT_MARKER, "### 🔍 Aletheore evidence diff", ""]

    if change_diagram:
        body.append(change_diagram)

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
