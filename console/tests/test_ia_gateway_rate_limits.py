from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import request_rate_limits
from ia_gateway_fixtures import GatewayHarness, gateway  # noqa: F401


class RecordingLimiter:
    def __init__(self, deny: set[str] | None = None) -> None:
        self.keys: list[tuple[str, int, int]] = []
        self.deny = deny or set()

    async def check(self, key, limit, window, sensitive=False):
        assert sensitive is True
        self.keys.append((key, limit, window))
        return not any(key.startswith(prefix) for prefix in self.deny)


USER = {"id": 42, "active_workspace_id": "w-1", "access_token_id": "t-1"}


@pytest.mark.asyncio
async def test_gateway_limits_never_key_on_client_ip(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    limiter = RecordingLimiter()
    await request_rate_limits.rate_limit_gateway(USER, limiter_factory=lambda: limiter)
    await request_rate_limits.rate_limit_gateway(USER, "ejecutar_extraccion", limiter_factory=lambda: limiter)
    await request_rate_limits.rate_limit_gateway(USER, "crear_app_analitica", limiter_factory=lambda: limiter)
    await request_rate_limits.rate_limit_gateway(USER, "consultar_contexto", limiter_factory=lambda: limiter)
    assert limiter.keys == [
        ("ia_gateway:token:t-1", 60, 60),
        ("ia_gateway:user:42", 120, 60),
        ("ia_gateway:workspace:w-1", 240, 60),
        ("ia_gateway:ejecutar_extraccion:user:42", 6, 600),
        ("ia_gateway:crear_app_analitica:user:42", 3, 600),
    ]


@pytest.mark.asyncio
async def test_gateway_limits_deny_and_require_identity(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc:
        await request_rate_limits.rate_limit_gateway(
            USER, limiter_factory=lambda: RecordingLimiter(deny={"ia_gateway:workspace"})
        )
    assert exc.value.status_code == 429
    with pytest.raises(HTTPException) as exc:
        await request_rate_limits.rate_limit_gateway(
            {"id": 42, "active_workspace_id": "w-1"}, limiter_factory=RecordingLimiter
        )
    assert exc.value.status_code == 401


def test_auth_ip_limit_runs_before_token_resolution(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    limiter = RecordingLimiter(deny={"ia_gateway:auth_ip"})
    monkeypatch.setattr(gateway.main, "get_rate_limiter", lambda: limiter)
    token = gateway.issue()
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    assert response.status_code == 429
    assert response.json()["error"]["codigo"] == "limite_de_uso"
    assert gateway.resolve_calls == []


def test_token_and_workspace_limits_apply_after_authentication(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    limiter = RecordingLimiter(deny={"ia_gateway:token"})
    monkeypatch.setattr(gateway.main, "get_rate_limiter", lambda: limiter)
    token = gateway.issue()
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    assert response.status_code == 429
    assert len(gateway.resolve_calls) == 1
    assert [key for key, _limit, _window in limiter.keys][0].startswith("ia_gateway:auth_ip:")
    assert any(key.startswith("ia_gateway:token:") for key, _limit, _window in limiter.keys)


def test_public_schema_is_limited_per_ip(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://consola.example.test")
    limiter = RecordingLimiter(deny={"/api/ia/v1/openapi.json"})
    monkeypatch.setattr(gateway.main, "get_rate_limiter", lambda: limiter)
    response = gateway.client.get("/api/ia/v1/openapi.json")
    assert response.status_code == 429
    assert limiter.keys[0][1:] == (30, 60)


@pytest.fixture()
def live_limits(gateway: GatewayHarness, monkeypatch):
    from app.services.rate_limiter import InMemoryRateLimiter

    limiter = InMemoryRateLimiter()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.setattr(gateway.main, "get_rate_limiter", lambda: limiter)
    return limiter


def test_auth_limits_protect_the_database_without_starving_shared_ips():
    limit, window = request_rate_limits.RATE_LIMITS["ia_gateway:auth_ip"]
    assert limit >= 1000 and window == 60
    assert request_rate_limits.RATE_LIMITS["ia_gateway:auth_failures"] == (60, 60)


def test_failed_tokens_from_a_shared_ip_never_block_a_valid_token(gateway: GatewayHarness, live_limits):
    from app.services import access_tokens

    statuses = []
    for _ in range(130):
        response = gateway.client.get(
            "/api/ia/v1/whoami", headers=gateway.bearer(access_tokens.generate_token())
        )
        statuses.append(response.status_code)
    assert statuses[:60] == [401] * 60
    assert set(statuses[60:]) == {429}
    blocked = gateway.client.get(
        "/api/ia/v1/whoami", headers=gateway.bearer(access_tokens.generate_token())
    )
    assert blocked.json()["error"]["codigo"] == "limite_de_uso"
    assert "tokens no válidos" in blocked.json()["error"]["mensaje"]
    victim = gateway.issue()
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(victim))
    assert response.status_code == 200, response.text
    assert response.json()["datos"]["usuario"] == "Ana Analista"


@pytest.mark.parametrize("kind", ["malformed", "revocado", "vencido", "usuario_inactivo", "sin_espacio"])
def test_every_failed_resolution_counts_toward_the_failure_bucket(gateway: GatewayHarness, monkeypatch, kind):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    limiter = RecordingLimiter()
    monkeypatch.setattr(gateway.main, "get_rate_limiter", lambda: limiter)
    if kind == "malformed":
        token = "omega_pat_" + "a" * 42
    elif kind == "sin_espacio":
        token = gateway.issue()
        gateway.workspaces = []
    else:
        token = gateway.issue(status=kind)
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    assert response.status_code == 401
    keys = [key for key, _limit, _window in limiter.keys]
    assert any(key.startswith("ia_gateway:auth_failures:") for key in keys)
    assert not any(key.startswith("ia_gateway:token:") for key in keys)


def test_valid_requests_never_touch_the_failure_bucket(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    limiter = RecordingLimiter(deny={"ia_gateway:auth_failures"})
    monkeypatch.setattr(gateway.main, "get_rate_limiter", lambda: limiter)
    token = gateway.issue()
    for _ in range(5):
        assert gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token)).status_code == 200
    assert not any(key.startswith("ia_gateway:auth_failures") for key, _limit, _window in limiter.keys)
    unknown = gateway.client.get(
        "/api/ia/v1/whoami", headers=gateway.bearer("omega_pat_" + "b" * 43)
    )
    assert unknown.status_code == 429


def test_pre_resolution_ceiling_still_guards_the_database(gateway: GatewayHarness, live_limits, monkeypatch):
    monkeypatch.setitem(request_rate_limits.RATE_LIMITS, "ia_gateway:auth_ip", (3, 60))
    token = gateway.issue()
    codes = [
        gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token)).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    assert len(gateway.resolve_calls) == 3


def test_one_user_cannot_exhaust_the_workspace_across_tokens(gateway: GatewayHarness, live_limits):
    tokens = [gateway.issue() for _ in range(4)]
    codes = []
    for index in range(121):
        token = tokens[index % 3]
        codes.append(
            gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token)).status_code
        )
    assert codes[:120] == [200] * 120
    assert codes[120] == 429
    fresh = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(tokens[3]))
    assert fresh.status_code == 429
    colleague = gateway.issue()
    gateway.tokens[colleague]["user_id"] = 43
    assert gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(colleague)).status_code == 200
