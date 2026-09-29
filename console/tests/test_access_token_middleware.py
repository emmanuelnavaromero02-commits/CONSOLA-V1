from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from ia_gateway_fixtures import (  # noqa: F401
    OTHER_WORKSPACE_ID,
    TENANT_ID,
    WORKSPACE_ID,
    GatewayHarness,
    gateway,
    workspace_row,
)


OUTSIDE = "Este token solo es válido en la pasarela de IA"
REQUIRED = "Esta ruta requiere un token personal"


def _error(response) -> dict:
    body = response.json()
    assert body["ok"] is False
    assert body["solicitud_id"]
    return body["error"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/datasets"),
        ("GET", "/api/control-room/summary"),
        ("GET", "/api/mcp/servers"),
        ("GET", "/api/me/access-tokens"),
        ("POST", "/api/me/access-tokens"),
        ("GET", "/api/me"),
        ("GET", "/datasets"),
        ("POST", "/api/cartridges/sap_b1/sync-now"),
        ("GET", "/internal/mcp/servers"),
    ],
)
def test_personal_token_is_rejected_outside_the_gateway(gateway: GatewayHarness, method, path):
    token = gateway.issue(scopes=("lectura", "acciones"), role="admin")
    response = gateway.client.request(method, path, headers=gateway.bearer(token))
    assert response.status_code == 401
    assert response.json() == {"detail": OUTSIDE}
    assert response.headers["www-authenticate"] == "Bearer"
    assert gateway.resolve_calls == []


def test_personal_token_is_ignored_on_public_paths(gateway: GatewayHarness):
    token = gateway.issue()
    response = gateway.client.get("/healthz", headers=gateway.bearer(token))
    assert response.status_code == 200
    assert gateway.resolve_calls == []


def test_bearer_dependency_rejects_personal_tokens_before_jwt_decode():
    from app import dependencies
    from app.services import access_tokens

    token = access_tokens.generate_token()
    request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())], "method": "GET", "path": "/"}
    )
    with pytest.raises(HTTPException) as exc:
        dependencies._bearer_token(request)
    assert exc.value.status_code == 401
    assert exc.value.detail == OUTSIDE


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.firma"},
        {"Authorization": "Basic YTpi"},
        {"Cookie": "mod_session=sesion-valida"},
    ],
)
def test_gateway_requires_a_personal_token(gateway: GatewayHarness, headers):
    response = gateway.client.get("/api/ia/v1/whoami", headers=headers)
    assert response.status_code == 401
    assert _error(response) == {"codigo": "token_invalido", "mensaje": REQUIRED, "reintentable": False}
    assert response.headers["www-authenticate"] == "Bearer"
    assert gateway.resolve_calls == []


def test_token_in_query_header_or_cookie_is_not_accepted(gateway: GatewayHarness):
    token = gateway.issue()
    for kwargs in (
        {"params": {"token": token}},
        {"headers": {"X-Api-Key": token}},
        {"headers": {"Cookie": f"mod_session={token}"}},
        {"headers": {"Authorization": token}},
    ):
        response = gateway.client.get("/api/ia/v1/whoami", **kwargs)
        assert response.status_code == 401, kwargs
    assert gateway.resolve_calls == []


def test_malformed_personal_token_never_reaches_the_database(gateway: GatewayHarness):
    token = gateway.issue()
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token[:-1]))
    assert response.status_code == 401
    assert _error(response)["mensaje"] == "El token no es válido."
    assert gateway.resolve_calls == []


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("vencido", "El token venció; genera uno nuevo en Mi acceso."),
        ("revocado", "El token fue revocado; genera uno nuevo en Mi acceso."),
        ("usuario_inactivo", "La cuenta asociada a este token está desactivada."),
        (
            "cambio_de_contrasena",
            "La cuenta asociada a este token debe cambiar su contraseña antes de usarlo.",
        ),
        ("espacio_inexistente", "El espacio de trabajo de este token ya no existe."),
    ],
)
def test_inactive_token_statuses_have_specific_spanish_messages(gateway: GatewayHarness, status, message):
    token = gateway.issue(status=status)
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    assert response.status_code == 401
    assert _error(response)["mensaje"] == message


def test_unknown_token_is_invalid(gateway: GatewayHarness):
    from app.services import access_tokens

    response = gateway.client.get(
        "/api/ia/v1/whoami", headers=gateway.bearer(access_tokens.generate_token())
    )
    assert response.status_code == 401
    assert _error(response)["codigo"] == "token_invalido"


def test_workspace_comes_from_the_token_not_from_headers_or_cookies(gateway: GatewayHarness):
    gateway.workspaces = [workspace_row(), workspace_row(workspace_id=OTHER_WORKSPACE_ID)]
    token = gateway.issue()
    response = gateway.client.get(
        "/api/ia/v1/whoami",
        headers={
            **gateway.bearer(token),
            "X-Workspace-Id": OTHER_WORKSPACE_ID,
            "Cookie": f"omega_active_workspace_id={OTHER_WORKSPACE_ID}",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["espacio_de_trabajo"] == {"id": WORKSPACE_ID, "nombre": "Operaciones"}


def test_lost_membership_is_rejected(gateway: GatewayHarness):
    gateway.workspaces = [workspace_row(workspace_id=OTHER_WORKSPACE_ID)]
    token = gateway.issue()
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    assert response.status_code == 401
    assert _error(response)["mensaje"] == "El token ya no tiene acceso a su espacio de trabajo"


def test_kill_switch_disables_the_gateway(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("IA_GATEWAY_ENABLED", "false")
    token = gateway.issue()
    for path in ("/api/ia/v1/whoami", "/api/ia/v1/openapi.json"):
        response = gateway.client.get(path, headers=gateway.bearer(token))
        assert response.status_code == 503
        assert _error(response) == {
            "codigo": "servicio_no_disponible",
            "mensaje": "La pasarela de IA está desactivada temporalmente.",
            "reintentable": True,
        }
    assert gateway.resolve_calls == []


def test_resolution_outage_is_a_spanish_503(gateway: GatewayHarness):
    gateway.resolve_error = RuntimeError("could not connect to server: secret host")
    token = gateway.issue()
    response = gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    assert response.status_code == 503
    assert "secret" not in response.text
    assert _error(response)["codigo"] == "servicio_no_disponible"


def test_gateway_user_is_bound_to_one_workspace(gateway: GatewayHarness):
    from app.domains.security import access_token_auth

    gateway.workspaces = [workspace_row(role="viewer"), workspace_row(workspace_id=OTHER_WORKSPACE_ID)]
    token = gateway.issue(scopes=("lectura",))
    captured: dict = {}

    async def spy(resolved):
        user = await original(resolved)
        captured.update(user)
        return user

    original = access_token_auth.gateway_user
    access_token_auth.gateway_user = spy
    try:
        gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token))
    finally:
        access_token_auth.gateway_user = original
    assert captured["auth_method"] == "pat"
    assert captured["active_workspace_id"] == WORKSPACE_ID
    assert captured["active_tenant_id"] == TENANT_ID
    assert [item["workspace_id"] for item in captured["workspaces"]] == [WORKSPACE_ID]
    assert captured["workspace_role"] == "viewer"
    assert captured["access_token_scopes"] == ["lectura"]
    assert captured["allowed_cartridges"] == ["sap_b1", "sap_successfactors"]
    assert token not in str(captured)


def test_unknown_gateway_route_is_a_spanish_404(gateway: GatewayHarness):
    token = gateway.issue()
    response = gateway.client.get("/api/ia/v1/no-existe", headers=gateway.bearer(token))
    assert response.status_code == 404
    assert _error(response)["codigo"] == "no_encontrado"


def test_gateway_prefix_match_is_segment_based(gateway: GatewayHarness):
    from app.domains.security.access_token_auth import is_gateway_path

    assert is_gateway_path("/api/ia/v1")
    assert is_gateway_path("/api/ia/v1/whoami")
    assert not is_gateway_path("/api/ia/v10/whoami")
    assert not is_gateway_path("/api/ia/v1x")
    assert not is_gateway_path("/api/ia")


def _cookie_session(monkeypatch) -> str:
    from app.services import auth as _auth

    async def session_user(token):
        return {
            "id": 7,
            "email": "root@example.invalid",
            "name": "Cuenta de sesión",
            "role": "owner",
            "is_active": True,
            "must_change_password": False,
            "tenant_id": TENANT_ID,
        }

    monkeypatch.setattr(_auth, "get_session_user", session_user)
    return f"{_auth.COOKIE_NAME}=cookie-de-navegador; csrf_token=otro"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/ia/v1/actions/consultar_contexto", {}),
        ("/api/ia/v1/actions/execute", {"accion": "consultar_contexto"}),
        ("/api/ia/v1/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
    ],
)
def test_personal_token_post_ignores_a_stray_session_cookie(gateway: GatewayHarness, monkeypatch, path, body):
    cookie = _cookie_session(monkeypatch)
    token = gateway.issue()
    response = gateway.client.post(
        path, json=body, headers={**gateway.bearer(token), "Cookie": cookie}
    )
    assert response.status_code == 200, response.text
    if path.endswith("/mcp"):
        assert response.json()["result"] == {}
        return
    assert response.json()["ok"] is True
    assert response.json()["espacio_de_trabajo"]["id"] == WORKSPACE_ID
    events = [event for event in gateway.audit if event.get("action") == "ia_gateway.action"]
    assert events and all(event["user_id"] == 42 for event in events)


def test_session_cookie_alone_still_cannot_post_to_the_gateway(gateway: GatewayHarness, monkeypatch):
    cookie = _cookie_session(monkeypatch)
    response = gateway.client.post(
        "/api/ia/v1/actions/consultar_contexto", json={}, headers={"Cookie": cookie}
    )
    assert response.status_code == 401
    assert _error(response)["mensaje"] == REQUIRED


def _csrf_request(path: str, headers: dict[str, str], user: object) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "query_string": b"",
        "state": {"user": user},
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_csrf_is_only_skipped_for_personal_tokens_on_the_gateway():
    from app.services import access_tokens, csrf

    token = access_tokens.generate_token()
    pat_user = {"auth_method": "pat", "access_token_id": "t-1"}
    cookie = {"Cookie": "mod_session=abc; csrf_token=uno"}
    await csrf.require_csrf(
        _csrf_request("/api/ia/v1/actions/execute", {"Authorization": f"Bearer {token}", **cookie}, pat_user)
    )
    for path, headers, user in (
        ("/api/me/access-tokens", {"Authorization": f"Bearer {token}", **cookie}, pat_user),
        ("/api/ia/v1/actions/execute", cookie, pat_user),
        ("/api/ia/v1/actions/execute", {"Authorization": "Bearer eyJ.x.y", **cookie}, pat_user),
        ("/api/ia/v1/actions/execute", {"Authorization": f"Bearer {token}", **cookie}, {"auth_method": "session"}),
        ("/api/ia/v1/actions/execute", {"Authorization": f"Bearer {token}", **cookie}, {"auth_method": "pat"}),
        ("/api/ia/v1/actions/execute", {"Authorization": f"Bearer {token}", **cookie}, None),
    ):
        with pytest.raises(HTTPException) as exc:
            await csrf.require_csrf(_csrf_request(path, headers, user))
        assert exc.value.status_code == 403


def test_csrf_failures_on_the_gateway_have_their_own_spanish_code():
    from app.services.mcp_gateway.errors import gateway_error_from_exception

    error = gateway_error_from_exception(HTTPException(403, "csrf token invalid or missing"))
    assert error.status_code == 403
    assert error.codigo == "csrf_invalido"
    assert "CSRF" in error.mensaje and "cookies" in error.mensaje
