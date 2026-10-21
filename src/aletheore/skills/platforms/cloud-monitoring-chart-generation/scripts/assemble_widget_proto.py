"""Stage 3: Assembles and validates google.monitoring.dashboard.v1.Widget textproto."""

import argparse
import json
import os
import sys
import uuid

VALID_ALIGNERS = {
    "ALIGN_NONE",
    "ALIGN_DELTA",
    "ALIGN_RATE",
    "ALIGN_INTERPOLATE",
    "ALIGN_NEXT_OLDER",
    "ALIGN_MIN",
    "ALIGN_MAX",
    "ALIGN_MEAN",
    "ALIGN_COUNT",
    "ALIGN_SUM",
    "ALIGN_STDDEV",
    "ALIGN_COUNT_TRUE",
    "ALIGN_COUNT_FALSE",
    "ALIGN_FRACTION_TRUE",
    "ALIGN_PERCENTILE_99",
    "ALIGN_PERCENTILE_95",
    "ALIGN_PERCENTILE_50",
    "ALIGN_PERCENTILE_05",
    "ALIGN_PERCENT_CHANGE",
}

VALID_REDUCERS = {
    "REDUCE_NONE",
    "REDUCE_MEAN",
    "REDUCE_MIN",
    "REDUCE_MAX",
    "REDUCE_SUM",
    "REDUCE_STDDEV",
    "REDUCE_COUNT",
    "REDUCE_COUNT_TRUE",
    "REDUCE_COUNT_FALSE",
    "REDUCE_FRACTION_TRUE",
    "REDUCE_PERCENTILE_99",
    "REDUCE_PERCENTILE_95",
    "REDUCE_PERCENTILE_50",
    "REDUCE_PERCENTILE_05",
}

def format_proto_string(val: str) -> str:
  """Escapes and quotes a string for protobuf text format."""
  escaped = val.replace("\\", "\\\\").replace('"', '\\"')
  return f'"{escaped}"'


def parse_duration_seconds(duration_str: str) -> int:
  """Robustly parses a time duration string into seconds."""
  if not duration_str:
    raise ValueError("Empty duration_str provided to parse_duration_seconds")
  duration_str = str(duration_str).strip()
  try:
    if duration_str.endswith("s"):
      return int(float(duration_str.rstrip("s")))
    if duration_str.endswith("m"):
      return int(float(duration_str.rstrip("m")) * 60)
    if duration_str.endswith("h"):
      return int(float(duration_str.rstrip("h")) * 3600)
    if duration_str.endswith("d"):
      return int(float(duration_str.rstrip("d")) * 86400)
    return int(float(duration_str))
  except ValueError:
    raise ValueError(f"Unsupported duration format: {duration_str}")


def assemble_widget_textproto(
    title: str,
    promql_query: str,
    lts_filter: dict | None = None,
    plot_type: str = "LINE",
    y_axis_label: str = "",
    unit_override: str = "",
    scale: str = "LINEAR",
) -> str:
  """Assembles a valid Widget protobuf textproto string with omitted legend_template."""
  clean_title = title.strip()
  clean_promql = promql_query.strip()
  clean_plot_type = plot_type.strip().upper() if plot_type else "LINE"
  clean_scale = scale.strip().upper() if scale else "LINEAR"

  if clean_plot_type not in ("LINE", "STACKED_AREA", "STACKED_BAR", "HEATMAP"):
    clean_plot_type = "LINE"

  lines = [
      "widget {",
      f"  title: {format_proto_string(clean_title)}",
      "  xy_chart {",
      "    chart_options {",
      "      mode: COLOR",
      "    }",
      "    data_sets {",
      "      time_series_query {",
  ]
  if lts_filter:
    lines.extend([
        "        time_series_filter {",
        (
            "          filter:"
            f" {format_proto_string(lts_filter.get('filter', ''))}"
        ),
    ])
    agg = lts_filter.get("aggregation", {})
    if agg:
      lines.append("          aggregation {")
      if "alignmentPeriod" in agg:
        secs = parse_duration_seconds(agg["alignmentPeriod"])
        lines.extend([
            "            alignment_period {",
            f"              seconds: {secs}",
            "            }",
        ])
      if "perSeriesAligner" in agg:
        aligner = agg["perSeriesAligner"]
        if aligner not in VALID_ALIGNERS:
          raise ValueError(f"Invalid perSeriesAligner: {aligner}")
        lines.append(f"            per_series_aligner: {aligner}")
      if "crossSeriesReducer" in agg:
        reducer = agg["crossSeriesReducer"]
        if reducer not in VALID_REDUCERS:
          raise ValueError(f"Invalid crossSeriesReducer: {reducer}")
        lines.append(f"            cross_series_reducer: {reducer}")
      for gb in agg.get("groupByFields", []):
        lines.append(f"            group_by_fields: {format_proto_string(gb)}")
      lines.append("          }")
    lines.append("        }")
  else:
    lines.append(
        f"        prometheus_query: {format_proto_string(clean_promql)}"
    )

  if unit_override and unit_override.strip():
    lines.append(
        f"        unit_override: {format_proto_string(unit_override.strip())}"
    )

  lines.extend([
      "      }",
      f"      plot_type: {clean_plot_type}",
      "      target_axis: Y1",
      "    }",
      "    y_axis {",
  ])

  if y_axis_label and y_axis_label.strip():
    lines.append(f"      label: {format_proto_string(y_axis_label.strip())}")

  lines.extend([
      f"      scale: {clean_scale}",
      "    }",
      "  }",
      "}",
  ])

  return "\n".join(lines)


def get_auto_output_path(work_dir: str) -> str:
  """Safely generates a random, parallel-robust filename to avoid race conditions."""
  # Adjust for Google3 environments where the agent may run from the CitC client root
  # rather than the google3/ directory. This check is safely bypassed in public (GitHub)
  # environments because the google3/ directory will not exist. Internally, the workspace
  # contains a parent CitC directory (causing flakiness if the agent runs from there). Externally,
  # the Git checkout is the true root, so agents won't accidentally run from a parent wrapper.
  if not work_dir.endswith("google3") and os.path.basename(work_dir) != "google3":
    if os.path.exists(os.path.join(work_dir, "google3")):
      work_dir = os.path.join(work_dir, "google3")

  while True:
    random_id = uuid.uuid4().hex[:8]
    candidate = os.path.join(work_dir, f"chart_{random_id}.textproto")
    if not os.path.exists(candidate):
      return candidate

def main() -> None:
  parser = argparse.ArgumentParser(
      description="Stage 3 Server-Driven UI (SDUI) widget textproto assembler."
  )
  parser.add_argument(
      "--promql_query", "-q", default="", help="PromQL query expression."
  )
  parser.add_argument(
      "--lts_request_json",
      "-l",
      default="",
      help="ListTimeSeries request JSON.",
  )
  parser.add_argument("--title", "-t", default="", help="Widget chart title.")
  parser.add_argument(
      "--plot_type",
      "-p",
      default="LINE",
      help="Plot type (for example: LINE, STACKED_AREA).",
  )
  parser.add_argument(
      "--y_axis_label", "-y", default="", help="Y-axis label string."
  )
  parser.add_argument(
      "--unit_override",
      "-u",
      default="",
      help="Unit override string (for example: '%%', 'By/s').",
  )
  parser.add_argument(
      "--scale", "-s", default="LINEAR", help="Y-axis scale (LINEAR or LOG10)."
  )
  parser.add_argument(
      "--spec_json",
      "-j",
      default="",
      help="JSON string of SemanticPlotSpec from Stage 2.",
  )

  args = parser.parse_args()

  title = args.title
  plot_type = args.plot_type
  y_axis_label = args.y_axis_label
  unit_override = args.unit_override

  if args.spec_json:
    try:
      spec = json.loads(args.spec_json)
      if isinstance(spec, dict):
        title = spec.get("title", title)
        plot_type = spec.get("plotType", plot_type)
        y_axis_label = spec.get("yAxisLabel", y_axis_label)
        unit_override = spec.get("unitOverride", unit_override)
    except Exception:
      pass

  if not title:
    title = "Monitoring Chart"

  lts_filter = None
  if args.lts_request_json:
    try:
      lts_filter = json.loads(args.lts_request_json)
    except Exception as e:
      print(f"ERROR: Failed to parse LTS JSON payload: {e}", file=sys.stderr)
      sys.exit(1)

    if not lts_filter.get("filter"):
      print(
          "ERROR: Provided LTS JSON payload is missing the mandatory 'filter'"
          " key.",
          file=sys.stderr,
      )
      sys.exit(1)

  try:
    proto_text = assemble_widget_textproto(
        title=title,
        promql_query=args.promql_query,
        lts_filter=lts_filter,
        plot_type=plot_type,
        y_axis_label=y_axis_label,
        unit_override=unit_override,
        scale=args.scale,
    )
  except ValueError as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)

  print(proto_text)
  work_dir = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())
  output_path = get_auto_output_path(work_dir)

  with open(output_path, "w", encoding="utf-8") as f:
    f.write(proto_text)
  print(f"Wrote widget textproto to: {output_path}", file=sys.stderr)

if __name__ == "__main__":
  main()
