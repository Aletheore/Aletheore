"""Real per-tool invocation. Each adapter takes (checkout_dir, case,
...) and returns raw output ready for scripts/normalize.py. Adapters
that shell out or call an API accept an injectable runner/fetcher so
command construction is unit-testable without actually invoking
external tools or the network.

Aletheore's real comparable feature is its hosted GitHub App's Flash
Review (deepseek-v4-flash), not the CLI's whole-repo `audit` -- it posts
findings as a PR comment from aletheore[bot], fetched and bot-filtered
the same way as DeepSource's GitHub App comments. CodeRabbit has been
dropped from this benchmark entirely (its Free-plan/rate-limit reality
made it unusable for a fair comparison); the remaining lineup is a named
3-way comparison: Aletheore vs. Qodo/PR-Agent vs. DeepSource.
"""
import subprocess

ALETHEORE_BOT_LOGIN = "aletheore[bot]"
DEEPSOURCE_BOT_LOGIN = "deepsource-io[bot]"


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
            "python", "-m", "pr_agent.cli",
            "--pr_url", pr_url,
            "--config.model=deepseek/deepseek-v4-flash",
            "review",
        ],
        capture_output=True, text=True, check=True,
    )
    return fetch_review(pr_url)
