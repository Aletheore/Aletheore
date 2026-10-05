import logging
import os

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from app_server.sentry_config import _scrub_event, init_sentry

_FAKE_DSN = "https://examplePublicKey@o0.ingest.sentry.io/0"


@pytest.fixture
def _reset_sentry_client():
    yield
    # Best-effort hygiene only - sentry_sdk's _Client.is_active() returns
    # True unconditionally once any real client has ever been constructed
    # in this process (it distinguishes _Client from the initial
    # NonRecordingClient placeholder, not "has an active transport"), so
    # calling init(dsn=None) here does NOT make later is_active() checks
    # reliable again. Tests below that need to tell "init_sentry() called
    # sentry_sdk.init()" apart from "it didn't" spy on sentry_sdk.init
    # directly instead of reading client state, for exactly this reason.
    #
    # Also: sentry_sdk.init(dsn=None) does not mean "disable" - it means
    # "resolve the DSN the normal way," which falls back to reading
    # SENTRY_DSN from the environment. monkeypatch's own setenv revert runs
    # AFTER this fixture's teardown (LIFO relative to this test's parameter
    # order), so SENTRY_DSN set by a test using the real init_sentry() is
    # still present here - popping it directly (rather than relying on
    # monkeypatch's later revert) is what actually prevents a real client
    # with a live (if fake) DSN from lingering and trying to flush pending
    # events over the network at process exit. Confirmed live: omitting
    # this line produced a real "Sentry is attempting to send N pending
    # events" network attempt at the end of the test run.
    os.environ.pop("SENTRY_DSN", None)
    sentry_sdk.init(dsn=None)


def test_init_sentry_is_a_noop_when_dsn_is_unset(monkeypatch, _reset_sentry_client):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **k: calls.append((a, k)))

    init_sentry("app_server")

    assert calls == []


def test_init_sentry_calls_sentry_sdk_init_when_dsn_is_set(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **k: calls.append(k))

    init_sentry("app_server")

    assert len(calls) == 1
    assert calls[0]["dsn"] == _FAKE_DSN


def test_init_sentry_configures_no_performance_tracing(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    assert sentry_sdk.get_client().options["traces_sample_rate"] == 0


def test_init_sentry_disables_default_pii(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    assert sentry_sdk.get_client().options["send_default_pii"] is False


def test_init_sentry_only_reports_error_level_logs_and_above(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    integration = sentry_sdk.get_client().get_integration(LoggingIntegration)
    assert integration._handler.level == logging.ERROR


def test_scrub_event_strips_request_data():
    event = {"request": {"headers": {"Cookie": "secret"}}, "exception": {"values": []}}

    scrubbed = _scrub_event(event, {})

    assert "request" not in scrubbed


def test_scrub_event_strips_local_variables_from_every_frame_of_every_exception():
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {
                        "frames": [{"filename": "a.py", "vars": {"secret": "x"}}]
                    },
                },
                {
                    "type": "RuntimeError",
                    "stacktrace": {
                        "frames": [{"filename": "b.py", "vars": {"token": "y"}}]
                    },
                },
            ]
        }
    }

    scrubbed = _scrub_event(event, {})

    for exc_value in scrubbed["exception"]["values"]:
        for frame in exc_value["stacktrace"]["frames"]:
            assert "vars" not in frame


def test_scrub_event_does_not_crash_on_a_message_only_event_with_no_exception():
    event = {"message": "something happened", "level": "warning"}

    scrubbed = _scrub_event(event, {})

    assert scrubbed["message"] == "something happened"
