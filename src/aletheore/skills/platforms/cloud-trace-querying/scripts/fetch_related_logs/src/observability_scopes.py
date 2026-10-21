"""Resolves logging query projects using GCP Observability Scopes."""

import json
import logging
from typing import Optional, Set
import google.auth as google_auth
from google.auth.transport.urllib3 import AuthorizedHttp

_logger = logging.getLogger(__name__)


def _get_authorized_http() -> AuthorizedHttp | None:
  """Initializes an authorized HTTP client using default credentials."""
  try:
    credentials, _ = google_auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return AuthorizedHttp(credentials)
  except Exception as e:
    _logger.warning("Failed to initialize authorized HTTP client: %s", e)
    return None


def _fetch_default_observability_scope(
    http: AuthorizedHttp, trace_proj: str
) -> str | None:
  """Retrieves the logScope resource name for the project's Default scope."""
  url = (
      "https://observability.googleapis.com/v1/projects/"
      f"{trace_proj}/locations/global/scopes/_Default"
  )
  try:
    resp = http.request("GET", url, timeout=10.0)
    if resp.status == 200:
      return json.loads(resp.data.decode("utf-8")).get("logScope")
    _logger.warning(
        "Observability scope request failed with status %d: %s",
        resp.status,
        resp.data.decode("utf-8"),
    )
  except Exception as e:
    _logger.warning("Failed to fetch observability scope: %s", e)
  return None


def _fetch_logging_scope_projects(
    http: AuthorizedHttp, log_scope_name: str
) -> list[str]:
  """Retrieves resource names from the specified log scope."""
  url = f"https://logging.googleapis.com/v2/{log_scope_name}"
  try:
    resp = http.request("GET", url, timeout=10.0)
    if resp.status == 200:
      return json.loads(resp.data.decode("utf-8")).get("resourceNames", [])
    _logger.warning(
        "Logging scope request failed with status %d: %s",
        resp.status,
        resp.data.decode("utf-8"),
    )
  except Exception as e:
    _logger.warning("Failed to fetch logging scope projects: %s", e)
  return []


def _parse_project_ids(resource_names: list[str]) -> set[str]:
  """Extracts project IDs from resource names."""
  project_ids = set()
  for rn in resource_names:
    parts = rn.split("/")
    if len(parts) >= 2 and parts[-2] == "projects":
      project_ids.add(parts[-1])
  return project_ids


def resolve_query_projects(
    trace_projects: list[str], log_projects: list[str] | None
) -> set[str]:
  """Resolves the set of logging project IDs to query."""
  resolved = set()
  if log_projects:
    resolved.update(log_projects)
  http = _get_authorized_http()
  if not http:
    return resolved | set(trace_projects)

  for trace_proj in trace_projects:
    log_scope_name = _fetch_default_observability_scope(http, trace_proj)
    if not log_scope_name:
      resolved.add(trace_proj)
      continue
    resources = _fetch_logging_scope_projects(http, log_scope_name)
    parsed = _parse_project_ids(resources)
    if parsed:
      resolved.update(parsed)
    else:
      resolved.add(trace_proj)

  return resolved
