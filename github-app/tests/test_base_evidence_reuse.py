"""Reusing the stored evidence of a PR's base commit instead of scanning the base again.

A PR scan scans the base commit and the head commit in full, then diffs the two. The base is usually the tip of
the default branch, which the push scan already scanned and stored (evidence is tagged with the exact commit it
describes). Reusing that skips the base checkout and half of the scan work. Stored evidence can be stale in one
way a fresh scan cannot: dependency advisories are looked up at scan time, so an old base would make an advisory
published since read as "introduced by this PR". Hence the guards (exact commit, recent, same scanner version) and
a shadow mode that compares both diffs in production before anything depends on the reuse.
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from scan_worker import jobs as jobs_module
from scan_worker.jobs import (
    BASE_EVIDENCE_MAX_AGE_SECONDS,
    _base_evidence_reuse_mode,
    _diff_sections_that_differ,
    _insert_history,
    _reusable_base_evidence,
    _reusable_base_evidence_with_reason,
    _scan_config_fingerprint,
    run_pr_scan_job,
)

BASE_SHA = "a" * 40


def _stored(age: timedelta = timedelta(hours=1), **overrides) -> dict:
    evidence = {
        "_scan_head_sha": BASE_SHA,
        "scanned_at": (datetime.now(timezone.utc) - age).isoformat(),
        "aletheore_version": "x",
        "_scan_config": _scan_config_fingerprint(),
    }
    evidence.update(overrides)
    return evidence


# --- the mode switch -------------------------------------------------------------------------------------------


def test_base_evidence_reuse_is_off_unless_an_operator_turns_it_on(monkeypatch):
    monkeypatch.delenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", raising=False)
    assert _base_evidence_reuse_mode() == "off"


@pytest.mark.parametrize("value, expected", [("1", "on"), ("true", "on"), (" On ", "on"), ("shadow", "shadow"), ("SHADOW", "shadow"), ("0", "off"), ("", "off")])
def test_base_evidence_reuse_mode_accepts_the_usual_spellings(monkeypatch, value, expected):
    monkeypatch.setenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", value)
    assert _base_evidence_reuse_mode() == expected


def test_an_unrecognised_reuse_setting_stays_off_and_says_so(monkeypatch, caplog):
    monkeypatch.setenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", "enabled")  # a plausible typo
    with caplog.at_level(logging.WARNING, logger="scan_worker.jobs"):
        assert _base_evidence_reuse_mode() == "off"
    assert "ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE" in caplog.text and "not a recognised value" in caplog.text


# --- the guards ------------------------------------------------------------------------------------------------


def _lookup_returns(monkeypatch, evidence):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: evidence)


def test_recent_evidence_for_exactly_the_base_commit_is_reusable(monkeypatch):
    stored = _stored()
    _lookup_returns(monkeypatch, stored)
    assert _reusable_base_evidence(1, "o/r", BASE_SHA) == stored


def test_nothing_stored_for_the_base_commit_means_no_reuse(monkeypatch):
    _lookup_returns(monkeypatch, None)
    assert _reusable_base_evidence(1, "o/r", BASE_SHA) is None


def test_evidence_older_than_the_limit_is_not_reused(monkeypatch):
    # The advisory lookup happens at scan time: a base scanned days ago would show every advisory published
    # since as "new in this PR".
    _lookup_returns(monkeypatch, _stored(age=timedelta(seconds=BASE_EVIDENCE_MAX_AGE_SECONDS + 60)))
    assert _reusable_base_evidence(1, "o/r", BASE_SHA) is None


@pytest.mark.parametrize("scanned_at", [None, "", "not-a-date", 12345])
def test_evidence_with_no_usable_scan_time_is_not_reused(monkeypatch, scanned_at):
    _lookup_returns(monkeypatch, _stored(scanned_at=scanned_at))
    assert _reusable_base_evidence(1, "o/r", BASE_SHA) is None


def test_evidence_tagged_with_a_different_commit_is_not_reused(monkeypatch):
    _lookup_returns(monkeypatch, _stored(_scan_head_sha="b" * 40))
    assert _reusable_base_evidence(1, "o/r", BASE_SHA) is None


def test_evidence_scanned_under_a_different_scan_configuration_is_not_reused(monkeypatch):
    # Switching an operator setting that changes what a scan reports (semgrep) after the base was stored would
    # make every finding the new setting adds read as introduced by the PR: the stored base has none of them.
    # No version bump marks that, so the configuration a scan ran with is recorded and compared.
    _lookup_returns(monkeypatch, _stored(_scan_config={"semgrep": True}))
    evidence, reason = _reusable_base_evidence_with_reason(1, "o/r", BASE_SHA)
    assert evidence is None and reason == "scan_config_changed"


def test_evidence_with_no_recorded_scan_configuration_is_not_reused(monkeypatch):
    # Stored before the configuration was recorded: there is no way to tell whether it still matches.
    stored = _stored()
    del stored["_scan_config"]
    _lookup_returns(monkeypatch, stored)
    evidence, reason = _reusable_base_evidence_with_reason(1, "o/r", BASE_SHA)
    assert evidence is None and reason == "scan_config_unknown"


def test_the_scan_configuration_follows_the_semgrep_operator_setting(monkeypatch):
    monkeypatch.delenv("ALETHEORE_HOSTED_ENABLE_SEMGREP", raising=False)
    assert _scan_config_fingerprint() == {"semgrep": False}
    monkeypatch.setenv("ALETHEORE_HOSTED_ENABLE_SEMGREP", "1")
    assert _scan_config_fingerprint() == {"semgrep": True}


def test_every_stored_scan_records_the_configuration_it_ran_under(monkeypatch):
    stored = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.insert_repo_history", lambda *a, **k: stored.append(a[4]) or 1)
    original = {"aletheore_version": "x"}

    _insert_history(1, "o/r", original, head_sha=BASE_SHA)

    assert stored[0]["_scan_config"] == _scan_config_fingerprint()
    assert "_scan_config" not in original, "the caller's evidence is not modified"


def test_a_failing_lookup_means_a_fresh_scan_not_a_failed_job(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    def _boom(*a, **k):
        raise ConnectionError("database unavailable")

    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", _boom)
    assert _reusable_base_evidence(1, "o/r", BASE_SHA) is None


# --- comparing the two diffs -----------------------------------------------------------------------------------


def test_diff_sections_that_differ_names_only_the_sections_that_changed():
    a = {"secrets": {"new": []}, "vulnerabilities": {"new": [1]}, "stats": {"total_commits": 3}}
    b = {"secrets": {"new": []}, "vulnerabilities": {"new": [1, 2]}, "stats": {"total_commits": 3}}
    assert _diff_sections_that_differ(a, b) == ["vulnerabilities"]
    assert _diff_sections_that_differ(a, a) == []


def test_diff_sections_that_differ_reports_a_section_present_on_one_side_only():
    assert _diff_sections_that_differ({"a": 1}, {"a": 1, "b": 2}) == ["b"]


# --- the real PR scan job, on a real git repository ----------------------------------------------------------


@contextmanager
def _noop_lock(*args, **kwargs):
    yield


def _patch_pr_scan(monkeypatch, bare_path, tmp_path):
    # The same stand-ins tests/test_jobs.py applies to every PR scan test (autouse there): the per-repo checkout
    # lock opens a real Postgres connection, and the "is the PR still open" check is a real GitHub call.
    monkeypatch.setattr("scan_worker.jobs.repo_checkout_lock", _noop_lock)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_is_open", lambda *a, **k: True)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("ALETHEORE_REPO_CHECKOUT_ROOT", str(tmp_path / "checkouts"))
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set(), "static_analysis": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_update_live_wiki", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._build_unchanged_scan_cache", lambda *a, **k: None)


class _Spies:
    def __init__(self, monkeypatch):
        self.scans, self.base_checkouts, self.diffs = [], [], []
        real_scan = jobs_module._run_scan
        real_prepare_base = jobs_module._prepare_base_checkout
        real_diff = jobs_module.compute_diff

        def scan(repo_dir, *a, **k):
            self.scans.append(repo_dir.name)
            return real_scan(repo_dir, *a, **k)

        def prepare_base(*a, **k):
            self.base_checkouts.append(a[1])
            return real_prepare_base(*a, **k)

        def diff(old, new, *a, **k):
            result = real_diff(old, new, *a, **k)
            self.diffs.append({"old": old, "new": new, "result": result})
            return result

        monkeypatch.setattr("scan_worker.jobs._run_scan", scan)
        monkeypatch.setattr("scan_worker.jobs._prepare_base_checkout", prepare_base)
        monkeypatch.setattr("scan_worker.jobs.compute_diff", diff)


def _run_job(base_sha, head_sha):
    run_pr_scan_job(installation_id=1, repo_full_name="octocat/hello-world", pr_number=7, base_sha=base_sha, head_sha=head_sha)


def _fresh_base_evidence(bare_repo_with_two_commits, monkeypatch, tmp_path):
    """The evidence a normal (mode off) PR scan computes for the base commit, tagged the way a stored scan is."""
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    _patch_pr_scan(monkeypatch, bare_path, tmp_path)
    monkeypatch.delenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", raising=False)
    spies = _Spies(monkeypatch)
    _run_job(base_sha, head_sha)
    stored = dict(spies.diffs[0]["old"])
    stored["_scan_head_sha"] = base_sha
    stored["scanned_at"] = datetime.now(timezone.utc).isoformat()
    stored["_scan_config"] = _scan_config_fingerprint()
    return stored, spies.diffs[0]["result"]


def test_with_reuse_on_the_base_checkout_and_scan_are_skipped_and_the_diff_is_unchanged(
    bare_repo_with_two_commits, monkeypatch, tmp_path
):
    stored, expected_diff = _fresh_base_evidence(bare_repo_with_two_commits, monkeypatch, tmp_path)
    _bare, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", "on")
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: stored)
    spies = _Spies(monkeypatch)

    _run_job(base_sha, head_sha)

    assert spies.base_checkouts == [], "the base commit must not be checked out when its evidence is reused"
    assert spies.scans == ["head"] or len(spies.scans) == 1, "only the head is scanned"
    assert spies.diffs[0]["old"] is stored
    assert spies.diffs[0]["result"] == expected_diff


def test_with_reuse_on_but_nothing_stored_the_base_is_scanned_as_before(bare_repo_with_two_commits, monkeypatch, tmp_path):
    _stored_evidence, expected_diff = _fresh_base_evidence(bare_repo_with_two_commits, monkeypatch, tmp_path)
    _bare, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", "on")
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: None)
    spies = _Spies(monkeypatch)

    _run_job(base_sha, head_sha)

    assert len(spies.base_checkouts) == 1
    assert len(spies.scans) == 2
    assert spies.diffs[0]["result"] == expected_diff


def test_in_shadow_mode_both_bases_are_used_and_agreement_is_logged(bare_repo_with_two_commits, monkeypatch, tmp_path, caplog):
    stored, expected_diff = _fresh_base_evidence(bare_repo_with_two_commits, monkeypatch, tmp_path)
    _bare, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", "shadow")
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: stored)
    spies = _Spies(monkeypatch)

    with caplog.at_level(logging.INFO, logger="scan_worker.jobs"):
        _run_job(base_sha, head_sha)

    # Shadow never changes what is posted: the base is still checked out and scanned, and the comment is built
    # from that fresh scan.
    assert len(spies.base_checkouts) == 1 and len(spies.scans) == 2
    assert spies.diffs[0]["result"] == expected_diff
    record = next(r for r in caplog.records if r.getMessage() == "base evidence reuse shadow comparison")
    assert record.reuse_identical is True
    assert record.reuse_differing_sections == []
    assert record.reuse_evidence_age_s >= 0


def test_in_shadow_mode_a_difference_between_the_two_diffs_is_logged_by_section(
    bare_repo_with_two_commits, monkeypatch, tmp_path, caplog
):
    stored, _expected = _fresh_base_evidence(bare_repo_with_two_commits, monkeypatch, tmp_path)
    stored["git"] = {**stored["git"], "total_commits": stored["git"].get("total_commits", 0) + 5}
    _bare, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("ALETHEORE_HOSTED_REUSE_BASE_EVIDENCE", "shadow")
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: stored)
    _Spies(monkeypatch)

    with caplog.at_level(logging.INFO, logger="scan_worker.jobs"):
        _run_job(base_sha, head_sha)

    record = next(r for r in caplog.records if r.getMessage() == "base evidence reuse shadow comparison")
    assert record.reuse_identical is False
    assert record.reuse_differing_sections, "the section that differs must be named so the cause can be found"
