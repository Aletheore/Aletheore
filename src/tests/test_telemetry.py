import httpx
import pytest

from aletheore.telemetry import (
    is_telemetry_disabled,
    load_or_create_anonymous_id,
    report_scan_event,
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
