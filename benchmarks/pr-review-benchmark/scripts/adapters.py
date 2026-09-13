"""Real per-tool invocation. Each adapter takes (checkout_dir, case,
...) and returns raw output ready for scripts/normalize.py. Adapters
that shell out or call an API accept an injectable runner/fetcher so
command construction is unit-testable without actually invoking
external tools or the network.

Aletheore's real comparable feature is its hosted GitHub App's Flash
Review (deepseek-v4-flash), not the CLI's whole-repo `audit` -- it posts
findings as a PR comment from aletheore[bot], fetched and bot-filtered
the same way as DeepSource's GitHub App comments. CodeRabbit, Snyk Code,
and Semgrep are excluded from this benchmark entirely -- their published
ToS bar benchmarking and/or publishing comparison results without prior
written consent (confirmed by reading the actual ToS documents, not a
search summary, on 2026-09-13). Bito, Korbit, Sourcery, and Greptile were
checked the same way and carry no such restriction.

Bot logins below were each confirmed against a real, finding-bearing
public PR (not guessed from marketing copy or a GitHub App's own display
name, which frequently differs from its actual commenting bot account) --
see the comment above each adapter for the specific PR checked.
"""
import subprocess
import sys

ALETHEORE_BOT_LOGIN = "aletheore[bot]"
DEEPSOURCE_BOT_LOGIN = "deepsource-io[bot]"
BITO_BOT_LOGIN = "bito-code-review[bot]"
KORBIT_BOT_LOGIN = "korbit-ai[bot]"
SOURCERY_BOT_LOGIN = "sourcery-ai[bot]"
GREPTILE_BOT_LOGIN = "greptile-apps[bot]"


def aletheore_adapter(checkout_dir, case, fetch_pr_comments):
    comments = fetch_pr_comments(case["repo"]["pr_url"])
    return [c for c in comments if c.get("user", {}).get("login") == ALETHEORE_BOT_LOGIN]


def deepsource_adapter(checkout_dir, case, fetch_pr_comments):
    comments = fetch_pr_comments(case["repo"]["pr_url"])
    return [c for c in comments if c.get("user", {}).get("login") == DEEPSOURCE_BOT_LOGIN]


def pr_agent_adapter(checkout_dir, case, fetch_review, runner=subprocess.run):
    pr_url = case["repo"]["pr_url"]
    runner(
        [
            sys.executable, "-m", "pr_agent.cli",
            "--pr_url", pr_url,
            "review",
            "--config.model=deepseek/deepseek-v4-flash",
        ],
        capture_output=True, text=True, check=True,
    )
    return fetch_review(pr_url)


def bito_adapter(checkout_dir, case, fetch_pr_review_comments):
    """Bito posts a run-summary as a plain issue comment (file/skip counts,
    a usage guide) and its actual findings as PR *review* comments (path/
    line/body) - confirmed against apache/superset#43729, a real,
    unrelated public PR with actual findings (most PRs sampled while
    verifying this had zero findings, which would have looked identical to
    an adapter bug if that had been the only PR checked). fetch_pr_review_
    comments must hit the review-comments endpoint (`pulls/{n}/comments}`),
    not issue comments - see normalize_bito for what's extracted from it."""
    comments = fetch_pr_review_comments(case["repo"]["pr_url"])
    return [c for c in comments if c.get("user", {}).get("login") == BITO_BOT_LOGIN]


def korbit_adapter(checkout_dir, case, fetch_pr_review_comments):
    """Korbit posts findings as PR review comments (path present, line
    often null even on a real finding - confirmed on apache/superset#35832,
    a real, unrelated public PR). A plain issue comment from this same bot
    login is possible too (e.g. "I don't review PRs from bots, comment
    /korbit-review to override") but carries no findings - not fetched by
    this adapter since only the review-comments endpoint carries real
    content."""
    comments = fetch_pr_review_comments(case["repo"]["pr_url"])
    return [c for c in comments if c.get("user", {}).get("login") == KORBIT_BOT_LOGIN]


def sourcery_adapter(checkout_dir, case, fetch_pr_review_comments):
    """Sourcery's real per-line findings are PR review comments (path
    present, line often null) - confirmed on genomehubs/kinfin#116, a
    real, unrelated public PR ("**nitpick:** ..." finding). Sourcery also
    posts a review-level summary (via the reviews endpoint, not fetched
    here) whose body is almost always just "Approved"/rate-limit
    boilerplate in the real PRs sampled while verifying this - the actual
    substance lives in the review comments this adapter fetches."""
    comments = fetch_pr_review_comments(case["repo"]["pr_url"])
    return [c for c in comments if c.get("user", {}).get("login") == SOURCERY_BOT_LOGIN]


def greptile_adapter(checkout_dir, case, fetch_pr_comments, fetch_pr_review_comments):
    """Greptile posts to both surfaces on the same PR: a prose summary as
    a plain issue comment ("Greptile Summary", a confidence score) and
    per-line findings as PR review comments (path/line, a priority badge
    P1-P4 embedded in the body) - confirmed on a real, unrelated public PR
    (moltis-org/moltis#1266) carrying both at once. Both are returned,
    tagged by kind, so normalize_greptile can treat the review comments as
    the grounding-relevant findings and the issue comment as supplementary
    context rather than silently dropping one surface."""
    pr_url = case["repo"]["pr_url"]
    issue_comments = [
        c for c in fetch_pr_comments(pr_url) if c.get("user", {}).get("login") == GREPTILE_BOT_LOGIN
    ]
    review_comments = [
        c for c in fetch_pr_review_comments(pr_url)
        if c.get("user", {}).get("login") == GREPTILE_BOT_LOGIN
    ]
    return {"issue_comments": issue_comments, "review_comments": review_comments}
