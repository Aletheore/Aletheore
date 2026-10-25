"""Validation utility for chart_generation widget textprotos."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import glob
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import textproto_util  # type: ignore[missing-import]

parse_and_validate_widget = textproto_util.parse_and_validate_widget


def validate_widget(
    widget: textproto_util.Widget,
    expected_promql_substring: str = "",
    expected_lts_filter_substring: str = "",
    expected_unit_override: str = "",
    expected_plot_type: str = "",
) -> None:
  """Validates the Widget message structure and fields."""
  if not widget.title:
    raise ValueError("Widget title must not be empty")
  if len(widget.title) > 80:
    raise ValueError(f"Widget title exceeds 80 chars: {len(widget.title)}")

  if not (widget.HasField("xy_chart") and widget.xy_chart is not None):
    raise ValueError("Widget must contain an xy_chart")
  xy_chart = widget.xy_chart

  if len(xy_chart.data_sets) == 0:
    raise ValueError("xy_chart must contain at least one data_set")

  # Phase 1: Structural exclusivity applies to ALL datasets
  for idx, ds in enumerate(xy_chart.data_sets):
    has_promql = bool(ds.time_series_query.prometheus_query)
    has_lts = bool(ds.time_series_query.time_series_filter)
    
    if not (has_promql or has_lts):
      raise ValueError(f"data_set[{idx}] must contain either prometheus_query or time_series_filter")
    if has_promql and has_lts:
      raise ValueError(f"data_set[{idx}] cannot contain BOTH prometheus_query and time_series_filter")

  # Phase 2: Find at least one explicitly matching dataset for the assertions
  last_err = None
  for ds in xy_chart.data_sets:
    try:
      has_promql = bool(ds.time_series_query.prometheus_query)
      has_lts = bool(ds.time_series_query.time_series_filter)

      if expected_promql_substring:
        if not has_promql:
          raise ValueError("Expected PromQL but found LTS filter")
        if expected_promql_substring not in ds.time_series_query.prometheus_query:
          raise ValueError(
              "PromQL query missing expected substring"
              f" '{expected_promql_substring}':"
              f" {ds.time_series_query.prometheus_query}"
          )

      if expected_lts_filter_substring:
        if not has_lts:
          raise ValueError("Expected LTS filter but found PromQL")
        if (
            expected_lts_filter_substring
            not in ds.time_series_query.time_series_filter.filter
        ):
          raise ValueError(
              "LTS filter missing expected substring"
              f" '{expected_lts_filter_substring}':"
              f" {ds.time_series_query.time_series_filter.filter}"
          )

      if expected_unit_override:
        if ds.time_series_query.unit_override != expected_unit_override:
          raise ValueError(
              f"Expected unit_override '{expected_unit_override}', got"
              f" '{ds.time_series_query.unit_override}'"
          )

      if expected_plot_type:
        if ds.plot_type != expected_plot_type:
          raise ValueError(
              f"Expected plot_type '{expected_plot_type}', got '{ds.plot_type}'"
          )
          
      return  # Found a dataset that completely matches the criteria!
      
    except ValueError as e:
      last_err = e
      
  # If we exhausted all datasets in this widget and none matched perfectly:
  raise ValueError(f"No matching data_set found in widget. Last dataset error: {last_err}")


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
      description="Validate widget textproto for chart_generation skill."
  )
  parser.add_argument(
      "--input_file",
      "-i",
      default="",
      help=(
          "Path to Widget textproto file or '-' for STDIN. If empty, searches"
          " all .textproto files."
      ),
  )
  parser.add_argument(
      "--expected_promql_substring",
      default="",
      help="Expected substring in PromQL query.",
  )
  parser.add_argument(
      "--expected_lts_filter_substring",
      default="",
      help="Expected LTS Filter substring.",
  )
  parser.add_argument(
      "--expected_unit_override",
      default="",
      help="Expected unit_override string (for example: '%%', 'By/s').",
  )
  parser.add_argument(
      "--expected_plot_type",
      default="",
      help="Expected plot_type (for example: 'LINE', 'STACKED_AREA').",
  )

  args = parser.parse_args(argv)

  try:
    files_to_check = []
    if args.input_file == "-":
      if not sys.stdin.isatty():
        files_to_check.append(("-", sys.stdin.read()))
    elif args.input_file:
      work_dir = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())
      input_path = args.input_file
      if not os.path.isabs(input_path):
        input_path = os.path.join(work_dir, input_path)
      if os.path.exists(input_path):
        with open(input_path, "r", encoding="utf-8") as f:
          files_to_check.append((input_path, f.read()))
      else:
        print(
            f"ERROR: Input file '{args.input_file}' not found on disk.",
            file=sys.stderr,
        )
        return 1
    else:

      work_dir = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())

      search_dirs = [work_dir]
      # Adjust for Google3 environments where the agent may run from the CitC client root
      # rather than the google3/ directory. This check is safely bypassed in public (GitHub)
      # environments because the google3/ directory will not exist. Internally, the workspace
      # contains a parent CitC directory (causing flakiness if the agent runs from there). Externally,
      # the Git checkout is the true root, so agents won't accidentally run from a parent wrapper.
      if not work_dir.endswith("google3") and os.path.exists(
          os.path.join(work_dir, "google3")
      ):
        search_dirs.append(os.path.join(work_dir, "google3"))

      for d in search_dirs:
        for p in glob.glob(os.path.join(d, "*.textproto")):
          with open(p, "r", encoding="utf-8") as f:
            files_to_check.append((p, f.read()))

      if not files_to_check:
        print(
            "ERROR: No .textproto files found in the workspace directories:"
            f" {search_dirs}",
            file=sys.stderr,
        )
        return 1

    # Phase 1: Guarantee ALL generated files are structurally valid.
    parsed_widgets = []
    for path, content in files_to_check:
      try:
        widget = textproto_util.parse_and_validate_widget(content)
        validate_widget(widget) # Strict structural rules
        parsed_widgets.append((path, widget))
      except Exception as e:
        print(f"ERROR: File '{path}' is structurally invalid: {e}", file=sys.stderr)
        return 1
        
    # Phase 2: If we have specific expectations, find at least one matching file.
    if not (args.expected_promql_substring or args.expected_lts_filter_substring 
            or args.expected_unit_override or args.expected_plot_type):
      # No specific assertions required, so passing structure is enough.
      for path, widget in parsed_widgets:
        print(f"Successfully validated structure of Widget '{path}'")
      return 0
      
    last_err = None
    for path, widget in parsed_widgets:
      try:
        validate_widget(
            widget=widget,
            expected_promql_substring=args.expected_promql_substring,
            expected_lts_filter_substring=args.expected_lts_filter_substring,
            expected_unit_override=args.expected_unit_override,
            expected_plot_type=args.expected_plot_type,
        )
        print(
            f"Successfully validated Widget textproto '{path}'. Title:"
            f" '{widget.title}'"
        )
        return 0
      except Exception as e:
        last_err = e
        continue

    # If we tried files but none passed:
    print(
        "ERROR: Failed to validate any Widget textproto finding a match. Last"
        f" error: {last_err}",
        file=sys.stderr,
    )
    return 1
  except Exception as e:  # pylint: disable=broad-except
    print(f"ERROR: Failed to validate Widget textproto: {e}", file=sys.stderr)
    return 1

if __name__ == "__main__":
  sys.exit(main())
