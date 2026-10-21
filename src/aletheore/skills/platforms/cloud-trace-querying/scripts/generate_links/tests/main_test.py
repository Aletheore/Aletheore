import unittest
from unittest import mock
from urllib.parse import quote

from main import generate_links


class TestGenerateLinks(unittest.TestCase):

  def test_generate_links_without_span_id(self):
    project = "test-project"
    trace_id = "testtraceid1234567890abcdef123"
    trace_link, logging_link = generate_links(project, trace_id)

    expected_trace = "https://console.cloud.google.com/traces/explorer;traceId=testtraceid1234567890abcdef123?project=test-project"
    self.assertEqual(trace_link, expected_trace)

    expected_log_query = (
        'trace="projects/test-project/traces/testtraceid1234567890abcdef123"'
    )
    expected_logging = (
        f"https://console.cloud.google.com/logs/query;query={quote(expected_log_query, safe='')}?project=test-project"
    )
    self.assertEqual(logging_link, expected_logging)

  def test_generate_links_with_span_id(self):
    project = "test-project"
    trace_id = "testtraceid1234567890abcdef123"
    span_id = "123456"
    trace_link, logging_link = generate_links(project, trace_id, span_id)

    expected_trace = "https://console.cloud.google.com/traces/explorer;traceId=testtraceid1234567890abcdef123;spanId=123456?project=test-project"
    self.assertEqual(trace_link, expected_trace)

    expected_log_query = (
        'trace="projects/test-project/traces/testtraceid1234567890abcdef123"'
        ' AND spanId="123456"'
    )
    expected_logging = (
        f"https://console.cloud.google.com/logs/query;query={quote(expected_log_query, safe='')}?project=test-project"
    )
    self.assertEqual(logging_link, expected_logging)

  @mock.patch(
      "sys.argv",
      ["main.py", "--project", "test-proj", "--trace-id", "trace123"],
  )
  @mock.patch("builtins.print")
  @mock.patch("main.generate_links", return_value=("link1", "link2"))
  def test_main_cli_without_span(self, mock_gen, mock_print):
    import main as main_module

    main_module.main()
    mock_gen.assert_called_once_with("test-proj", "trace123", None)
    mock_print.assert_any_call("Cloud Trace Deep Link:\nlink1\n")
    mock_print.assert_any_call("Cloud Logging Deep Link:\nlink2")

  @mock.patch(
      "sys.argv",
      [
          "main.py",
          "--project",
          "test-proj",
          "--trace-id",
          "trace123",
          "--span-id",
          "span456",
      ],
  )
  @mock.patch("builtins.print")
  @mock.patch("main.generate_links", return_value=("link1", "link2"))
  def test_main_cli_with_span(self, mock_gen, mock_print):
    import main as main_module

    main_module.main()
    mock_gen.assert_called_once_with("test-proj", "trace123", "span456")
    mock_print.assert_any_call("Cloud Trace Deep Link:\nlink1\n")
    mock_print.assert_any_call("Cloud Logging Deep Link:\nlink2")


if __name__ == "__main__":
  unittest.main()
