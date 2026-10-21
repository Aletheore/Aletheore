"""Unit tests for fetch_related_logs script bundle modules."""

import datetime
import json
import unittest
from unittest import mock

# Mock google.cloud SDKs in sys.modules prior to importing log_fetcher/observability_scopes
mock_logging = mock.MagicMock()
mock_trace = mock.MagicMock()
mock_auth = mock.MagicMock()
mock_auth_transport_urllib3 = mock.MagicMock()
mock_urllib3 = mock.MagicMock()

with mock.patch.dict(
    "sys.modules",
    {
        "google.cloud": mock_logging,
        "google.cloud.logging": mock_logging,
        "google.cloud.trace_v1": mock_trace,
        "google.auth": mock_auth,
        "google.auth.transport": mock.MagicMock(),
        "google.auth.transport.urllib3": mock_auth_transport_urllib3,
        "urllib3": mock_urllib3,
    },
):
  import log_fetcher
  import main
  import observability_scopes


class TestFetchRelatedLogs(unittest.TestCase):

  def setUp(self):
    super().setUp()
    mock_logging.reset_mock()
    mock_trace.reset_mock()
    mock_auth.reset_mock()
    mock_auth_transport_urllib3.reset_mock()
    mock_urllib3.reset_mock()

    mock_auth.default.return_value = (mock.MagicMock(), "proj-a")
    self.mock_pool = mock.MagicMock()
    mock_auth_transport_urllib3.AuthorizedHttp.return_value = self.mock_pool

  def test_to_datetime_conversion(self):
    """Verifies _to_datetime handles None, protobuf Timestamp, and datetime."""
    self.assertIsNone(log_fetcher._to_datetime(None))

    now = datetime.datetime(2026, 6, 25, 13, 0, 0, tzinfo=datetime.timezone.utc)
    self.assertEqual(log_fetcher._to_datetime(now), now)

    mock_ts = mock.MagicMock(spec=["to_datetime"])
    mock_ts.to_datetime.return_value = now
    self.assertEqual(log_fetcher._to_datetime(mock_ts), now)

  def test_resolve_query_projects_only_trace(self):
    res = observability_scopes.resolve_query_projects(["proj-a"], None)
    self.assertEqual(res, {"proj-a"})

  def test_resolve_query_projects_both(self):
    res = observability_scopes.resolve_query_projects(
        ["proj-a"], ["proj-b", "proj-a"]
    )
    self.assertEqual(res, {"proj-a", "proj-b"})

  def test_resolve_query_projects_with_observability_scopes(self):
    mock_creds = mock.MagicMock()
    mock_creds.token = "mock-token"
    mock_auth.default.return_value = (mock_creds, "proj-a")

    r1 = mock.MagicMock()
    r1.status = 200
    r1.data = json.dumps({
        "name": "projects/proj-a/locations/global/scopes/_Default",
        "logScope": "projects/proj-a/locations/global/logScopes/my-scope",
    }).encode("utf-8")

    r2 = mock.MagicMock()
    r2.status = 200
    r2.data = json.dumps({
        "name": "projects/proj-a/locations/global/logScopes/my-scope",
        "resourceNames": [
            "projects/proj-monitored-1",
            "projects/proj-monitored-2",
        ],
    }).encode("utf-8")

    self.mock_pool.request.side_effect = [r1, r2]

    res = observability_scopes.resolve_query_projects(["proj-a"], ["proj-b"])
    self.assertEqual(res, {"proj-monitored-1", "proj-monitored-2", "proj-b"})

    self.mock_pool.request.assert_has_calls([
        mock.call(
            "GET",
            "https://observability.googleapis.com/v1/projects/"
            "proj-a/locations/global/scopes/_Default",
            timeout=10.0,
        ),
        mock.call(
            "GET",
            "https://logging.googleapis.com/v2/projects/proj-a/"
            "locations/global/logScopes/my-scope",
            timeout=10.0,
        ),
    ])

  def test_resolve_query_projects_observability_scopes_error_fallback(self):
    mock_creds = mock.MagicMock()
    mock_creds.token = "mock-token"
    mock_auth.default.return_value = (mock_creds, "proj-a")
    self.mock_pool.request.side_effect = Exception("API error")

    res = observability_scopes.resolve_query_projects(["proj-a"], None)
    self.assertEqual(res, {"proj-a"})

  @mock.patch.object(log_fetcher.cloud_logging, "Client")
  def test_fetch_logs_success(self, mock_logging_client_class):
    mock_logging_client = mock_logging_client_class.return_value

    mock_entry = mock.MagicMock()
    mock_entry.payload = "Test log line"
    mock_entry.timestamp = datetime.datetime(
        2026, 6, 24, 12, 0, 0, tzinfo=datetime.timezone.utc
    )
    mock_entry.severity = "INFO"
    mock_entry.log_name = "projects/trace-proj-a/logs/test-log"
    mock_entry.span_id = "span-1"
    mock_entry.trace = "projects/trace-proj-a/traces/trace-123"

    mock_logging_client.list_entries.return_value = [mock_entry]

    result = log_fetcher.fetch_logs(
        trace_id="trace-123",
        trace_projects=["trace-proj-a"],
        span_id="span-1",
        limit=5,
        start_time="2026-06-25T13:00:00Z",
        end_time="2026-06-25T14:00:00Z",
    )

    self.assertEqual(len(result), 1)
    self.assertEqual(result[0]["payload"], "Test log line")
    self.assertEqual(result[0]["timestamp"], "2026-06-24T12:00:00+00:00")
    self.assertEqual(result[0]["trace_project"], "trace-proj-a")

    mock_logging_client.list_entries.assert_called_once_with(
        resource_names=["projects/trace-proj-a"],
        filter_=(
            'trace = "projects/trace-proj-a/traces/trace-123" AND'
            ' spanId="span-1" AND timestamp >= "2026-06-25T13:00:00Z" AND'
            ' timestamp <= "2026-06-25T14:00:00Z"'
        ),
        page_size=5,
        order_by="timestamp desc",
    )

  @mock.patch.object(log_fetcher.trace_v1, "TraceServiceClient")
  @mock.patch.object(log_fetcher.cloud_logging, "Client")
  def test_fetch_logs_time_range_inference(
      self, mock_logging_client_class, mock_trace_client_class
  ):
    mock_trace_client_instance = mock_trace_client_class.return_value
    mock_logging_client_instance = mock_logging_client_class.return_value

    mock_trace_response = mock.MagicMock()

    # Test with native datetime.datetime instances (which caused the original AttributeError)
    mock_span_1 = mock.MagicMock(spec=["start_time", "end_time"])
    mock_span_1.start_time = datetime.datetime(
        2026, 6, 25, 13, 0, 5, tzinfo=datetime.timezone.utc
    )
    mock_span_1.end_time = datetime.datetime(
        2026, 6, 25, 13, 0, 10, tzinfo=datetime.timezone.utc
    )

    mock_span_2 = mock.MagicMock(spec=["start_time", "end_time"])
    mock_span_2.start_time = datetime.datetime(
        2026, 6, 25, 13, 0, 0, tzinfo=datetime.timezone.utc
    )
    mock_span_2.end_time = datetime.datetime(
        2026, 6, 25, 13, 0, 15, tzinfo=datetime.timezone.utc
    )

    mock_trace_response.spans = [mock_span_1, mock_span_2]
    mock_trace_client_instance.get_trace.return_value = mock_trace_response

    mock_entry = mock.MagicMock()
    mock_entry.payload = "Log entry within time window"
    mock_entry.timestamp = datetime.datetime(
        2026, 6, 25, 13, 0, 8, tzinfo=datetime.timezone.utc
    )
    mock_entry.severity = "INFO"
    mock_entry.log_name = "projects/trace-proj-a/logs/test-log"
    mock_entry.span_id = "span-1"
    mock_entry.trace = "projects/trace-proj-a/traces/trace-123"
    mock_logging_client_instance.list_entries.return_value = [mock_entry]

    result = log_fetcher.fetch_logs(
        trace_id="trace-123",
        trace_projects=["trace-proj-a"],
        span_id="span-1",
        limit=5,
        start_time=None,
        end_time=None,
    )

    self.assertEqual(len(result), 1)
    mock_trace_client_instance.get_trace.assert_called_once_with(
        project_id="trace-proj-a", trace_id="trace-123"
    )
    mock_logging_client_instance.list_entries.assert_called_once_with(
        resource_names=["projects/trace-proj-a"],
        filter_=(
            'trace = "projects/trace-proj-a/traces/trace-123" AND'
            ' spanId="span-1" AND timestamp >= "2026-06-25T12:59:55Z" AND'
            ' timestamp <= "2026-06-25T13:00:20Z"'
        ),
        page_size=5,
        order_by="timestamp desc",
    )

  @mock.patch.object(log_fetcher.trace_v1, "TraceServiceClient")
  @mock.patch.object(log_fetcher.cloud_logging, "Client")
  def test_fetch_logs_time_range_inference_fallback(
      self, mock_logging_client_class, mock_trace_client_class
  ):
    mock_trace_client_instance = mock_trace_client_class.return_value
    mock_logging_client_instance = mock_logging_client_class.return_value

    mock_trace_client_instance.get_trace.side_effect = Exception(
        "Trace API error"
    )
    mock_logging_client_instance.list_entries.return_value = []

    result = log_fetcher.fetch_logs(
        trace_id="trace-123",
        trace_projects=["trace-proj-a"],
        span_id="span-1",
        limit=5,
        start_time=None,
        end_time=None,
    )

    self.assertEqual(len(result), 0)
    mock_trace_client_instance.get_trace.assert_called_once_with(
        project_id="trace-proj-a", trace_id="trace-123"
    )
    mock_logging_client_instance.list_entries.assert_called_once_with(
        resource_names=["projects/trace-proj-a"],
        filter_=(
            'trace = "projects/trace-proj-a/traces/trace-123" AND'
            ' spanId="span-1"'
        ),
        page_size=5,
        order_by="timestamp desc",
    )

  @mock.patch.object(main, "fetch_logs", return_value=[{"payload": "test log"}])
  @mock.patch("builtins.print")
  @mock.patch(
      "sys.argv", ["main.py", "--trace-id", "t1", "--trace-projects", "p1"]
  )
  def test_main_cli_stdout(self, mock_print, mock_fetch):
    main.main()
    mock_fetch.assert_called_once_with(
        trace_id="t1",
        span_id=None,
        limit=100,
        trace_projects=["p1"],
        log_projects=None,
        start_time=None,
        end_time=None,
    )
    mock_print.assert_called_once()
    printed_str = mock_print.call_args[0][0]
    payload = printed_str.split(">", 1)[1].rsplit("<", 1)[0].strip()
    self.assertEqual(json.loads(payload), [{"payload": "test log"}])

  @mock.patch.object(main, "fetch_logs", return_value=[{"payload": "test log"}])
  @mock.patch(
      "sys.argv",
      [
          "main.py",
          "--trace-id",
          "t1",
          "--trace-projects",
          "p1",
          "--output",
          "out.json",
      ],
  )
  @mock.patch("builtins.open", new_callable=mock.mock_open)
  def test_main_cli_file_write(self, mock_file_open, mock_fetch):
    main.main()
    mock_fetch.assert_called_once_with(
        trace_id="t1",
        span_id=None,
        limit=100,
        trace_projects=["p1"],
        log_projects=None,
        start_time=None,
        end_time=None,
    )
    mock_file_open.assert_called_once_with("out.json", "w")
    mock_file_open().write.assert_called_once()
    written_data = mock_file_open().write.call_args[0][0]
    payload = written_data.split(">", 1)[1].rsplit("<", 1)[0].strip()
    self.assertEqual(json.loads(payload), [{"payload": "test log"}])


if __name__ == "__main__":
  unittest.main()
