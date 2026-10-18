#!/usr/bin/env python3
r"""Hermetic CLI utility for evaluating CAIS telemetry against Org Policy specs.

Evaluates the 3 deterministic Organization Policy parameter sets bound in
`google.cloud.cloudsecuritycompliance.v1.Framework.cloudControlDetails`:
  1. Resource Locations (`builtin-restrict-resource-locations`)
  2. Service Usage Restrictions / SUR
     (`builtin-enforce-service-usage-restrictions`)
  3. CMEK Encryption Requirements (`builtin-require-cmek`)

Usage:
  python3 scripts/evaluate_control_rules.py \
    --telemetry-file=macro_telemetry.json \
    --deep-telemetry-file=deep_telemetry.json \
    --framework=builtin-il5 \
    --frameworks-file=frameworks.json
"""

import argparse
import json
import sys
import types
from typing import Any

CONTROL_PLANE_SERVICE_PREFIXES: frozenset[str] = frozenset({
    "cloudresourcemanager.googleapis.com",
    "orgpolicy.googleapis.com",
    "cloudasset.googleapis.com",
    "accesscontextmanager.googleapis.com",
})

CONTROL_PLANE_ASSET_TYPES: frozenset[str] = frozenset({
    "cloudresourcemanager.googleapis.com/Organization",
    "cloudresourcemanager.googleapis.com/Folder",
    "cloudresourcemanager.googleapis.com/Project",
    "orgpolicy.googleapis.com/Policy",
    "cloudasset.googleapis.com/Feed",
    "logging.googleapis.com/BillingAccountSettings",
    "logging.googleapis.com/OrganizationSettings",
    "logging.googleapis.com/FolderSettings",
})

# Standard CAI data-plane asset types associated with CMEK-governed services
# when get_target_asset_types is invoked without Pass 1 active_resources.
DEFAULT_CMEK_SERVICE_ASSET_TYPES: types.MappingProxyType[
    str, tuple[str, ...]
] = types.MappingProxyType({
    "compute.googleapis.com": (
        "compute.googleapis.com/Instance",
        "compute.googleapis.com/Disk",
    ),
    "storage.googleapis.com": ("storage.googleapis.com/Bucket",),
    "sqladmin.googleapis.com": ("sqladmin.googleapis.com/Instance",),
    "container.googleapis.com": ("container.googleapis.com/Cluster",),
    "bigquery.googleapis.com": (
        "bigquery.googleapis.com/Dataset",
        "bigquery.googleapis.com/Table",
    ),
    "pubsub.googleapis.com": ("pubsub.googleapis.com/Topic",),
    "secretmanager.googleapis.com": ("secretmanager.googleapis.com/Secret",),
    "spanner.googleapis.com": ("spanner.googleapis.com/Instance",),
    "run.googleapis.com": ("run.googleapis.com/Service",),
    "artifactregistry.googleapis.com": (
        "artifactregistry.googleapis.com/Repository",
    ),
    "aiplatform.googleapis.com": ("aiplatform.googleapis.com/Dataset",),
})

# Location value groups used by constraints/gcp.resourceLocations in csclibrary
LOCATION_GROUP_PREFIXES: types.MappingProxyType[str, tuple[str, ...]] = (
    types.MappingProxyType({
        "us-locations": ("us-", "us"),
        "in:us-locations": ("us-", "us"),
        "eu-locations": ("europe-", "eu"),
        "in:eu-locations": ("europe-", "eu"),
    })
)

LOCATION_GROUP_EXACT_REGIONS: types.MappingProxyType[str, frozenset[str]] = (
    types.MappingProxyType({
        "canada-locations": frozenset(
            {"northamerica-northeast1", "northamerica-northeast2"}
        ),
        "in:canada-locations": frozenset(
            {"northamerica-northeast1", "northamerica-northeast2"}
        ),
        "australia-locations": frozenset(
            {"australia-southeast1", "australia-southeast2"}
        ),
        "in:australia-locations": frozenset(
            {"australia-southeast1", "australia-southeast2"}
        ),
        "india-locations": frozenset({"asia-south1", "asia-south2"}),
        "in:india-locations": frozenset({"asia-south1", "asia-south2"}),
        "japan-locations": frozenset({"asia-northeast1", "asia-northeast2"}),
        "in:japan-locations": frozenset({"asia-northeast1", "asia-northeast2"}),
        "israel-locations": frozenset({"me-west1"}),
        "in:israel-locations": frozenset({"me-west1"}),
        "ksa-locations": frozenset({"me-central2"}),
        "in:ksa-locations": frozenset({"me-central2"}),
        "switzerland-locations": frozenset({"europe-west6"}),
        "in:switzerland-locations": frozenset({"europe-west6"}),
        "uk-locations": frozenset({"europe-west2"}),
        "in:uk-locations": frozenset({"europe-west2"}),
    })
)


def is_control_plane_metadata(resource: dict[str, Any]) -> bool:
  """Identifies control-plane metadata to exclude from workload evaluation."""
  asset_type = resource.get("assetType", "")
  prefix = asset_type.split("/")[0] if "/" in asset_type else asset_type
  return (
      prefix in CONTROL_PLANE_SERVICE_PREFIXES
      or asset_type in CONTROL_PLANE_ASSET_TYPES
  )


def _extract_string_list_from_param_value(
    param_val: dict[str, Any],
) -> list[str]:
  """Extracts a list of strings from a v1.ParamValue protobuf JSON structure."""
  if not isinstance(param_val, dict):
    return []
  if "stringListValue" in param_val:
    values = param_val["stringListValue"].get("values", [])
    return [str(val) for val in values if val]
  if "stringValue" in param_val and param_val["stringValue"]:
    return [str(param_val["stringValue"])]
  if "oneofValue" in param_val and isinstance(param_val["oneofValue"], dict):
    nested_param_val = param_val["oneofValue"].get("parameterValue", {})
    return _extract_string_list_from_param_value(nested_param_val)
  return []


def parse_framework_org_policy_spec(
    frameworks: list[dict[str, Any]], framework_id: str
) -> dict[str, Any]:
  """Extracts the 3 quantitative Org Policy parameter sets from a Framework."""
  if not frameworks:
    raise ValueError(
        "Empty frameworks list provided. Assured Workloads Frameworks Library"
        " export is empty."
    )

  clean_framework_id = framework_id.split("/")[-1].strip()
  matched_framework: dict[str, Any] | None = None
  for framework in frameworks:
    framework_name = framework.get("name", "")
    candidate_id = framework_name.split("/")[-1] if framework_name else ""
    if candidate_id == clean_framework_id or candidate_id.replace(
        "_", "-"
    ) == clean_framework_id.replace("_", "-"):
      matched_framework = framework
      break

  if matched_framework is None:
    raise ValueError(
        f"Framework '{clean_framework_id}' not found in frameworks.json export."
        " Ensure 'gcloud compliance-manager frameworks list' output includes"
        " this framework."
    )

  allowed_locations: set[str] = set()
  allowed_services: set[str] = set()
  cmek_services: set[str] = set()
  cmek_required = False

  for control in matched_framework.get("cloudControlDetails", []):
    control_name = control.get("name", "")
    control_id = control_name.split("/")[-1] if control_name else ""
    params = control.get("parameters", [])

    if control_id == "builtin-restrict-resource-locations":
      for param in params:
        if param.get("name") in ("locations", "allowedLocations"):
          allowed_locations.update(
              _extract_string_list_from_param_value(
                  param.get("parameterValue", {})
              )
          )
    elif control_id == "builtin-enforce-service-usage-restrictions":
      for param in params:
        if param.get("name") in ("serviceUsage", "allowedServices"):
          allowed_services.update(
              _extract_string_list_from_param_value(
                  param.get("parameterValue", {})
              )
          )
    elif control_id == "builtin-require-cmek":
      cmek_required = True
      for param in params:
        if param.get("name") == "services":
          cmek_services.update(
              _extract_string_list_from_param_value(
                  param.get("parameterValue", {})
              )
          )

  return {
      "framework_id": clean_framework_id,
      "allowed_locations": allowed_locations,
      "allowed_services": allowed_services,
      "cmek_services": cmek_services,
      "cmek_required": cmek_required,
  }


def is_location_allowed(location: str, allowed_locations: set[str]) -> bool:
  """Checks whether a CAIS resource location satisfies resourceLocations."""
  if not allowed_locations:
    return True
  loc = location.strip().lower()
  region = "-".join(loc.split("-")[:2]) if "-" in loc else loc

  normalized_allowed = {
      allowed_loc.strip().lower() for allowed_loc in allowed_locations
  }
  if (
      loc in normalized_allowed
      or region in normalized_allowed
      or f"in:{loc}-locations" in normalized_allowed
      or f"in:{region}-locations" in normalized_allowed
  ):
    return True

  for group_token, prefixes in LOCATION_GROUP_PREFIXES.items():
    if group_token in normalized_allowed:
      if any(
          loc == prefix
          or loc.startswith(prefix)
          or region == prefix
          or region.startswith(prefix)
          for prefix in prefixes
      ):
        return True

  for group_token, exact_regions in LOCATION_GROUP_EXACT_REGIONS.items():
    if group_token in normalized_allowed:
      if loc in exact_regions or region in exact_regions:
        return True

  return False


def get_target_asset_types(
    framework_id: str,
    frameworks: list[dict[str, Any]],
    active_resources: list[dict[str, Any]] | None = None,
) -> list[str]:
  """Derives the CAIS asset types requiring Pass 2 CMEK inspection (`kmsKeys`).

  Only the 6/38 Data Boundary frameworks that include `builtin-require-cmek`
  require Pass 2 CMEK ingestion; for the remaining 32/38 frameworks, this
  returns an empty list so Pass 2 is skipped.

  Args:
    framework_id: Target framework identifier (e.g. 'builtin-il5').
    frameworks: Parsed list of v1.Framework objects from frameworks.json.
    active_resources: Optional Pass 1 CAIS resource list to intersect against.

  Returns:
    Sorted list of CAIS assetType strings requiring Pass 2 CMEK inspection.
  """
  spec = parse_framework_org_policy_spec(frameworks, framework_id)
  if not spec["cmek_required"] or not spec["cmek_services"]:
    return []

  cmek_services: set[str] = spec["cmek_services"]
  if active_resources is not None:
    targets: set[str] = set()
    for resource in active_resources:
      if is_control_plane_metadata(resource):
        continue
      asset_type = resource.get("assetType", "")
      if "/" not in asset_type:
        continue
      service_prefix = asset_type.split("/")[0]
      if service_prefix in cmek_services:
        targets.add(asset_type)
    return sorted(targets)

  default_targets: set[str] = set()
  for service_name in cmek_services:
    for asset_type in DEFAULT_CMEK_SERVICE_ASSET_TYPES.get(service_name, ()):
      default_targets.add(asset_type)
  return sorted(default_targets)


def _extract_kms_keys(resource: dict[str, Any]) -> list[str]:
  """Extracts kmsKeys from CAIS ResourceSearchResult or additionalAttributes."""
  top_keys = resource.get("kmsKeys")
  if isinstance(top_keys, list) and top_keys:
    return [str(key) for key in top_keys if key]
  if isinstance(top_keys, str) and top_keys:
    return [top_keys]
  attrs = resource.get("additionalAttributes", {})
  if isinstance(attrs, dict):
    attr_keys = attrs.get("kmsKeys", [])
    if isinstance(attr_keys, list) and attr_keys:
      return [str(key) for key in attr_keys if key]
  return []


def evaluate_telemetry(
    resources: list[dict[str, Any]],
    framework_id: str,
    frameworks: list[dict[str, Any]],
    deep_resources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  """Evaluates CAIS telemetry against the 3 deterministic Org Policy specs."""
  spec = parse_framework_org_policy_spec(frameworks, framework_id)
  cmek_required: bool = spec["cmek_required"]

  # Index Pass 2 deep telemetry kmsKeys by resource name so Pass 1 telemetry
  # remains the full-scope source of truth for Location and SUR.
  deep_kms_by_name: dict[str, list[str]] = {}
  if deep_resources:
    for deep_resource in deep_resources:
      resource_name = deep_resource.get("name")
      if resource_name:
        deep_kms_by_name[resource_name] = _extract_kms_keys(deep_resource)

  workload_resources = [
      resource
      for resource in resources
      if not is_control_plane_metadata(resource)
  ]
  total_assets = len(workload_resources)

  evaluation_note = (
      "Pre-flight readiness indicators evaluate high-confidence Organization"
      " Policy parameters (location, service usage, CMEK) over CAIS metadata."
      " Authoritative CES and REST control evaluations run server-side via the"
      " Continuous Evaluation Service (CES) after framework deployment."
  )

  if total_assets == 0:
    return {
        "frameworkId": framework_id,
        "totalAssets": 0,
        "cmekRequired": cmek_required,
        "scores": {
            "locationScore": 1.0,
            "serviceScore": 1.0,
            "cmekScore": 1.0,
            "compositeScore": 1.0,
        },
        "orgPolicyDriftIndicators": {
            "outOfBoundaryLocations": [],
            "unapprovedServices": [],
            "missingCmekAssets": [],
        },
        "evaluationNote": evaluation_note,
    }

  # 1. Location Org Policy Check (constraints/gcp.resourceLocations)
  allowed_locations: set[str] = spec["allowed_locations"]
  loc_targets = [
      resource
      for resource in workload_resources
      if resource.get("location") and resource.get("location") != "global"
  ]
  compliant_loc_count = 0
  out_of_boundary_locations: list[dict[str, Any]] = []
  for resource in loc_targets:
    loc = resource.get("location", "")
    if is_location_allowed(loc, allowed_locations):
      compliant_loc_count += 1
    else:
      out_of_boundary_locations.append(
          {"name": resource.get("name"), "location": loc}
      )

  total_loc_assets = len(loc_targets)
  location_score = (
      compliant_loc_count / total_loc_assets if total_loc_assets else 1.0
  )

  # 2. Service Usage Restriction Check (constraints/gcp.restrictServiceUsage)
  allowed_services: set[str] = spec["allowed_services"]
  compliant_svc_count = 0
  unapproved_services: list[dict[str, Any]] = []
  for resource in workload_resources:
    asset_type = resource.get("assetType", "")
    prefix = asset_type.split("/")[0] if "/" in asset_type else asset_type
    if not allowed_services or prefix in allowed_services:
      compliant_svc_count += 1
    else:
      unapproved_services.append(
          {"name": resource.get("name"), "assetType": asset_type}
      )

  service_score = compliant_svc_count / total_assets if total_assets else 1.0

  # 3. CMEK Org Policy Check (constraints/gcp.restrictNonCmekServices)
  cmek_services: set[str] = spec["cmek_services"]
  missing_cmek_assets: list[str] = []
  if cmek_required and cmek_services:
    cmek_targets = [
        resource
        for resource in workload_resources
        if (
            resource.get("assetType", "").split("/")[0]
            if "/" in resource.get("assetType", "")
            else resource.get("assetType", "")
        )
        in cmek_services
    ]
    for resource in cmek_targets:
      resource_name = resource.get("name", "")
      kms_keys = _extract_kms_keys(resource) or deep_kms_by_name.get(
          resource_name, []
      )
      if not kms_keys:
        missing_cmek_assets.append(resource_name)
    cmek_score = (
        1.0 - (len(missing_cmek_assets) / len(cmek_targets))
        if cmek_targets
        else 1.0
    )
  else:
    cmek_score = 1.0

  if cmek_required:
    composite_score = round(
        0.40 * location_score + 0.35 * service_score + 0.25 * cmek_score, 4
    )
  else:
    composite_score = round(
        (0.40 / 0.75) * location_score + (0.35 / 0.75) * service_score, 4
    )

  return {
      "frameworkId": framework_id,
      "totalAssets": total_assets,
      "cmekRequired": cmek_required,
      "scores": {
          "locationScore": round(location_score, 4),
          "serviceScore": round(service_score, 4),
          "cmekScore": round(cmek_score, 4),
          "compositeScore": composite_score,
      },
      "orgPolicyDriftIndicators": {
          "outOfBoundaryLocations": out_of_boundary_locations,
          "unapprovedServices": unapproved_services,
          "missingCmekAssets": missing_cmek_assets,
      },
      "evaluationNote": evaluation_note,
  }


def main() -> None:
  parser = argparse.ArgumentParser(
      description=(
          "Evaluate CAIS telemetry against Assured Workloads Organization"
          " Policy parameters from frameworks.json."
      )
  )
  parser.add_argument(
      "--framework", required=True, help="Framework ID (e.g. builtin-il5)"
  )
  parser.add_argument(
      "--telemetry-file",
      required=False,
      help="Path to Pass 1 CAIS macro telemetry JSON export",
  )
  parser.add_argument(
      "--deep-telemetry-file",
      required=False,
      help="Optional path to Pass 2 CAIS deep telemetry JSON export (kmsKeys)",
  )
  parser.add_argument(
      "--frameworks-file",
      required=True,
      help=(
          "Path to frameworks JSON export from 'gcloud compliance-manager"
          " frameworks list' (authoritative SOT)"
      ),
  )
  parser.add_argument(
      "--get-target-asset-types",
      action="store_true",
      help=(
          "Derive and print CMEK-governed CAIS asset types for Pass 2"
          " inspection"
      ),
  )
  args = parser.parse_args()

  try:
    with open(args.frameworks_file, "r", encoding="utf-8") as f:
      frameworks = json.load(f)

    if args.get_target_asset_types:
      active_resources = None
      if args.telemetry_file:
        with open(args.telemetry_file, "r", encoding="utf-8") as f:
          active_resources = json.load(f)
      target_types = get_target_asset_types(
          args.framework, frameworks, active_resources
      )
      print(",".join(target_types))
      return

    if not args.telemetry_file:
      sys.stderr.write(
          "EVALUATION_ERROR: --telemetry-file is required for rule"
          " evaluation.\n"
      )
      sys.exit(1)

    with open(args.telemetry_file, "r", encoding="utf-8") as f:
      resources = json.load(f)

    deep_resources = None
    if args.deep_telemetry_file:
      try:
        with open(args.deep_telemetry_file, "r", encoding="utf-8") as f:
          deep_resources = json.load(f)
      except FileNotFoundError:
        deep_resources = None

    result = evaluate_telemetry(
        resources, args.framework, frameworks, deep_resources=deep_resources
    )
    print(json.dumps(result, indent=2))
  except (ValueError, OSError, KeyError, TypeError) as e:
    sys.stderr.write(f"EVALUATION_ERROR: {str(e)}\n")
    sys.exit(1)


if __name__ == "__main__":
  main()
