"""Score fixture.json's questions against a real Aletheore search, and gate
on a real regression versus baseline.json. build_fixture.py must run first
to produce the index this scores against.

    python3 score.py                     # gate: exit 1 if MRR drops too far
    python3 score.py --update-baseline   # after a deliberate, reviewed change

Metric definitions (top-k hit rate, MRR) match aletheore-benchmarks'
scripts/score_retrieval_matrix.py exactly, so a number here is directly
comparable to that repo's published ones for the same corpus.
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from aletheore.search_index import search_index  # noqa: E402

FIXTURE = json.loads((HERE / "fixture.json").read_text())
TOP_K = 10


def _workspace() -> Path:
    root = Path(os.environ.get("BENCH_CI_WORKSPACE", HERE / ".fixture-cache"))
    return root / FIXTURE["name"]


def _ranked_files(repo: Path, question: str) -> list[str]:
    # De-duplicated by path, matching run_retrieval_matrix.py: several
    # chunks of one file can each hit, and a file that answers the question
    # answers it once.
    hits = search_index(repo, question, k=TOP_K, allow_hosted=False)
    files: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        path = hit.get("module_path")
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def _summarise(rows: list[dict]) -> dict:
    def hits_at(k: int) -> int:
        return sum(
            1 for r in rows if any(f in r["ground_truth_files"] for f in r["ranked_files"][:k])
        )

    reciprocal = []
    for r in rows:
        rank = next(
            (i + 1 for i, f in enumerate(r["ranked_files"]) if f in r["ground_truth_files"]),
            None,
        )
        reciprocal.append(1.0 / rank if rank else 0.0)

    return {
        "n": len(rows),
        "top1": hits_at(1),
        "top3": hits_at(3),
        "top5": hits_at(5),
        "mrr": statistics.mean(reciprocal) if rows else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-mrr-drop", type=float, default=0.02,
        help="fail if MRR falls this far below baseline.json's recorded value",
    )
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="write the just-measured score into baseline.json instead of gating on it",
    )
    args = parser.parse_args()

    repo = _workspace()
    questions = []
    for rel in FIXTURE["questions"]:
        questions.extend(json.loads((HERE / rel).read_text()))

    rows = []
    for q in questions:
        question = q.get("question") or q.get("q")
        rows.append({
            "id": q["id"],
            "ground_truth_files": q["ground_truth_files"],
            "ranked_files": _ranked_files(repo, question),
        })

    score = _summarise(rows)
    print(json.dumps(score, indent=2))

    baseline_path = HERE / "baseline.json"
    if args.update_baseline:
        baseline = json.loads(baseline_path.read_text())
        baseline.update(score)
        baseline_path.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"baseline.json updated: mrr {score['mrr']:.4f}")
        return 0

    baseline = json.loads(baseline_path.read_text())
    drop = baseline["mrr"] - score["mrr"]
    print(f"baseline MRR: {baseline['mrr']:.4f}, current MRR: {score['mrr']:.4f}, drop: {drop:.4f}")
    if drop > args.max_mrr_drop:
        print(
            f"REGRESSION: MRR dropped by {drop:.4f}, more than the allowed {args.max_mrr_drop:.4f}",
            file=sys.stderr,
        )
        return 1
    print("OK: no regression beyond tolerance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
