"""Logic for searching and listing traces using GCP Trace client library."""

from datetime import datetime, timezone
import hashlib
import logging
from typing import Any, Dict, List, Optional

from google.cloud import trace_v1
from google.protobuf.json_format import MessageToDict
from timestamp_utils import parse_iso8601, parse_timestamp, to_datetime

_logger = logging.getLogger(__name__)


def _is_status_code_error(val: Any) -> bool:
  """Returns True if the status code value represents an error."""
  if val is None:
    return False

  val_int = None
  if isinstance(val, (int, float)):
    val_int = int(val)
  elif isinstance(val, str):
    try:
      val_int = int(val)
    except ValueError:
      pass

  if val_int is not None:
    # Standard HTTP status codes
    if 100 <= val_int < 600:
      return val_int < 200 or val_int >= 400
    # RPC numeric status codes (0 is OK, non-zero is error)
    return val_int != 0

  if isinstance(val, str):
    val_clean = val.strip().lower()
    return val_clean not in ("ok", "success")

  return True


class ErrorCheckResult:
  """Result of checking a span for errors."""

  def __init__(self, is_error: bool, causes: Dict[str, Any]):
    self.is_error = is_error
    self.causes = causes


def _check_otel_http_error(
    labels: Dict[str, Any], causes: Dict[str, Any]
) -> bool:
  """Checks for OTel/legacy HTTP errors."""
  for key in (
      "http.response.status_code",
      "http.status_code",
      "/http/status_code",
  ):
    if key in labels:
      val = labels[key]
      if _is_status_code_error(val):
        causes[key] = val
        return True
  return False


def _check_otel_grpc_error(
    labels: Dict[str, Any], causes: Dict[str, Any]
) -> bool:
  """Checks for OTel gRPC/grpc.status errors."""
  for key in ("rpc.grpc.status_code", "grpc.status"):
    if key in labels:
      val = labels[key]
      if _is_status_code_error(val):
        causes[key] = val
        return True
  return False


def _check_rpc_system_error(
    labels: Dict[str, Any], causes: Dict[str, Any]
) -> bool:
  """Checks for RPC response status errors."""
  if "rpc.response.status_code" in labels:
    val = labels["rpc.response.status_code"]
    if _is_status_code_error(val):
      causes["rpc.response.status_code"] = val
      system = labels.get("rpc.system.name")
      if system:
        causes["rpc.system.name"] = system
      return True
  return False


def _check_generic_error_attributes(
    labels: Dict[str, Any], causes: Dict[str, Any]
) -> bool:
  """Checks for generic error attributes/labels."""
  error_pairs = {
      "error.type": "error.message",
      "error.message": "error.type",
      "/error/name": "/error/message",
      "/error/message": "/error/name",
  }
  for key, other_key in error_pairs.items():
    if key in labels:
      val = labels[key]
      if val is not None and isinstance(val, str) and val.strip():
        causes[key] = val
        if other_key in labels and labels[other_key] is not None:
          causes[other_key] = labels[other_key]
        return True
  return False


def _check_gcp_top_level_status(status: Any, causes: Dict[str, Any]) -> bool:
  """Checks top level status object for errors."""
  if isinstance(status, dict):
    code = status.get("code")
    if code is not None:
      try:
        if int(code) != 0:
          causes["status"] = status
          return True
      except (ValueError, TypeError):
        pass
  elif isinstance(status, str):
    status_clean = status.strip().lower()
    if status_clean and status_clean not in ("ok", "0"):
      causes["status"] = status
      return True
  return False


def check_error_span(span: Dict[str, Any]) -> ErrorCheckResult:
  """Checks if a span is an error and extracts the cause.

  Follows OpenTelemetry Semantic Conventions and Google RPC status codes.
  """
  labels = span.get("labels") or span.get("attributes") or {}
  causes = {}

  if _check_otel_http_error(labels, causes):
    return ErrorCheckResult(True, causes)
  if _check_otel_grpc_error(labels, causes):
    return ErrorCheckResult(True, causes)
  if _check_rpc_system_error(labels, causes):
    return ErrorCheckResult(True, causes)
  if _check_generic_error_attributes(labels, causes):
    return ErrorCheckResult(True, causes)

  status = span.get("status")
  if status and _check_gcp_top_level_status(status, causes):
    return ErrorCheckResult(True, causes)

  return ErrorCheckResult(False, {})


def is_error_span(span: Dict[str, Any]) -> bool:
  """Checks if a span represents an error."""
  return check_error_span(span).is_error


def _get_span_start(span: Dict[str, Any]) -> datetime:
  """Gets span start time."""
  return to_datetime(span.get("startTime"))


def _get_span_end(span: Dict[str, Any]) -> datetime:
  """Gets span end time."""
  return to_datetime(span.get("endTime"))


def _get_service_identifiers(span: Dict[str, Any]) -> Dict[str, str]:
  """Extracts all service-related identifiers from a span's labels/attributes."""
  labels = span.get("labels") or span.get("attributes") or {}
  ids = {}

  # 1. OTel service attributes
  otel_name = labels.get("service.name")
  if otel_name:
    ids["otel_service_name"] = str(otel_name)
  otel_ns = labels.get("service.namespace")
  if otel_ns:
    ids["otel_service_namespace"] = str(otel_ns)

  # 2. AppHub attributes
  apphub_id = labels.get("gcp.apphub.service.id")
  if apphub_id:
    ids["apphub_service_id"] = str(apphub_id)
  apphub_workload = labels.get("gcp.apphub.workload.id")
  if apphub_workload:
    ids["apphub_workload_id"] = str(apphub_workload)

  # 3. GCP MCP attributes
  mcp_id = labels.get("gcp.mcp.server.id")
  if mcp_id:
    ids["gcp_mcp_server_id"] = str(mcp_id)

  return ids


def _get_canonical_service_name(ids: Dict[str, str]) -> Optional[str]:
  """Resolves a single canonical service name string from the identifiers."""
  if "otel_service_name" in ids:
    name = ids["otel_service_name"]
    ns = ids.get("otel_service_namespace")
    return f"{ns}/{name}" if ns else name
  if "apphub_service_id" in ids:
    return ids["apphub_service_id"]
  if "gcp_mcp_server_id" in ids:
    return ids["gcp_mcp_server_id"]
  return None


def _extract_error_labels(span: Dict[str, Any]) -> Dict[str, Any]:
  """Extracts labels indicating the failure from a span."""
  res = check_error_span(span)
  if not res.is_error:
    return {}

  error_labels = dict(res.causes)
  labels = span.get("labels") or span.get("attributes") or {}

  # Also capture general keys containing 'message' or 'error' (case-insensitive)
  for k, v in labels.items():
    k_lower = k.lower()
    if v is not None and ("message" in k_lower or "error" in k_lower):
      # Don't overwrite if already set by cause detection
      if k not in error_labels:
        error_labels[k] = v

  return error_labels


_trace_client: Optional[trace_v1.TraceServiceClient] = None


def _get_trace_client() -> trace_v1.TraceServiceClient:
  """Instantiates and caches the TraceServiceClient."""
  global _trace_client
  if _trace_client is None:
    _trace_client = trace_v1.TraceServiceClient()
  return _trace_client


class TraceExample:
  """Holds distilled trace metrics."""

  def __init__(
      self,
      trace_id: str,
      earliest_timestamp: Optional[str],
      last_timestamp: Optional[str],
      duration_seconds: float,
      num_spans: int,
      unique_services: List[str],
      unique_attribute_keys: List[str],
      longest_non_root_span_duration_seconds: float,
      num_error_spans: int,
      errors: List[Dict[str, Any]],
  ):
    self.trace_id = trace_id
    self.earliest_timestamp = earliest_timestamp
    self.last_timestamp = last_timestamp
    self.duration_seconds = duration_seconds
    self.num_spans = num_spans
    self.unique_services = unique_services
    self.unique_attribute_keys = unique_attribute_keys
    self.longest_non_root_span_duration_seconds = (
        longest_non_root_span_duration_seconds
    )
    self.num_error_spans = num_error_spans
    self.errors = errors

  def to_dict(self) -> Dict[str, Any]:
    res = {}
    omit_fields = {
        "unique_services",
        "num_spans",
        "num_error_spans",
        "unique_attribute_keys",
    }
    for k, v in self.__dict__.items():
      if k in omit_fields:
        continue
      # Omit fields whose values are None or empty lists/sets
      if v is not None and v != [] and v != set():
        res[k] = v
    return res


class TraceCluster:
  """Represents a cluster of traces grouped by service set, spans, and errors."""

  def __init__(
      self,
      services: List[str],
      num_spans: int,
      num_error_spans: int,
      matching_trace_ids: List[str],
      unique_attribute_keys: List[str],
      examples: List[TraceExample],
  ):
    self.services = services
    self.num_spans = num_spans
    self.num_error_spans = num_error_spans
    self.matching_trace_ids = matching_trace_ids
    self.unique_attribute_keys = unique_attribute_keys
    self.examples = examples

  @property
  def total_matching_traces(self) -> int:
    return len(self.matching_trace_ids)

  @property
  def cluster_id(self) -> str:
    """Computes MD5 hash (hex digest) of cluster signature."""
    services_str = ",".join(self.services)
    canonical = f"services={services_str};num_spans={self.num_spans};num_error_spans={self.num_error_spans}"
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()

  def to_dict(self) -> Dict[str, Any]:
    return {
        "cluster_id": self.cluster_id,
        "services": self.services,
        "unique_attribute_keys": self.unique_attribute_keys,
        "num_spans": self.num_spans,
        "num_error_spans": self.num_error_spans,
        "total_matching_traces": self.total_matching_traces,
        "matching_trace_ids": self.matching_trace_ids,
        "examples": [e.to_dict() for e in self.examples],
    }


def distill_trace(trace: Dict[str, Any]) -> TraceExample:
  """Computes distilled characteristics of a trace."""
  trace_id = trace.get("traceId") or trace.get("trace_id") or ""
  spans = trace.get("spans", [])

  if not spans:
    return TraceExample(
        trace_id=trace_id,
        earliest_timestamp=None,
        last_timestamp=None,
        duration_seconds=0.0,
        num_spans=0,
        unique_services=[],
        unique_attribute_keys=[],
        longest_non_root_span_duration_seconds=0.0,
        num_error_spans=0,
        errors=[],
    )

  min_start = None
  max_end = None
  unique_services = set()
  unique_attribute_keys = set()
  longest_non_root_span_duration_seconds = 0.0
  num_error_spans = 0
  errors = []

  for span in spans:
    start_dt = None
    end_dt = None

    if "startTime" in span:
      try:
        start_dt = _get_span_start(span)
        if min_start is None or start_dt < min_start:
          min_start = start_dt
      except Exception:
        pass

    if "endTime" in span:
      try:
        end_dt = _get_span_end(span)
        if max_end is None or end_dt > max_end:
          max_end = end_dt
      except Exception:
        pass

    service_ids = _get_service_identifiers(span)
    service_name = _get_canonical_service_name(service_ids)
    if service_name:
      unique_services.add(service_name)

    labels = span.get("labels") or span.get("attributes") or {}
    for key in labels.keys():
      unique_attribute_keys.add(key)

    parent_id = span.get("parentSpanId") or span.get("parent_span_id")
    if parent_id is not None and parent_id != 0 and parent_id != "0":
      if start_dt and end_dt:
        span_dur = (end_dt - start_dt).total_seconds()
        if span_dur > longest_non_root_span_duration_seconds:
          longest_non_root_span_duration_seconds = span_dur

    if is_error_span(span):
      num_error_spans += 1
      error_labels = _extract_error_labels(span)
      service_identifier = service_name or span.get("name") or ""
      errors.append({
          "span_id": span.get("spanId") or span.get("span_id") or "",
          "service": service_identifier,
          "service_identifiers": service_ids,
          "error_signifying_labels": error_labels,
      })

  duration_seconds = 0.0
  if min_start and max_end:
    duration_seconds = (max_end - min_start).total_seconds()

  earliest_timestamp_str = None
  if min_start:
    earliest_timestamp_str = min_start.isoformat().replace("+00:00", "Z")

  last_timestamp_str = None
  if max_end:
    last_timestamp_str = max_end.isoformat().replace("+00:00", "Z")

  return TraceExample(
      trace_id=trace_id,
      earliest_timestamp=earliest_timestamp_str,
      last_timestamp=last_timestamp_str,
      duration_seconds=duration_seconds,
      num_spans=len(spans),
      unique_services=sorted(list(unique_services)),
      unique_attribute_keys=sorted(list(unique_attribute_keys)),
      longest_non_root_span_duration_seconds=longest_non_root_span_duration_seconds,
      num_error_spans=num_error_spans,
      errors=errors,
  )


def search_traces(
    projects: List[str],
    filter_query: str,
    limit: int,
    start_time_str: Optional[str] = None,
    end_time_str: Optional[str] = None,
    examples_per_cluster: int = 3,
) -> Dict[str, Any]:
  """Searches traces, distills them, and groups by unique services."""

  # Time range validation: verify the difference does not exceed 7 days
  if start_time_str and end_time_str:
    start_dt = parse_iso8601(start_time_str)
    end_dt = parse_iso8601(end_time_str)
    if abs((end_dt - start_dt).total_seconds()) > 604800:
      raise ValueError(
          "The difference between start_time and end_time cannot exceed 7 days"
          " (604800 seconds)."
      )

  client = _get_trace_client()

  start_time = parse_timestamp(start_time_str)
  end_time = parse_timestamp(end_time_str)

  all_traces = []
  search_errors = []

  for proj in projects:
    try:
      req = {
          "project_id": proj,
          "filter": filter_query,
          "page_size": limit,
          "order_by": "trace_id",
      }
      if start_time:
        req["start_time"] = start_time
      if end_time:
        req["end_time"] = end_time

      traces_pager = client.list_traces(request=req)
      for trace in traces_pager:
        all_traces.append(MessageToDict(trace._pb))
    except Exception as e:
      _logger.warning("Failed to search traces in project %s: %s", proj, e)
      search_errors.append((proj, str(e)))

  if not all_traces and search_errors:
    raise RuntimeError(
        f"Trace API query failed for project '{search_errors[0][0]}': {search_errors[0][1]}"
    )

  retrieved_traces = all_traces[:limit]

  clusters_map = {}
  for trace in retrieved_traces:
    example = distill_trace(trace)
    cluster_key = (
        frozenset(example.unique_services),
        example.num_spans,
        example.num_error_spans,
    )
    if cluster_key not in clusters_map:
      clusters_map[cluster_key] = []
    clusters_map[cluster_key].append(example)

  clusters = []
  for cluster_key, examples in clusters_map.items():
    services_frozenset, num_spans, num_error_spans = cluster_key
    services_list = sorted(list(services_frozenset))
    matching_trace_ids = sorted([e.trace_id for e in examples])
    unique_keys_set = set()
    for e in examples:
      unique_keys_set.update(e.unique_attribute_keys)
    unique_attribute_keys = sorted(list(unique_keys_set))
    clusters.append(
        TraceCluster(
            services=services_list,
            num_spans=num_spans,
            num_error_spans=num_error_spans,
            matching_trace_ids=matching_trace_ids,
            unique_attribute_keys=unique_attribute_keys,
            examples=examples,
        )
    )

  # Sort clusters:
  # - Rank clusters containing traces with errors higher (sorted by the maximum error spans count of any trace in the cluster descending).
  # - Then sort by the maximum latency/duration of any trace in the cluster descending.
  # - Finally, sort by the total count of traces in the cluster descending.
  clusters.sort(
      key=lambda c: (
          -c.num_error_spans,
          -(max(e.duration_seconds for e in c.examples) if c.examples else 0.0),
          -c.total_matching_traces,
      )
  )

  # Cap examples per cluster
  for c in clusters:
    c.examples = c.examples[:examples_per_cluster]

  return {"clusters": [c.to_dict() for c in clusters]}
