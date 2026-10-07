import logging
import os

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport
from starlette.requests import ClientDisconnect

from app_server.sentry_config import _scrub_event, init_sentry

_FAKE_DSN = "https://examplePublicKey@o0.ingest.sentry.io/0"


class _CapturingTransport(Transport):
    """A real (non-mocked) Transport that records error events instead of
    sending them over the network - lets tests exercise the SDK's actual
    event pipeline (ignore_errors filtering, before_send, breadcrumbs)
    rather than only the arguments passed to sentry_sdk.init().
    """

    def __init__(self):
        super().__init__()
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.data_category == "error":
                self.events.append(item.payload.json)


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


def test_client_disconnect_is_never_reported(monkeypatch, _reset_sentry_client):
    # app_server/main.py's global exception handler returns a plain 499 for
    # ClientDisconnect and explicitly does NOT call send_error_alert for it
    # (see main.py's own comment: "neither a bug alert nor a 5xx"). But
    # sentry_sdk's auto-enabled Starlette/FastAPI integration captures ANY
    # exception that propagates through the ASGI app, independent of
    # LoggingIntegration's event_level - a real capture, not a mock, proves
    # ignore_errors actually suppresses it at the client level regardless of
    # which code path (manual capture_exception or the auto integration)
    # triggers it.
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)
    init_sentry("app_server")
    transport = _CapturingTransport()
    sentry_sdk.get_client().transport = transport

    sentry_sdk.capture_exception(ClientDisconnect())
    sentry_sdk.capture_exception(KeyError("a real bug"))

    assert len(transport.events) == 1
    assert transport.events[0]["exception"]["values"][0]["type"] == "KeyError"


def test_breadcrumbs_are_stripped_from_reported_events(monkeypatch, _reset_sentry_client):
    # LoggingIntegration(level=logging.INFO, ...) attaches up to 100 recent
    # INFO+ log records as breadcrumbs on every event, with their fully
    # formatted messages and `extra` dicts - unscrubbed, this leaks whatever
    # an earlier log call mentioned (e.g. a customer email in a receipt
    # log) into an unrelated later error event. before_send's existing
    # request/vars stripping does not touch breadcrumbs at all.
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)
    init_sentry("app_server")
    transport = _CapturingTransport()
    sentry_sdk.get_client().transport = transport

    logging.getLogger("app_server.test").info(
        "sending receipt to %s", "alice@example.com", extra={"customer_email": "alice@example.com"}
    )
    sentry_sdk.capture_exception(KeyError("unrelated error"))

    assert len(transport.events) == 1
    assert "breadcrumbs" not in transport.events[0]


def test_malformed_dsn_does_not_raise(monkeypatch, _reset_sentry_client):
    # SENTRY_DSN is an optional, operator-set env var - a typo in it must
    # not be the reason app_server/scan_worker/jina_embed fail to start.
    monkeypatch.setenv("SENTRY_DSN", "not-a-valid-dsn")

    init_sentry("app_server")  # must not raise


def test_service_tag_survives_a_raw_thread(monkeypatch, _reset_sentry_client):
    # sentry_sdk.set_tag() writes to the isolation scope, which a thread
    # started via _thread.start_new_thread (not threading.Thread) does not
    # inherit - confirmed live, events from such a thread carried no tags
    # at all. RQ's own worker forking and FastAPI's sync-route threadpool
    # are exactly this kind of boundary. The global scope is read by every
    # thread regardless of how it was started.
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)
    init_sentry("app_server")
    transport = _CapturingTransport()
    sentry_sdk.get_client().transport = transport

    import _thread
    import time

    def _raise_from_a_raw_thread():
        try:
            raise KeyError("from a raw thread")
        except KeyError as exc:
            sentry_sdk.capture_exception(exc)

    _thread.start_new_thread(_raise_from_a_raw_thread, ())
    for _ in range(50):
        if transport.events:
            break
        time.sleep(0.02)

    assert len(transport.events) == 1
    assert transport.events[0]["tags"]["service"] == "app_server"
