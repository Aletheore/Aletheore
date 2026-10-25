import os
import shutil
import subprocess
import tempfile
import unittest


class TestGetTraceProjectIntegration(unittest.TestCase):

  def setUp(self):
    self.temp_dir = tempfile.mkdtemp()
    self.script_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../run.sh")
    )
    # Copy current environment but clear project-related vars
    self.env = os.environ.copy()
    self.env.pop("GOOGLE_CLOUD_TRACE_PROJECT", None)
    self.env.pop("GOOGLE_CLOUD_TELEMETRY_PROJECT", None)
    self.env.pop("GOOGLE_CLOUD_PROJECT", None)
    self.env.pop("GCLOUD_PROJECT", None)
    self.env.pop("DEVSHELL_PROJECT_ID", None)
    self.env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    self.env["TMPDIR"] = self.temp_dir
    self.env["TEMP"] = self.temp_dir

  def tearDown(self):
    shutil.rmtree(self.temp_dir)

  def test_resolve_from_env_var(self):
    self.env["GOOGLE_CLOUD_TRACE_PROJECT"] = "integration-env-proj"
    result = subprocess.run(
        [self.script_path],
        env=self.env,
        capture_output=True,
        text=True,
    )
    self.assertEqual(result.returncode, 0)
    self.assertEqual(result.stdout.strip(), "integration-env-proj")

  def test_resolve_from_gcloud_mock(self):
    # Create a mock gcloud executable in the temp dir
    gcloud_mock_path = os.path.join(self.temp_dir, "gcloud")
    with open(gcloud_mock_path, "w") as f:
      f.write("#!/bin/sh\n")
      # Check if it was called with 'config list'
      f.write('if [ "$1" = "config" ] && [ "$2" = "list" ]; then\n')
      f.write('  echo "gcloud-mock-proj"\n')
      f.write("else\n")
      f.write("  exit 1\n")
      f.write("fi\n")
    os.chmod(gcloud_mock_path, 0o755)

    # Prepend temp dir to PATH so our mock gcloud is found first
    self.env["PATH"] = f"{self.temp_dir}:{self.env.get('PATH', '')}"

    result = subprocess.run(
        [self.script_path],
        env=self.env,
        capture_output=True,
        text=True,
    )

    self.assertEqual(result.returncode, 0)
    self.assertEqual(result.stdout.strip(), "gcloud-mock-proj")

  def test_failure_when_no_project(self):
    # Mock gcloud to return (unset)
    gcloud_mock_path = os.path.join(self.temp_dir, "gcloud")
    with open(gcloud_mock_path, "w") as f:
      f.write("#!/bin/sh\n")
      f.write('echo "(unset)"\n')
    os.chmod(gcloud_mock_path, 0o755)

    self.env["PATH"] = f"{self.temp_dir}:{self.env.get('PATH', '')}"
    self.env["GOOGLE_APPLICATION_CREDENTIALS"] = "/nonexistent/creds.json"

    result = subprocess.run(
        [self.script_path],
        env=self.env,
        capture_output=True,
        text=True,
    )

    self.assertNotEqual(result.returncode, 0)
    self.assertIn("Error: Could not resolve GCP Project ID", result.stderr)


if __name__ == "__main__":
  unittest.main()
