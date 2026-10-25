#!/usr/bin/env python3
"""CLI wrapper to search and list traces in a GCP project."""

import argparse
import json
import logging
import secrets
import sys

from trace_searcher import search_traces

_logger = logging.getLogger(__name__)


def main():
  parser = argparse.ArgumentParser(description="Search GCP Cloud Traces.")
  parser.head = getattr(parser, "head", None)  # avoid linter warning
  parser.add_argument(
      "--filter", required=True, help="Trace query filter expression"
  )
  parser.add_argument(
      "--start-time", type=str, help="Optional start time ISO 8601"
  )
  parser.add_argument("--end-time", type=str, help="Optional end time ISO 8601")
  parser.add_argument(
      "--projects",
      nargs="+",
      required=True,
      help="List of GCP Project IDs to search.",
  )
  parser.add_argument(
      "--limit", type=int, default=10, help="Max traces to return"
  )
  parser.add_argument(
      "--examples-per-cluster",
      type=int,
      default=3,
      help="Max trace summary examples per cluster",
  )
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

  traces = search_traces(
      projects=args.projects,
      filter_query=args.filter,
      limit=args.limit,
      start_time_str=args.start_time,
      end_time_str=args.end_time,
      examples_per_cluster=args.examples_per_cluster,
  )
  raw_json = json.dumps(traces, indent=2)

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
