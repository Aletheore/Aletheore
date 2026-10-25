#!/usr/bin/env python3
"""CLI tool to generate and fetch HCL code for a template revision."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from absl import app
from absl import flags

_PROJECT = flags.DEFINE_string("project", "", "Optional target GCP project ID.")
_LOCATION = flags.DEFINE_string(
    "location", "us-central1", "Design Center location ID."
)
_SPACE_ID = flags.DEFINE_string("space_id", "", "Optional target Space ID.")
_CATALOG_ID = flags.DEFINE_string(
    "catalog_id", "", "Optional target Catalog ID."
)
_OUT_DIR = flags.DEFINE_string(
    "out_dir", "", "Optional local directory path to save the files."
)


def _run_cmd(cmd: list[str]) -> str:
  """Runs a command and returns its stdout, raising an error on failure."""
  result = subprocess.run(cmd, capture_output=True, text=True, check=True)
  return result.stdout


def _parse_input(
    input_id: str,
) -> tuple[str, str, str, str, str]:
  """Parses the input resource path or resolves parameters using flags."""
  # Step 1: Parse the input path or resolve using flags
  # Supports full resource paths:
  # projects/{project}/locations/{location}/spaces/{space}/
  # catalogs/{catalog}/templates/{template_id}
  if input_id.startswith("projects/"):
    parts = input_id.split("/")
    try:
      project = parts[1]
      location = parts[3]
      space_id = parts[5]
      if "catalogs" in parts:
        catalog_index = parts.index("catalogs")
        catalog_id = parts[catalog_index + 1]
        short_template_id = parts[catalog_index + 3]
      else:
        catalog_id = _CATALOG_ID.value or "default-catalog"
        short_template_id = parts[-1]
    except (IndexError, ValueError) as e:
      print(f"Error parsing resource path '{input_id}': {e}", file=sys.stderr)
      sys.exit(1)
  else:
    project = _PROJECT.value or "gcpdesigncenter"
    space_id = _SPACE_ID.value or "googlespace"
    location = _LOCATION.value or "us-central1"
    catalog_id = _CATALOG_ID.value or "googlecatalog"
    short_template_id = input_id
  return project, location, space_id, catalog_id, short_template_id


def _get_latest_revision_id(
    project: str,
    location: str,
    space_id: str,
    catalog_id: str,
    short_template_id: str,
) -> str:
  """Describes the Catalog Template to find the latestRevisionId."""
  # Step 2: Describe the Catalog Template to find the latestRevisionId
  describe_template_cmd = [
      "gcloud",
      "alpha",
      "design-center",
      "spaces",
      "catalogs",
      "templates",
      "describe",
      short_template_id,
      f"--project={project}",
      f"--space={space_id}",
      f"--location={location}",
      f"--catalog={catalog_id}",
      "--format=json",
  ]

  try:
    stdout = _run_cmd(describe_template_cmd)
    template_data = json.loads(stdout)
    latest_revision_id = template_data.get("latestRevisionId")
    if not latest_revision_id:
      print(
          f"Error: Catalog template '{short_template_id}' does not have a"
          " latest revision ID.",
          file=sys.stderr,
      )
      sys.exit(1)
    return latest_revision_id
  except subprocess.CalledProcessError as e:
    print(f"Error describing catalog template: {e.stderr}", file=sys.stderr)
    sys.exit(1)


def _get_app_template_revision_source(
    latest_revision_id: str,
    project: str,
    location: str,
    space_id: str,
    catalog_id: str,
    short_template_id: str,
) -> str:
  """Describes the Catalog Template Revision to find the source.

  Args:
    latest_revision_id: The latest revision ID or full resource name.
    project: Target GCP project ID.
    location: Target location ID.
    space_id: Target Space ID.
    catalog_id: Target Catalog ID.
    short_template_id: Short template ID.

  Returns:
    The applicationTemplateRevisionSource.
  """
  # Step 3: Describe the Catalog Template Revision to find
  # applicationTemplateRevisionSource.
  # Revision path: projects/{project}/locations/{location}/spaces/{space_id}/
  # catalogs/{catalog_id}/templates/{short_template_id}/revisions/{rev}
  if latest_revision_id.startswith("projects/"):
    revision_path = latest_revision_id
  else:
    revision_path = (
        f"projects/{project}/locations/{location}/spaces/{space_id}/"
        f"catalogs/{catalog_id}/templates/{short_template_id}/revisions/"
        f"{latest_revision_id}"
    )
  print(
      f"Retrieving Catalog Template Revision: {latest_revision_id}...",
      file=sys.stderr,
  )

  describe_revision_cmd = [
      "gcloud",
      "alpha",
      "design-center",
      "spaces",
      "catalogs",
      "templates",
      "revisions",
      "describe",
      revision_path,
      "--format=json",
  ]

  try:
    stdout = _run_cmd(describe_revision_cmd)
    revision_data = json.loads(stdout)
    app_template_revision_source = revision_data.get(
        "applicationTemplateRevisionSource"
    )
    if not app_template_revision_source:
      print(
          f"Error: Catalog revision '{latest_revision_id}' does not specify an"
          " applicationTemplateRevisionSource.",
          file=sys.stderr,
      )
      sys.exit(1)
    return app_template_revision_source
  except subprocess.CalledProcessError as e:
    print(
        f"Error describing catalog template revision: {e.stderr}",
        file=sys.stderr,
    )
    sys.exit(1)


def _generate_iac(app_template_revision_source: str) -> str:
  """Generates IaC for the Application Template Revision.

  Args:
    app_template_revision_source: Source application template revision.

  Returns:
    The GCS URI.
  """
  # Step 4: Generate IaC for the Application Template Revision
  generate_cmd = [
      "gcloud",
      "alpha",
      "design-center",
      "spaces",
      "application-templates",
      "revisions",
      "generate",
      app_template_revision_source,
      "--format=json",
  ]

  try:
    stdout = _run_cmd(generate_cmd)
    data = json.loads(stdout)
    artifact_location = data.get("artifactLocation") or {}
    gcs_uri = data.get("gcsUri") or artifact_location.get("gcsUri")
    if not gcs_uri:
      print(
          "Error: Generation completed but no GCS URI was returned. Response:"
          f" {stdout}",
          file=sys.stderr,
      )
      sys.exit(1)
    return gcs_uri
  except subprocess.CalledProcessError as e:
    print(
        f"Error generating IaC from application template revision: {e.stderr}",
        file=sys.stderr,
    )
    sys.exit(1)


def _download_from_gcs(gcs_uri: str, local_temp_dir: str) -> None:
  """Downloads files from GCS to local SSD temp directory."""
  # Step 5: Download files from GCS to local SSD temp directory (/tmp)
  gcs_source = f"{gcs_uri.rstrip('/')}/*"
  cp_cmd = ["gcloud", "storage", "cp", "-r", gcs_source, local_temp_dir]

  try:
    _run_cmd(cp_cmd)
  except subprocess.CalledProcessError as e:
    print(f"Error downloading files from GCS: {e.stderr}", file=sys.stderr)
    sys.exit(1)


def _output_files(
    local_temp_dir: str, short_template_id: str, gcs_uri: str
) -> None:
  """Distributes files to target destination or prints HCL content to stdout."""
  working_dir = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())

  # Step 6: Distribute files to target destination
  if _OUT_DIR.value:
    if os.path.isabs(_OUT_DIR.value):
      target_dir = _OUT_DIR.value
    else:
      target_dir = os.path.abspath(os.path.join(working_dir, _OUT_DIR.value))

    os.makedirs(target_dir, exist_ok=True)
    saved_files = []
    for filename in sorted(os.listdir(local_temp_dir)):
      src_file = os.path.join(local_temp_dir, filename)
      if os.path.isfile(src_file):
        dest_file = os.path.join(target_dir, filename)
        shutil.copy2(src_file, dest_file)
        saved_files.append(dest_file)

    print(
        f"SUCCESS: Fetched template '{short_template_id}' and saved files"
        f" to {target_dir}"
    )
  else:
    hcl_files = []
    for filename in sorted(os.listdir(local_temp_dir)):
      file_path = os.path.join(local_temp_dir, filename)
      if os.path.isfile(file_path):
        try:
          with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            hcl_files.append(f"# === {filename} ===\n{content}")
        except (OSError, UnicodeDecodeError) as e:
          print(
              f"Warning: Could not read file '{filename}': {e}",
              file=sys.stderr,
          )

    if hcl_files:
      print("\n\n".join(hcl_files))
    else:
      print(
          f"Warning: No files were downloaded from {gcs_uri}", file=sys.stderr
      )


def main(argv: list[str]) -> None:
  if len(argv) < 2:
    print(
        "Usage: fetch_terraform_template <template_path_or_id>"
        " [--project=<project>] [--location=<location>] [--space_id=<space_id>]"
        " [--catalog_id=<catalog_id>] [--out_dir=<out_dir>]",
        file=sys.stderr,
    )
    sys.exit(1)

  project, location, space_id, catalog_id, short_template_id = _parse_input(
      argv[1]
  )

  print(
      "Resolving Catalog Template:"
      f" {project}/{location}/{space_id}/{catalog_id}/{short_template_id}...",
      file=sys.stderr,
  )

  latest_revision_id = _get_latest_revision_id(
      project, location, space_id, catalog_id, short_template_id
  )

  app_template_revision_source = _get_app_template_revision_source(
      latest_revision_id,
      project,
      location,
      space_id,
      catalog_id,
      short_template_id,
  )

  print(
      "Resolved Source Application Template Revision:"
      f" {app_template_revision_source}",
      file=sys.stderr,
  )
  print("Generating IaC for Application Template Revision...", file=sys.stderr)

  gcs_uri = _generate_iac(app_template_revision_source)

  print(f"Downloading HCL code from GCS: {gcs_uri}...", file=sys.stderr)

  # Download files from GCS to local SSD temp directory (/tmp)
  with tempfile.TemporaryDirectory() as local_temp_dir:
    _download_from_gcs(gcs_uri, local_temp_dir)
    _output_files(local_temp_dir, short_template_id, gcs_uri)


if __name__ == "__main__":
  app.run(main)
