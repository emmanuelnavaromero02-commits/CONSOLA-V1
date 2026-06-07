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


@pytest.mark.asyncio
async def test_dashboard_kpis_falls_back_to_catalog_without_active_connection():
    with (
        patch.object(dashboard.auth, "pool", new=AsyncMock(return_value=_zero_pool())),
        patch.object(dashboard, "_active_scoped_cartridges", new=AsyncMock(return_value=())),
    ):
        result = await dashboard.dashboard_kpis(user=USER)

    # No connection yet -> show the full built-in catalog, not an empty table.
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
