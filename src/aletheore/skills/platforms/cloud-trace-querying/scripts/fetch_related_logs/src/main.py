#!/usr/bin/env python3
"""Fetches Google Cloud Logging entries correlated with a trace and/or span."""

import argparse
import json
import logging
import secrets
import sys
from log_fetcher import fetch_logs

_logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
  """Defines and parses command-line arguments."""
  parser = argparse.ArgumentParser(
      description="Fetch logs correlated with a GCP Trace."
  )
  parser.head = getattr(parser, "head", None)  # avoid linter warning
  parser.add_argument("--trace-id", required=True, help="GCP Trace ID")
  parser.add_argument(
      "--span-id", help="Optional GCP Span ID to narrow search"
  )
  parser.add_argument(
      "--trace-projects",
      nargs="+",
      required=True,
      help="List of trace projects to scan.",
  )
  parser.add_argument(
      "--log-projects", nargs="+", help="Logging projects to scan."
  )
  parser.add_argument("--start-time", help="Optional start time ISO 8601")
  parser.add_argument("--end-time", help="Optional end time ISO 8601")
  parser.add_argument(
      "--limit", type=int, default=100, help="Max log entries to return"
  )
  parser.add_argument(
      "--output",
      type=str,
      help="Optional output file path to write results.",
  )
  parser.add_argument(
      "--no-wrapper-tag",
      action="store_true",
      default=False,
      help="Do not wrap output in untrusted telemetry tags.",
  )
  return parser.parse_args()


def _execute_fetch(args: argparse.Namespace):
  """Orchestrates fetching and writing/printing output."""
  logs = fetch_logs(
      trace_id=args.trace_id,
      span_id=args.span_id,
      limit=args.limit,
      trace_projects=args.trace_projects,
      log_projects=args.log_projects,
      start_time=args.start_time,
      end_time=args.end_time,
  )
  raw_json = json.dumps(logs, indent=2)

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


def main():
  args = _parse_args()
  _execute_fetch(args)


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  try:
    main()
  except Exception as err:
    _logger.exception(err)
    sys.exit(1)
