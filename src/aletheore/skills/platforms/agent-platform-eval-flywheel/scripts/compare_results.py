#!/usr/bin/env python3
"""Diff two Agent Platform Eval result JSON files side by side.

Use in Stage 5 of the Quality Flywheel (Optimize & Iterate) to confirm a fix
improved the target metric without regressing others.

Reads two JSON files produced by ``result.model_dump_json()``, joins summary
metrics by metric_name, and prints baseline vs. candidate scores with a delta.

Usage:
  python compare_results.py --baseline baseline.json --candidate candidate.json
  python compare_results.py -b baseline.json -c candidate.json --threshold 0.05
  python compare_results.py -b baseline.json -c candidate.json --json

Exit codes: 0 = candidate >= baseline on every metric (within threshold),
            1 = at least one metric regressed beyond the threshold.
"""

import argparse
import json
import sys
from typing import Any


def _summary_by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index summary metrics by metric_name."""
    out = {}
    for s in result.get("summary_metrics") or []:
        name = s.get("metric_name") or s.get("metricName")
        if name:
            out[name] = {
                "mean_score": s.get("mean_score") or s.get("meanScore"),
                "pass_rate": s.get("pass_rate") or s.get("passRate"),
                "num_cases": s.get("num_cases") or s.get("numCases"),
            }
    return out


def _delta(a: float | None, b: float | None) -> float | None:
    """Compute b - a, or None if either input is missing."""
    if a is None or b is None:
        return None
    return b - a


def _format_signed(x: float | None) -> str:
    """Render a delta with explicit sign and 4 decimal places."""
    if x is None:
        return "—"
    return f"{x:+.4f}"


def compare(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    threshold: float = 0.0,
) -> tuple[list[dict[str, Any]], bool]:
    """Return per-metric diff rows and whether the candidate is safe to ship.

    A row regresses when (candidate - baseline) < -threshold on either
    mean_score or pass_rate. Threshold defaults to 0 (any drop is a regression).
    """
    b = _summary_by_name(baseline)
    c = _summary_by_name(candidate)
    metric_names = sorted(set(b) | set(c))

    rows = []
    regressed = False
    for name in metric_names:
        bv = b.get(name, {})
        cv = c.get(name, {})
        d_mean = _delta(bv.get("mean_score"), cv.get("mean_score"))
        d_pass = _delta(bv.get("pass_rate"), cv.get("pass_rate"))
        row = {
            "metric_name": name,
            "baseline_mean": bv.get("mean_score"),
            "candidate_mean": cv.get("mean_score"),
            "delta_mean": d_mean,
            "baseline_pass": bv.get("pass_rate"),
            "candidate_pass": cv.get("pass_rate"),
            "delta_pass": d_pass,
        }
        is_regression = (d_mean is not None and d_mean < -threshold) or (
            d_pass is not None and d_pass < -threshold
        )
        row["regressed"] = is_regression
        if is_regression:
            regressed = True
        rows.append(row)
    return rows, not regressed


def _format_table(rows: list[dict[str, Any]]) -> str:
    """Render the diff as a fixed-width text table."""
    if not rows:
        return "(no metrics in either result)\n"
    header = [
        "metric_name",
        "base_mean",
        "cand_mean",
        "Δmean",
        "base_pass",
        "cand_pass",
        "Δpass",
        "regressed",
    ]

    def _cell(r, key):
        v = r.get(key)
        if isinstance(v, bool):
            return "YES" if v else ""
        if isinstance(v, float):
            return (
                f"{v:.4f}"
                if key not in ("delta_mean", "delta_pass")
                else _format_signed(v)
            )
        if v is None:
            return "—"
        return str(v)

    name_map = {
        "metric_name": "metric_name",
        "base_mean": "baseline_mean",
        "cand_mean": "candidate_mean",
        "Δmean": "delta_mean",
        "base_pass": "baseline_pass",
        "cand_pass": "candidate_pass",
        "Δpass": "delta_pass",
        "regressed": "regressed",
    }
    widths = {}
    for col in header:
        cells = [_cell(r, name_map[col]) for r in rows]
        widths[col] = max(len(col), *(len(c) for c in cells))

    line1 = "  ".join(c.ljust(widths[c]) for c in header)
    line2 = "  ".join("-" * widths[c] for c in header)
    body = "\n".join(
        "  ".join(_cell(r, name_map[c]).ljust(widths[c]) for c in header) for r in rows
    )
    return f"{line1}\n{line2}\n{body}\n"


def main():
    parser = argparse.ArgumentParser(
        description="Diff two Agent Platform Eval result JSON files side by side."
    )
    parser.add_argument(
        "--baseline",
        "-b",
        required=True,
        help="Path to the baseline result JSON.",
    )
    parser.add_argument(
        "--candidate",
        "-c",
        required=True,
        help="Path to the candidate result JSON (after the fix).",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=0.0,
        help="A candidate metric counts as regressed only if it drops more"
        " than this much vs. baseline. Default 0.0 (any drop regresses).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the diff as JSON instead of a text table.",
    )
    args = parser.parse_args()

    try:
        with open(args.baseline) as f:
            baseline = json.load(f)
        with open(args.candidate) as f:
            candidate = json.load(f)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    rows, ok = compare(baseline, candidate, threshold=args.threshold)

    if args.json:
        print(json.dumps({"rows": rows, "no_regression": ok}, indent=2))
    else:
        print(_format_table(rows))
        if not ok:
            print(
                f"REGRESSION: at least one metric dropped by more than"
                f" {args.threshold:+.4f} vs. baseline.",
                file=sys.stderr,
            )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
