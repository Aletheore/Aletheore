#!/usr/bin/env python3
"""Resolves and outputs the active GCP Project ID, scanning standard variables and auth configs."""

import argparse
import sys

import project_resolver


def main():
  try:
    project_id = project_resolver.resolve_project_id()
    print(project_id)
  except RuntimeError as err:
    print(f"Error: {err}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()
