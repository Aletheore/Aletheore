import threading
import time

import httpx

from aletheore.telemetry import (
    is_telemetry_disabled,
    load_or_create_anonymous_id,
    report_scan_event,
    report_scan_event_in_background,
)


def test_is_telemetry_disabled_false_by_default(monkeypatch):
    monkeypatch.delenv("ALETHEORE_TELEMETRY_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    assert is_telemetry_disabled() is False


def test_is_telemetry_disabled_true_via_aletheore_env_var(monkeypatch):
    monkeypatch.setenv("ALETHEORE_TELEMETRY_DISABLED", "1")
    assert is_telemetry_disabled() is True


def test_is_telemetry_disabled_true_via_do_not_track(monkeypatch):
    monkeypatch.delenv("ALETHEORE_TELEMETRY_DISABLED", raising=False)
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert is_telemetry_disabled() is True


def test_load_or_create_anonymous_id_creates_a_new_id(tmp_path):
    path = tmp_path / "telemetry_id"
    assert not path.exists()

    anonymous_id = load_or_create_anonymous_id(path)

    assert path.exists()
    assert path.read_text().strip() == anonymous_id
    assert len(anonymous_id) > 8


def test_load_or_create_anonymous_id_reuses_existing_id(tmp_path):
    path = tmp_path / "telemetry_id"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("existing-id-12345")

    assert load_or_create_anonymous_id(path) == "existing-id-12345"


def test_load_or_create_anonymous_id_regenerates_when_file_is_empty(tmp_path):
    path = tmp_path / "telemetry_id"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")

    anonymous_id = load_or_create_anonymous_id(path)

    assert anonymous_id
    assert path.read_text().strip() == anonymous_id


def test_report_scan_event_posts_the_expected_payload(tmp_path, monkeypatch):
    monkeypatch.delenv("ALETHEORE_TELEMETRY_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    id_path = tmp_path / "telemetry_id"

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    report_scan_event(path=id_path, http_client=client)

    assert len(calls) == 1
    assert calls[0].url.path == "/v1/telemetry"
    import json

    payload = json.loads(calls[0].content)
    assert payload["event"] == "scan"
    assert payload["anonymous_id"] == id_path.read_text().strip()


def test_report_scan_event_does_nothing_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEORE_TELEMETRY_DISABLED", "1")
    id_path = tmp_path / "telemetry_id"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make a network call when telemetry is disabled")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    report_scan_event(path=id_path, http_client=client)

    assert not id_path.exists()


def test_report_scan_event_never_raises_on_network_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("ALETHEORE_TELEMETRY_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    id_path = tmp_path / "telemetry_id"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    report_scan_event(path=id_path, http_client=client)  # must not raise


def test_report_scan_event_in_background_waits_for_the_report_to_finish():
    # The regression this guards: a bare Thread(daemon=True).start() returned
    # immediately, so a scan that finished before the POST completed exited and
    # took the unsent event with it.
    completed = []

    def slow_report() -> None:
        time.sleep(0.2)
        completed.append("done")

    thread = report_scan_event_in_background(wait_seconds=5.0, report_fn=slow_report)

    assert completed == ["done"]
    assert not thread.is_alive()


def test_report_scan_event_in_background_gives_up_on_a_hanging_report():
    started = threading.Event()
    release = threading.Event()

    def hanging_report() -> None:
        started.set()
        release.wait(30)

    start = time.monotonic()
    thread = report_scan_event_in_background(wait_seconds=0.05, report_fn=hanging_report)
    elapsed = time.monotonic() - start

    assert started.wait(5), "the report should have been started at all"
    assert elapsed < 2.0, f"a hanging report held up the scan for {elapsed:.2f}s"
    # Left running rather than waited on - it is a daemon, so interpreter
    # shutdown reclaims it instead of the CLI blocking on it.
    assert thread.daemon
    release.set()


def test_report_scan_event_in_background_defaults_to_the_real_reporter(monkeypatch):
    monkeypatch.setenv("ALETHEORE_TELEMETRY_DISABLED", "1")

    thread = report_scan_event_in_background(wait_seconds=5.0)

    assert not thread.is_alive()
