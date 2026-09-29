from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.control_room.business_decision_persistence import (
    _decision_fields,
    _impact_snapshot,
    create_and_link_decision,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


USER = {"id": 7, "tenant_id": "tenant-a", "workspace_id": "workspace-a"}


def _item(details: dict | None = None) -> dict:
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "tenant_id": "tenant-a",
        "title": "Valid anomaly",
        "entity_label": "Employee",
        "description": "Measured anomaly",
        "recommendation": "Review",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "severity": "high",
        "status": "open",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
        "details": details or {},
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            source_row={"item_id": item["id"]},
            locator_field="item_id",
            observed_at=item["observation_date"],
            business_observation=item,
        ),
    }


class _InsertCapturingConnection:
    def __init__(self) -> None:
        self.decision_kpis: list | None = None

    async def fetchrow(self, sql: str, *args):
        normalized = " ".join(sql.split()).upper()
        if "FOR UPDATE" in normalized:
            return {
                "item_id": "business-1",
                "decision_id": None,
                "metadata": {},
                "owner_user_id": 7,
            }
        if normalized.startswith("INSERT INTO DECISIONS"):
            self.decision_kpis = json.loads(args[2])
            return {"id": 42, "title": "Valid anomaly"}
        if normalized.startswith("INSERT INTO DECISION_ACTIONS"):
            return {"id": 8}
        if normalized.startswith("UPDATE CONTROL_ROOM_ITEMS"):
            return {"item_id": "business-1"}
        raise AssertionError(normalized)


def _snapshot_entries(kpis: list) -> list[dict]:
    return [entry for entry in kpis if isinstance(entry, dict) and "impacto_estimado" in entry]


def test_impact_snapshot_uses_the_monetary_rule_and_spanish_label():
    snapshot = _impact_snapshot(_item({"monthly_cost_usd": 1500}))

    assert snapshot is not None
    assert snapshot["regla"] == "Regla: costo mensual directo"
    assert snapshot["impacto_estimado"]["valor"] == 1500.0
    assert snapshot["impacto_estimado"]["moneda"] == "USD"
    assert snapshot["impacto_estimado"]["base"] == "rule"


def test_impact_snapshot_is_absent_without_a_monetary_basis():
    assert _impact_snapshot(_item()) is None
    assert _impact_snapshot(_item({"free_text": "no money"})) is None


def test_decision_fields_append_the_snapshot_after_the_provenance_entry():
    _title, _description, kpis = _decision_fields(_item({"monthly_cost_usd": 900}))

    entries = _snapshot_entries(kpis)
    assert len(entries) == 1
    assert set(entries[0]) == {"impacto_estimado", "regla"}
    assert kpis[-1] == entries[0]
    assert any(
        isinstance(entry, dict) and entry.get("provenance") for entry in kpis[:-1]
    )


def test_decision_fields_without_impact_keep_the_legacy_kpi_shape():
    _title, _description, kpis = _decision_fields(_item())

    assert _snapshot_entries(kpis) == []
    assert [entry.get("label") for entry in kpis[:3]] == [
        "Severidad",
        "Entidad",
        "Estado OMEGA",
    ]


@pytest.mark.asyncio
async def test_create_and_link_decision_persists_the_snapshot_in_kpis():
    conn = _InsertCapturingConnection()

    await create_and_link_decision(
        conn,
        user=USER,
        item=_item({"monthly_cost_usd": 1200}),
        workspace_id="workspace-a",
        ensure_item_row=AsyncMock(),
        record_item_event=AsyncMock(),
    )

    assert conn.decision_kpis is not None
    entries = _snapshot_entries(conn.decision_kpis)
    assert len(entries) == 1
    assert entries[0]["impacto_estimado"]["valor"] == 1200.0
    assert entries[0]["regla"] == "Regla: costo mensual directo"


@pytest.mark.asyncio
async def test_create_and_link_decision_without_impact_stays_additive_only():
    conn = _InsertCapturingConnection()

    await create_and_link_decision(
        conn,
        user=USER,
        item=_item(),
        workspace_id="workspace-a",
        ensure_item_row=AsyncMock(),
        record_item_event=AsyncMock(),
    )

    assert conn.decision_kpis is not None
    assert _snapshot_entries(conn.decision_kpis) == []
