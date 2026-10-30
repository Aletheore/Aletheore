"""The scan workers' job scratch directory (ALETHEORE_JOBS_ROOT).

It used to be a 1 GB in-memory tmpfs, which large repos overflow (2026-10-30), and is moving to a
disk-backed volume. A live GitHub token sits in a checkout's .git/config for the seconds a fetch runs
and a hard-killed job can leave it behind, so on disk the directory must be private, short-lived and
emptied whenever its worker restarts.
"""
import os
import stat
import time

from scan_worker.jobs import _job_temp_dir, purge_job_scratch_dir, run_job_temp_dir_cleanup_job


def test_job_temp_dirs_and_their_root_are_owner_only(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    monkeypatch.setattr("scan_worker.jobs.JOBS_ROOT", root)

    job_dir = _job_temp_dir()

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(job_dir.stat().st_mode) == 0o700


def test_the_default_scratch_root_is_under_the_system_temp_dir():
    # Built from tempfile instead of a hardcoded "/tmp/..." string (bandit B108). In the worker container the
    # system temp dir is /tmp, so the default location is unchanged.
    import tempfile
    from pathlib import Path

    from scan_worker.jobs import _FALLBACK_JOBS_ROOT, _default_jobs_root

    assert _default_jobs_root() == Path(tempfile.gettempdir()) / "aletheore-jobs"
    assert _FALLBACK_JOBS_ROOT == _default_jobs_root()


def test_job_temp_dir_falls_back_to_tmp_when_the_scratch_volume_is_not_writable(tmp_path, monkeypatch, caplog):
    # A host directory that the unprivileged container user cannot write to must degrade to the old behaviour
    # (small repos keep working) rather than failing every scan job with PermissionError.
    blocked = tmp_path / "a-file-not-a-directory"
    blocked.write_text("x")
    fallback = tmp_path / "fallback"
    monkeypatch.setattr("scan_worker.jobs.JOBS_ROOT", blocked / "jobs")
    monkeypatch.setattr("scan_worker.jobs._FALLBACK_JOBS_ROOT", fallback)

    job_dir = _job_temp_dir()

    assert job_dir.parent == fallback
    assert job_dir.is_dir()
    assert "falling back" in caplog.text


def test_the_sweep_age_leaves_a_wide_margin_over_the_longest_checkout_job():
    # The sweeper deletes any job directory older than this, token-bearing .git/config included. If a job
    # timeout is ever raised past it, the sweeper could delete a live job's checkout out from under it, so the
    # margin is checked against the timeouts themselves instead of resting on a comment.
    from app_server.job_timeouts import SCAN_JOB_TIMEOUT_SECONDS
    from app_server.webhooks.pull_request import FLASH_REVIEW_JOB_TIMEOUT_SECONDS
    from scan_worker.jobs import JOB_TEMP_DIR_MAX_AGE_SECONDS

    assert JOB_TEMP_DIR_MAX_AGE_SECONDS >= 2 * max(SCAN_JOB_TIMEOUT_SECONDS, FLASH_REVIEW_JOB_TIMEOUT_SECONDS)


def test_job_temp_dirs_are_swept_after_an_hour_not_six(tmp_path, monkeypatch):
    # The longest job that uses a checkout times out at 900s, so an hour is generous. Six hours was fine
    # for memory-backed scratch space that vanished on restart; on disk a leftover would linger that long.
    monkeypatch.setattr("scan_worker.jobs.JOBS_ROOT", tmp_path)
    two_hours_old = tmp_path / "two-hours-old"
    thirty_minutes_old = tmp_path / "thirty-minutes-old"
    two_hours_old.mkdir()
    thirty_minutes_old.mkdir()
    now = time.time()
    os.utime(two_hours_old, (now - 7200, now - 7200))
    os.utime(thirty_minutes_old, (now - 1800, now - 1800))

    run_job_temp_dir_cleanup_job()

    assert not two_hours_old.exists()
    assert thirty_minutes_old.exists()


def test_purge_job_scratch_dir_empties_the_root_but_keeps_it(tmp_path, monkeypatch):
    # A worker that has just started owns no jobs, so anything under its own scratch root is left over from
    # a job that died with the previous process (the 2026-10-30 worker roll killed several mid-clone) and
    # may still hold a token in .git/config.
    root = tmp_path / "jobs"
    leftover = root / "killed-job" / "base" / ".git"
    leftover.mkdir(parents=True)
    (leftover / "config").write_text("[remote \"origin\"]\n\turl = https://x-access-token:live@github.com/o/r.git\n")
    (root / "stray-file").write_text("x")
    monkeypatch.setattr("scan_worker.jobs.JOBS_ROOT", root)

    purge_job_scratch_dir()

    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_purge_job_scratch_dir_is_a_noop_when_the_root_does_not_exist_yet(tmp_path, monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.JOBS_ROOT", tmp_path / "never-created")

    purge_job_scratch_dir()

    assert not (tmp_path / "never-created").exists()
