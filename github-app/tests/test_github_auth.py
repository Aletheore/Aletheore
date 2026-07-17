import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app_server.github_auth import generate_app_jwt, get_installation_token

TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def test_generated_jwt_has_correct_claims():
    token = generate_app_jwt("12345", TEST_PRIVATE_KEY)
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "12345"
    assert decoded["exp"] - decoded["iat"] <= 660


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


@pytest.mark.asyncio
async def test_get_installation_token_returns_token_from_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/app/installations/999/access_tokens"
        assert request.headers["Authorization"] == "Bearer fake-jwt"
        return httpx.Response(
            201,
            json={"token": "ghs_faketoken123", "expires_at": "2026-01-01T00:00:00Z"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    token = await get_installation_token(999, "fake-jwt", http_client=client)
    assert token == "ghs_faketoken123"
