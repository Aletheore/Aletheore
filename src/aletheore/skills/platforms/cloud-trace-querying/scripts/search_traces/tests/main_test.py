import json
import sys
import unittest
from unittest import mock

# Mock trace_v1 dependencies by directly injecting into sys.modules
mock_trace = mock.MagicMock()
mock_trace_v1 = mock.MagicMock()
mock_trace.trace_v1 = mock_trace_v1
sys.modules["google.cloud"] = mock_trace
sys.modules["google.cloud.trace_v1"] = mock_trace_v1

import main


class TestMainCli(unittest.TestCase):

  @mock.patch(
      "sys.argv",
      ["main.py", "--filter", "latency:1s", "--projects", "p1", "p2"],
  )
  @mock.patch("main.search_traces")
  def test_main_success(self, mock_search_traces):
    mock_search_traces.return_value = {"clusters": []}
    with mock.patch("builtins.print") as mock_print:
      main.main()
      mock_search_traces.assert_called_once_with(
          projects=["p1", "p2"],
          filter_query="latency:1s",
          limit=10,
          start_time_str=None,
          end_time_str=None,
          examples_per_cluster=3,
      )
      mock_print.assert_called_once()

  @mock.patch(
      "sys.argv",
      [
          "main.py",
          "--filter",
          "latency:1s",
          "--projects",
          "p1",
          "--examples-per-cluster",
          "5",
      ],
  )
  @mock.patch("main.search_traces")
  def test_main_custom_examples_per_cluster(self, mock_search_traces):
    mock_search_traces.return_value = {"clusters": []}
    with mock.patch("builtins.print") as mock_print:
      main.main()
      mock_search_traces.assert_called_once_with(
          projects=["p1"],
          filter_query="latency:1s",
          limit=10,
          start_time_str=None,
          end_time_str=None,
          examples_per_cluster=5,
      )
      mock_print.assert_called_once()

  @mock.patch(
      "sys.argv", ["main.py", "--filter", "latency:1s", "--projects", "p1"]
  )
  @mock.patch("main.search_traces")
  def test_main_error_propagation(self, mock_search_traces):
    mock_search_traces.side_effect = RuntimeError("API error")
    with self.assertRaises(RuntimeError):
      main.main()

  @mock.patch(
      "sys.argv",
      [
          "main.py",
          "--filter",
          "latency:1s",
          "--projects",
          "p1",
          "--no-wrapper-tag",
      ],
  )
  @mock.patch("main.search_traces")
  def test_main_no_wrapper_tag(self, mock_search_traces):
    mock_search_traces.return_value = {"clusters": []}
    with mock.patch("builtins.print") as mock_print:
      main.main()
      mock_search_traces.assert_called_once_with(
          projects=["p1"],
          filter_query="latency:1s",
          limit=10,
          start_time_str=None,
          end_time_str=None,
          examples_per_cluster=3,
      )
      mock_print.assert_called_once_with(json.dumps({"clusters": []}, indent=2))


if __name__ == "__main__":
  unittest.main()
