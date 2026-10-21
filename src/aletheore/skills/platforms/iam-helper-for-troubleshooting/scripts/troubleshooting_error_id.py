#!/usr/bin/env python3
"""Troubleshoots IAM errors using the Policy Troubleshooter API.

Uses a base64 encoded error ID to query the API.
"""

import base64
import binascii
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict
import urllib.error
import urllib.request


def extract_project_id(error_id: str) -> str:
  """Best-effort extraction of a project ID from a base64 error_info_id.

  Args:
    error_id: The base64 error info ID string.

  Returns:
    Extracted project ID string, or "" if error_id isn't valid base64 or
    contains no projects/<id> segment.
  """
  try:
    decoded = base64.b64decode(error_id).decode("utf-8", errors="ignore")
  except (binascii.Error, ValueError):
    return ""
  match = re.search(r"projects/([^\s\x00-\x1f\"]+)", decoded)
  return match.group(1) if match else ""


def build_summary(data: Dict[str, Any]) -> Dict[str, Any]:
  """Projects raw troubleshootError API response down to decision tree fields.

  Args:
    data: Raw response dict from troubleshootError API.

  Returns:
    Dict containing projected fields for decision tree consumption.
  """
  return {
      "overallAccessState": data.get(
          "overallAccessState", data.get("accessState")
      ),
      "accessContext": data.get("accessContext", data.get("accessTuple", {})),
      "allowPolicyExplanation": data.get(
          "allowPolicyExplanation", data.get("explainedPolicies", [])
      ),
      "denyPolicyExplanation": data.get("denyPolicyExplanation", {}),
      "pabPolicyExplanation": data.get("pabPolicyExplanation", {}),
      "errors": data.get("errors", []),
  }


def enable_service(target_project_id: str) -> None:
  """Enables the Policy Troubleshooter API for the target project."""
  if target_project_id:
    subprocess.run(
        [
            "gcloud",
            "services",
            "enable",
            "policytroubleshooter.googleapis.com",
            f"--project={target_project_id}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def get_access_token() -> str:
  """Retrieves an access token using gcloud."""
  try:
    return subprocess.check_output(
        ["gcloud", "auth", "application-default", "print-access-token"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
  except (subprocess.SubprocessError, OSError):
    try:
      return subprocess.check_output(
          ["gcloud", "auth", "print-access-token"],
          text=True,
          stderr=subprocess.DEVNULL,
      ).strip()
    except (subprocess.SubprocessError, OSError) as e:
      print(
          "Error: Failed to obtain access token from gcloud. Please ensure"
          " you are authenticated (e.g. `gcloud auth application-default login`"
          f" or `gcloud auth login`). Details: {e}",
          file=sys.stderr,
      )
      sys.exit(1)


def main() -> None:
  """Queries Policy Troubleshooter for a specified IAM error ID."""
  error_id = os.environ.get("ERROR_ID", "")
  if not error_id and len(sys.argv) > 1:
    error_id = sys.argv[1]

  if not error_id:
    print(
        "Error: ERROR_ID environment variable or argument must be provided.",
        file=sys.stderr,
    )
    sys.exit(1)

  target_project_id = os.environ.get("TARGET_PROJECT_ID", "")

  if not target_project_id and error_id:
    target_project_id = extract_project_id(error_id)
  if not target_project_id:
    try:
      target_project_id = subprocess.check_output(
          ["gcloud", "config", "get-value", "project"],
          text=True,
          stderr=subprocess.DEVNULL,
      ).strip()
    except (subprocess.SubprocessError, OSError):
      pass

  enable_service(target_project_id)
  token = get_access_token()

  url = (
      "https://policytroubleshooter.googleapis.com/v3beta/iam:troubleshootError"
  )
  headers = {
      "Authorization": f"Bearer {token}",
      "Content-Type": "application/json",
  }
  if target_project_id:
    headers["X-Goog-User-Project"] = target_project_id

  req = urllib.request.Request(
      url,
      data=json.dumps({"error_info_id": error_id}).encode("utf-8"),
      headers=headers,
      method="POST",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      data = json.loads(resp.read().decode("utf-8"))
      print(json.dumps(build_summary(data), indent=2))
  except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="ignore")
    print(
        f"Error calling Policy Troubleshooter API: HTTP {e.code}"
        f" {e.reason}\n{body}",
        file=sys.stderr,
    )
    sys.exit(1)
  except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
    print(f"Error calling Policy Troubleshooter API: {e}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()
