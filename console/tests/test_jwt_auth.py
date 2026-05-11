from datetime import timedelta

import pytest

from app.services.jwt_auth import JWTAuthError, create_access_token, decode_access_token


JWT_SECRET = "unit_signing_material_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def jwt_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")


def _claims() -> dict:
    return {
        "sub": "123",
        "email": "analyst@example.com",
        "role": "analyst",
    }


def test_valid_token_decodes_correctly():
    token = create_access_token(_claims())

    decoded = decode_access_token(token)

    assert decoded["sub"] == "123"
    assert decoded["email"] == "analyst@example.com"
    assert decoded["role"] == "analyst"


def test_expired_token_fails():
    token = create_access_token(_claims(), expires_delta=timedelta(seconds=-1))

    with pytest.raises(JWTAuthError, match="expired"):
        decode_access_token(token)


def test_invalid_signature_fails(monkeypatch):
    token = create_access_token(_claims())
    monkeypatch.setenv("JWT_SECRET_KEY", "other_unit_signing_material_bbbbbbbbbbbbbbbbbbbb")

    with pytest.raises(JWTAuthError, match="invalid"):
        decode_access_token(token)


def test_minimum_claims_exist():
    token = create_access_token(_claims())

    decoded = decode_access_token(token)

    for claim in ("sub", "email", "role", "iat", "exp", "jti"):
        assert claim in decoded
