"""Generic dataset writers cannot redefine packaged SuccessFactors SQL."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from refinement.app import main as refinement_main
from refinement.app.dataset_store import DatasetStore, ProtectedDatasetError


PAIR_KEY = "packaged-dataset-test-key-more-than-32-characters"
TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PACKAGED_UPSTREAM = (
    "sap_successfactors_employee_360",
    "sap_successfactors_performance_cycle",
    "sap_successfactors_employee_competency",
    "sap_successfactors_employee_aspiration",
)


def _body(name: str) -> dict:
    context = {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "user_id": 41,
        "permissions": ["datasets.read", "datasets.write"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_prefixes": [],
    }
    return {
        "tool": "save_dataset",
        "args": {
            "name": name,
            "sql": "SELECT 1 AS forged_employee",
            "layer": "gold",
            "cartridge": "sap_successfactors",
            "sources": [],
        },
        "security_context": refinement_main._sign_security_context(context),
    }


async def _post(name: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=refinement_main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://refinement.test"
    ) as client:
        return await client.post(
            "/mcp/invoke",
            headers={"x-api-key": PAIR_KEY, "x-internal-service": "console"},
            json=_body(name),
        )


@pytest.mark.parametrize("name", PACKAGED_UPSTREAM)
def test_store_rejects_packaged_upstream_before_connecting(name: str) -> None:
    store = DatasetStore.__new__(DatasetStore)
    with pytest.raises(ProtectedDatasetError):
        store.save_dataset({"name": name, "sql_def": "SELECT 1"})


@pytest.mark.parametrize("name", PACKAGED_UPSTREAM)
@pytest.mark.asyncio
async def test_http_rejects_packaged_upstream_without_calling_store(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    saved = MagicMock()
    monkeypatch.setattr(refinement_main.store, "save_dataset", saved)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)

    response = await _post(name)

    assert response.status_code == 403
    assert response.json() == {
        "detail": "dataset is server-owned and cannot be replaced"
    }
    saved.assert_not_called()


def test_custom_dataset_name_remains_writable(monkeypatch: pytest.MonkeyPatch) -> None:
    store = DatasetStore.__new__(DatasetStore)
    reached = MagicMock(side_effect=RuntimeError("database path reached"))
    monkeypatch.setattr("refinement.app.dataset_store._conn", reached)

    with pytest.raises(RuntimeError, match="database path reached"):
        store.save_dataset({"name": "custom_workforce_view", "sql_def": "SELECT 1"})
    reached.assert_called_once()
