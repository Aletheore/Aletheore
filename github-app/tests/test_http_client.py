from app_server.http_client import (
    get_generic_http_client,
    get_github_api_client,
    get_github_oauth_client,
)


def test_get_github_api_client_returns_the_same_instance_across_calls():
    assert get_github_api_client() is get_github_api_client()


def test_get_github_api_client_has_the_right_base_url():
    assert str(get_github_api_client().base_url) == "https://api.github.com"


def test_get_github_oauth_client_returns_the_same_instance_across_calls():
    assert get_github_oauth_client() is get_github_oauth_client()


def test_get_github_oauth_client_has_the_right_base_url():
    assert str(get_github_oauth_client().base_url) == "https://github.com"


def test_get_github_api_client_and_oauth_client_are_distinct():
    # Different hosts need different pools - conflating them would mean a
    # relative-path request meant for one host silently resolving against
    # the other's base_url.
    assert get_github_api_client() is not get_github_oauth_client()


def test_get_generic_http_client_returns_the_same_instance_across_calls():
    assert get_generic_http_client() is get_generic_http_client()


def test_get_generic_http_client_has_no_base_url():
    assert str(get_generic_http_client().base_url) == ""
