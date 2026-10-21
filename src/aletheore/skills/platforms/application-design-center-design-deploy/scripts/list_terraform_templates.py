#!/usr/bin/env python3
"""CLI tool to list Application Templates from Google and Private catalogs."""

import json
import subprocess
import sys
from typing import Any
from absl import app
from absl import flags

_PROJECT = flags.DEFINE_string(
    "project",
    "",
    "Optional target GCP project ID to query your private catalog.",
)
_LOCATION = flags.DEFINE_string(
    "location", "us-central1", "Design Center location ID."
)
_SPACE_ID = flags.DEFINE_string(
    "space_id", "", "Optional target Space ID to query your private catalog."
)
_CATALOG_ID = flags.DEFINE_string(
    "catalog_id",
    "",
    "Optional target Catalog ID to query your private catalog.",
)


def _run_gcloud_list(
    project: str, location: str, space: str, catalog: str
) -> list[dict[str, Any]]:
  """Runs gcloud command to list templates and returns parsed JSON list."""
  cmd = [
      "gcloud",
      "alpha",
      "design-center",
      "spaces",
      "catalogs",
      "templates",
      "list",
      f"--project={project}",
      f"--location={location}",
      f"--space={space}",
      f"--catalog={catalog}",
      "--format=json",
  ]
  try:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    if result.stdout.strip():
      return json.loads(result.stdout)
  except subprocess.CalledProcessError as e:
    print(
        f"Warning: gcloud query failed for space '{space}' in project"
        f" '{project}': {e.stderr}",
        file=sys.stderr,
    )
  except (json.JSONDecodeError, ValueError, TypeError) as e:
    print(f"Warning: Failed to parse gcloud output: {e}", file=sys.stderr)
  return []


def _process_templates(
    templates: list[dict[str, Any]],
    is_private: bool,
    seen_template_ids: set[str],
    aggregated_results: list[dict[str, Any]],
) -> None:
  """Filters and appends structured templates to aggregated results."""
  for template in templates:
    if not isinstance(template, dict):
      continue

    # Filter for APPLICATION_TEMPLATE only
    category = template.get("templateCategory", "")
    if category != "APPLICATION_TEMPLATE":
      continue

    name = template.get("name", "")
    description = template.get("description", "")
    display_name = template.get("displayName", "")

    # Extract unique template ID suffix
    template_id = name.split("/")[-1] if "/" in name else name
    if not template_id:
      continue

    # Skip if already seen (private has priority because it runs first)
    if template_id in seen_template_ids:
      continue

    seen_template_ids.add(template_id)
    aggregated_results.append({
        "name": name,
        "templateId": template_id,
        "description": description,
        "displayName": display_name,
        "templateCategory": category,
        "source": "private" if is_private else "google",
    })


def main(argv: list[str]) -> None:
  del argv  # Unused.
  is_private_query = bool(_PROJECT.value and _SPACE_ID.value)
  private_raw = []

  # Step 1: Query Private Catalog (if project and space are provided)
  if is_private_query:
    target_catalog = _CATALOG_ID.value or "default-catalog"
    private_raw = _run_gcloud_list(
        project=_PROJECT.value,
        location=_LOCATION.value,
        space=_SPACE_ID.value,
        catalog=target_catalog,
    )

  # Step 2: Query Public Google Catalog
  google_raw = _run_gcloud_list(
      project="gcpdesigncenter",
      location="us-central1",
      space="googlespace",
      catalog="googlecatalog",
  )

  aggregated_results = []
  seen_template_ids = set()

  # Process private templates FIRST (priority and top placement)
  if private_raw:
    _process_templates(
        private_raw,
        is_private=True,
        seen_template_ids=seen_template_ids,
        aggregated_results=aggregated_results,
    )

  # Process public Google templates SECOND
  if google_raw:
    _process_templates(
        google_raw,
        is_private=False,
        seen_template_ids=seen_template_ids,
        aggregated_results=aggregated_results,
    )

  print(json.dumps(aggregated_results, indent=2))


if __name__ == "__main__":
  app.run(main)
