from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99u_decision_orchestrator.sql"


def runtime_evidence(user, item_id, *, kind="anomaly", value=1):
    observed_at = "2026-07-10T00:00:00Z"
    source_row = {
        "item_id": item_id,
        "kind": kind,
        "metric_type": "scalar",
        "observed_value": value,
        "observation_date": observed_at,
    }
    return runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_hcm",
        cartridge="sap_hcm",
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        source_row=source_row,
        locator_field="item_id",
        observed_at=observed_at,
    )


@pytest.fixture()
def orchestrator(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    from app.services.intelligence import decision_orchestrator as mod

    monkeypatch.delenv("DECISION_ORCHESTRATOR_CREATE_ACTIONS", raising=False)
    monkeypatch.delenv("DECISION_ORCHESTRATOR_ALLOW_MANUAL_FIXTURE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    return mod


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Acquire:
    def __init__(self, db: "FakeOrchestratorDB"):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class FakePool:
    def __init__(self, db: "FakeOrchestratorDB"):
        self.db = db

    def acquire(self):
        return _Acquire(self.db)


class FakeOrchestratorDB:
    def __init__(self):
        self.scope_calls: list[tuple[str | None, str]] = []
        self.current_tenant_id: str | None = None
        self.current_workspace_id: str | None = None
        self.sources: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.runs: dict[tuple[str, str], dict[str, Any]] = {}

    def transaction(self):
        return _Tx()

    def add_control_room_item(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        item_id: str,
        item_kind: str = "intelligence_signal",
        title: str = "Forecast risk",
        source_dataset: str = "intelligence_signals",
        metadata: Any = None,
    ) -> None:
        self.sources[(workspace_id, "control_room_item", item_id)] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "item_id": item_id,
            "source_id": item_id,
            "item_kind": item_kind,
            "title": title,
            "severity": "high",
            "status": "open",
            "domain": "Operacion",
            "cartridge_id": "sap_hcm",
            "source_dataset": source_dataset,
            "metadata": metadata or {},
        }
        if item_kind == "agent_alert":
            self.sources[(workspace_id, "agent_alert", item_id)] = {
                **self.sources[(workspace_id, "control_room_item", item_id)],
                "item_kind": "agent_alert",
            }

    def _visible(self, row: dict[str, Any] | None) -> bool:
        if not row or not self.current_workspace_id:
            return False
        return str(row["workspace_id"]) == str(self.current_workspace_id) and str(
            row.get("tenant_id") or ""
        ) == str(self.current_tenant_id or "")

    async def execute(self, query: str, *args):
        if "set_config('app.tenant_id'" in query:
            self.current_tenant_id = str(args[0]) if args[0] else None
            self.current_workspace_id = str(args[1])
            self.scope_calls.append((self.current_tenant_id, self.current_workspace_id))
            return None
        raise AssertionError(f"unmocked execute: {' '.join(query.split())[:180]}")

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if "FROM control_room_items" in q:
            workspace_id, source_id = str(args[0]), str(args[1])
            source_type = (
                "agent_alert"
                if "item_kind = 'agent_alert'" in q
                else "control_room_item"
            )
            row = self.sources.get((workspace_id, source_type, source_id))
            return row if self._visible(row) else None
        if "FROM intelligence_signals" in q:
            row = self.sources.get((str(args[0]), "intelligence_signal", str(args[1])))
            return row if self._visible(row) else None
        if "FROM monte_carlo_simulations" in q:
            row = self.sources.get(
                (str(args[0]), "monte_carlo_simulation", str(args[1]))
            )
            return row if self._visible(row) else None
        if "FROM calibration_observations" in q:
            row = self.sources.get(
                (str(args[0]), "calibration_observation", str(args[1]))
            )
            return row if self._visible(row) else None
        if q.startswith("INSERT INTO decision_orchestration_runs"):
            (
                orchestration_id,
                tenant_id,
                workspace_id,
                source_type,
                source_id,
                problem_type,
                secondary_problem_types,
                confidence,
                recommended_engines,
                candidate_engines,
                engine_plan,
                decision_plan,
                action_recommended,
                reasoning_summary,
                safety_notes,
                missing_data,
                created_by,
            ) = args
            key = (str(workspace_id), str(orchestration_id))
            existing = self.runs.get(key, {})
            row = {
                **existing,
                "id": existing.get("id") or len(self.runs) + 1,
                "orchestration_id": orchestration_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "source_type": source_type,
                "source_id": source_id,
                "problem_type": problem_type,
                "secondary_problem_types": secondary_problem_types,
                "confidence": confidence,
                "recommended_engines": recommended_engines,
                "candidate_engines": candidate_engines,
                "engine_plan": engine_plan,
                "decision_plan": decision_plan,
                "action_recommended": action_recommended,
                "external_action_id": existing.get("external_action_id"),
                "reasoning_summary": reasoning_summary,
                "safety_notes": safety_notes,
                "missing_data": missing_data,
                "created_by": created_by,
                "created_at": existing.get("created_at") or datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            self.runs[key] = row
            return row
        if q.startswith("UPDATE decision_orchestration_runs SET external_action_id"):
            workspace_id, orchestration_id, action_id = (
                str(args[0]),
                str(args[1]),
                str(args[2]),
            )
            row = self.runs.get((workspace_id, orchestration_id))
            if not self._visible(row):
                return None
            row["external_action_id"] = action_id
            row["updated_at"] = datetime.now(UTC)
            return row
        if q.startswith("SELECT * FROM decision_orchestration_runs"):
            row = self.runs.get((str(args[0]), str(args[1])))
            return row if self._visible(row) else None
        raise AssertionError(f"unmocked fetchrow: {q[:180]}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("WITH RECURSIVE lineage AS"):
            workspace_id = str(args[0])
            pending = list(args[1])
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            while pending:
                item_id = str(pending.pop())
                if item_id in seen:
                    continue
                seen.add(item_id)
                row = self.sources.get((workspace_id, "control_room_item", item_id))
                if not self._visible(row):
                    continue
                rows.append(row)
                metadata = row.get("metadata") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                lineage = metadata.get("lineage") or {}
                for parent_id in (
                    metadata.get("parent_item_id"),
                    metadata.get("source_item_id"),
                    metadata.get("derived_from"),
                    lineage.get("parent_item_id"),
                    lineage.get("source_item_id"),
                ):
                    if isinstance(parent_id, str) and parent_id:
                        pending.append(parent_id)
            return rows
        if q.startswith("SELECT * FROM decision_orchestration_runs"):
            workspace_id = str(args[0])
            rows = [
                row
                for (row_workspace, _run_id), row in self.runs.items()
                if row_workspace == workspace_id and self._visible(row)
            ]
            if "source_type =" in q:
                rows = [row for row in rows if row["source_type"] == args[1]]
            if "source_id =" in q:
                source_id = args[2] if "source_type =" in q else args[1]
                rows = [row for row in rows if row["source_id"] == source_id]
            return rows[: int(args[-1])]
        raise AssertionError(f"unmocked fetch: {q[:180]}")


def _user(
    user_id: int,
    *,
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
    workspace_id: str = "22222222-2222-2222-2222-222222222222",
) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"user{user_id}@example.com",
        "role": "workspace_admin",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


def _patch_pool(mod, monkeypatch, db: FakeOrchestratorDB) -> None:
    monkeypatch.setattr(mod.auth, "pool", AsyncMock(return_value=FakePool(db)))
