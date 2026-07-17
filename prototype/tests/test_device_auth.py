import subprocess

import httpx
import pytest

from aletheore.device_auth import (
    DeviceCode,
    DeviceFlowError,
    fetch_my_installations,
    infer_org_from_cwd_git_remote,
    mint_cli_token,
    poll_for_access_token,
    request_device_code,
    resolve_installation,
)


def test_request_device_code_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "device_code": "dc123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = request_device_code(http_client=client)
    assert code.user_code == "ABCD-1234"
    assert code.interval == 5


def test_poll_for_access_token_succeeds_on_first_try():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "gho_real"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    token = poll_for_access_token(code, http_client=client, sleep_fn=lambda _seconds: None)
    assert token == "gho_real"


def test_poll_for_access_token_keeps_polling_on_authorization_pending():
    responses = iter(
        [
            httpx.Response(200, json={"error": "authorization_pending"}),
            httpx.Response(200, json={"access_token": "gho_real"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    token = poll_for_access_token(code, http_client=client, sleep_fn=lambda _seconds: None)
    assert token == "gho_real"


def test_poll_for_access_token_raises_on_expired_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "expired_token"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    with pytest.raises(DeviceFlowError, match="expired"):
        poll_for_access_token(code, http_client=client, sleep_fn=lambda _seconds: None)


def test_poll_for_access_token_raises_on_access_denied():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "access_denied"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    with pytest.raises(DeviceFlowError, match="denied"):
        poll_for_access_token(code, http_client=client, sleep_fn=lambda _seconds: None)


def test_poll_for_access_token_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "authorization_pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=1, expires_in=1)
    clock_values = iter([0, 0, 2])
    with pytest.raises(DeviceFlowError, match="timed out"):
        poll_for_access_token(
            code,
            http_client=client,
            sleep_fn=lambda _seconds: None,
            clock=lambda: next(clock_values),
        )


def test_fetch_my_installations_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gho_real"
        return httpx.Response(
            200,
            json={"installations": [{"installation_id": 100, "account_login": "acme"}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    result = fetch_my_installations("gho_real", http_client=client)
    assert result == [{"installation_id": 100, "account_login": "acme"}]


def test_mint_cli_token_returns_raw_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "aletheore-tok", "id": 1, "label": "x"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    token = mint_cli_token("gho_real", 100, "x", http_client=client)
    assert token == "aletheore-tok"


def test_infer_org_from_cwd_git_remote_ssh_style():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="git@github.com:acme/widgets.git\n")

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) == "acme"


def test_infer_org_from_cwd_git_remote_https_style():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/acme/widgets\n")

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) == "acme"


def test_infer_org_from_cwd_git_remote_non_github_remote():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://gitlab.com/acme/widgets\n")

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) is None


def test_infer_org_from_cwd_git_remote_no_git_repo():
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args)

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) is None


def test_resolve_installation_auto_selects_single_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "installations": [
                    {"installation_id": 100, "account_login": "acme"},
                    {"installation_id": 200, "account_login": "other"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="git@github.com:acme/widgets.git\n")

    result = resolve_installation("gho_real", http_client=client, run_fn=fake_run)
    assert result == {"installation_id": 100, "account_login": "acme"}


def test_resolve_installation_returns_full_list_when_ambiguous():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "installations": [
                    {"installation_id": 100, "account_login": "acme"},
                    {"installation_id": 200, "account_login": "other"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args)

    result = resolve_installation("gho_real", http_client=client, run_fn=fake_run)
    assert result == [
        {"installation_id": 100, "account_login": "acme"},
        {"installation_id": 200, "account_login": "other"},
    ]


def test_resolve_installation_raises_when_no_installations():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"installations": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1)

    with pytest.raises(DeviceFlowError, match="no paid Aletheore installations"):
        resolve_installation("gho_real", http_client=client, run_fn=fake_run)
