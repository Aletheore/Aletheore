import hashlib
import re

import asyncpg

# Unlike a secret (path+pattern+match_preview) or a vulnerability
# (ecosystem+package+advisory_id), a Flash Review finding has no natural
# unique id - "issue" is free-text a model wrote, and the same underlying
# bug can come back reworded (different phrasing, different word order)
# across two independent generations of the same diff. Hashing the raw
# text would treat any rewording as a brand-new finding, silently
# resurrecting something a user already dismissed. Hashing file+line alone
# would go too far the other way: two genuinely different bugs that happen
# to land on the same line across two unrelated pushes (rare, but not
# impossible - a fixed bug's line later hosting an unrelated new one) would
# collapse into one identity.
#
# The middle ground here: normalize the issue text (lowercase, strip
# punctuation, drop a short stopword list, collapse to a set of the
# remaining significant words) and hash that set rather than the raw
# string. A set - not a sequence - so word-order changes ("the null check
# was removed" vs "removed the null check") don't change the key, while
# genuinely different wording (different nouns/verbs describing a
# different problem) does. This is a considered design choice, not a
# verified one: there is no real corpus of "same bug, reworded across two
# independent re-reviews of the same diff" available to test it against,
# so its resilience to real model rewording is untested past the
# synthetic cases in test_dismissed_findings.py. Line is still exact-match
# (consistent with how secret/vulnerability identity already works, no
# fuzzy tolerance there either) - a finding that shifts by even one line
# because of an unrelated edit above it gets a fresh identity, a known,
# accepted limitation shared with the other two finding types.
_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have if in into is it its "
    "of on or that the this to was were will with".split()
)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _issue_fingerprint(issue: str) -> str:
    words = {w for w in _WORD_RE.findall(issue.lower()) if w not in _STOPWORDS}
    normalized = "\x1e".join(sorted(words))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


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

    flash_review_llm and flash_review_semantic (Flash Review's model-
    generated and deterministic findings respectively - kept as two
    distinct types rather than one, so dismissal-rate comparisons between
    them stay possible) use file+line+_issue_fingerprint(issue) - see that
    function's docstring for why a fingerprint rather than the raw text.
    """
    if finding_type == "secret":
        return f"{finding['path']}\x1f{finding['pattern']}\x1f{finding['match_preview']}"
    if finding_type == "vulnerability":
        return f"{finding['ecosystem']}\x1f{finding['package']}\x1f{finding['advisory_id']}"
    if finding_type in ("flash_review_llm", "flash_review_semantic"):
        return f"{finding['file']}\x1f{finding['line']}\x1f{_issue_fingerprint(finding['issue'])}"
    raise ValueError(f"unknown finding_type: {finding_type!r}")


def filter_dismissed(findings: list[dict], finding_type: str, dismissed_keys: set[str]) -> list[dict]:
    """Drops already-dismissed findings from a list - used by the PR-scan
    job on a diff's "new" findings before building the PR comment. Returns
    a new list; never mutates the one passed in.
    """
    return [f for f in findings if finding_identity_key(finding_type, f) not in dismissed_keys]


async def dismiss_finding_by_identity_key(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    finding_type: str,
    identity_key: str,
    dismissed_by: str,
    reason: str | None = None,
) -> None:
    """Same INSERT as dismiss_finding, but takes an already-known
    identity_key directly instead of a finding dict to recompute it from.

    Exists for the reply-based dismissal webhook
    (webhooks/pull_request_review_comment.py): a reply's payload only
    carries in_reply_to_id, resolved (via
    get_flash_review_finding_comment_by_github_id) straight to a stored
    identity_key - there is no original finding dict (file/line/issue) to
    reconstruct at that point, and fabricating one just to feed it back
    through finding_identity_key would be pointless indirection for a
    value already on hand."""
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
    await dismiss_finding_by_identity_key(
        pool, installation_id, repo_full_name, finding_type, identity_key, dismissed_by, reason
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
    result: dict[str, set[str]] = {
        "secret": set(),
        "vulnerability": set(),
        "flash_review_llm": set(),
        "flash_review_semantic": set(),
    }
    for row in rows:
        result[row["finding_type"]].add(row["identity_key"])
    return result
