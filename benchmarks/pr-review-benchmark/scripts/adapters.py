"""Real per-tool invocation. Each adapter takes (checkout_dir, case,
...) and returns raw output ready for scripts/normalize.py. Adapters
that shell out or call an API accept an injectable runner/fetcher so
command construction is unit-testable without actually invoking
external tools or the network."""
import json
import subprocess


def aletheore_adapter(checkout_dir, case, runner=subprocess.run):
    result = runner(
        ["aletheore", "audit", str(checkout_dir)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def pr_agent_adapter(checkout_dir, case, runner=subprocess.run):
    result = runner(
        ["python", "-m", "pr_agent.cli", "--pr_url", case["repo"]["pr_url"], "review"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def deepsource_adapter(checkout_dir, case, fetch_issues):
    return fetch_issues(case["repo"]["deepsource_run_id"])


def coderabbit_adapter(checkout_dir, case, fetch_pr_comments):
    return fetch_pr_comments(case["repo"]["pr_url"])
