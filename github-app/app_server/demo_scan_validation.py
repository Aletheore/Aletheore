"""Shared repo-URL validation for the public, unauthenticated "paste a
repo" website demo. Used both by the API route (fast 400 before anything
is queued) and again by the worker right before it shells out to Docker -
never trust a single choke point for something that ends up as an argv
element passed to `git clone` inside a container we spun up ourselves.
"""

import re

# GitHub username/org rules: alphanumeric or single hyphens, 1-39 chars,
# can't start/end with a hyphen. Repo name: alphanumeric, ., -, _, 1-100
# chars. Anchored, https-only, github.com only - this is the only thing
# standing between a pasted string and a `git clone` argument.
_REPO_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)"
    r"(?:\.git)?/?$"
)

MAX_REPO_SIZE_KB = 400 * 1024


def parse_github_repo_url(url: str) -> tuple[str, str] | None:
    match = _REPO_URL_RE.match(url.strip())
    if match is None:
        return None
    return match.group("owner"), match.group("repo")


def normalized_clone_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"
