import httpx
import jwt
import pytest
import time
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app_server.github_auth import generate_app_jwt, get_installation_token

TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def test_generated_jwt_has_correct_claims(monkeypatch):
    monkeypatch.setattr("app_server.github_auth.time.time", lambda: 1_700_000_000)
    token = generate_app_jwt("12345", TEST_PRIVATE_KEY)
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "12345"
    assert decoded["iat"] == 1_699_999_940
    assert decoded["exp"] == 1_700_000_540


def test_generated_jwt_is_verifiable_with_public_key():
    private_key = serialization.load_pem_private_key(TEST_PRIVATE_KEY.encode(), password=None)
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    token = generate_app_jwt("12345", TEST_PRIVATE_KEY)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "12345"


def test_get_installation_token_returns_token_from_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/app/installations/999/access_tokens"
        assert request.headers["Authorization"] == "Bearer fake-jwt"
        return httpx.Response(
            201,
            json={"token": "ghs_faketoken123", "expires_at": "2026-01-01T00:00:00Z"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    token = get_installation_token(999, "fake-jwt", http_client=client)
    assert token == "ghs_faketoken123"


def test_get_installation_token_retries_once_after_a_transport_error(monkeypatch):
    # Regression test for a real production failure (2026-09-07,
    # run_push_scan_job, job_id 5931fc3d): the first attempt hit
    # RemoteProtocolError, the identical call succeeded moments later.
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(201, json={"token": "ghs_recovered", "expires_at": "2026-01-01T00:00:00Z"})

    slept = []
    monkeypatch.setattr("app_server.github_auth.time.sleep", lambda seconds: slept.append(seconds))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    token = get_installation_token(999, "fake-jwt", http_client=client)

    assert token == "ghs_recovered"
    assert len(calls) == 2
    assert slept == [1.0]


def test_get_installation_token_raises_when_the_retry_also_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    with pytest.raises(httpx.RemoteProtocolError):
        get_installation_token(999, "fake-jwt", http_client=client)


def test_get_installation_token_does_not_retry_a_real_http_error():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"message": "Bad credentials"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    with pytest.raises(httpx.HTTPStatusError):
        get_installation_token(999, "fake-jwt", http_client=client)
    assert len(calls) == 1
