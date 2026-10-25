#!/usr/bin/env python3
"""Validates that a candidate OpenAPI spec keeps the active quota metrics.

API Gateway applies quota metrics and limits to the entire API rather than to an
individual API config. When a new API config is created, its quota definitions
immediately replace the definitions for the whole API. If a new API config
renames or removes a quota metric or limit that is still referenced by an active
gateway serving an older API config, requests to quota-enforced methods on that
older gateway fail with HTTP 500 ("Failed to call Service Control Quota").

The script also flags a metric that has a limit in a baseline but no limit in
the candidate, because the candidate's limits replace the limits of every
gateway of the API and the metric would no longer be rate limited.

It also flags an x-google-quota extension outside an operation, because API
Gateway reads x-google-quota only on operations and ignores it at the root and
path levels without an error.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from typing import Any

try:
  import yaml

  _HAS_YAML = True
except ImportError:
  _HAS_YAML = False

# Matches the API ID and the config ID in a gateway's API config resource name.
_API_CONFIG_NAME_RE = re.compile(
    r'/apis/(?P<api>[^/]+)/configs/(?P<config>[^/]+)$'
)

# The maximum time, in seconds, to wait for each gcloud command.
_GCLOUD_TIMEOUT_SECONDS = 60


def _load_spec(content: str, source_name: str) -> dict[str, Any]:
  """Parses a YAML or JSON OpenAPI specification string into a dictionary."""
  try:
    data = json.loads(content)
    if isinstance(data, dict):
      return data
  except json.JSONDecodeError:
    pass

  if not _HAS_YAML:
    raise RuntimeError(
        'PyYAML is required to parse YAML specifications. Install it with: '
        'pip install "PyYAML>=6.0,<7.0"'
    )
  try:
    data = yaml.safe_load(content)
  except yaml.YAMLError as exc:
    raise ValueError(
        f'Specification {source_name} is not valid JSON or YAML: {exc}'
    ) from exc
  if not isinstance(data, dict):
    raise ValueError(f'Specification {source_name} did not parse as a mapping.')
  return data


def _read_spec(path: str) -> dict[str, Any]:
  """Reads and parses the OpenAPI specification file at path."""
  with open(path, 'r', encoding='utf-8') as f:
    return _load_spec(f.read(), path)


def extract_quota_metrics(spec: dict[str, Any]) -> set[str]:
  """Extracts declared quota metric names from an OpenAPI 2.0 or 3.x dict."""
  metrics: set[str] = set()

  # OpenAPI 2.0: x-google-management.metrics is a list of {name: ...} objects.
  oas2_management = spec.get('x-google-management')
  if isinstance(oas2_management, dict):
    oas2_metrics = oas2_management.get('metrics', [])
    if isinstance(oas2_metrics, list):
      for item in oas2_metrics:
        if isinstance(item, dict) and item.get('name') is not None:
          metrics.add(str(item['name']))

  # OpenAPI 3.x: x-google-api-management.metrics is a map keyed by metric name.
  oas3_management = spec.get('x-google-api-management')
  if isinstance(oas3_management, dict):
    oas3_metrics = oas3_management.get('metrics', {})
    if isinstance(oas3_metrics, dict):
      for key in oas3_metrics.keys():
        if key is not None:
          metrics.add(str(key))

  return metrics


def extract_limited_metrics(spec: dict[str, Any]) -> set[str]:
  """Extracts metric names that have a limit in quota.limits."""
  limited: set[str] = set()

  oas2_management = spec.get('x-google-management')
  if isinstance(oas2_management, dict):
    quota = oas2_management.get('quota', {})
    if isinstance(quota, dict):
      limits = quota.get('limits', [])
      if isinstance(limits, list):
        for item in limits:
          if isinstance(item, dict) and item.get('metric') is not None:
            limited.add(str(item['metric']))

  oas3_management = spec.get('x-google-api-management')
  if isinstance(oas3_management, dict):
    quota = oas3_management.get('quota', {})
    if isinstance(quota, dict):
      limits = quota.get('limits', {})
      if isinstance(limits, dict):
        for item in limits.values():
          if isinstance(item, dict) and item.get('metric') is not None:
            limited.add(str(item['metric']))

  return limited


def _quota_extension_metrics(quota_extension: Any) -> set[str]:
  """Returns the metric names that one x-google-quota extension charges."""
  if not isinstance(quota_extension, dict):
    return set()
  costs = quota_extension.get('metricCosts')
  if isinstance(costs, dict):
    return {str(metric_name) for metric_name in costs}
  return {str(metric_name) for metric_name in quota_extension}


def extract_referenced_quota_metrics(spec: dict[str, Any]) -> set[str]:
  """Extracts metric names that x-google-quota references at any level."""
  referenced = _quota_extension_metrics(spec.get('x-google-quota'))
  paths = spec.get('paths', {})
  if isinstance(paths, dict):
    for path_item in paths.values():
      if isinstance(path_item, dict):
        referenced |= _quota_extension_metrics(path_item.get('x-google-quota'))
        for operation in path_item.values():
          if isinstance(operation, dict):
            referenced |= _quota_extension_metrics(
                operation.get('x-google-quota')
            )

  return referenced


def find_ignored_quota_extensions(spec: dict[str, Any]) -> list[str]:
  """Returns the locations of x-google-quota extensions outside an operation.

  API Gateway reads x-google-quota only on operations. It ignores the extension
  at the root and path levels without an error, so the metrics that it charges
  are never counted.

  Args:
    spec: The parsed OpenAPI 2.0 or 3.x specification.

  Returns:
    The location of each ignored extension, such as "x-google-quota" or
    "paths./v1/orders.x-google-quota".
  """
  locations: list[str] = []
  if 'x-google-quota' in spec:
    locations.append('x-google-quota')
  paths = spec.get('paths', {})
  if isinstance(paths, dict):
    for path, path_item in paths.items():
      if isinstance(path_item, dict) and 'x-google-quota' in path_item:
        locations.append(f'paths.{path}.x-google-quota')
  return locations


def _decode_contents(b64_contents: str) -> str:
  """Decodes base64 or base64url API config contents."""
  normalized = b64_contents.replace('-', '+').replace('_', '/')
  normalized += '=' * (-len(normalized) % 4)
  return base64.b64decode(normalized).decode('utf-8-sig')


def _fetch_live_config_metrics(
    project: str, api: str
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
  """Reads the quota metrics of each active API config of an API.

  Args:
    project: The Google Cloud project ID of the gateways.
    api: The API Gateway API ID.

  Returns:
    The metrics of each active API config, the limited metrics of each active
    API config, and the IDs of the active configs that have no OpenAPI
    documents (for example, gRPC configs), which the script cannot inspect.

  Raises:
    RuntimeError: If a gcloud command fails, or if no active API config has
      OpenAPI documents.
  """
  gateways_command = [
      'gcloud',
      'api-gateway',
      'gateways',
      'list',
      f'--project={project}',
      '--format=json',
  ]
  gateways_result = subprocess.run(
      gateways_command,
      capture_output=True,
      text=True,
      check=False,
      stdin=subprocess.DEVNULL,
      timeout=_GCLOUD_TIMEOUT_SECONDS,
  )
  if gateways_result.returncode != 0:
    raise RuntimeError(
        f'Failed to list gateways via gcloud: {gateways_result.stderr.strip()}'
    )

  gateways = json.loads(gateways_result.stdout or '[]')
  config_ids: set[str] = set()
  for gateway in gateways:
    match = _API_CONFIG_NAME_RE.search(gateway.get('apiConfig', ''))
    if match and match.group('api') == api:
      config_ids.add(match.group('config'))

  metrics_by_source: dict[str, list[str]] = {}
  limited_by_source: dict[str, list[str]] = {}
  skipped_configs: list[str] = []
  for config_id in sorted(config_ids):
    config_command = [
        'gcloud',
        'api-gateway',
        'api-configs',
        'describe',
        config_id,
        f'--api={api}',
        f'--project={project}',
        '--view=FULL',
        '--format=json',
    ]
    config_result = subprocess.run(
        config_command,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=_GCLOUD_TIMEOUT_SECONDS,
    )
    if config_result.returncode != 0:
      raise RuntimeError(
          f'Failed to describe API config {config_id}:'
          f' {config_result.stderr.strip()}'
      )
    config_data = json.loads(config_result.stdout or '{}')
    openapi_documents = config_data.get('openapiDocuments', [])
    if not openapi_documents:
      # One gRPC config must not block the check of the other configs.
      skipped_configs.append(config_id)
      continue
    config_metrics: set[str] = set()
    config_limited: set[str] = set()
    for document_entry in openapi_documents:
      document = document_entry.get('document', {})
      b64_contents = document.get('contents', '')
      if b64_contents:
        raw_text = _decode_contents(b64_contents)
        parsed = _load_spec(
            raw_text, f'{config_id}:{document.get("path", "spec")}'
        )
        config_metrics.update(extract_quota_metrics(parsed))
        config_metrics.update(extract_referenced_quota_metrics(parsed))
        config_limited.update(extract_limited_metrics(parsed))
    metrics_by_source[f'api-config:{config_id}'] = sorted(config_metrics)
    limited_by_source[f'api-config:{config_id}'] = sorted(config_limited)

  if skipped_configs and not metrics_by_source:
    raise RuntimeError(
        f'No active API config of API {api} has OpenAPI documents (for'
        f' example, gRPC configs: {", ".join(skipped_configs)}), so the script'
        ' cannot inspect them. Check their quota metrics and limits manually'
        ' before rollout.'
    )
  return metrics_by_source, limited_by_source, skipped_configs


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
      description=(
          'Verify that a candidate OpenAPI specification retains all quota '
          'metrics and limits required by baseline specs or active gateways.'
      )
  )
  parser.add_argument(
      '--spec',
      required=True,
      help='Path to the candidate OpenAPI 2.0 or 3.x specification file.',
  )
  parser.add_argument(
      '--baseline-spec',
      action='append',
      default=[],
      help=(
          'Path to an existing OpenAPI spec currently served by a gateway. '
          'Can be passed multiple times for offline comparison.'
      ),
  )
  parser.add_argument(
      '--project',
      help='Google Cloud project ID to inspect active gateways via gcloud.',
  )
  parser.add_argument(
      '--api',
      help='API Gateway API ID to inspect active gateways via gcloud.',
  )
  parser.add_argument(
      '--dry-run',
      action='store_true',
      help='Validate local --baseline-spec files only without calling gcloud.',
  )

  args = parser.parse_args(argv)

  if args.dry_run and (args.project or args.api):
    print(
        'Error: Do not combine --dry-run with --project or --api. Use '
        '--project and --api for live gateway checks, or --baseline-spec with '
        '--dry-run for offline file comparisons.',
        file=sys.stderr,
    )
    return 2

  if (args.project or args.api) and not (args.project and args.api):
    print(
        'Error: Both --project and --api must be provided together when '
        'querying live gateways.',
        file=sys.stderr,
    )
    return 2

  if not args.baseline_spec and not (args.project and args.api):
    print(
        'Error: Provide at least one baseline source via --project and --api '
        '(live gateway check) or --baseline-spec (offline spec check).',
        file=sys.stderr,
    )
    return 2

  try:
    candidate_spec = _read_spec(args.spec)
    baseline_specs = {path: _read_spec(path) for path in args.baseline_spec}
    live_sources, live_limited, skipped_configs = (
        _fetch_live_config_metrics(args.project, args.api)
        if args.project and args.api
        else ({}, {}, [])
    )
  except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
    print(f'Error: {exc}', file=sys.stderr)
    return 2

  for config_id in skipped_configs:
    print(
        f'Warning: Skipped active API config {config_id} because it has no'
        ' OpenAPI documents (for example, a gRPC config). Check its quota'
        ' metrics and limits manually.',
        file=sys.stderr,
    )

  candidate_metrics = extract_quota_metrics(candidate_spec)
  candidate_limited = extract_limited_metrics(candidate_spec)
  candidate_references = extract_referenced_quota_metrics(candidate_spec)
  ignored_quota_extensions = find_ignored_quota_extensions(candidate_spec)

  baseline_sources: dict[str, list[str]] = {}
  baseline_limited: dict[str, list[str]] = {}
  for base_path, base_spec in baseline_specs.items():
    base_names = extract_quota_metrics(
        base_spec
    ) | extract_referenced_quota_metrics(base_spec)
    baseline_sources[base_path] = sorted(base_names)
    baseline_limited[base_path] = sorted(extract_limited_metrics(base_spec))
  baseline_sources.update(live_sources)
  baseline_limited.update(live_limited)

  missing_by_source: dict[str, list[str]] = {}
  all_missing: set[str] = set()
  missing_limits: set[str] = set()
  undeclared_candidate_references = sorted(
      candidate_references - candidate_metrics
  )

  for source, old_metrics in baseline_sources.items():
    missing = sorted(set(old_metrics) - candidate_metrics)
    if missing:
      missing_by_source[source] = missing
      all_missing.update(missing)
  # Flag only limits that a baseline has and the candidate drops. A metric
  # that never had a limit does not need one now.
  for old_limited in baseline_limited.values():
    missing_limits.update(set(old_limited) - candidate_limited)

  has_failure = bool(
      all_missing
      or missing_limits
      or undeclared_candidate_references
      or ignored_quota_extensions
  )
  status = 'FAIL' if has_failure else 'PASS'
  report = {
      'status': status,
      'candidate_spec': args.spec,
      'candidate_metrics': sorted(candidate_metrics),
      'candidate_limited_metrics': sorted(candidate_limited),
      'undeclared_candidate_references': undeclared_candidate_references,
      'ignored_quota_extensions': ignored_quota_extensions,
      'skipped_configs': skipped_configs,
      'baseline_sources': baseline_sources,
      'baseline_limited_metrics': baseline_limited,
      'missing_metrics': sorted(all_missing),
      'missing_limits_for_metrics': sorted(missing_limits),
      'missing_by_source': missing_by_source,
  }
  print(json.dumps(report, indent=2))

  if all_missing or undeclared_candidate_references:
    print(
        'Quota metric validation failed. Creating this API config will '
        'overwrite API-wide quota definitions and can cause HTTP 500 "Failed '
        'to call Service Control Quota" on active gateways.',
        file=sys.stderr,
    )
  if missing_limits:
    print(
        'Quota limit validation failed. Creating this API config removes the '
        'limits for the metrics in "missing_limits_for_metrics" from every '
        'gateway of the API, so those metrics are no longer rate limited.',
        file=sys.stderr,
    )
  if ignored_quota_extensions:
    print(
        'Quota extension validation failed. API Gateway reads x-google-quota '
        'only on operations and ignores the extensions in '
        '"ignored_quota_extensions", so the metrics that they charge are never '
        'counted. Move each x-google-quota into the operations it applies to.',
        file=sys.stderr,
    )
  return 1 if has_failure else 0


if __name__ == '__main__':
  sys.exit(main())
