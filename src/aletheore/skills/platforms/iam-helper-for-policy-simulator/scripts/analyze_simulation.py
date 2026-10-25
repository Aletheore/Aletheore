#!/usr/bin/env python3
"""Analyzes IAM policy simulation results and identifies access revocations."""

import json
import os
import sys


def main():
  results_file = "/tmp/simulation_results.json"

  if not os.path.exists(results_file):
    print(f"PARSE_ERROR: File {results_file} not found.")
    sys.exit(1)

  try:
    with open(results_file, "r") as f:
      data = json.load(f)

    results = (
        data
        if isinstance(data, list)
        else data.get("results", data.get("replayResults", []))
    )
    revoked = []

    for item in results:
      if not isinstance(item, dict):
        continue

      # Safely navigate the deeply nested JSON structure
      diff = item.get("diff", {}).get("accessDiff", {})
      change = diff.get("accessChange")

      # Fallbacks just in case the API structure flattens
      if not change:
        change = item.get("accessChange")
      if not change:
        change = item.get("accessTuple", {}).get("accessChange")

      if change in ["ACCESS_REVOKED", "ACCESS_MAYBE_REVOKED"]:
        revoked.append(item)

    print(f"REVOKED_COUNT={len(revoked)}")
    if revoked:
      print(json.dumps(revoked, indent=2))

  except Exception as e:  # pylint: disable=broad-exception-caught
    print(f"PARSE_ERROR: {e}")
    sys.exit(1)


if __name__ == "__main__":
  main()
