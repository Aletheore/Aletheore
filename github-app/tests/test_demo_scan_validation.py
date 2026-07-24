import pytest

from app_server.demo_scan_validation import normalized_clone_url, parse_github_repo_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/octocat/Hello-World", ("octocat", "Hello-World")),
        ("https://github.com/octocat/Hello-World.git", ("octocat", "Hello-World")),
        ("https://github.com/octocat/Hello-World/", ("octocat", "Hello-World")),
        ("https://github.com/some-org/some_repo.name", ("some-org", "some_repo.name")),
    ],
)
def test_valid_urls_parse(url, expected):
    assert parse_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octocat/Hello-World",  # not https
        "https://gitlab.com/octocat/Hello-World",  # not github.com
        "https://github.com/octocat/Hello-World/extra",  # extra path segment
        "https://github.com/octocat/Hello-World?x=1",  # query string
        "https://github.com@evil.com/octocat/Hello-World",  # host confusion
        "https://github.com/-octocat/Hello-World",  # owner starts with hyphen
        "https://github.com/octocat",  # missing repo
        "git@github.com:octocat/Hello-World.git",  # ssh form
        "file:///etc/passwd",
        "https://169.254.169.254/latest/meta-data/",  # SSRF attempt
        "",
    ],
)
def test_invalid_urls_rejected(url):
    assert parse_github_repo_url(url) is None


def test_normalized_clone_url_always_has_git_suffix():
    assert normalized_clone_url("octocat", "Hello-World") == "https://github.com/octocat/Hello-World.git"
