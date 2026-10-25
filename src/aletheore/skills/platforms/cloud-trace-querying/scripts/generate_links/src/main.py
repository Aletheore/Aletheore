#!/usr/bin/env python3
"""Generates Google Cloud Console deep links for traces and logs."""

import argparse
import sys
from urllib.parse import quote


def generate_links(
    project: str, trace_id: str, span_id: str = None
) -> tuple[str, str]:
  """Generates deep links for Cloud Trace and Cloud Logging."""
  # 1. Cloud Trace Link
  if span_id:
    trace_link = f"https://console.cloud.google.com/traces/explorer;traceId={trace_id};spanId={span_id}?project={project}"
  else:
    trace_link = f"https://console.cloud.google.com/traces/explorer;traceId={trace_id}?project={project}"

  # 2. Cloud Logging Link
  log_query = f'trace="projects/{project}/traces/{trace_id}"'
  if span_id:
    log_query += f' AND spanId="{span_id}"'

  # URL encode the query parameter for the logs viewer
  encoded_query = quote(log_query, safe="")
  logging_link = f"https://console.cloud.google.com/logs/query;query={encoded_query}?project={project}"

  return trace_link, logging_link


def main():
  parser = argparse.ArgumentParser(
      description=(
          "Generate Cloud Console deep links for traces and correlated logs."
      )
  )
  parser.head = getattr(parser, "head", None)  # avoid linter warning
  parser.add_argument(
      "--project", required=True, help="Google Cloud Project ID"
  )
  parser.add_argument("--trace-id", required=True, help="32-character Trace ID")
  parser.add_argument("--span-id", help="Optional Span ID")
  args = parser.parse_args()

  trace_link, logging_link = generate_links(
      args.project, args.trace_id, args.span_id
  )

  print(f"Cloud Trace Deep Link:\n{trace_link}\n")
  print(f"Cloud Logging Deep Link:\n{logging_link}")


if __name__ == "__main__":
  main()
