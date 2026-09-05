"""Placeholders for secret-corpus fixtures that must look like real
credentials to the scanner under test, but must not be real-looking *in
this repository* - the exact same problem and fix as
benchmarks/pr-review-benchmark/scripts/fixtures.py's docstring describes
for its Stripe-key case, generalized to every secret format this corpus
covers.

Each value is assembled from fragments rather than written as one
contiguous literal, so this file itself doesn't reproduce the pattern
GitHub's push protection (and aletheore's own scanner) matches on.
"""
import shutil
from pathlib import Path

_FAKE_AWS_ACCESS_KEY = "AKIA" + "QZXNRTVYWMPKLBHG"
_FAKE_GITHUB_TOKEN = "ghp_" + "9K2vQ7xR4mZ1pL8tY6wU3nB0cF5hD9aE2gJ4sK7i"
_FAKE_STRIPE_KEY = "sk_" + "live_" + "7hT2kM9pL4xQ8wR1vN6cB3d"
_FAKE_SLACK_TOKEN = "xoxb-" + "4721609583-T5Rk9mPz2Qa"
_FAKE_GOOGLE_API_KEY = "AIza" + "Sy8kR2mN9pQ4xT6wU1vB3cF5hD7aE0gJ8sZ"

# "PLACEHOLDER" is deliberately part of every token, not just "BENCHMARK":
# secrets.py's own _value_names_itself_a_placeholder check (PLACEHOLDER_VALUE_MARKERS)
# only suppresses a finding whose *value* names itself a placeholder - a
# committed token missing that word can itself trip the generic
# PASSWORD/SECRET/API_KEY= pattern (confirmed directly: "stripe.api_key ="
# and "GOOGLE_MAPS_API_KEY =" both matched against the placeholder text
# before this rename). This only affects the committed corpus text, never
# the benchmark's actual measurement - materialize_case_repo() replaces
# these tokens with the real fake values before any scan runs.
PLACEHOLDER_VALUES = {
    "__BENCHMARK_PLACEHOLDER_AWS_ACCESS_KEY__": _FAKE_AWS_ACCESS_KEY,
    "__BENCHMARK_PLACEHOLDER_GITHUB_TOKEN__": _FAKE_GITHUB_TOKEN,
    "__BENCHMARK_PLACEHOLDER_STRIPE_KEY__": _FAKE_STRIPE_KEY,
    "__BENCHMARK_PLACEHOLDER_SLACK_TOKEN__": _FAKE_SLACK_TOKEN,
    "__BENCHMARK_PLACEHOLDER_GOOGLE_API_KEY__": _FAKE_GOOGLE_API_KEY,
}


def expand_placeholders(text: str) -> str:
    for placeholder, value in PLACEHOLDER_VALUES.items():
        text = text.replace(placeholder, value)
    return text


def materialize_case_repo(case_dir: Path, dest_dir: Path) -> Path:
    """Copies a case's repo/ tree to dest_dir and expands placeholders in
    place there. The committed corpus never contains a scannable secret;
    only this ephemeral, gitignored copy does - the same point in the
    pipeline pr-review-benchmark's expand_placeholders_in_tree expands at.
    """
    src = case_dir / "repo"
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(src, dest_dir)
    for path in dest_dir.rglob("*"):
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        expanded = expand_placeholders(original)
        if expanded != original:
            path.write_text(expanded, encoding="utf-8")
    return dest_dir
