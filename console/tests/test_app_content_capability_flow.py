from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

import app.main as main
from app.domains.apps import capability


class _AsyncContext:
    def __init__(self, value, *, on_enter=None, on_exit=None):
        self.value = value
        self.on_enter = on_enter
        self.on_exit = on_exit

    async def __aenter__(self):
        if self.on_enter:
            self.on_enter()
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        if self.on_exit:
            self.on_exit()
        return False


def _request(
    path: str,
    *,
    frame: bool = False,
    ambient_auth: bool = False,
) -> Request:
    headers = []
    if frame:
        headers = [
            (b"sec-fetch-dest", b"iframe"),
            (b"sec-fetch-mode", b"navigate"),
            (b"sec-fetch-site", b"same-origin"),
        ]
    if ambient_auth:
        headers.extend(
            [
                (b"cookie", b"mod_session=untrusted-ambient-cookie"),
                (b"authorization", b"Bearer untrusted-ambient-token"),
            ]
        )
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"cap=signed.capability",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("console.test", 443),
            "app": main.app,
        }
    )


@pytest.mark.asyncio
async def test_credentialless_content_reaches_its_route_without_login_redirect(
    monkeypatch,
):
    async def no_preflight(*_args, **_kwargs):
        return None

    async def ambient_auth_must_not_run(*_args, **_kwargs):
        raise AssertionError("content capability consulted ambient authentication")

    async def middleware_rate_limit_must_not_run(*_args, **_kwargs):
        raise AssertionError("content capability used the pre-auth IP limiter")

    reached: list[str] = []

    async def content_route(request):
        reached.append(request.url.path)
        return Response("content guard ran", status_code=403)

    monkeypatch.setattr(main, "_auth_preflight_response", no_preflight)
    monkeypatch.setattr(
        main,
        "_middleware_authenticated_user",
        ambient_auth_must_not_run,
    )
    monkeypatch.setattr(
        main,
        "_rate_limit_api_surface",
        middleware_rate_limit_must_not_run,
    )

    response = await main.auth_middleware(
        _request(
            "/apps/skill_gaps_heatmap/content",
            frame=True,
            ambient_auth=True,
        ),
        content_route,
    )

    assert response.status_code == 403
    assert response.headers.get("location") is None
    assert reached == ["/apps/skill_gaps_heatmap/content"]


def test_content_capability_envelope_is_signed_short_lived_and_app_bound():
    token = capability.issue_content_capability(
        app_name="skill_gaps_heatmap",
        cartridge_id="replicon",
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="aaaaaaaa-0000-0000-0000-000000000001",
        user_id=42,
        manifest_digest="a" * 64,
        now=1_000,
    )

    claims = capability.verify_content_capability_envelope(
        token,
        app_name="skill_gaps_heatmap",
        now=1_001,
    )
    assert claims is not None
    assert claims["user"] == "42"
    assert capability.verify_content_capability(
        token,
        app_name="skill_gaps_heatmap",
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="aaaaaaaa-0000-0000-0000-000000000001",
        user_id=42,
        manifest_digest="a" * 64,
        now=1_001,
    ) is not None
    assert capability.verify_content_capability(
        token,
        app_name="skill_gaps_heatmap",
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="aaaaaaaa-0000-0000-0000-000000000002",
        user_id=42,
        manifest_digest="a" * 64,
        now=1_001,
    ) is None
    assert (
        capability.verify_content_capability_envelope(
            token,
            app_name="another_app",
            now=1_001,
        )
        is None
    )
    assert (
        capability.verify_content_capability_envelope(
            token + "tampered",
            app_name="skill_gaps_heatmap",
            now=1_001,
        )
        is None
    )
    assert (
        capability.verify_content_capability_envelope(
            token,
            app_name="skill_gaps_heatmap",
            now=1_120,
        )
        is None
    )
    assert (
        capability.verify_content_capability_envelope(
            "e30.é",
            app_name="skill_gaps_heatmap",
            now=1_001,
        )
        is None
    )


@pytest.mark.asyncio
async def test_forged_content_capability_is_rejected_before_database_work(
    monkeypatch,
):
    async def database_must_not_run(*_args, **_kwargs):
        raise AssertionError("forged capability reached a database lookup")

    monkeypatch.setattr(main, "_resolve_capability_subject", database_must_not_run)
    monkeypatch.setattr(main, "_active_manifest_digest", database_must_not_run)
    monkeypatch.setattr(
        main._request_rate_limits,
        "rate_limit_app_content_capability",
        database_must_not_run,
    )

    with pytest.raises(HTTPException) as exc_info:
        await main._require_app_content_capability(
            _request("/apps/skill_gaps_heatmap/content", frame=True),
            "skill_gaps_heatmap",
            "e30.invalid",
            None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "app content is not available"


@pytest.mark.asyncio
async def test_content_mode_fails_in_frame_instead_of_redirecting_to_login(
    monkeypatch,
):
    monkeypatch.setattr(main, "_app_cartridge_id", lambda _app: "replicon")
    monkeypatch.setattr(main, "_require_cartridge_visible", lambda *_args: None)

    async def no_active_installation(*_args, **_kwargs):
        return set()

    async def content_must_not_load(*_args, **_kwargs):
        raise AssertionError("inactive app content was loaded")

    monkeypatch.setattr(
        main,
        "_active_scoped_connection_cartridges",
        no_active_installation,
    )
    monkeypatch.setattr(main, "_refinement_app_html", content_must_not_load)

    with pytest.raises(HTTPException) as exc_info:
        await main._proxy_workspace_app(
            _request("/apps/skill_gaps_heatmap/content", frame=True),
            "skill_gaps_heatmap",
            content=True,
            user={"id": 42, "allowed_cartridges": ["replicon"]},
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "app content is not available"


@pytest.mark.asyncio
async def test_capability_subject_rebuilds_server_owned_cartridge_entitlements(
    monkeypatch,
):
    class Connection:
        async def fetchrow(self, sql, *_args):
            assert "LEFT JOIN public.roles" in sql
            assert "ELSE r.name" in sql
            assert "ORDER BY r.name ASC NULLS LAST" in sql
            return {
                "id": 42,
                "role": "viewer",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "workspace_role": "analyst",
            }

    class Pool:
        def acquire(self):
            return _AsyncContext(Connection())

    async def pool():
        return Pool()

    async def cartridges(workspace_id, *, user_id):
        assert workspace_id == "aaaaaaaa-0000-0000-0000-000000000001"
        assert user_id == 42
        return ["replicon", "sap_successfactors"]

    monkeypatch.setattr(main, "_get_db_pool", pool)
    monkeypatch.setattr(main, "_workspace_cartridges", cartridges)

    resolved = await main._resolve_capability_subject(
        {
            "user": "42",
            "tenant": "11111111-1111-1111-1111-111111111111",
            "workspace": "aaaaaaaa-0000-0000-0000-000000000001",
        }
    )

    assert resolved is not None
    assert resolved["workspace_role"] == "analyst"
    assert resolved["allowed_cartridges"] == ["replicon", "sap_successfactors"]


@pytest.mark.asyncio
async def test_capability_subject_fails_closed_when_entitlements_are_unavailable(
    monkeypatch,
):
    class Connection:
        async def fetchrow(self, *_args):
            return {
                "id": 42,
                "role": "viewer",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "workspace_role": "viewer",
            }

    class Pool:
        def acquire(self):
            return _AsyncContext(Connection())

    async def pool():
        return Pool()

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("entitlement ledger unavailable")

    monkeypatch.setattr(main, "_get_db_pool", pool)
    monkeypatch.setattr(main, "_workspace_cartridges", unavailable)

    resolved = await main._resolve_capability_subject(
        {
            "user": "42",
            "tenant": "11111111-1111-1111-1111-111111111111",
            "workspace": "aaaaaaaa-0000-0000-0000-000000000001",
        }
    )

    assert resolved is not None
    assert resolved["allowed_cartridges"] == []


@pytest.mark.asyncio
async def test_capability_subject_accepts_global_admin_without_target_membership(
    monkeypatch,
):
    class Connection:
        async def fetchrow(self, sql, *_args):
            assert "LEFT JOIN public.user_workspace_roles" in sql
            assert "u.role IN ('admin', 'owner', 'super_admin')" in sql
            assert "THEN 'workspace_admin'" in sql
            return {
                "id": 7,
                "role": "admin",
                "tenant_id": "22222222-2222-2222-2222-222222222222",
                "workspace_id": "bbbbbbbb-0000-0000-0000-000000000002",
                "workspace_role": "workspace_admin",
            }

    class Pool:
        def acquire(self):
            return _AsyncContext(Connection())

    async def pool():
        return Pool()

    async def cartridges(workspace_id, *, user_id):
        assert workspace_id == "bbbbbbbb-0000-0000-0000-000000000002"
        assert user_id == 7
        return ["replicon"]

    monkeypatch.setattr(main, "_get_db_pool", pool)
    monkeypatch.setattr(main, "_workspace_cartridges", cartridges)

    resolved = await main._resolve_capability_subject(
        {
            "user": "7",
            "tenant": "22222222-2222-2222-2222-222222222222",
            "workspace": "bbbbbbbb-0000-0000-0000-000000000002",
        }
    )

    assert resolved is not None
    assert resolved["role"] == "admin"
    assert resolved["workspace_role"] == "workspace_admin"
    assert resolved["allowed_cartridges"] == ["replicon"]


@pytest.mark.asyncio
async def test_grant_lookup_keeps_transaction_local_rls_scope_alive(monkeypatch):
    events: list[str] = []

    class Connection:
        active_transaction = False

        async def execute(self, sql, *_args):
            assert "set_config('app.tenant_id'" in sql
            assert self.active_transaction is True
            events.append("scope")

        def transaction(self):
            return _AsyncContext(
                self,
                on_enter=lambda: setattr(self, "active_transaction", True),
                on_exit=lambda: setattr(self, "active_transaction", False),
            )

    connection = Connection()

    class Pool:
        def acquire(self):
            return _AsyncContext(connection)

    async def pool():
        return Pool()

    async def app_html(*_args):
        return "<html></html>", {"name": "skill_gaps_heatmap"}

    async def scope(*_args):
        return (
            "11111111-1111-1111-1111-111111111111",
            "aaaaaaaa-0000-0000-0000-000000000001",
        )

    async def grants(conn, **_kwargs):
        assert conn.active_transaction is True
        events.append("grants")
        return ["replicon_resource_skill_daily"]

    monkeypatch.setattr(main, "_refinement_app_html", app_html)
    monkeypatch.setattr(
        main,
        "_packaged_manifest",
        lambda _name: {"cartridge_id": "replicon"},
    )
    monkeypatch.setattr(main, "_served_manifest_digest", lambda *_args: "a" * 64)
    monkeypatch.setattr(main, "_app_scope_for", scope)
    monkeypatch.setattr(main, "_get_db_pool", pool)
    monkeypatch.setattr(main, "_granted_datasets", grants)

    request = SimpleNamespace(state=SimpleNamespace(user={"id": 42}))
    _html, granted, digest, cartridge = await main._app_grant_context(
        request,
        "skill_gaps_heatmap",
        {"id": 42},
    )

    assert granted == ["replicon_resource_skill_daily"]
    assert digest == "a" * 64
    assert cartridge == "replicon"
    assert events == ["scope", "grants"]
    assert connection.active_transaction is False
