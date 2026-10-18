#!/usr/bin/env python3
"""Fetches all spans within a trace by ID."""

import argparse
import json
import logging
import secrets
import sys

from google.cloud import trace_v1
from google.protobuf.json_format import MessageToDict

_logger = logging.getLogger(__name__)


def fetch_entire_trace(project_id: str, trace_id: str) -> list[dict]:
  client = trace_v1.TraceServiceClient()
  trace_response = client.get_trace(project_id=project_id, trace_id=trace_id)

  spans_data = []
  for span in trace_response.spans:
    spans_data.append(MessageToDict(span._pb))

  return spans_data


def main():
  parser = argparse.ArgumentParser(
      description="Fetch details of all trace spans."
  )
  parser.head = getattr(parser, "head", None)  # avoid linter warning
  parser.add_argument("--trace-id", required=True, help="Trace ID")
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

  spans = fetch_entire_trace(args.project, args.trace_id)
  raw_json = json.dumps(spans, indent=2)

  if args.no_wrapper_tag:
    output_str = raw_json
  else:
    token = secrets.token_hex(8)
    output_str = f"<untrusted_telemetry_{token}>\n{raw_json}\n</untrusted_telemetry_{token}>"

  if args.output:
    with open(args.output, "w") as f:
      f.write(output_str)
    _logger.info("Results written to: %s", args.output)
  else:
    print(output_str)


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  try:
    main()
  except Exception as err:
    _logger.exception(err)
    sys.exit(1)
