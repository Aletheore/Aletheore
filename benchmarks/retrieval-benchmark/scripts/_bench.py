"""Shared paths and question loading.

Every path is an environment variable with a default, so the harness runs on a
machine that is not the one it was written on. It previously hard-coded
/private/tmp/... and an absolute /Users/... path, which made third-party
reproduction impossible.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The corpus under test, and the copy RepoWise indexed (it writes .repowise/
# into the repo, so the two tools get separate checkouts of the same commit).
FLASK = os.environ.get("BENCH_FLASK", "/tmp/bench-flask")
FLASK_RW = os.environ.get("BENCH_FLASK_RW", "/tmp/bench-flask-rw/flask")
OUT = os.environ.get("BENCH_OUT", os.path.join(ROOT, "results"))
ENV_FILE = os.environ.get("BENCH_ENV_FILE", "/tmp/bench/.env")

# Checkout the ground truth was verified against. A different commit invalidates
# the answer key, so runners fail loudly rather than scoring against drift.
CORPUS_COMMIT = "2a8a38b051fc248865730bf3511bf2e2ea325e81"


def load_questions(name="location"):
    """Returns [{id, q, gt, anchor}] from the self-describing on-disk schema."""
    path = os.path.join(ROOT, "questions", f"{name}.json")
    raw = json.load(open(path))
    out = []
    for entry in raw:
        out.append(
            {
                "id": entry["id"],
                "q": entry.get("question") or entry.get("q"),
                "gt": entry.get("ground_truth_files") or entry.get("gt") or [],
                "anchor": entry.get("verification_anchor") or entry.get("anchor"),
            }
        )
    return out


def load_env():
    """API keys, from a file outside the repo so a key is never committed."""
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
                os.environ.setdefault(k, v)
    return env


def check_corpus_commit(repo=None):
    """Loud failure beats a silently wrong number scored against a moved corpus."""
    import subprocess

    repo = repo or FLASK
    try:
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception as exc:
        raise SystemExit(f"cannot read corpus at {repo}: {exc}")
    if head != CORPUS_COMMIT:
        raise SystemExit(
            f"corpus is at {head[:12]}, ground truth was verified against "
            f"{CORPUS_COMMIT[:12]}.\n"
            f"  git -C {repo} checkout {CORPUS_COMMIT}"
        )
