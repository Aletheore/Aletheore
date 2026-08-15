"""Computes exact ground truth for hotspots and ownership from the same
input files given to the bare LLM - i.e. NOT from Aletheore's full-history
scan output, but from the identical truncated slice the model saw. This is
what makes the comparison fair: both sides work from the same data, one
via deterministic counting, one via an LLM asked to do the same counting.

Usage: python exact_ground_truth.py <input_dir>
"""
import sys
from collections import Counter
from pathlib import Path


def hotspots_ground_truth(path: Path, top_n: int = 10) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for line in path.read_text().splitlines():
        if line.startswith("COMMIT ") or not line.strip():
            continue
        counts[line.strip()] += 1
    return counts.most_common(top_n)


def ownership_ground_truth(path: Path, top_n: int = 8) -> list[tuple[str, int, float]]:
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    counts = Counter(lines)
    total = len(lines)
    return [(who, n, round(n / total * 100, 2)) for who, n in counts.most_common(top_n)]


def main():
    in_dir = Path(sys.argv[1])

    print(f"=== Hotspots ground truth (top 10, {in_dir / 'hotspots_input.txt'}) ===")
    for path, n in hotspots_ground_truth(in_dir / "hotspots_input.txt"):
        print(f"  {n:5d}  {path}")

    print(f"\n=== Ownership ground truth (top 8, {in_dir / 'ownership_input.txt'}) ===")
    for who, n, pct in ownership_ground_truth(in_dir / "ownership_input.txt"):
        print(f"  {n:5d} ({pct:5.2f}%)  {who}")


if __name__ == "__main__":
    main()
