from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]


def _load_control_room_tool(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.syspath_prepend(str(REPO / "mcp-infra"))
    return importlib.import_module("app.tools.control_room")


def _ctx() -> dict:
    return {
        "trusted": True,
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "agent_id": "33333333-3333-3333-3333-333333333333",
        "agent_slug": "monitor",
        "agent_name": "Monitor",
        "agent_run_id": 42,
        "email": "agent-runner@omega.local",
        "permissions": ["datasets.read", "cartridges.read", "control_room.write"],
    }


def _valid_args() -> dict:
    return {
        "alert_type": "margin_watch",
        "cartridge_id": "replicon",
        "domain": "Finanzas",
        "source_dataset": "gold_pnl_mensual",
        "entity_key": "client=acme/month=2026-06",
        "title": "Margen fuera de rango",
        "message": "El monitor detecto margen bajo.",
        "severity": "high",
        "confidence": 0.82,
        "security_context": _ctx(),
    }


def test_control_room_alert_tool_rejects_outcome_and_scope_args(monkeypatch):
    mod = _load_control_room_tool(monkeypatch)

    with pytest.raises(HTTPException) as outcome:
        mod.control_room__raise_alert(**_valid_args(), outcome="true_positive")
    assert outcome.value.status_code == 400

    with pytest.raises(HTTPException) as tenant:
        mod.control_room__raise_alert(**_valid_args(), tenant_id="other")
    assert tenant.value.status_code == 400


def test_control_room_alert_tool_validates_ranges_before_db(monkeypatch):
    mod = _load_control_room_tool(monkeypatch)

    with pytest.raises(HTTPException) as severity:
        mod.control_room__raise_alert(**{**_valid_args(), "severity": "urgent"})
    assert severity.value.status_code == 400

    with pytest.raises(HTTPException) as confidence:
        mod.control_room__raise_alert(**{**_valid_args(), "confidence": 1.5})
    assert confidence.value.status_code == 400

    with pytest.raises(HTTPException) as impact:
        mod.control_room__raise_alert(**{**_valid_args(), "impact_estimate": "not money"})
    assert impact.value.status_code == 400


def test_control_room_alert_tool_caps_evidence_refs(monkeypatch):
    mod = _load_control_room_tool(monkeypatch)

    with pytest.raises(HTTPException) as refs:
        mod.control_room__raise_alert(**{
            **_valid_args(),
            "evidence_refs": [f"ref-{idx}" for idx in range(11)],
        })
    assert refs.value.status_code == 400


def test_control_room_alert_tool_deduplicates_agent_alerts(monkeypatch):
    mod = _load_control_room_tool(monkeypatch)
    store: dict[str, dict] = {"items": {}, "events": []}

    def _json_payload(value):
        return getattr(value, "adapted", value)

    class Cursor:
        def __init__(self, db: dict[str, dict]):
            self.db = db
            self._row = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=None):
            params = params or ()
            if "set_config" in sql:
                return None
            if "SELECT status, metadata" in sql:
                item = self.db["items"].get(params[1])
                self._row = (item["status"], item["metadata"]) if item else None
                return None
            if "UPDATE control_room_items" in sql:
                item = self.db["items"][params[8]]
                item["metadata"].update(_json_payload(params[6]))
                item["severity"] = params[1]
                item["confidence"] = params[3]
                return None
            if "INSERT INTO control_room_items" in sql:
                self.db["items"][params[2]] = {
                    "status": "open",
                    "metadata": _json_payload(params[11]),
                    "severity": params[7],
                    "confidence": params[14],
                }
                return None
            if "INSERT INTO control_room_item_events" in sql:
                self.db["events"].append({
                    "item_id": params[2],
                    "event_type": params[3],
                    "metadata": _json_payload(params[5]),
                })
                return None
            raise AssertionError(f"unexpected SQL: {sql}")

        def fetchone(self):
            return self._row

    class Connection:
        def __init__(self, db: dict[str, dict]):
            self.db = db
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return Cursor(self.db)

        def commit(self):
            self.commits += 1

    monkeypatch.setattr(mod, "_conn", lambda: Connection(store))

    results = [mod.control_room__raise_alert(**_valid_args()) for _ in range(3)]

    assert len(store["items"]) == 1
    assert [result["created"] for result in results] == [True, False, False]
    assert [result["deduped"] for result in results] == [False, True, True]
    assert results[-1]["occurrence_count"] == 3
    item = next(iter(store["items"].values()))
    assert item["metadata"]["occurrence_count"] == 3
    assert item["metadata"]["advisory"] is True
    assert item["metadata"]["source"] == "agent"
    assert [event["event_type"] for event in store["events"]] == [
        "agent_alert_created",
        "agent_alert_deduped",
        "agent_alert_deduped",
    ]


def test_control_room_alert_tool_source_contract_is_advisory_only():
    source = (REPO / "mcp-infra/app/tools/control_room.py").read_text(encoding="utf-8")
    assert "item_kind = 'agent_alert'" in source or "'agent_alert'" in source
    assert "'execution_status': 'not_started'" not in source
    assert "'execution_status': \"not_started\"" not in source
    assert "'execution_status': \"completed\"" not in source
    assert '"execution_status": "not_started"' in source
    assert "control_room_action_executions" not in source
    assert "external_writeback" in source


def test_mcp_main_enforces_control_room_alert_scope():
    source = (REPO / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    assert '_CONTROL_ROOM_ALERT_TOOLS = {"control_room__raise_alert"}' in source
    assert '_require_context_permission(req, "control_room.write", internal_service)' in source
    assert "control room alerts require tenant/workspace scope" in source
    assert "backend-owned arg is not allowed" in source
