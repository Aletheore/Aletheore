from pathlib import Path

import pytest

from app_server.db import upsert_installation
from app_server.dismissed_findings import (
    dismiss_finding,
    filter_dismissed,
    finding_identity_key,
    get_dismissed_identity_keys,
    undismiss_finding,
)

SECRET_FINDING = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
VULN_FINDING = {"ecosystem": "PyPI", "package": "requests", "advisory_id": "GHSA-1"}

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def test_finding_identity_key_secret():
    assert finding_identity_key("secret", SECRET_FINDING) == "config.py\x1faws_access_key_id\x1fAKIA****...MNOP"


def test_finding_identity_key_vulnerability():
    assert finding_identity_key("vulnerability", VULN_FINDING) == "PyPI\x1frequests\x1fGHSA-1"


def test_finding_identity_key_raises_on_unknown_type():
    with pytest.raises(ValueError):
        finding_identity_key("layer_violation", {})


def test_filter_dismissed_removes_matching_findings():
    findings = [SECRET_FINDING, {"path": "other.py", "pattern": "aws_access_key_id", "match_preview": "x"}]
    dismissed_keys = {finding_identity_key("secret", SECRET_FINDING)}
    result = filter_dismissed(findings, "secret", dismissed_keys)
    assert result == [findings[1]]


def test_filter_dismissed_empty_set_returns_all_unchanged():
    findings = [SECRET_FINDING]
    result = filter_dismissed(findings, "secret", set())
    assert result == findings


def test_filter_dismissed_does_not_mutate_input_list():
    findings = [SECRET_FINDING]
    filter_dismissed(findings, "secret", {finding_identity_key("secret", SECRET_FINDING)})
    assert findings == [SECRET_FINDING]


def test_finding_identity_key_flash_review_llm_and_semantic_can_share_a_raw_key():
    # The raw identity_key string is NOT required to differ between the two
    # flash_review finding_types for the same file/line/issue - finding_type
    # is tracked as its own column (dismissed_findings' UNIQUE constraint is
    # on (installation_id, repo_full_name, finding_type, identity_key)) and
    # its own dict bucket in get_dismissed_identity_keys, so a raw-key
    # collision across the two types is harmless: dismissing the LLM
    # finding never marks the semantic one (or vice versa) as dismissed.
    # See test_get_dismissed_identity_keys_groups_rows_by_finding_type for
    # the actual scoping guarantee this depends on.
    finding = {"file": "a.py", "line": 10, "issue": "removed the null check"}
    llm_key = finding_identity_key("flash_review_llm", finding)
    semantic_key = finding_identity_key("flash_review_semantic", finding)
    assert llm_key == semantic_key


def test_finding_identity_key_flash_review_survives_word_reordering():
    reworded = {"file": "a.py", "line": 10, "issue": "the null check was removed"}
    original = {"file": "a.py", "line": 10, "issue": "removed the null check"}
    assert finding_identity_key("flash_review_llm", reworded) == finding_identity_key(
        "flash_review_llm", original
    )


def test_finding_identity_key_flash_review_survives_punctuation_and_case_changes():
    a = {"file": "a.py", "line": 10, "issue": "Removed the null check!"}
    b = {"file": "a.py", "line": 10, "issue": "removed the null check"}
    assert finding_identity_key("flash_review_llm", a) == finding_identity_key("flash_review_llm", b)


def test_finding_identity_key_flash_review_different_line_differs():
    a = {"file": "a.py", "line": 10, "issue": "removed the null check"}
    b = {"file": "a.py", "line": 11, "issue": "removed the null check"}
    assert finding_identity_key("flash_review_llm", a) != finding_identity_key("flash_review_llm", b)


def test_finding_identity_key_flash_review_different_file_differs():
    a = {"file": "a.py", "line": 10, "issue": "removed the null check"}
    b = {"file": "b.py", "line": 10, "issue": "removed the null check"}
    assert finding_identity_key("flash_review_llm", a) != finding_identity_key("flash_review_llm", b)


def test_finding_identity_key_flash_review_genuinely_different_issue_differs():
    a = {"file": "a.py", "line": 10, "issue": "removed the null check"}
    b = {"file": "a.py", "line": 10, "issue": "introduced a SQL injection risk"}
    assert finding_identity_key("flash_review_llm", a) != finding_identity_key("flash_review_llm", b)


class _FakePool:
    """Minimal stand-in for asyncpg.Pool - get_dismissed_identity_keys only
    ever calls .fetch() on it, so this only needs to satisfy that one call
    with rows shaped the way asyncpg would return them (mapping-like, keyed
    by column name)."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def fetch(self, query, *args):
        return self._rows


@pytest.mark.asyncio
async def test_get_dismissed_identity_keys_seeds_all_four_types_with_zero_rows():
    # Always seeds all four known finding_type keys even with zero rows, so
    # a caller building a dict comprehension over its result (e.g. jobs.py's
    # dismissed["flash_review_llm"]) never KeyErrors on an installation
    # with no dismissals of that type yet.
    result = await get_dismissed_identity_keys(_FakePool([]), 1, "owner/repo")
    assert result == {
        "secret": set(),
        "vulnerability": set(),
        "flash_review_llm": set(),
        "flash_review_semantic": set(),
    }


@pytest.mark.asyncio
async def test_get_dismissed_identity_keys_groups_rows_by_finding_type():
    rows = [
        {"finding_type": "flash_review_llm", "identity_key": "k1"},
        {"finding_type": "flash_review_semantic", "identity_key": "k2"},
        {"finding_type": "secret", "identity_key": "k3"},
    ]
    result = await get_dismissed_identity_keys(_FakePool(rows), 1, "owner/repo")
    assert result["flash_review_llm"] == {"k1"}
    assert result["flash_review_semantic"] == {"k2"}
    assert result["secret"] == {"k3"}
    assert result["vulnerability"] == set()


@pytest.mark.asyncio
async def test_dismiss_finding_then_get_dismissed_identity_keys(pool):
    await upsert_installation(pool, 1, "octocat")

    await dismiss_finding(pool, 1, "octocat/repo", "secret", SECRET_FINDING, "octocat")

    dismissed = await get_dismissed_identity_keys(pool, 1, "octocat/repo")
    assert finding_identity_key("secret", SECRET_FINDING) in dismissed["secret"]
    assert dismissed["vulnerability"] == set()


@pytest.mark.asyncio
async def test_dismiss_finding_is_idempotent(pool):
    await upsert_installation(pool, 1, "octocat")

    await dismiss_finding(pool, 1, "octocat/repo", "secret", SECRET_FINDING, "octocat")
    await dismiss_finding(pool, 1, "octocat/repo", "secret", SECRET_FINDING, "octocat")

    dismissed = await get_dismissed_identity_keys(pool, 1, "octocat/repo")
    assert len(dismissed["secret"]) == 1


@pytest.mark.asyncio
async def test_dismiss_finding_stores_reason(pool):
    await upsert_installation(pool, 1, "octocat")

    await dismiss_finding(pool, 1, "octocat/repo", "secret", SECRET_FINDING, "octocat", reason="test fixture")

    row = await pool.fetchrow("SELECT reason, dismissed_by FROM dismissed_findings WHERE installation_id = $1", 1)
    assert row["reason"] == "test fixture"
    assert row["dismissed_by"] == "octocat"


@pytest.mark.asyncio
async def test_undismiss_finding_removes_it(pool):
    await upsert_installation(pool, 1, "octocat")
    await dismiss_finding(pool, 1, "octocat/repo", "vulnerability", VULN_FINDING, "octocat")

    await undismiss_finding(pool, 1, "octocat/repo", "vulnerability", VULN_FINDING)

    dismissed = await get_dismissed_identity_keys(pool, 1, "octocat/repo")
    assert dismissed["vulnerability"] == set()


@pytest.mark.asyncio
async def test_undismiss_finding_that_was_never_dismissed_is_a_no_op(pool):
    await upsert_installation(pool, 1, "octocat")

    await undismiss_finding(pool, 1, "octocat/repo", "secret", SECRET_FINDING)

    dismissed = await get_dismissed_identity_keys(pool, 1, "octocat/repo")
    assert dismissed["secret"] == set()


@pytest.mark.asyncio
async def test_dismissed_findings_are_scoped_per_repo(pool):
    await upsert_installation(pool, 1, "octocat")
    await dismiss_finding(pool, 1, "octocat/repo-a", "secret", SECRET_FINDING, "octocat")

    dismissed = await get_dismissed_identity_keys(pool, 1, "octocat/repo-b")
    assert dismissed["secret"] == set()


@pytest.mark.asyncio
async def test_migration_045_purges_legacy_format_secret_dismissals_only(pool):
    # SECRET_FINDING's match_preview ("AKIA****...MNOP") is the pre-CLI-3
    # format - a dismissal keyed on it can never match a finding from the
    # current scanner (which always produces a sha256:-prefixed preview), so
    # it's permanently dead weight migration 045 is meant to clear out.
    await upsert_installation(pool, 1, "octocat")
    hash_format_finding = {
        "path": "config.py",
        "pattern": "aws_access_key_id",
        "match_preview": "sha256:aaaaaaaaaaaa",
    }
    await dismiss_finding(pool, 1, "octocat/repo", "secret", SECRET_FINDING, "octocat")
    await dismiss_finding(pool, 1, "octocat/repo", "secret", hash_format_finding, "octocat")
    await dismiss_finding(pool, 1, "octocat/repo", "vulnerability", VULN_FINDING, "octocat")

    migration_sql = (MIGRATIONS_DIR / "045_purge_legacy_secret_dismissals.sql").read_text()
    await pool.execute(migration_sql)

    dismissed = await get_dismissed_identity_keys(pool, 1, "octocat/repo")
    assert dismissed["secret"] == {finding_identity_key("secret", hash_format_finding)}
    # Vulnerability dismissals aren't touched - the legacy-format concern is
    # specific to secrets' match_preview, which vulnerabilities don't have.
    assert dismissed["vulnerability"] == {finding_identity_key("vulnerability", VULN_FINDING)}
