#!/usr/bin/env python3
"""Finds the candidate IAM role with least privilege granting a specific permission."""

import json
import os
import subprocess
import sys
from typing import List, Optional, Tuple


def get_default_project() -> str:
  """Retrieves the default project ID from gcloud config."""
  try:
    return subprocess.check_output(
        ["gcloud", "config", "get-value", "project"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
  except (subprocess.SubprocessError, OSError):
    return ""


def list_roles(
    filter_perm: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
  """Lists role names using gcloud iam roles list, optionally filtered by permission."""
  cmd = ["gcloud", "iam", "roles", "list", "--format=value(name)"]
  if filter_perm:
    cmd.append(f"--filter=includedPermissions:{filter_perm}")
  if extra_args:
    cmd.extend(extra_args)
  try:
    output = subprocess.check_output(
        cmd, text=True, stderr=subprocess.DEVNULL
    ).strip()
    return [line.strip() for line in output.splitlines() if line.strip()]
  except (subprocess.SubprocessError, OSError):
    return []


def describe_role(
    role_id: str, extra_args: Optional[List[str]] = None
) -> Optional[dict]:
  """Describes a role using gcloud iam roles describe and returns parsed JSON."""
  cmd = ["gcloud", "iam", "roles", "describe", role_id, "--format=json"]
  if extra_args:
    cmd.extend(extra_args)
  try:
    output = subprocess.check_output(
        cmd, text=True, stderr=subprocess.DEVNULL
    ).strip()
    if output:
      return json.loads(output)
  except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
    pass
  return None


def find_least_privileged_role(
    permission: str,
    project: Optional[str] = None,
    org: Optional[str] = None,
) -> Optional[str]:
  """Finds the role with the fewest permissions that includes the given permission."""
  candidates: List[Tuple[str, Optional[List[str]]]] = []

  # Predefined roles (filtered by permission, excluding overly broad basic roles)
  for role_name in list_roles(filter_perm=permission):
    if role_name not in [
        "roles/owner",
        "roles/editor",
        "roles/viewer",
        "roles/reader",
        "roles/writer",
        "roles/admin",
    ]:
      candidates.append((role_name, None))

  # Project-level custom roles
  if project:
    for role_name in list_roles(
        filter_perm=permission, extra_args=[f"--project={project}"]
    ):
      short_id = role_name.split("/")[-1]
      candidates.append((short_id, [f"--project={project}"]))

  # Organization-level custom roles
  if org:
    for role_name in list_roles(
        filter_perm=permission, extra_args=[f"--organization={org}"]
    ):
      short_id = role_name.split("/")[-1]
      candidates.append((short_id, [f"--organization={org}"]))

  valid_roles = []

  for role_id, extra_args in candidates:
    role_data = describe_role(role_id, extra_args)
    if not role_data:
      continue

    included_perms = role_data.get("includedPermissions", [])
    if permission in included_perms:
      role_name = role_data.get("name", role_id)
      role_name_lower = role_name.lower()

      # Identify service-level standard roles (for example, roles/compute.admin), avoiding basic overly broad roles
      is_service_level_standard = role_name.startswith("roles/") and (
          role_name_lower.endswith("admin")
          or role_name_lower.endswith("editor")
          or role_name_lower.endswith("viewer")
          or role_name_lower.endswith("reader")
          or role_name_lower.endswith("writer")
      )

      valid_roles.append({
          "name": role_name,
          "perm_count": len(included_perms),
          "is_service_level_standard": is_service_level_standard,
          "is_predefined": role_name.startswith("roles/"),
      })

  if not valid_roles:
    return None

  # Updated sorting logic: surface service-level standard roles first, then predefined roles, then by minimum permissions
  valid_roles.sort(
      key=lambda x: (
          not x["is_service_level_standard"],
          not x["is_predefined"],
          x["perm_count"],
      )
  )

  return valid_roles[0]["name"]


def main() -> None:
  permission = os.environ.get("IAM_PERMISSION", "")
  if not permission and len(sys.argv) > 1:
    permission = sys.argv[1]

  if not permission:
    print(
        "Error: IAM_PERMISSION environment variable or argument must be"
        " provided.",
        file=sys.stderr,
    )
    sys.exit(1)

  project = os.environ.get("TARGET_PROJECT_ID", "")
  if not project:
    project = get_default_project()

  org = os.environ.get("TARGET_ORGANIZATION_ID", "")

  best_role = find_least_privileged_role(permission, project=project, org=org)
  if best_role:
    print(best_role)
  else:
    print(
        "Warning: No candidate IAM role found that includes permission"
        f" '{permission}'.",
        file=sys.stderr,
    )


if __name__ == "__main__":
  main()
