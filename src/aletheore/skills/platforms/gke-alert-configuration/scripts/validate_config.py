#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HCL configuration and PromQL validation script for GKE alert policies.

Parses Terraform HCL alert policy resource blocks or pre-edit change plans
(such as changes.json), detects duplicate targets for Kubernetes resources,
and lints Prometheus queries for correct syntax, time windows, and essential
label filters.
"""

import argparse
import glob
import json
import os
import re
import sys


class PromQLLinter:
  """Linter for PromQL query strings inside alert policies."""

  @classmethod
  def check_balanced_chars(cls, query, open_char, close_char):
    """Checks if parenthesis or braces are balanced."""
    count = 0
    for i, char in enumerate(query):
      if char == open_char:
        count += 1
      elif char == close_char:
        count -= 1
        if count < 0:
          return f"Unbalanced '{close_char}' at position {i}"
    if count != 0:
      return f"Unbalanced '{open_char}' (net count: {count})"
    return None

  @classmethod
  def lint_query(cls, query):
    """Runs a suite of sanity lint checks on a PromQL query.

    Args:
        query: The PromQL query string to lint.

    Returns:
        A list of string lint error messages. Empty if valid.
    """
    errors = []

    # 1. Balanced parentheses
    paren_err = cls.check_balanced_chars(query, "(", ")")
    if paren_err:
      errors.append(f"Parentheses error: {paren_err}")

    # 2. Balanced curly braces
    brace_err = cls.check_balanced_chars(query, "{", "}")
    if brace_err:
      errors.append(f"Curly braces error: {brace_err}")

    # 3. Time window validations (such as [5m], [1w:5m], [3d], [1h])
    window_matches = re.finditer(r"\[([^\]]+)\]", query)
    for match in window_matches:
      window_str = match.group(1)
      if not re.fullmatch(r"\d+[smhdw](:(\d+[smhdw])?)?", window_str):
        errors.append(
            "Invalid Prometheus time window/subquery interval:"
            f" '[{window_str}]' at position {match.start()}"
        )

    # 4. Lookback offset range validation (such as offset 1w, offset 1d)
    offset_matches = re.finditer(r"\boffset\s+(\S+)", query)
    for match in offset_matches:
      offset_str = match.group(1)
      if not re.fullmatch(r"\d+[smhdw]", offset_str):
        errors.append(
            f"Invalid lookback offset format: 'offset {offset_str}' at position"
            f" {match.start()}"
        )

    # 5. Ensure the query references Kubernetes relevant labels in a label filter
    # or grouping aggregation.
    k8s_labels = [
        "cluster",
        "cluster_name",
        "namespace",
        "namespace_name",
        "pod",
        "pod_name",
        "container",
        "container_name",
        "service",
        "job",
        "node",
        "instance",
    ]

    has_k8s_reference = False
    for label in k8s_labels:
      if label in query:
        has_k8s_reference = True
        break

    if not has_k8s_reference:
      errors.append(
          "Query is missing Kubernetes-relevant resource references. It should "
          "reference at least one standard label like 'cluster', 'namespace', "
          "'pod', 'container', 'service', or 'job' in filters or groupings."
      )

    return errors


class HCLScanner:
  """Scanner for HCL alert policies."""

  @classmethod
  def extract_alert_policies(cls, hcl_content):
    """Extracts resource 'google_monitoring_alert_policy' blocks and metadata."""
    policies = []
    pattern = re.compile(
        r'resource\s+"google_monitoring_alert_policy"\s+"([^"]+)"\s*\{'
    )

    for match in pattern.finditer(hcl_content):
      resource_name = match.group(1)
      start_pos = match.start()

      brace_count = 0
      end_pos = -1
      in_string = False
      escape = False

      for i in range(match.end() - 1, len(hcl_content)):
        char = hcl_content[i]
        if escape:
          escape = False
          continue
        if char == "\\":
          escape = True
          continue
        if char == '"':
          in_string = not in_string
          continue
        if not in_string:
          if char == "{":
            brace_count += 1
          elif char == "}":
            brace_count -= 1
            if brace_count == 0:
              end_pos = i + 1
              break

      if end_pos == -1:
        continue

      block_content = hcl_content[start_pos:end_pos]

      # Extract display_name
      display_name_match = re.search(
          r'display_name\s*=\s*"([^"]+)"', block_content
      )
      display_name = display_name_match.group(1) if display_name_match else ""

      # Extract PromQL queries
      queries = [
          q.group(1)
          for q in re.finditer(
              r"query\s*=\s*<<-?EOT\n(.*?)\n\s*EOT",
              block_content,
              re.DOTALL,
          )
      ]
      if not queries:
        for q_match in re.finditer(
            r'query\s*=\s*"((?:[^"\\]|\\.)*)"', block_content
        ):
          raw_query = q_match.group(1)
          clean_query = re.sub(r'\\+"', '"', raw_query).replace("\\\\", "\\")
          queries.append(clean_query)

      # Extract threshold filters
      filters = []
      filter_matches = re.finditer(
          r'filter\s*=\s*"((?:[^"\\]|\\.)*)"', block_content
      )
      for f_match in filter_matches:
        filters.append(f_match.group(1))

      # Infer signal type
      signal_type = "unknown"
      res_lower, disp_lower = resource_name.lower(), display_name.lower()
      rules = [
          ("latency", "latency", "latency"),
          ("error", "error", "errors"),
          ("traffic", "traffic", "traffic"),
          ("saturation", "saturation", "saturation"),
          ("cpu", "cpu", "saturation_cpu"),
          ("memory", "memory", "saturation_memory"),
          ("crash", "crash", "health_crashloop"),
          ("ready", "ready", "health_ready"),
      ]
      for res_pat, disp_pat, sig in rules:
        if res_pat in res_lower or disp_pat in disp_lower:
          signal_type = sig
          break

      policies.append({
          "resource_name": resource_name,
          "display_name": display_name,
          "signal_type": signal_type,
          "queries": queries,
          "filters": filters,
          "start_pos": start_pos,
          "end_pos": end_pos,
          "block_content": block_content,
      })

    return policies


def validate_plan_file(plan_path):
  """Validates a pre-edit Plan JSON file (such as changes.json)."""
  errors = []
  try:
    with open(plan_path, "r") as f:
      plan_data = json.load(f)
  except Exception as e:
    return {
        "valid": False,
        "errors": [f"Failed to parse plan JSON file '{plan_path}': {e}"],
        "policies_scanned_count": 0,
        "duplicates_found": [],
    }

  policies = plan_data.get("policies", [])
  if not isinstance(policies, list) or not policies:
    errors.append(
        "Plan file must contain a non-empty 'policies' array of planned alert"
        " policy objects."
    )
    return {
        "valid": False,
        "errors": errors,
        "policies_scanned_count": 0,
        "duplicates_found": [],
    }

  target_map = {}
  duplicates = []

  for idx, p in enumerate(policies):
    res_name = p.get("resource_name", f"policy_{idx}")
    query = p.get("query", "")
    signal_type = p.get("signal_type", "unknown")
    duration = str(p.get("duration", "0s"))

    if not query:
      errors.append(f"Policy '{res_name}' is missing a required 'query' field.")
      continue

    # Lint query syntax
    lint_errs = PromQLLinter.lint_query(query)
    for err in lint_errs:
      errors.append(f"Lint error in planned policy '{res_name}': {err}")

    # Lookback vs Duration rule validation
    if re.search(r"\[(15m|30m|1h|6h|3d)\]", query) and duration not in [
        "0s",
        "60s",
        "0",
        "60",
    ]:
      errors.append(
          f"Duration warning in planned policy '{res_name}': Query uses"
          f" lookback aggregation but enforces long duration '{duration}'."
          " Expected '0s' or '60s' to prevent redundant delay."
      )

    # Check duplicates
    if signal_type != "unknown":
      if signal_type not in target_map:
        target_map[signal_type] = []
      target_map[signal_type].append(res_name)

  for signal_type, matched_names in target_map.items():
    if len(matched_names) > 1:
      duplicates.append({
          "signal_type": signal_type,
          "policies": matched_names,
      })
      errors.append(
          "Duplicate Plan Target: Multiple planned policies target"
          f" '{signal_type}': {matched_names}. Please consolidate them."
      )

  return {
      "valid": len(errors) == 0,
      "errors": errors,
      "policies_scanned_count": len(policies),
      "duplicates_found": duplicates,
  }


def validate_directory_tf_files(directory, expected_cluster_var=None):
  """Scans and validates all *.tf files in a given directory."""
  tf_files = glob.glob(os.path.join(directory, "*.tf"))
  all_errors = []
  all_policies = []
  duplicates = []
  target_map = {}

  for filepath in tf_files:
    filename = os.path.basename(filepath)
    try:
      with open(filepath, "r") as f:
        content = f.read()
    except Exception as e:
      all_errors.append(f"File error in '{filename}': {e}")
      continue

    policies = HCLScanner.extract_alert_policies(content)
    for policy in policies:
      policy["filename"] = filename
      all_policies.append(policy)

      for query in policy["queries"]:
        lint_errs = PromQLLinter.lint_query(query)
        for err in lint_errs:
          all_errors.append(
              f"Lint error in '{filename}' -> resource"
              f" '{policy['resource_name']}': {err}"
          )

      key = policy["signal_type"]
      if key not in target_map:
        target_map[key] = []
      target_map[key].append(policy)

  for signal_type, matches in target_map.items():
    if len(matches) > 1 and signal_type != "unknown":
      duplicates.append({
          "signal_type": signal_type,
          "policies": [
              {
                  "filename": p["filename"],
                  "resource_name": p["resource_name"],
                  "display_name": p["display_name"],
              }
              for p in matches
          ],
      })

  for dup in duplicates:
    policy_list = ", ".join(
        f"'{p['resource_name']}' in '{p['filename']}'" for p in dup["policies"]
    )
    all_errors.append(
        "Duplicate Target Error: Multiple alert policies are targeting the"
        f" same signal '{dup['signal_type']}': [{policy_list}]."
        " Please merge them or ensure they target different resources/labels."
    )

  return {
      "valid": len(all_errors) == 0,
      "errors": all_errors,
      "policies_scanned_count": len(all_policies),
      "duplicates_found": duplicates,
  }


def main():
  parser = argparse.ArgumentParser(
      description=(
          "Lints HCL alerts, pre-edit change plans, and PromQL query targets in"
          " Kubernetes tf templates."
      )
  )
  parser.add_argument(
      "--plan",
      type=str,
      help="Path to pre-edit change plan JSON file (such as changes.json).",
  )
  parser.add_argument(
      "--directory",
      type=str,
      default=".",
      help="Directory containing *.tf files to scan.",
  )
  parser.add_argument(
      "--cluster-var",
      type=str,
      default="${var.cluster_name}",
      help="The expected variable or literal for the cluster name.",
  )
  parser.add_argument(
      "--file",
      type=str,
      help="Validate a single specific HCL file instead of scanning directory.",
  )
  args = parser.parse_args()

  if args.plan:
    results = validate_plan_file(args.plan)
    print(json.dumps(results, indent=2))
    if not results["valid"]:
      sys.exit(1)
    sys.exit(0)

  if args.file:
    try:
      with open(args.file, "r") as f:
        content = f.read()
      policies = HCLScanner.extract_alert_policies(content)
      errors = []
      for p in policies:
        for q in p["queries"]:
          errors.extend(PromQLLinter.lint_query(q))
      if errors:
        print(f"Validation failed for '{args.file}':", file=sys.stderr)
        for err in errors:
          print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
      else:
        print(f"Validation passed for '{args.file}'!")
        sys.exit(0)
    except Exception as e:
      print(f"Error reading file '{args.file}': {e}", file=sys.stderr)
      sys.exit(1)

  results = validate_directory_tf_files(args.directory, args.cluster_var)
  print(json.dumps(results, indent=2))
  if not results["valid"]:
    sys.exit(1)


if __name__ == "__main__":
  main()
