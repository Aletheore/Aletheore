import json
import sys
import unittest
from unittest import mock

# Mock trace_v1 client
mock_trace = mock.MagicMock()
mock_trace_v1 = mock.MagicMock()
mock_trace.trace_v1 = mock_trace_v1

with mock.patch.dict(
    "sys.modules",
    {"google.cloud": mock_trace, "google.cloud.trace_v1": mock_trace_v1},
):
  import main as main_module


class TestFetchTraceSpan(unittest.TestCase):

  def test_fetch_span_exact_match(self):
    with mock.patch.object(
        main_module.trace_v1, "TraceServiceClient"
    ) as mock_client_class:
      mock_client = mock_client_class.return_value

      mock_span = mock.MagicMock()
      mock_span.span_id = "123"
      mock_span._pb = mock.MagicMock()

      mock_response = mock.MagicMock()
      mock_response.spans = [mock_span]
      mock_client.get_trace.return_value = mock_response

      with mock.patch.object(main_module, "MessageToDict") as mock_to_dict:
        mock_to_dict.return_value = {"name": "my-span", "spanId": "123"}

        result = main_module.fetch_span("test-proj", "trace-abc", "123")

        self.assertEqual(result, {"name": "my-span", "spanId": "123"})
        mock_client.get_trace.assert_called_once_with(
            project_id="test-proj", trace_id="trace-abc"
        )
        mock_to_dict.assert_called_once_with(mock_span._pb)

  def test_fetch_span_integer_fallback_hex(self):
    with mock.patch.object(
        main_module.trace_v1, "TraceServiceClient"
    ) as mock_client_class:
      mock_client = mock_client_class.return_value

      mock_span = mock.MagicMock()
      mock_span.span_id = "123"  # DB is decimal 123
      mock_span._pb = mock.MagicMock()

      mock_response = mock.MagicMock()
      mock_response.spans = [mock_span]
      mock_client.get_trace.return_value = mock_response

      with mock.patch.object(main_module, "MessageToDict") as mock_to_dict:
        mock_to_dict.return_value = {"name": "my-span", "spanId": "123"}

        # User queries using hex format 7b (which is decimal 123)
        result = main_module.fetch_span("test-proj", "trace-abc", "7b")

        self.assertEqual(result, {"name": "my-span", "spanId": "123"})
        mock_to_dict.assert_called_once_with(mock_span._pb)

  def test_fetch_span_integer_fallback_0x_hex(self):
    with mock.patch.object(
        main_module.trace_v1, "TraceServiceClient"
    ) as mock_client_class:
      mock_client = mock_client_class.return_value

      mock_span = mock.MagicMock()
      mock_span.span_id = "123"
      mock_span._pb = mock.MagicMock()

      mock_response = mock.MagicMock()
      mock_response.spans = [mock_span]
      mock_client.get_trace.return_value = mock_response

      with mock.patch.object(main_module, "MessageToDict") as mock_to_dict:
        mock_to_dict.return_value = {"name": "my-span", "spanId": "123"}

        # User queries using hex format with prefix 0x7b
        result = main_module.fetch_span("test-proj", "trace-abc", "0x7b")

        self.assertEqual(result, {"name": "my-span", "spanId": "123"})
        mock_to_dict.assert_called_once_with(mock_span._pb)

  def test_fetch_span_not_found(self):
    with mock.patch.object(
        main_module.trace_v1, "TraceServiceClient"
    ) as mock_client_class:
      mock_client = mock_client_class.return_value

      mock_span = mock.MagicMock()
      mock_span.span_id = "123"
      mock_span._pb = mock.MagicMock()

      mock_response = mock.MagicMock()
      mock_response.spans = [mock_span]
      mock_client.get_trace.return_value = mock_response

      with self.assertRaises(ValueError) as ctx:
        main_module.fetch_span("test-proj", "trace-abc", "999")

      self.assertIn("Span 999 not found in trace trace-abc", str(ctx.exception))

  @mock.patch.object(
      main_module,
      "fetch_span",
      return_value={"name": "my-span", "spanId": "123"},
  )
  @mock.patch("builtins.print")
  @mock.patch(
      "sys.argv",
      ["main.py", "--trace-id", "t1", "--span-id", "s1", "--project", "p1"],
  )
  def test_main_cli_stdout(self, mock_print, mock_fetch):
    with self.assertRaises(SystemExit) as cm:
      main_module.main()
    self.assertEqual(cm.exception.code, 0)
    mock_fetch.assert_called_once_with("p1", "t1", "s1")
    mock_print.assert_called_once()
    printed_str = mock_print.call_args[0][0]
    payload = printed_str.split(">", 1)[1].rsplit("<", 1)[0].strip()
    self.assertEqual(json.loads(payload), {"name": "my-span", "spanId": "123"})

  @mock.patch.object(
      main_module,
      "fetch_span",
      return_value={"name": "my-span", "spanId": "123"},
  )
  @mock.patch("builtins.print")
  @mock.patch(
      "sys.argv",
      [
          "main.py",
          "--trace-id",
          "t1",
          "--span-id",
          "s1",
          "--project",
          "p1",
          "--no-wrapper-tag",
      ],
  )
  def test_main_cli_no_wrapper_tag(self, mock_print, mock_fetch):
    with self.assertRaises(SystemExit) as cm:
      main_module.main()
    self.assertEqual(cm.exception.code, 0)
    mock_fetch.assert_called_once_with("p1", "t1", "s1")
    mock_print.assert_called_once_with(
        json.dumps({"name": "my-span", "spanId": "123"}, indent=2)
    )


if __name__ == "__main__":
  unittest.main()
