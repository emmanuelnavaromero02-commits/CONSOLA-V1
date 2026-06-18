"""Catalog fallback for scoped dashboard + Control Room surfaces.

#273 scoped the dashboard KPIs and the Control Room cockpit to cartridges
with an active scoped Vault connection. That correctly declutters FEMSA
(only ``femsa_sf`` is connected), but it also emptied the surfaces in any
environment with NO active connection (fresh install / local / E2E / demo).

These tests pin the agreed fallback: when nothing is connected yet, the
surfaces fall back to the installed catalog so they are never dead; as soon
as a real connection exists the view scopes down to it automatically. The
scoping guarantee itself is still covered by test_control_room_service.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import dashboard
from app.services import control_room_service


USER = {"id": 7, "email": "ops@example.com", "active_workspace_id": "ws-A", "tenant_id": "t-A"}


def _zero_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow.return_value = {}
    pool.fetchval.return_value = 0
    pool.fetch.return_value = []
    return pool


def test_control_room_known_non_ready_sources_default_to_hidden(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_SHOW_KNOWN_NON_READY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)

    assert control_room_service._show_known_non_ready_sources() is False


def test_control_room_domain_payload_hides_modules_without_runtime_data(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_SHOW_KNOWN_NON_READY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    modules = [
        module
        for module in control_room_service.MODULES
        if module.cartridge == "sap_successfactors"
    ]
    sources = [
        {
            "module_id": "sap_successfactors_org",
            "domain": "Recursos Humanos",
            "dataset": "sap_successfactors_org_structure",
            "count": 515,
            "status": "ok",
            "operationally_ready": True,
            "data_readiness": "ready",
        }
    ]

    payload = control_room_service._domain_payload("Recursos Humanos", modules, [], sources)

    assert [module["id"] for module in payload["modules"]] == ["sap_successfactors_org"]


@pytest.mark.asyncio
async def test_dashboard_kpis_tenant_without_active_connection_stays_empty():
    with (
        patch.object(dashboard.auth, "pool", new=AsyncMock(return_value=_zero_pool())),
        patch.object(dashboard, "_active_scoped_cartridges", new=AsyncMock(return_value=())),
    ):
        result = await dashboard.dashboard_kpis(user=USER)

    # 20B: tenant users must not fall back to global KPIs when no scoped
    # connection exists.
    assert result["active_cartridges"] == []
    assert result["data_freshness"] == {}


@pytest.mark.asyncio
async def test_dashboard_kpis_platform_admin_falls_back_to_catalog_without_active_connection():
    platform_user = {**USER, "role": "admin"}
    with (
        patch.object(dashboard.auth, "pool", new=AsyncMock(return_value=_zero_pool())),
        patch.object(dashboard, "_active_scoped_cartridges", new=AsyncMock(return_value=())),
    ):
        result = await dashboard.dashboard_kpis(user=platform_user)

    assert result["active_cartridges"] == list(dashboard._CARTRIDGES)
    assert set(result["data_freshness"]) == set(dashboard._CARTRIDGES)


@pytest.mark.asyncio
async def test_dashboard_kpis_scopes_to_active_connection_when_present():
    with (
        patch.object(dashboard.auth, "pool", new=AsyncMock(return_value=_zero_pool())),
        patch.object(
            dashboard,
            "_active_scoped_cartridges",
            new=AsyncMock(return_value=("sap_successfactors",)),
        ),
    ):
        result = await dashboard.dashboard_kpis(user=USER)

    # A real connection (FEMSA femsa_sf) scopes the view down to it.
    assert result["active_cartridges"] == ["sap_successfactors"]
    assert set(result["data_freshness"]) == {"sap_successfactors"}


@pytest.mark.asyncio
async def test_filter_installations_falls_back_to_catalog_without_connections():
    installations = [
        {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
        {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
    ]
    with patch.object(
        control_room_service,
        "_vault_connections_for_cartridge",
        new=AsyncMock(return_value=[]),
    ):
        result = await control_room_service._filter_installations_by_scoped_connections(
            installations, USER
        )

    assert {row["cartridge_id"] for row in result} == {"replicon", "sap_hcm"}
    assert all(row["connection_count"] == 0 for row in result)


@pytest.mark.asyncio
async def test_filter_installations_scopes_when_a_connection_exists():
    installations = [
        {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
        {"cartridge_id": "sap_successfactors", "installation_status": "ready", "label": "SF"},
    ]

    async def connections(cartridge_id: str, _user):
        if cartridge_id == "sap_successfactors":
            return [{"conn_id": "femsa_sf", "auth_method": "saml_bearer_assertion"}]
        return []

    with patch.object(
        control_room_service,
        "_vault_connections_for_cartridge",
        new=AsyncMock(side_effect=connections),
    ):
        result = await control_room_service._filter_installations_by_scoped_connections(
            installations, USER
        )

    # Only the connected cartridge survives; no fallback to the catalog.
    assert {row["cartridge_id"] for row in result} == {"sap_successfactors"}
    assert result[0]["connection_id"] == "femsa_sf"


@pytest.mark.asyncio
async def test_control_room_scoped_dashboard_omits_empty_domains(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_SHOW_KNOWN_NON_READY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    user = {**USER, "allowed_cartridges": ["sap_successfactors"]}

    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_org_structure":
            return [{"node_id": "org-1", "node_type": "department"}]
        if dataset == "sap_successfactors_employee_360":
            return [{"user_id": "u-1", "department": "Ventas"}]
        return []

    with (
        patch.object(control_room_service.auth, "pool", new=AsyncMock(return_value=_zero_pool())),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "connection_id": "femsa_sf",
                    "connection_count": 1,
                    "active_connection_ids": ["femsa_sf"],
                    "auth_method": "saml_bearer_assertion",
                }
            ]),
        ),
    ):
        result = await control_room_service.dashboard(user, fetcher=fetcher, persist=False)

    assert [domain["label"] for domain in result["domains"]] == ["Recursos Humanos"]
    assert all(domain["modules"] for domain in result["domains"])
