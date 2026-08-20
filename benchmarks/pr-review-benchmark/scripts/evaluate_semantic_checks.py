"""Materializes every benchmark case at its pinned base commit, applies its
real diff, runs Aletheore's deterministic semantic checks against it, and
compares findings with committed ground truth.

This is local and offline: no live PR, no hosted tools, no GitHub API calls
beyond the `git clone` each case already needs. It exercises the real scan
(scan_repository) and the real review-context builders
(build_referenced_symbol_context, find_semantic_regressions) against real
cloned repositories - the same code paths production runs, pointed at real
historical bugs instead of live PR events.

Usage:
    python3 scripts/evaluate_semantic_checks.py
    python3 scripts/evaluate_semantic_checks.py --only 001,016,020
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent / "src"))

from scripts.build_case_repo import prepare_case_checkout  # noqa: E402
from scripts.cases import load_case  # noqa: E402

from aletheore.evidence import scan_repository  # noqa: E402

sys.path.insert(0, str(ROOT.parent.parent / "github-app"))
from scan_worker.flash_review import build_referenced_symbol_context  # noqa: E402
from scan_worker.semantic_checks import find_semantic_regressions  # noqa: E402

# How close a finding's line has to land to the ground truth's expected_line
# to count as "found the real bug" rather than "found something else in the
# same file". Matches flash_review.py's own DIFF_LINE_TOLERANCE /
# LINE_CITATION_CONTEXT_WINDOW convention deliberately - one proximity
# rationale, reused rather than re-derived a third time.
LINE_TOLERANCE = 8

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")


def git_diff_to_review_format(raw_diff: str) -> str:
    """A real `git diff`/`.diff` file uses `diff --git a/X b/Y`, `index
    ...`, `--- a/X`, `+++ b/Y` framing per file. Aletheore's internal
    review format - what GitHub's compare API's own `patch` field already
    looks like, and what flash_review.py/semantic_checks.py both parse -
    is `--- {filename} ---` followed directly by the `@@ ... @@` hunks,
    with no a/-b/ framing at all. This converts real diffs (as stored in
    the benchmark corpus) into that shape, so the real code path under
    test sees exactly the format it sees in production."""
    parts: list[str] = []
    current_filename: str | None = None
    body_lines: list[str] = []
    # Real git-diff header lines (index/---/+++) only ever appear between a
    # `diff --git` marker and that file's first `@@` hunk - never inside a
    # hunk body. Filtering on prefix alone, with no positional guard, also
    # matches a genuine removed/added source line shaped like "--- old
    # constant" (a deleted comment: "-- old constant" renders as "---  old
    # constant" once diffed) and silently drops it from body_lines,
    # indistinguishable from a real "--- a/path" header. seen_first_hunk
    # scopes the filter to only the real header region.
    seen_first_hunk = False

    def flush() -> None:
        if current_filename is not None and body_lines:
            parts.append(f"--- {current_filename} ---\n" + "\n".join(body_lines))

    for line in raw_diff.splitlines():
        match = _DIFF_GIT_RE.match(line)
        if match:
            flush()
            current_filename = match.group(2)
            body_lines = []
            seen_first_hunk = False
            continue
        if not seen_first_hunk and line.startswith(("index ", "--- ", "+++ ")):
            continue
        if line.startswith("@@"):
            seen_first_hunk = True
        if current_filename is not None:
            body_lines.append(line)
    flush()
    return "\n\n".join(parts)


def _changed_files_from_diff(raw_diff: str) -> list[str]:
    # Line-by-line via .match(), not .finditer() with ^/$ across the whole
    # multi-line string - without re.MULTILINE, ^ and $ anchor to the start
    # and end of the *entire* string, not each line, so .finditer() here
    # silently matched nothing on any real multi-file diff. Confirmed via a
    # real corpus run: every one of 21 cases came back with changed_files
    # == [], which zeroed file_contents and changed_files everywhere
    # downstream and made every case's finding count trivially 0 - not a
    # real result about the semantic checker's recall, a broken harness
    # reporting silence as an empty answer.
    return [
        match.group(2)
        for line in raw_diff.splitlines()
        if (match := _DIFF_GIT_RE.match(line)) is not None
    ]


def _line_near(line: int, expected: int) -> bool:
    return abs(line - expected) <= LINE_TOLERANCE


def evaluate_case(case_dir: Path) -> dict:
    case = load_case(case_dir)
    ground_truth = case["ground_truth"]
    raw_diff = case["diff_path"].read_text()
    changed_files = _changed_files_from_diff(raw_diff)

    with tempfile.TemporaryDirectory() as workdir:
        try:
            checkout_dir = prepare_case_checkout(case["repo"], case["diff_path"], Path(workdir))
        except RuntimeError as exc:
            return {"case_id": case["case_id"], "error": str(exc)}

        evidence = scan_repository(
            checkout_dir,
            check_vulnerabilities=False,
            scan_git_history=False,
            check_licenses=False,
            map_endpoints=False,
            map_schema=False,
        )

        diff_text = git_diff_to_review_format(raw_diff)

        file_contents: dict[str, str] = {}
        for path in changed_files:
            full_path = checkout_dir / path
            if full_path.is_file():
                try:
                    file_contents[path] = full_path.read_text(errors="replace")
                except OSError:
                    continue

        def _fetch_symbol_source(file_path: str, start_line: int, end_line: int) -> str | None:
            full_path = checkout_dir / file_path
            if not full_path.is_file():
                return None
            try:
                lines = full_path.read_text(errors="replace").splitlines()
            except OSError:
                return None
            return "\n".join(lines[max(0, start_line - 1) : end_line])

        referenced_symbol_context = build_referenced_symbol_context(
            evidence, changed_files, diff_text, _fetch_symbol_source
        )

        findings = find_semantic_regressions(diff_text, file_contents, referenced_symbol_context)

    expected_file = ground_truth.get("expected_file")
    expected_line = ground_truth.get("expected_line")
    category = ground_truth.get("category")

    matched = None
    if category != "clean" and expected_file and expected_line:
        for finding in findings:
            if finding["file"] == expected_file and _line_near(finding["line"], expected_line):
                matched = finding
                break

    return {
        "case_id": case["case_id"],
        "category": category,
        "expected_file": expected_file,
        "expected_line": expected_line,
        "findings": findings,
        "matched_expected": matched is not None,
        "matched_finding": matched,
        "false_positive_count": len(findings) if category == "clean" else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="comma-separated case-id prefixes, e.g. 001,016")
    parser.add_argument(
        "--include-swebench",
        action="store_true",
        help=(
            "Include swebench-* cases. Excluded by default: they were added for a separate "
            "citation-grounding effort against large real-world repos (django, astropy), not "
            "for this semantic-checker corpus, and cloning+scanning them is much slower."
        ),
    )
    parser.add_argument("--out", default=str(HERE.parent / "results" / "semantic_check_eval.json"))
    args = parser.parse_args()

    cases_dir = ROOT / "cases"
    case_dirs = sorted(cases_dir.iterdir())
    if not args.include_swebench and not args.only:
        case_dirs = [d for d in case_dirs if not d.name.startswith("swebench")]
    if args.only:
        prefixes = {p.strip() for p in args.only.split(",")}
        case_dirs = [d for d in case_dirs if any(d.name.startswith(p) for p in prefixes)]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for case_dir in case_dirs:
        if not case_dir.is_dir():
            continue
        print(f"  running {case_dir.name} ...", file=sys.stderr)
        try:
            result = evaluate_case(case_dir)
        except Exception as exc:  # noqa: BLE001 - one case's failure must not abort the run
            result = {"case_id": case_dir.name, "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        # Written after every case, not just at the end - a run against
        # dozens of real repo clones is the kind of thing worth
        # interrupting partway through, and losing every already-computed
        # result to an interrupt defeats the point of checking in on it.
        out_path.write_text(json.dumps(results, indent=2))

    errored = [r for r in results if "error" in r]
    real_bug = [r for r in results if r.get("category") in {"real_bug_fix", "injected_bug"}]
    clean = [r for r in results if r.get("category") == "clean"]
    recall = sum(1 for r in real_bug if r.get("matched_expected")) if real_bug else 0
    false_positives = sum(1 for r in clean if (r.get("false_positive_count") or 0) > 0)

    print()
    print(f"cases: {len(results)}, errored: {len(errored)}")
    print(f"real_bug_fix/injected_bug: {recall}/{len(real_bug)} found by a semantic check")
    print(f"clean cases with a false-positive semantic finding: {false_positives}/{len(clean)}")
    if errored:
        print("errors:")
        for r in errored:
            print(f"  {r['case_id']}: {r['error']}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
