#!/usr/bin/env python3
r"""Hermetic CLI utility for validating Assured Workloads request payloads.

Requires dynamic ingestion of Data Boundary frameworks directly from Assured
Workloads Frameworks Library via --frameworks-file:
  python3 scripts/validate_workload_payload.py \
    --payload-json='{"displayName":"Enclave","complianceRegime":"FEDRAMP_HIGH"}' \
    --org-id=123456789 \
    --frameworks-file=frameworks.json

Assured Workloads Frameworks Library (gcloud compliance-manager frameworks list)
is the authoritative Source of Truth (SOT). No static fallback lists are used.
"""

import argparse
import json
import re
import sys
from typing import Any

VALID_ENFORCEMENT_MODES: frozenset[str] = frozenset({
    "PREVENTIVE",
    "DETECTIVE",
    "AUDIT",
})

_BUILTIN_PREFIX_RE = re.compile(r"^builtin[-_]")
_FOLDER_RE = re.compile(r"folders/\d+")
_PROJECT_RE = re.compile(r"projects/[a-z0-9-]+")
_FRAMEWORK_NAME_RE = re.compile(
    r"organizations/(\d+)/locations/([^/]+)/frameworks/([^/]+)"
)


def load_allowed_frameworks_and_regimes(
    frameworks_data: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
  """Extracts valid framework IDs and compliance regimes from v1.Framework."""
  if not frameworks_data:
    raise ValueError(
        "Empty frameworks list provided. Assured Workloads Frameworks Library"
        " export is empty."
    )

  allowed_frameworks: set[str] = set()
  allowed_regimes: set[str] = set()

  for framework in frameworks_data:
    # Filter by public v1.Framework.category = ASSURED_WORKLOADS
    category = framework.get("category", [])
    if isinstance(category, list):
      category_matches = any(
          "ASSURED_WORKLOADS" in str(cat).upper() for cat in category
      )
    else:
      category_matches = "ASSURED_WORKLOADS" in str(category).upper()

    if category and not category_matches:
      continue

    # Extract framework ID from public v1.Framework.name
    name = framework.get("name", "")
    framework_id = name.split("/")[-1] if name else ""
    if framework_id:
      allowed_frameworks.add(framework_id)
      allowed_frameworks.add(framework_id.replace("_", "-"))
      allowed_frameworks.add(framework_id.replace("-", "_"))

    # Extract compliance regimes from public v1.CloudControlDetails.parameters
    for control in framework.get("cloudControlDetails", []):
      for param in control.get("parameters", []):
        param_value = param.get("parameterValue", {})
        if isinstance(param_value, dict):
          string_value = str(param_value.get("stringValue", "")).strip().upper()
          if string_value.startswith("COMPLIANCE_REGIME_"):
            clean_regime = string_value.replace("COMPLIANCE_REGIME_", "")
            allowed_regimes.add(string_value)
            allowed_regimes.add(clean_regime)

    # Derive standard regime token from framework resource ID
    if framework_id:
      normalized_regime = (
          _BUILTIN_PREFIX_RE.sub("", framework_id).upper().replace("-", "_")
      )
      allowed_regimes.add(normalized_regime)
      if normalized_regime == "EU_DATA_BOUNDARY_AND_SUPPORT":
        allowed_regimes.add("EU_REGIONS_AND_SUPPORT")
      elif normalized_regime == "US_DATA_BOUNDARY":
        allowed_regimes.add("US_REGIONAL_DATA_BOUNDARY")
      elif normalized_regime == "AUSTRALIA_DATA_BOUNDARY":
        allowed_regimes.add("AU_REGIONS_AND_SUPPORT")
      elif normalized_regime == "JAPAN_DATA_BOUNDARY":
        allowed_regimes.add("JP_REGIONS_AND_SUPPORT")
      elif normalized_regime == "ISRAEL_DATA_BOUNDARY":
        allowed_regimes.add("ISR_REGIONS_AND_SUPPORT")

  if not allowed_frameworks:
    raise ValueError(
        "No Assured Workloads frameworks found in frameworks.json export."
        " Ensure the catalog contains frameworks with category"
        " ASSURED_WORKLOADS."
    )

  return allowed_frameworks, allowed_regimes


def validate_v1_workload_payload(
    payload: dict[str, Any],
    org_id: str,
    location: str,
    allowed_regimes: set[str],
) -> dict[str, Any]:
  """Validates payload against Assured Workloads v1 GA schema."""
  display_name = payload.get("displayName")
  if not display_name or not isinstance(display_name, str):
    raise ValueError("Missing or invalid 'displayName' in v1 workload payload.")

  regime = payload.get("complianceRegime")
  if not regime or not isinstance(regime, str):
    raise ValueError("Missing 'complianceRegime' in v1 workload payload.")

  regime_upper = regime.upper().strip()
  if regime_upper not in allowed_regimes and not any(
      regime_upper == candidate
      or f"COMPLIANCE_REGIME_{regime_upper}" == candidate
      for candidate in allowed_regimes
  ):
    raise ValueError(
        f"Invalid complianceRegime '{regime}'. Allowed Data Boundary regimes"
        " derived dynamically from Assured Workloads Frameworks Library SOT:"
        f" {sorted(allowed_regimes)}"
    )

  if "resourceSettings" in payload:
    for setting in payload.get("resourceSettings", []):
      resource_id = setting.get("resourceId", "")
      if str(resource_id).startswith("folders/") or str(resource_id).startswith(
          "projects/"
      ):
        raise ValueError(
            f"Invalid resourceSettings: resourceId '{resource_id}'. Assured"
            " Workloads v1 creates a NEW folder and does not support adopting"
            " existing folders or projects. Use Assured Workloads v2"
            " (resourceConfig) for brownfield adoption."
        )

  clean_org = org_id.replace("organizations/", "").strip()
  clean_loc = location.strip() or "us-central1"

  endpoint_path = (
      "POST https://assuredworkloads.googleapis.com/v1/"
      f"organizations/{clean_org}/locations/{clean_loc}/workloads"
  )
  console_url = f"https://console.cloud.google.com/compliance/assuredworkloads?organizationId={clean_org}"

  return {
      "apiVersion": "v1 (GA)",
      "consoleUrl": console_url,
      "restEndpoint": endpoint_path,
      "requestBody": payload,
  }


def validate_v2_workload_payload(
    payload: dict[str, Any],
    org_id: str,
    location: str,
    allowed_frameworks: set[str],
) -> dict[str, Any]:
  """Validates payload against Assured Workloads v2 preview schema."""
  # 1. Validate Target Resource Config
  resource_config = payload.get("resourceConfig")
  if not resource_config or not isinstance(resource_config, dict):
    raise ValueError(
        "Missing required 'resourceConfig' object in workload payload."
    )

  has_target = False
  if "existingFolder" in resource_config:
    folder = resource_config["existingFolder"]
    if not _FOLDER_RE.fullmatch(folder):
      raise ValueError(
          f"Invalid 'existingFolder' format: {folder}. Expected"
          " 'folders/{id}'."
      )
    has_target = True
  elif "existingProject" in resource_config:
    project = resource_config["existingProject"]
    if not _PROJECT_RE.fullmatch(project):
      raise ValueError(f"Invalid 'existingProject' format: {project}.")
    has_target = True

  if not has_target:
    raise ValueError(
        "resourceConfig must specify 'existingFolder' or 'existingProject'."
    )

  # 2. Validate Framework Reference
  framework_ref = payload.get("framework")
  if (
      not framework_ref
      or not isinstance(framework_ref, dict)
      or "framework" not in framework_ref
  ):
    raise ValueError(
        "Missing required 'framework.framework' resource reference in workload"
        " payload."
    )

  framework_name = framework_ref["framework"]
  framework_match = _FRAMEWORK_NAME_RE.fullmatch(framework_name)
  if not framework_match:
    raise ValueError(
        f"Invalid framework resource path: {framework_name}. Expected"
        " 'organizations/{org}/locations/{loc}/frameworks/{framework_id}'."
    )
  framework_id = framework_match.group(3)
  if framework_id not in allowed_frameworks:
    raise ValueError(
        f"Unsupported framework ID '{framework_id}'. Allowed Data Boundary"
        " frameworks derived dynamically from Assured Workloads Frameworks"
        f" Library SOT: {sorted(allowed_frameworks)}"
    )

  # 3. Validate Cloud Control Configs (if provided)
  control_configs = payload.get("cloudControlConfigs", [])
  for idx, control in enumerate(control_configs):
    mode = control.get("enforcementMode")
    if mode not in VALID_ENFORCEMENT_MODES:
      raise ValueError(
          f"Control index {idx} has invalid enforcementMode '{mode}'. Valid"
          f" modes: {VALID_ENFORCEMENT_MODES}"
      )

  # 4. Construct REST Endpoint URL & Google Cloud Console URL
  clean_org = org_id.replace("organizations/", "").strip()
  clean_loc = location.strip() or "global"
  endpoint_path = (
      "POST https://assuredworkloads.googleapis.com/v2/"
      f"organizations/{clean_org}/locations/{clean_loc}/workloads"
  )
  console_url = f"https://console.cloud.google.com/compliance/assuredworkloads?organizationId={clean_org}"

  return {
      "apiVersion": "v2 (Preview)",
      "consoleUrl": console_url,
      "restEndpoint": endpoint_path,
      "requestBody": payload,
  }


def validate_workload_payload(
    payload: dict[str, Any],
    org_id: str,
    location: str,
    frameworks_data: list[dict[str, Any]],
) -> dict[str, Any]:
  """Dispatches validation to v1 GA or v2 Preview schema."""
  allowed_frameworks, allowed_regimes = load_allowed_frameworks_and_regimes(
      frameworks_data
  )

  # If complianceRegime is present, validate as GA v1 schema
  if "complianceRegime" in payload or (
      "resourceConfig" not in payload and "framework" not in payload
  ):
    v1_loc = "us-central1" if location == "global" else location
    return validate_v1_workload_payload(
        payload, org_id, v1_loc, allowed_regimes
    )

  # Otherwise validate as v2 preview schema
  return validate_v2_workload_payload(
      payload, org_id, location, allowed_frameworks
  )


def main() -> None:
  parser = argparse.ArgumentParser(
      description=(
          "Validate Assured Workloads request payload against dynamic Assured"
          " Workloads Frameworks Library frameworks."
      )
  )
  parser.add_argument(
      "--payload-json",
      required=True,
      help="Raw JSON string or path to JSON payload",
  )
  parser.add_argument(
      "--org-id", required=True, help="Organization ID (e.g. 123456789)"
  )
  parser.add_argument(
      "--location",
      default="global",
      help="Location (e.g. global, us-central1, europe-west3)",
  )
  parser.add_argument(
      "--frameworks-file",
      required=True,
      help=(
          "Path to Assured Workloads Frameworks Library frameworks JSON export"
          " (authoritative SOT)"
      ),
  )
  args = parser.parse_args()

  try:
    if args.payload_json.startswith("{"):
      payload = json.loads(args.payload_json)
    else:
      with open(args.payload_json, "r", encoding="utf-8") as f:
        payload = json.load(f)

    with open(args.frameworks_file, "r", encoding="utf-8") as f:
      frameworks_data = json.load(f)

    output = validate_workload_payload(
        payload, args.org_id, args.location, frameworks_data=frameworks_data
    )
    print(json.dumps(output, indent=2))
  except (ValueError, OSError, KeyError, TypeError) as e:
    sys.stderr.write(f"VALIDATION_ERROR: {str(e)}\n")
    sys.exit(1)


if __name__ == "__main__":
  main()
