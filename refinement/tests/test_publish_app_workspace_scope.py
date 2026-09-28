from __future__ import annotations

import hashlib

import pytest

from refinement.app import main as refinement_main


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FOREIGN_TENANT = "22222222-2222-2222-2222-222222222222"
FOREIGN_WORKSPACE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
HTML = (
    "<html><body><h1>Ventas</h1>"
    "<script>fetch('/api/data/ventas_diarias')</script></body></html>"
    + "<!-- relleno -->" * 20
)


def _context(**overrides) -> dict:
    context = {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "user_id": 41,
        "permissions": ["apps.read", "apps.write"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": [],
        "allowed_prefixes": [],
    }
    context.update(overrides)
    return context


def _body(args: dict, context: dict) -> dict:
    return {
        "tool": "publish_app",
        "args": args,
        "security_context": refinement_main._sign_security_context(context),
        "_verified_internal_service": "console",
    }


@pytest.fixture
def pg(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_pg_exec(sql, params=None, fetch=False, security_context=None):
        calls.append((" ".join(str(sql).split()), tuple(params or ())))
        return [] if fetch else None

    monkeypatch.setattr(refinement_main, "_pg_exec", fake_pg_exec)
    return calls


def _writes(pg):
    return [(sql, params) for sql, params in pg if "INSERT INTO analytic_apps" in sql]


def test_cartridge_less_publish_is_accepted_with_workspace_scope(pg):
    result = refinement_main._mcp_invoke_sync(
        _body({"name": "ventas_semana", "title": "Ventas", "html": HTML}, _context())
    )
    assert result["published"] is True
    assert result["name"] == "ventas_semana"
    assert result["datasets_used"] == ["ventas_diarias"]
    assert result["html_sha256"] == hashlib.sha256(HTML.encode("utf-8")).hexdigest()
    probe_sql, probe_params = pg[0]
    assert "analytic_app_manifests" in probe_sql
    assert "source = 'packaged_manifest'" in probe_sql
    assert probe_params == ("ventas_semana",)
    sql, params = _writes(pg)[0]
    assert TENANT in params and WORKSPACE in params


def test_cartridge_less_publish_rejects_packaged_manifest_names(pg, monkeypatch):
    def fake_pg_exec(sql, params=None, fetch=False, security_context=None):
        pg.append((" ".join(str(sql).split()), tuple(params or ())))
        if fetch and "analytic_app_manifests" in str(sql):
            return [{"?column?": 1}]
        return [] if fetch else None

    monkeypatch.setattr(refinement_main, "_pg_exec", fake_pg_exec)
    result = refinement_main._mcp_invoke_sync(
        _body(
            {"name": "sap_b1_margen", "title": "Margen", "html": HTML}, _context()
        )
    )
    assert "reserved by a packaged app" in result["error"]
    assert _writes(pg) == []


def test_cartridge_publish_skips_the_packaged_name_probe(pg):
    result = refinement_main._mcp_invoke_sync(
        _body(
            {
                "name": "ventas_semana",
                "title": "Ventas",
                "html": HTML,
                "cartridge_id": "sap_successfactors",
            },
            _context(allowed_cartridges=["sap_successfactors"]),
        )
    )
    assert result["published"] is True
    assert all("analytic_app_manifests" not in sql for sql, _ in pg)


def test_scopeless_non_admin_publish_is_still_rejected(pg):
    result = refinement_main._mcp_invoke_sync(
        _body(
            {"name": "ventas_semana", "title": "Ventas", "html": HTML},
            _context(tenant_id="", workspace_id=""),
        )
    )
    assert "cartridge_id is required" in result["error"]
    assert pg == []


def test_publish_scope_comes_from_the_signed_context_not_the_args(pg):
    result = refinement_main._mcp_invoke_sync(
        _body(
            {
                "name": "ventas_semana",
                "title": "Ventas",
                "html": HTML,
                "tenant_id": FOREIGN_TENANT,
                "workspace_id": FOREIGN_WORKSPACE,
                "created_by_id": 999,
            },
            _context(),
        )
    )
    assert result["published"] is True
    _sql, params = _writes(pg)[0]
    assert FOREIGN_TENANT not in params
    assert FOREIGN_WORKSPACE not in params
    assert TENANT in params and WORKSPACE in params
    assert 999 not in params
    assert 41 in params


def test_shared_visibility_is_workspace_visible_never_cross_workspace():
    row = {
        "cartridge_id": None,
        "created_by_id": 7,
        "visibility": "shared",
    }
    same_workspace_colleague = {
        "trusted": True,
        "user_id": 8,
        "role": "user",
        "workspace_role": "analyst",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_cartridges": [],
        "allowed_prefixes": [],
    }
    assert refinement_main._app_visible(same_workspace_colleague, row) is True
    assert refinement_main._app_visible(
        same_workspace_colleague, {**row, "visibility": "private"}
    ) is False
    # Cross-workspace isolation comes from RLS on analytic_apps, not from
    # visibility: the listing query cannot return foreign rows at all
    # (covered live in tests/test_analytic_app_dataset_grants.py).
    listing_sql = None
    import inspect

    listing_sql = inspect.getsource(refinement_main._list_apps)
    assert "security_context=sec" in listing_sql


def test_publish_without_apps_write_is_forbidden(pg):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        refinement_main._mcp_invoke_sync(
            _body(
                {"name": "ventas_semana", "title": "Ventas", "html": HTML},
                _context(permissions=["apps.read"]),
            )
        )
    assert excinfo.value.status_code == 403
    assert pg == []


def test_foreign_cartridge_publish_is_still_scope_checked(pg):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        refinement_main._mcp_invoke_sync(
            _body(
                {
                    "name": "ventas_semana",
                    "title": "Ventas",
                    "html": HTML,
                    "cartridge_id": "sap_successfactors",
                },
                _context(allowed_cartridges=["replicon"]),
            )
        )
    assert excinfo.value.status_code == 403
    assert pg == []
