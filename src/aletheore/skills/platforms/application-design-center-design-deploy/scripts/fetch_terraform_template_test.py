import subprocess
import unittest
from unittest import mock

import fetch_terraform_template


class FetchTerraformTemplateTest(unittest.TestCase):

  @mock.patch("subprocess.run")
  def test_run_cmd_success(self, mock_run: mock.Mock) -> None:
    mock_run.return_value = mock.Mock(stdout="success output")
    result = fetch_terraform_template._run_cmd(["gcloud", "--version"])
    self.assertEqual(result, "success output")

  @mock.patch("subprocess.run")
  def test_run_cmd_failure(self, mock_run: mock.Mock) -> None:
    mock_run.side_effect = subprocess.CalledProcessError(1, ["cmd"])
    with self.assertRaises(subprocess.CalledProcessError):
      fetch_terraform_template._run_cmd(["invalid_cmd"])

  def test_parse_input_short_id(self) -> None:
    result = fetch_terraform_template._parse_input("my-template")
    self.assertEqual(
        result,
        (
            "gcpdesigncenter",
            "us-central1",
            "googlespace",
            "googlecatalog",
            "my-template",
        ),
    )

  def test_parse_input_full_path(self) -> None:
    full_path = (
        "projects/my-proj/locations/us-east1/spaces/my-sp/catalogs/my-cat/"
        "templates/tpl-123"
    )
    result = fetch_terraform_template._parse_input(full_path)
    self.assertEqual(
        result, ("my-proj", "us-east1", "my-sp", "my-cat", "tpl-123")
    )


if __name__ == "__main__":
  unittest.main()
