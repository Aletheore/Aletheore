import json
import sys
import unittest
from unittest import mock

# Mock trace_v1
mock_trace = mock.MagicMock()
mock_trace_v1 = mock.MagicMock()
mock_trace.trace_v1 = mock_trace_v1

with mock.patch.dict(
    "sys.modules",
    {"google.cloud": mock_trace, "google.cloud.trace_v1": mock_trace_v1},
):
  import main as main_module


class TestFetchEntireTrace(unittest.TestCase):

  def _patch_message_to_dict(self, return_val):
    """Robustly patches MessageToDict for both module and function level imports."""
    mock_to_dict = mock.MagicMock(return_value=return_val)
    patcher_global = mock.patch(
        "google.protobuf.json_format.MessageToDict", mock_to_dict
    )

    if hasattr(main_module, "MessageToDict"):
      patcher_module = mock.patch.object(
          main_module, "MessageToDict", mock_to_dict
      )
      return patcher_global, patcher_module, mock_to_dict
    else:
      return patcher_global, None, mock_to_dict

  def test_fetch_entire_trace_success(self):
    with mock.patch.object(
        main_module.trace_v1, "TraceServiceClient"
    ) as mock_client_class:
      mock_client = mock_client_class.return_value

      mock_span = mock.MagicMock()
      mock_span._pb = mock.MagicMock()

      mock_response = mock.MagicMock()
      mock_response.spans = [mock_span]
      mock_client.get_trace.return_value = mock_response

      p_global, p_module, mock_to_dict = self._patch_message_to_dict(
          {"name": "span-1", "spanId": "s1"}
      )
      with p_global:
        if p_module:
          with p_module:
            result = main_module.fetch_entire_trace("test-proj", "trace-123")
        else:
          result = main_module.fetch_entire_trace("test-proj", "trace-123")

        self.assertEqual(result, [{"name": "span-1", "spanId": "s1"}])
        mock_client.get_trace.assert_called_once_with(
            project_id="test-proj", trace_id="trace-123"
        )

  @mock.patch.object(
      main_module,
      "fetch_entire_trace",
      return_value=[{"name": "span-1", "spanId": "s1"}],
  )
  @mock.patch("builtins.print")
  @mock.patch("sys.argv", ["main.py", "--trace-id", "t1", "--project", "p1"])
  def test_main_cli_stdout(self, mock_print, mock_fetch):
    main_module.main()
    mock_fetch.assert_called_once_with("p1", "t1")
    mock_print.assert_called_once()
    printed_str = mock_print.call_args[0][0]
    payload = printed_str.split(">", 1)[1].rsplit("<", 1)[0].strip()
    printed_json = json.loads(payload)
    self.assertEqual(printed_json, [{"name": "span-1", "spanId": "s1"}])

  @mock.patch.object(
      main_module,
      "fetch_entire_trace",
      return_value=[{"name": "span-1", "spanId": "s1"}],
  )
  @mock.patch(
      "sys.argv",
      [
          "main.py",
          "--trace-id",
          "t1",
          "--project",
          "p1",
          "--output",
          "out.json",
      ],
  )
  @mock.patch("builtins.open", new_callable=mock.mock_open)
  def test_main_cli_file_write(self, mock_file_open, mock_fetch):
    main_module.main()
    mock_fetch.assert_called_once_with("p1", "t1")
    mock_file_open.assert_called_once_with("out.json", "w")
    mock_file_open().write.assert_called_once()
    written_data = mock_file_open().write.call_args[0][0]
    payload = written_data.split(">", 1)[1].rsplit("<", 1)[0].strip()
    self.assertEqual(json.loads(payload), [{"name": "span-1", "spanId": "s1"}])

  @mock.patch.object(
      main_module,
      "fetch_entire_trace",
      return_value=[{"name": "span-1", "spanId": "s1"}],
  )
  @mock.patch("builtins.print")
  @mock.patch(
      "sys.argv",
      ["main.py", "--trace-id", "t1", "--project", "p1", "--no-wrapper-tag"],
  )
  def test_main_cli_no_wrapper_tag(self, mock_print, mock_fetch):
    main_module.main()
    mock_fetch.assert_called_once_with("p1", "t1")
    mock_print.assert_called_once_with(
        json.dumps([{"name": "span-1", "spanId": "s1"}], indent=2)
    )


if __name__ == "__main__":
  unittest.main()
