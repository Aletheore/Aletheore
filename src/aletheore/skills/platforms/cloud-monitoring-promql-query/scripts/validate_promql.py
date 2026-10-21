#!/usr/bin/env python3
"""PromQL query validator for Google Cloud Monitoring metric queries.

Validates PromQL syntax and Cloud Monitoring specific semantic requirements
using the
promql-parser Python library:
- Requires 'monitored_resource' label matchers on metric selectors.
- Requires rate(), increase(), or irate() wrapping around counter metrics
  (_count, _total, usage_time).
- Requires histogram_quantile() wrapping around distribution bucket metrics
  (_bucket).
"""

import argparse
import re
import sys

try:
  import promql_parser  # pylint: disable=g-import-not-at-top
except ImportError:
  print(
      "Error: 'promql-parser' Python package is not installed.",
      file=sys.stderr,
  )
  print(
      "Please install it using: pip install promql-parser",
      file=sys.stderr,
  )
  sys.exit(1)

# Google Cloud Monitoring semantic requirement: counter metrics must be
# evaluated using a rate, increase, or irate function.
COUNTER_WRAPPERS = (
    "rate",
    "increase",
    "irate",
)

# Metric name suffixes identifying Google Cloud Monitoring counter metrics.
COUNTER_METRIC_SUFFIXES = (
    "_count",
    "_total",
    "usage_time",
)

# Metric names that require specific state label filtering.
STATE_FILTERED_METRICS = (
    "agent_googleapis_com:memory_percent_used",
    "agent_googleapis_com:disk_percent_used",
)


def extract_query(content: str) -> str:
  """Extracts a PromQL query from markdown and sanitizes macro variables."""
  pattern = re.compile(r"(?s)```(?:[a-zA-Z0-9_-]+)?\s*\r?\n(.*?)\r?\n```")
  matches = pattern.findall(content)
  if matches:
    content = matches[0]

  # Sanitize template variables like ${__interval} to [5m] for validation.
  content = re.sub(r"\[\s*\$\{?__interval\}?\s*\]", "[5m]", content)
  return content.strip()


def validate_promql_query(query: str) -> tuple[list[str], list[str]]:
  """Validates syntax and Cloud Monitoring semantics using the promql-parser AST."""
  try:
    ast = promql_parser.parse(query)
  except Exception as e:  # pylint: disable=broad-exception-caught
    return [f"Syntax Error: {e}"], []

  errors = []
  warnings = []

  def get_func_name(node) -> str:
    """Returns function name or aggregation operator name from an AST node."""
    if hasattr(node, "func"):  # Matches Call nodes (rate, histogram_quantile)
      f = node.func
      return getattr(f, "name", str(f))
    if hasattr(node, "op"):  # Matches AggregateExpr nodes (sum, avg)
      o = node.op
      return getattr(o, "name", str(o))
    if hasattr(node, "name"):  # Matches VectorSelector nodes
      return str(node.name)
    return str(node)

  def walk(node, ancestors):
    if node is None:
      return

    # Check VectorSelector AST nodes (has matchers or label_matchers)
    matchers_obj = getattr(node, "matchers", None) or getattr(
        node, "label_matchers", None
    )
    if matchers_obj is not None:
      metric_name = getattr(node, "name", "") or "{...}"

      if hasattr(matchers_obj, "matchers"):
        matcher_list = matchers_obj.matchers
      elif isinstance(matchers_obj, (list, tuple)):
        matcher_list = matchers_obj
      else:
        matcher_list = []

      # Rule 1: monitored_resource filter requirement
      matcher_names = [getattr(m, "name", "") for m in matcher_list]
      if "monitored_resource" not in matcher_names:
        errors.append(
            f"Metric selector '{metric_name}' is missing the"
            " 'monitored_resource' type filter."
        )

      # Rule 2: Counter metric wrapping requirement
      is_counter = any(metric_name.endswith(s) for s in COUNTER_METRIC_SUFFIXES)
      if is_counter:
        has_rate_wrapper = any(
            get_func_name(a) in COUNTER_WRAPPERS for a in ancestors
        )
        if not has_rate_wrapper:
          warnings.append(
              f"Counter metric '{metric_name}' is not wrapped in rate() or"
              " increase()."
          )

      # Rule 3: Histogram bucket metric wrapping requirement
      if metric_name.endswith("_bucket"):
        has_histogram_wrapper = any(
            get_func_name(a) == "histogram_quantile" for a in ancestors
        )
        if not has_histogram_wrapper:
          errors.append(
              f"Histogram bucket metric '{metric_name}' is not wrapped in"
              " histogram_quantile()."
          )

      # Rule 4: GCE Agent memory/disk utilization state label filtering
      if metric_name in STATE_FILTERED_METRICS:
        has_state_not_free = False
        has_state_used = False
        for m in matcher_list:
          m_name = getattr(m, "name", "")
          m_value = getattr(m, "value", "")
          m_op = getattr(m, "op", None)

          if m_name == "state":
            if m_op == promql_parser.MatchOp.NotEqual and m_value == "free":
              has_state_not_free = True
            if m_op == promql_parser.MatchOp.Equal and m_value == "used":
              has_state_used = True

        if not has_state_not_free:
          errors.append(
              f"Metric selector '{metric_name}' is missing the"
              " 'state!=\"free\"' filter to exclude free memory/disk state."
          )
        if has_state_used:
          errors.append(
              f"Metric selector '{metric_name}' must not use 'state=\"used\"'"
              " as it ignores cached and buffered memory/disk."
          )

    # Recurse children AST nodes
    children = []
    for attr in (
        "expressions",
        "expression",
        "vector_selector",
        "vector",
        "args",
        "lhs",
        "rhs",
        "expr",
    ):
      val = getattr(node, attr, None)
      if val:
        children.extend(val if isinstance(val, (list, tuple)) else [val])

    for child in children:
      if child and isinstance(child, object):
        walk(child, ancestors + [node])

  walk(ast, [])
  return errors, warnings


def main():
  parser = argparse.ArgumentParser(
      description="Validate PromQL queries for Google Cloud Monitoring."
  )
  parser.add_argument(
      "--file",
      type=str,
      default="",
      help="Path to the file containing the PromQL query.",
  )
  parser.add_argument(
      "--query",
      type=str,
      nargs="*",
      default=[],
      help="Direct PromQL query string(s) to validate.",
  )
  args = parser.parse_args()

  if args.file:
    try:
      with open(args.file, "r", encoding="utf-8") as f:
        queries = [extract_query(f.read())]
    except OSError as e:
      print(f"Error reading file: {e}", file=sys.stderr)
      sys.exit(1)
  elif args.query:
    queries = [extract_query(q) for q in args.query if q.strip()]
  else:
    print("Error: Must specify either --file or --query.", file=sys.stderr)
    parser.print_help(file=sys.stderr)
    sys.exit(1)

  if not queries:
    print("Error: Empty query.", file=sys.stderr)
    sys.exit(1)

  has_errors = False
  for i, query in enumerate(queries, 1):
    errors, warnings = validate_promql_query(query)
    prefix = f"[{i}/{len(queries)}] " if len(queries) > 1 else ""

    if warnings:
      print(f"{prefix}Validation Warnings for '{query}':", file=sys.stderr)
      for warn in warnings:
        print(f"  - {warn}", file=sys.stderr)

    if errors:
      has_errors = True
      print(f"{prefix}Validation Failed for '{query}':", file=sys.stderr)
      for err in errors:
        print(f"  - {err}", file=sys.stderr)

  if has_errors:
    sys.exit(1)

  if len(queries) == 1:
    print(
        "OK: PromQL query passed all syntax and Cloud Monitoring validation"
        " checks."
    )
  else:
    print(f"OK: All {len(queries)} PromQL queries passed validation checks.")
  sys.exit(0)


if __name__ == "__main__":
  main()
