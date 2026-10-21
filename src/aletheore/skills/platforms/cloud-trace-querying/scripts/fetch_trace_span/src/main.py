#!/usr/bin/env python3
"""Fetches details of a specific span within a trace by querying the trace v1 API."""

import argparse
import json
import logging
import secrets
import sys
from typing import Optional

from google.cloud import trace_v1
from google.protobuf.json_format import MessageToDict


def _span_id_to_int(span_id_str: str) -> Optional[int]:
  try:
    if span_id_str.startswith("0x"):
      return int(span_id_str, 16)
    return int(span_id_str, 10)
  except ValueError:
    try:
      return int(span_id_str, 16)
    except ValueError:
      return None


def fetch_span(project_id: str, trace_id: str, span_id: str) -> dict:
  client = trace_v1.TraceServiceClient()
  trace_response = client.get_trace(project_id=project_id, trace_id=trace_id)

  # First Pass (Exact String Match)
  for span in trace_response.spans:
    if span_id == str(span.span_id):
      return MessageToDict(span._pb)

  # Second Pass (Integer Fallback)
  target_span_val = _span_id_to_int(span_id)
  if target_span_val is not None:
    for span in trace_response.spans:
      db_span_val = _span_id_to_int(str(span.span_id))
      if db_span_val is not None and target_span_val == db_span_val:
        return MessageToDict(span._pb)

  raise ValueError(f"Span {span_id} not found in trace {trace_id}")


def main():
  parser = argparse.ArgumentParser(description="Fetch details of a trace span.")
  parser.head = getattr(parser, "head", None)  # avoid linter warning
  parser.add_argument("--trace-id", required=True, help="Trace ID")
  parser.add_argument("--span-id", required=True, help="Span ID")
  parser.add_argument("--project", required=True, help="GCP Project ID.")
  parser.add_argument(
      "--output", type=str, help="Optional output file path to write results."
  )
  parser.add_argument(
      "--no-wrapper-tag",
      action="store_true",
      default=False,
      help="Do not wrap output in untrusted telemetry tags.",
  )
  args = parser.parse_args()

  project_id = args.project

  try:
    span_data = fetch_span(project_id, args.trace_id, args.span_id)
    raw_json = json.dumps(span_data, indent=2)

    if args.no_wrapper_tag:
      output_str = raw_json
    else:
      token = secrets.token_hex(8)
      output_str = f"<untrusted_telemetry_{token}>\n{raw_json}\n</untrusted_telemetry_{token}>"

    if args.output:
      with open(args.output, "w") as f:
        f.write(output_str)
      print(f"Results written to: {args.output}")
    else:
      print(output_str)
    sys.exit(0)
  except Exception as e:
    logging.exception(e)
    sys.exit(1)


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
  main()
