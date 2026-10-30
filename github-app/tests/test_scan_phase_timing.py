"""Scan jobs log how long each phase took, so a slow or timed-out scan can be attributed.

On 2026-10-30 a cal.com push scan hit its time limit and the logs only said "exceeded maximum timeout":
nothing recorded whether the checkout or the scan itself had eaten the time, so a faster checkout could only
be argued from a local benchmark. Phase timings turn that into a measurement.
"""
import logging

import pytest

from scan_worker.jobs import _timed_phase


def test_timed_phase_logs_the_job_phase_and_duration(caplog):
    with caplog.at_level(logging.INFO, logger="scan_worker.jobs"):
        with _timed_phase("run_pr_scan_job", "base_checkout"):
            pass

    record = next(r for r in caplog.records if r.getMessage() == "scan phase finished")
    assert record.scan_job == "run_pr_scan_job"
    assert record.phase == "base_checkout"
    assert record.phase_duration_s >= 0
    assert record.phase_ok is True


def test_timed_phase_still_logs_when_the_phase_fails_and_lets_the_error_through(caplog):
    # A job killed by its time limit never reaches a "finished" line for the phase it died in, but a phase
    # that fails with an exception must still say how long it ran before failing.
    with caplog.at_level(logging.INFO, logger="scan_worker.jobs"):
        with pytest.raises(ValueError):
            with _timed_phase("run_push_scan_job", "head_scan"):
                raise ValueError("boom")

    record = next(r for r in caplog.records if r.getMessage() == "scan phase finished")
    assert record.phase == "head_scan"
    assert record.phase_ok is False
