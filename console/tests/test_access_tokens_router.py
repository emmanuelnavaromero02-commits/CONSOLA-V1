from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ia_gateway_fixtures import WORKSPACE_ID, workspace_row


CSRF = "csrf-for-access-token-tests"
SESSION = "sesion-de-prueba"
REAL_REVOKE_TOKEN = importlib.import_module("app.services.access_tokens").revoke_token


class Harness:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.user = {
            "id": 42,
            "email": "ana@example.invalid",
            "name": "Ana",
            "role": "user",
            "is_active": True,
            "must_change_password": False,
            "tenant_id": None,
        }
        self.workspaces = [workspace_row(role="viewer")]
        self.created: list[dict] = []
        self.revoked: list[tuple[int, str]] = []
        self.tokens: list[dict] = []
        self.audit: list[dict] = []
        self.secret = ""

    def session(self) -> None:
        self.client.cookies.set("mod_session", SESSION)
        self.client.cookies.set("csrf_token", CSRF)


@pytest.fixture()
def harness(monkeypatch) -> Harness:
    main = importlib.import_module("app.main")
    deps = importlib.import_module("app.dependencies")
    auth = importlib.import_module("app.services.auth")
    access_tokens = importlib.import_module("app.services.access_tokens")
    audit_service = importlib.import_module("app.services.audit_service")
    state = Harness(TestClient(main.app, raise_server_exceptions=False))

    async def get_session_user(token):
        return dict(state.user) if token == SESSION else None

    async def options(user):
        return [dict(row) for row in state.workspaces]

    async def cartridges(workspace_id, user_id=None):
        return []

    async def create_token(**kwargs):
        state.created.append(kwargs)
        state.secret = access_tokens.generate_token()
        now = datetime.now(timezone.utc).isoformat()
        token = {
            "id": str(uuid.uuid4()),
            "nombre": kwargs["name"],
            "espacio_de_trabajo": {"id": kwargs["workspace_id"], "nombre": kwargs["workspace_name"]},
            "token_prefix": state.secret[:14],
            "alcances": kwargs["scopes"],
            "creado_en": now,
            "vence_en": now,
            "ultimo_uso_en": None,
            "revocado_en": None,
            "motivo_revocacion": None,
            "estado": "activo",
        }
        state.tokens.append(token)
        return access_tokens.CreatedToken(secret=state.secret, token=token)

    async def list_tokens(user_id):
        return [dict(item) for item in state.tokens]

    async def revoke_token(user_id, token_id):
        state.revoked.append((user_id, token_id))
        token = next(
            (item for item in state.tokens if item["id"] == token_id and item["estado"] != "revocado"),
            None,
        )
        if token is None:
            return None
        token["estado"] = "revocado"
        return {
            "id": token_id,
            "token_prefix": token["token_prefix"],
            "alcances": token["alcances"],
            "espacio_de_trabajo": {"id": token["espacio_de_trabajo"]["id"]},
            "vence_en": token["vence_en"],
        }

    async def record_event(**kwargs):
        state.audit.append(kwargs)

    monkeypatch.setattr(auth, "get_session_user", get_session_user)
    monkeypatch.setattr(main, "_workspace_access_options", options)
    monkeypatch.setattr(main, "_workspace_cartridges", cartridges)
    monkeypatch.setattr(deps, "_workspace_access_options", options)
    monkeypatch.setattr(deps, "_workspace_cartridges", cartridges)
    monkeypatch.setattr(access_tokens, "create_token", create_token)
    monkeypatch.setattr(access_tokens, "list_tokens", list_tokens)
    monkeypatch.setattr(access_tokens, "revoke_token", revoke_token)
    monkeypatch.setattr(audit_service, "record_event", record_event)
    return state


def _create(harness: Harness, body: dict, **headers):
    return harness.client.post(
        "/api/me/access-tokens", json=body, headers={"X-CSRF-Token": CSRF, **headers}
    )


def test_listing_requires_an_interactive_session(harness: Harness):
    assert harness.client.get("/api/me/access-tokens").status_code == 401
    harness.session()
    response = harness.client.get("/api/me/access-tokens")
    assert response.status_code == 200
    body = response.json()
    assert body["puede_crear"] is True
    assert body["alcances_permitidos"] == ["lectura"]
    assert body["limite_activos"] == 10
    assert body["dias_permitidos"] == [7, 30, 90]
    assert response.headers["cache-control"] == "no-store"


def test_jwt_or_bearer_cannot_manage_tokens_even_with_a_session(harness: Harness):
    harness.session()
    response = harness.client.get(
        "/api/me/access-tokens", headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.x"}
    )
    assert response.status_code == 401
    response = _create(harness, {"nombre": "x"}, Authorization="Bearer eyJhbGciOiJIUzI1NiJ9.e30.x")
    assert response.status_code in {401, 403}
    assert harness.created == []


def test_create_requires_csrf(harness: Harness):
    harness.session()
    response = harness.client.post("/api/me/access-tokens", json={"nombre": "Asistente"})
    assert response.status_code == 403
    assert harness.created == []


def test_create_returns_the_secret_once_with_no_store(harness: Harness):
    harness.session()
    response = _create(harness, {"nombre": "Asistente", "alcance": "lectura", "dias": 7})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token"] == harness.secret
    assert body["token_info"]["token_prefix"] == harness.secret[:14]
    created = harness.created[0]
    assert created["user_id"] == 42
    assert created["workspace_id"] == WORKSPACE_ID
    assert created["scopes"] == ["lectura"]
    assert created["days"] == 7
    listing = harness.client.get("/api/me/access-tokens").text
    assert harness.secret not in listing
    audit = [event for event in harness.audit if event["action"] == "access_token.create"]
    assert len(audit) == 1
    assert harness.secret not in str(audit)
    assert set(audit[0]["metadata"]) == {"token_id", "token_prefix", "scopes", "workspace_id", "expires_at"}


def test_action_scope_requires_run_or_write_permission(harness: Harness):
    harness.session()
    response = _create(harness, {"nombre": "Asistente", "alcance": "acciones"})
    assert response.status_code == 403
    assert harness.created == []
    harness.workspaces = [workspace_row(role="workspace_admin")]
    response = _create(harness, {"nombre": "Asistente", "alcance": "acciones"})
    assert response.status_code == 201
    assert harness.created[0]["scopes"] == ["acciones", "lectura"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"nombre": ""},
        {"nombre": "x" * 81},
        {"nombre": "x", "dias": 45},
        {"nombre": "x", "alcance": "admin"},
        {"nombre": "x", "workspace_id": "otro"},
        {"nombre": "x", "tenant_id": "otro"},
    ],
)
def test_create_rejects_invalid_or_foreign_fields(harness: Harness, body):
    harness.session()
    response = _create(harness, body)
    assert response.status_code == 400
    assert harness.created == []


def test_revoke_only_touches_own_active_tokens(harness: Harness):
    harness.session()
    _create(harness, {"nombre": "Asistente"})
    token_id = harness.tokens[0]["id"]
    response = harness.client.delete(
        f"/api/me/access-tokens/{token_id}", headers={"X-CSRF-Token": CSRF}
    )
    assert response.status_code == 200
    assert response.json() == {"revocado": True, "id": token_id}
    assert harness.revoked == [(42, token_id)]
    missing = harness.client.delete(
        f"/api/me/access-tokens/{uuid.uuid4()}", headers={"X-CSRF-Token": CSRF}
    )
    assert missing.status_code == 404
    no_csrf = harness.client.delete(f"/api/me/access-tokens/{token_id}")
    assert no_csrf.status_code == 403
    audit = [event for event in harness.audit if event["action"] == "access_token.revoke"]
    assert len(audit) == 1
    assert audit[0]["metadata"]["token_id"] == token_id
    assert audit[0]["metadata"]["token_prefix"] == harness.tokens[0]["token_prefix"]
    assert audit[0]["metadata"]["workspace_id"] == WORKSPACE_ID
    again = harness.client.delete(
        f"/api/me/access-tokens/{token_id}", headers={"X-CSRF-Token": CSRF}
    )
    assert again.status_code == 404


def test_revoke_goes_straight_to_the_owner_scoped_function(harness: Harness, monkeypatch):
    access_tokens = importlib.import_module("app.services.access_tokens")

    async def list_tokens(user_id):
        raise AssertionError("revocation must not depend on the listed page")

    monkeypatch.setattr(access_tokens, "list_tokens", list_tokens)
    harness.session()
    beyond_listing = {
        "id": str(uuid.uuid4()),
        "token_prefix": "omega_pat_Zzzz",
        "alcances": ["lectura"],
        "espacio_de_trabajo": {"id": WORKSPACE_ID, "nombre": "Operaciones"},
        "vence_en": None,
        "estado": "vencido",
    }
    harness.tokens.append(beyond_listing)
    response = harness.client.delete(
        f"/api/me/access-tokens/{beyond_listing['id']}", headers={"X-CSRF-Token": CSRF}
    )
    assert response.status_code == 200
    assert harness.revoked == [(42, beyond_listing["id"])]
    audit = [event for event in harness.audit if event["action"] == "access_token.revoke"]
    assert audit[0]["metadata"]["token_prefix"] == "omega_pat_Zzzz"


def test_legacy_boolean_revoke_function_is_never_reported_as_revoked(harness: Harness, monkeypatch):
    access_tokens = importlib.import_module("app.services.access_tokens")

    class LegacyPool:
        async def fetchrow(self, sql, *args):
            return {"omega_auth_revoke_access_token": True}

    async def legacy_pool():
        return LegacyPool()

    monkeypatch.setattr(access_tokens, "revoke_token", REAL_REVOKE_TOKEN)
    monkeypatch.setattr(access_tokens._auth, "pool", legacy_pool)
    harness.session()
    response = harness.client.delete(
        f"/api/me/access-tokens/{uuid.uuid4()}", headers={"X-CSRF-Token": CSRF}
    )
    assert response.status_code == 404
    assert "revocado" not in response.text
    assert not [event for event in harness.audit if event["action"] == "access_token.revoke"]


def test_listing_counts_tokens_without_access_toward_the_limit(harness: Harness):
    harness.session()
    for index in range(10):
        harness.tokens.append(
            {
                "id": str(uuid.uuid4()),
                "nombre": f"t{index}",
                "espacio_de_trabajo": {"id": WORKSPACE_ID, "nombre": "Operaciones"},
                "token_prefix": "omega_pat_Abcd",
                "alcances": ["lectura"],
                "estado": "sin_acceso" if index < 3 else "activo",
            }
        )
    body = harness.client.get("/api/me/access-tokens").json()
    assert body["tokens_activos"] == 10
    assert body["puede_crear"] is False
    assert [item["estado"] for item in body["tokens"]].count("sin_acceso") == 3


def test_forced_password_change_blocks_token_creation(harness: Harness):
    harness.user["must_change_password"] = True
    harness.session()
    response = _create(harness, {"nombre": "Asistente"})
    assert response.status_code == 403
    assert harness.created == []


@pytest.mark.parametrize(
    "body",
    [b"[" * 2000 + b"]" * 2000, b'{"nombre":' + b"[" * 1900 + b"]" * 1900 + b"}"],
)
def test_deeply_nested_body_is_a_400_not_a_500(harness: Harness, body):
    harness.session()
    response = harness.client.post(
        "/api/me/access-tokens",
        content=body,
        headers={"X-CSRF-Token": CSRF, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert harness.created == []
