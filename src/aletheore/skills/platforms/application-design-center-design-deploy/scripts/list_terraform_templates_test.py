import subprocess
import unittest
from unittest import mock

import list_terraform_templates


class ListTerraformTemplatesTest(unittest.TestCase):

  @mock.patch("subprocess.run")
  def test_run_gcloud_list_success(self, mock_run: mock.Mock) -> None:
    mock_run.return_value = mock.Mock(
        stdout='[{"name": "app1", "templateCategory": "APPLICATION_TEMPLATE"}]'
    )
    result = list_terraform_templates._run_gcloud_list(
        project="p", location="l", space="s", catalog="c"
    )
    self.assertEqual(
        result, [{"name": "app1", "templateCategory": "APPLICATION_TEMPLATE"}]
    )

  @mock.patch("subprocess.run")
  def test_run_gcloud_list_failure(self, mock_run: mock.Mock) -> None:
    mock_run.side_effect = subprocess.CalledProcessError(
        1, ["cmd"], stderr="err"
    )
    result = list_terraform_templates._run_gcloud_list("p", "l", "s", "c")
    self.assertEqual(result, [])


if __name__ == "__main__":
  unittest.main()
