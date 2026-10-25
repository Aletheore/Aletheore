import os
import subprocess
import unittest
from unittest import mock

from project_resolver import resolve_project_id


@mock.patch("project_resolver.google_auth.default")
@mock.patch("subprocess.run")
class TestGetTraceProject(unittest.TestCase):

  @mock.patch.dict(os.environ, {}, clear=True)
  def test_resolve_empty(self, mock_run, mock_auth):
    mock_run.side_effect = FileNotFoundError()
    mock_auth.return_value = (None, None)
    with self.assertRaises(RuntimeError):
      resolve_project_id()

  @mock.patch.dict(os.environ, {"GOOGLE_CLOUD_TRACE_PROJECT": "trace-proj"})
  def test_resolve_from_trace_project(self, mock_run, mock_auth):
    self.assertEqual(resolve_project_id(), "trace-proj")

  @mock.patch.dict(os.environ, {"GOOGLE_CLOUD_TELEMETRY_PROJECT": "telem-proj"})
  def test_resolve_from_telemetry_project(self, mock_run, mock_auth):
    self.assertEqual(resolve_project_id(), "telem-proj")

  @mock.patch.dict(os.environ, {"DEVSHELL_PROJECT_ID": "shell-proj"})
  def test_resolve_from_devshell(self, mock_run, mock_auth):
    mock_run.side_effect = FileNotFoundError()
    self.assertEqual(resolve_project_id(), "shell-proj")

  @mock.patch.dict(os.environ, {}, clear=True)
  def test_resolve_from_gcloud(self, mock_run, mock_auth):
    mock_proc = mock.MagicMock()
    mock_proc.stdout = "gcloud-proj-999\n"
    mock_run.return_value = mock_proc

    self.assertEqual(resolve_project_id(), "gcloud-proj-999")

  @mock.patch.dict(os.environ, {}, clear=True)
  def test_resolve_from_gcloud_unset(self, mock_run, mock_auth):
    mock_proc = mock.MagicMock()
    mock_proc.stdout = "(unset)\n"
    mock_run.return_value = mock_proc
    mock_auth.return_value = (None, "creds-proj-123")

    self.assertEqual(resolve_project_id(), "creds-proj-123")

  @mock.patch.dict(os.environ, {}, clear=True)
  def test_resolve_from_gcloud_failed(self, mock_run, mock_auth):
    mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
    mock_auth.return_value = (None, "creds-proj-123")

    self.assertEqual(resolve_project_id(), "creds-proj-123")

  @mock.patch.dict(os.environ, {}, clear=True)
  def test_resolve_from_credentials(self, mock_run, mock_auth):
    mock_run.side_effect = FileNotFoundError()
    mock_auth.return_value = (None, "creds-proj-123")

    self.assertEqual(resolve_project_id(), "creds-proj-123")

  @mock.patch.dict(os.environ, {}, clear=True)
  def test_resolve_from_credentials_error(self, mock_run, mock_auth):
    mock_run.side_effect = FileNotFoundError()
    try:
      from google.auth.exceptions import GoogleAuthError
    except ImportError:

      class GoogleAuthError(Exception):
        pass

    mock_auth.side_effect = GoogleAuthError("Auth failed")
    with self.assertRaises(RuntimeError):
      resolve_project_id()


if __name__ == "__main__":
  unittest.main()
