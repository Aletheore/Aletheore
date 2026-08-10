import asyncpg


def finding_identity_key(finding_type: str, finding: dict) -> str:
    """Canonical identity string for a finding, used both to store a
    dismissal and to check whether a fresh finding matches one already
    dismissed. Uses the same field tuples history.py already uses for
    new/resolved diffing: (path, pattern, match_preview) for secrets,
    (ecosystem, package, advisory_id) for vulnerabilities. \x1f (unit
    separator) joins fields - not a character any of these fields would
    plausibly contain.

    For secrets, match_preview embeds this server's only knowledge of "which
    credential" - the raw value never leaves the scan (see
    src/aletheore/secrets.py's _redact), so if that preview's format ever
    changes again, a dismissal keyed on the old format becomes permanently
    unmatchable here, unlike the CLI's own accepted_secrets baseline, which
    can recompute a legacy-format preview from the value it still has on
    hand. There's no dual-check to add on this side - see migration 045 for
    the cleanup this fired once already, and repeat that pattern (a
    migration that purges now-unreachable rows, not a data migration) if the
    preview format changes again.
    """
    if finding_type == "secret":
        return f"{finding['path']}\x1f{finding['pattern']}\x1f{finding['match_preview']}"
    if finding_type == "vulnerability":
        return f"{finding['ecosystem']}\x1f{finding['package']}\x1f{finding['advisory_id']}"
    raise ValueError(f"unknown finding_type: {finding_type!r}")


def filter_dismissed(findings: list[dict], finding_type: str, dismissed_keys: set[str]) -> list[dict]:
    """Drops already-dismissed findings from a list - used by the PR-scan
    job on a diff's "new" findings before building the PR comment. Returns
    a new list; never mutates the one passed in.
    """
    return [f for f in findings if finding_identity_key(finding_type, f) not in dismissed_keys]


async def dismiss_finding(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    finding_type: str,
    finding: dict,
    dismissed_by: str,
    reason: str | None = None,
) -> None:
    identity_key = finding_identity_key(finding_type, finding)
    await pool.execute(
        """
        INSERT INTO dismissed_findings
            (installation_id, repo_full_name, finding_type, identity_key, dismissed_by, reason)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (installation_id, repo_full_name, finding_type, identity_key) DO NOTHING
        """,
        installation_id,
        repo_full_name,
        finding_type,
        identity_key,
        dismissed_by,
        reason,
    )


async def undismiss_finding(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    finding_type: str,
    finding: dict,
) -> None:
    identity_key = finding_identity_key(finding_type, finding)
    await pool.execute(
        """
        DELETE FROM dismissed_findings
        WHERE installation_id = $1 AND repo_full_name = $2
          AND finding_type = $3 AND identity_key = $4
        """,
        installation_id,
        repo_full_name,
        finding_type,
        identity_key,
    )


async def get_dismissed_identity_keys(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str
) -> dict[str, set[str]]:
    rows = await pool.fetch(
        """
        SELECT finding_type, identity_key FROM dismissed_findings
        WHERE installation_id = $1 AND repo_full_name = $2
        """,
        installation_id,
        repo_full_name,
    )
    result: dict[str, set[str]] = {"secret": set(), "vulnerability": set()}
    for row in rows:
        result[row["finding_type"]].add(row["identity_key"])
    return result
