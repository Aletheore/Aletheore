"""Utility to resolve the active GCP Project ID from environment and config."""

import json
import os
import subprocess
from typing import Optional

try:
  from google import auth as google_auth
except ImportError:
  google_auth = None


def _resolve_project_from_env_vars(env_vars: list[str]) -> Optional[str]:
  for var in env_vars:
    project_id = os.environ.get(var)
    if project_id:
      return project_id.strip()
  return None


def _resolve_project_from_telemetry_env_vars() -> Optional[str]:
  env_vars = [
      "GOOGLE_CLOUD_TRACE_PROJECT",
      "GOOGLE_CLOUD_TELEMETRY_PROJECT",
  ]
  return _resolve_project_from_env_vars(env_vars)


def _resolve_project_from_current_project_env_vars() -> Optional[str]:
  env_vars = [
      "GOOGLE_CLOUD_PROJECT",
      "GCLOUD_PROJECT",
      "DEVSHELL_PROJECT_ID",
  ]
  return _resolve_project_from_env_vars(env_vars)


def _resolve_project_from_gcloud_config() -> Optional[str]:
  try:
    result = subprocess.run(
        [
            "gcloud",
            "config",
            "list",
            "--format='text(core.project)'",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    project_id = result.stdout.strip()
    if project_id and project_id != "(unset)":
      return project_id
  except (subprocess.SubprocessError, FileNotFoundError):
    pass
  return None


def _resolve_project_from_application_credentials() -> Optional[str]:
  if google_auth is None:
    return None
  try:
    unused_creds, project_id = google_auth.default()
    return project_id
  except Exception:
    return None


def resolve_project_id() -> str:
  """Scans environment variables, gcloud config, and credentials to find project ID."""
  for resolver in [
      _resolve_project_from_telemetry_env_vars,
      _resolve_project_from_gcloud_config,
      _resolve_project_from_current_project_env_vars,
      _resolve_project_from_application_credentials,
  ]:
    project_id = resolver()
    if project_id:
      return project_id
  raise RuntimeError("Could not resolve GCP Project ID")
