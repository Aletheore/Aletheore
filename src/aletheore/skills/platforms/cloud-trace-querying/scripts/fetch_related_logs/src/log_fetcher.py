"""Retrieves correlated logs using Cloud Logging and Cloud Trace APIs."""

import datetime
import logging
from google.cloud import logging as cloud_logging
from google.cloud import trace_v1
from observability_scopes import resolve_query_projects
from timestamp_utils import validate_iso8601

_logger = logging.getLogger(__name__)


def _to_datetime(ts) -> datetime.datetime | None:
  """Converts a protobuf Timestamp or datetime object to standard datetime."""
  if ts is None:
    return None
  if hasattr(ts, "to_datetime"):
    return ts.to_datetime()
  if isinstance(ts, datetime.datetime):
    return ts
  return None


def _build_log_filter(
    trace_id: str,
    trace_projects: list[str],
    span_id: str | None,
    start_ts: str | None,
    end_ts: str | None,
) -> str:
  """Builds a structured log query filter."""
  trace_filters = [
      f'trace = "projects/{p}/traces/{trace_id}"' for p in trace_projects
  ]
  trace_clause = (
      trace_filters[0]
      if len(trace_filters) == 1
      else f"({' OR '.join(trace_filters)})"
  )

  parts = [trace_clause]
  if span_id:
    parts.append(f'spanId="{span_id}"')
  if start_ts:
    parts.append(f'timestamp >= "{start_ts}"')
  if end_ts:
    parts.append(f'timestamp <= "{end_ts}"')
  return " AND ".join(parts)


def _format_entry(entry, log_project: str) -> dict:
  """Formats a log entry into a standardized dictionary."""
  if isinstance(entry.payload, (str, dict)):
    payload = entry.payload
  else:
    payload = str(entry.payload)

  trace_proj = None
  if entry.trace:
    parts = entry.trace.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
      trace_proj = parts[1]

  return {
      "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
      "severity": entry.severity,
      "log_name": entry.log_name,
      "payload": payload,
      "span_id": entry.span_id,
      "log_project": log_project,
      "trace_project": trace_proj,
  }


def _extract_project_from_log_name(log_name: str) -> str:
  """Extracts the project ID from a log resource name."""
  parts = log_name.split("/")
  if len(parts) >= 2 and parts[0] == "projects":
    return parts[1]
  return ""


def _infer_time_range_from_project(
    trace_client: trace_v1.TraceServiceClient, trace_proj: str, trace_id: str
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
  """Fetches min start and max end times from spans in a specific project."""
  try:
    trace = trace_client.get_trace(project_id=trace_proj, trace_id=trace_id)
    starts = [_to_datetime(s.start_time) for s in trace.spans if s.start_time]
    ends = [_to_datetime(s.end_time) for s in trace.spans if s.end_time]
    starts = [dt for dt in starts if dt is not None]
    ends = [dt for dt in ends if dt is not None]
    return (
        min(starts) if starts else None,
        max(ends) if ends else None,
    )
  except Exception as e:
    _logger.warning(
        "Failed to fetch trace %s from project %s: %s",
        trace_id,
        trace_proj,
        e,
    )
    return None, None


def _infer_time_range_from_trace(
    trace_client: trace_v1.TraceServiceClient,
    trace_projects: list[str],
    trace_id: str,
) -> tuple[str | None, str | None]:
  """Queries Trace API to determine active trace time bounds."""
  min_start, max_end = None, None
  for trace_proj in trace_projects:
    start_dt, end_dt = _infer_time_range_from_project(
        trace_client, trace_proj, trace_id
    )
    if start_dt and (min_start is None or start_dt < min_start):
      min_start = start_dt
    if end_dt and (max_end is None or end_dt > max_end):
      max_end = end_dt

  start_ts = (
      (min_start - datetime.timedelta(seconds=5))
      .isoformat()
      .replace("+00:00", "Z")
      if min_start
      else None
  )
  end_ts = (
      (max_end + datetime.timedelta(seconds=5))
      .isoformat()
      .replace("+00:00", "Z")
      if max_end
      else None
  )
  return start_ts, end_ts


def _fetch_entries_batch(
    client: cloud_logging.Client,
    log_projects: list[str],
    trace_id: str,
    trace_projects: list[str],
    span_id: str | None,
    start_ts: str | None,
    end_ts: str | None,
    limit: int,
) -> list[dict]:
  """Fetches log entries from multiple projects in a single API call."""
  resource_names = [f"projects/{p}" for p in log_projects]
  filter_str = _build_log_filter(
      trace_id, trace_projects, span_id, start_ts, end_ts
  )
  pager = client.list_entries(
      resource_names=resource_names,
      filter_=filter_str,
      page_size=limit,
      order_by="timestamp desc",
  )
  return [
      _format_entry(entry, _extract_project_from_log_name(entry.log_name))
      for entry in pager
  ]


def _fetch_entries_individually(
    client: cloud_logging.Client,
    log_projects: list[str],
    trace_id: str,
    trace_projects: list[str],
    span_id: str | None,
    start_ts: str | None,
    end_ts: str | None,
    limit: int,
) -> list[dict]:
  """Fallback method to fetch log entries by querying projects one-by-one."""
  all_logs = []
  fetch_errors = []
  filter_str = _build_log_filter(
      trace_id, trace_projects, span_id, start_ts, end_ts
  )
  for log_proj in log_projects:
    try:
      pager = client.list_entries(
          resource_names=[f"projects/{log_proj}"],
          filter_=filter_str,
          page_size=limit,
          order_by="timestamp desc",
      )
      all_logs.extend([_format_entry(entry, log_proj) for entry in pager])
    except Exception as e:
      _logger.warning("Failed to fetch logs from project %s: %s", log_proj, e)
      fetch_errors.append((log_proj, str(e)))

  if not all_logs and fetch_errors:
    raise RuntimeError(
        f"Logging API query failed for project '{fetch_errors[0][0]}': {fetch_errors[0][1]}"
    )
  return all_logs


def fetch_logs(
    trace_id: str,
    trace_projects: list[str],
    span_id: str | None = None,
    limit: int = 100,
    log_projects: list[str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
  """Retrieves, sorts, and limits trace-correlated log entries."""
  log_projects_to_query = resolve_query_projects(trace_projects, log_projects)

  try:
    trace_client = trace_v1.TraceServiceClient()
  except Exception as e:
    _logger.warning("Failed to initialize TraceServiceClient: %s", e)
    trace_client = None

  if (not start_time or not end_time) and trace_client:
    inferred_start, inferred_end = _infer_time_range_from_trace(
        trace_client, trace_projects, trace_id
    )
    start_time = start_time or inferred_start
    end_time = end_time or inferred_end

  start_ts = validate_iso8601(start_time)
  end_ts = validate_iso8601(end_time)

  try:
    logging_client = cloud_logging.Client()
  except Exception as e:
    _logger.error("Failed to initialize cloud logging client: %s", e)
    return []

  try:
    all_logs = _fetch_entries_batch(
        logging_client,
        list(log_projects_to_query),
        trace_id,
        trace_projects,
        span_id,
        start_ts,
        end_ts,
        limit,
    )
  except Exception as e:
    _logger.warning(
        "Batch query failed (likely permissions issue). Falling back to"
        " individual queries: %s",
        e,
    )
    all_logs = _fetch_entries_individually(
        logging_client,
        list(log_projects_to_query),
        trace_id,
        trace_projects,
        span_id,
        start_ts,
        end_ts,
        limit,
    )

  all_logs.sort(
      key=lambda x: x["timestamp"] if x["timestamp"] else "", reverse=True
  )
  return all_logs[:limit]
