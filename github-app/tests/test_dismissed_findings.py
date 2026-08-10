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
