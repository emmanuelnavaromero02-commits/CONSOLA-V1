from __future__ import annotations

import contextlib
import importlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient


WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"


@dataclass
class GatewayHarness:
    client: TestClient
    main: Any
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    workspaces: list[dict[str, Any]] = field(default_factory=list)
    cartridges: list[str] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    resolve_calls: list[tuple[str, str | None]] = field(default_factory=list)
    resolve_error: Exception | None = None
    name_locks: set[str] = field(default_factory=set)

    def issue(
        self,
        *,
        scopes: tuple[str, ...] = ("lectura",),
        role: str = "user",
        status: str = "activo",
        workspace_id: str = WORKSPACE_ID,
    ) -> str:
        from app.services import access_tokens

        token = access_tokens.generate_token()
        self.tokens[token] = {
            "token_id": str(uuid.uuid4()),
            "status": status,
            "user_id": 42,
            "email": "ana@example.invalid",
            "name": "Ana Analista",
            "role": role,
            "user_tenant_id": TENANT_ID,
            "tenant_id": TENANT_ID,
            "workspace_id": workspace_id,
            "workspace_name": "Operaciones",
            "token_name": "Asistente",
            "token_prefix": token[:14],
            "scopes": list(scopes),
            "expires_at": "2026-12-31T00:00:00+00:00",
        }
        return token

    @staticmethod
    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


def in_memory_name_lock(held: set[str]):
    @contextlib.asynccontextmanager
    async def app_name_lock(user, *, name):
        key = f"{user['active_workspace_id']}:{name.strip().casefold()}"
        if key in held:
            yield False
            return
        held.add(key)
        try:
            yield True
        finally:
            held.discard(key)

    return app_name_lock


def workspace_row(role: str = "workspace_admin", workspace_id: str = WORKSPACE_ID) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "workspace_name": "Operaciones",
        "tenant_id": TENANT_ID,
        "tenant_name": "Empresa",
        "workspace_role": role,
    }


@pytest.fixture()
def gateway(monkeypatch) -> GatewayHarness:
    main = importlib.import_module("app.main")
    deps = importlib.import_module("app.dependencies")
    access_tokens = importlib.import_module("app.services.access_tokens")
    audit_service = importlib.import_module("app.services.audit_service")
    adapters = importlib.import_module("app.services.mcp_gateway.adapters")
    harness = GatewayHarness(client=TestClient(main.app, raise_server_exceptions=False), main=main)
    harness.workspaces = [workspace_row()]
    harness.cartridges = ["sap_b1", "sap_successfactors"]

    async def resolve(token, ip):
        harness.resolve_calls.append((token, ip))
        if harness.resolve_error is not None:
            raise harness.resolve_error
        return dict(harness.tokens[token]) if token in harness.tokens else None

    async def options(user):
        return [dict(row) for row in harness.workspaces]

    async def cartridges(workspace_id, user_id=None):
        return list(harness.cartridges)

    async def record_event(**kwargs):
        harness.audit.append(kwargs)

    monkeypatch.setattr(access_tokens, "resolve_token", resolve)
    monkeypatch.setattr(deps, "_workspace_access_options", options)
    monkeypatch.setattr(deps, "_workspace_cartridges", cartridges)
    monkeypatch.setattr(audit_service, "record_event", record_event)
    monkeypatch.setattr(adapters, "app_name_lock", in_memory_name_lock(harness.name_locks))
    monkeypatch.delenv("IA_GATEWAY_ENABLED", raising=False)
    return harness
