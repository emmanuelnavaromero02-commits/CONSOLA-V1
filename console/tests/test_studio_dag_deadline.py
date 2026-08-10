from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import HTTPException


USER = {
    "id": 17,
    "email": "studio-user@example.test",
    "role": "user",
    "workspace_role": "analyst",
    "active_tenant_id": "00000000-0000-0000-0000-000000000001",
    "active_workspace_id": "00000000-0000-0000-0000-000000000002",
    "allowed_cartridges": ["acme"],
}


@pytest.mark.asyncio
async def test_dags_list_times_out_and_cancels_hung_airflow(monkeypatch):
    studio = importlib.import_module("app.routers.studio")
    cancelled = asyncio.Event()

    async def get_cartridge(cartridge_id):
        assert cartridge_id == "acme"
        return {"id": "acme", "dags": []}

    async def hung_invoke(server, tool, args, *, user):
        assert (server, tool, args) == ("infra", "airflow_list_dags", {})
        assert user is USER
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(studio, "_AIRFLOW_DAGS_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(studio.mcp_registry, "invoke", hung_invoke)

    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(
            studio.dags_list(cartridge="acme", user=USER), timeout=0.2
        )

    assert exc.value.status_code == 504
    assert exc.value.detail == "Airflow DAG list timed out"
    await asyncio.wait_for(cancelled.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_dags_list_preserves_user_and_cartridge_visibility(monkeypatch):
    studio = importlib.import_module("app.routers.studio")
    calls = []

    async def get_cartridge(cartridge_id):
        assert cartridge_id == "acme"
        return {
            "id": "acme",
            "dags": [
                {"dag_id": "acme_registered"},
                {"dag_id": "platform_shared"},
            ],
        }

    async def invoke(server, tool, args, *, user):
        calls.append((server, tool, args, user))
        return {
            "dags": [
                {"dag_id": "other_private"},
                {"dag_id": "acme_live", "is_paused": True},
                {"dag_id": "platform_shared", "cartridge_id": "platform"},
            ]
        }

    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(studio.mcp_registry, "invoke", invoke)

    result = await studio.dags_list(cartridge="acme", user=USER)

    assert calls == [("infra", "airflow_list_dags", {}, USER)]
    assert [dag["dag_id"] for dag in result["dags"]] == [
        "acme_live",
        "acme_registered",
        "platform_shared",
    ]
    assert result["dags"][0]["is_paused"] is True
    assert result["dags"][1]["registered_only"] is True
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_dags_list_rejects_hidden_cartridge_before_downstream_calls(
    monkeypatch,
):
    studio = importlib.import_module("app.routers.studio")

    async def forbidden_call(*_args, **_kwargs):
        raise AssertionError("hidden cartridge must fail before downstream I/O")

    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", forbidden_call)
    monkeypatch.setattr(studio.mcp_registry, "invoke", forbidden_call)

    with pytest.raises(HTTPException) as exc:
        await studio.dags_list(cartridge="other", user=USER)

    assert exc.value.status_code == 403
    assert exc.value.detail == "cartridge not allowed"
