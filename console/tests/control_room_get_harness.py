from __future__ import annotations

import asyncio
import re
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request

from app.dependencies import require_authenticated
from app.routers import control_room as routes


TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WORKSPACE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm", "sap_successfactors"],
}
ITEM_ID = routes.control_room_service._encode_id(
    {
        "dataset": "employees_anomalies",
        "type": "terminated_but_active",
        "entity": "1001",
    }
)
_REQUEST_ID: ContextVar[str] = ContextVar("purity_request_id", default="")
_MUTATING_SQL = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|CALL)\b", re.I)


class MutationSentinel:
    def __init__(self) -> None:
        timestamp = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
        self.tables: dict[str, list[dict[str, Any]]] = {
            "control_room_items": [
                {
                    "tenant_id": TENANT_ID,
                    "workspace_id": WORKSPACE_ID,
                    "owner_user_id": 7,
                    "item_id": ITEM_ID,
                    "cartridge_id": "sap_hcm",
                    "domain": "Personas",
                    "source_dataset": "employees_anomalies",
                    "item_kind": "anomaly",
                    "title": "Empleado terminado activo",
                    "severity": "critical",
                    "status": "open",
                    "decision_id": 2,
                    "entity_kind": "Empleado",
                    "entity_id": "1001",
                    "entity_label": "Ana Gomez",
                    "anomaly_type": "terminated_but_active",
                    "metadata": {"data_status": "ready"},
                    "first_seen_at": timestamp,
                    "last_seen_at": timestamp,
                    "resolved_at": None,
                    "dismissed_at": None,
                    "impact_estimate": 4200,
                    "impact_currency": "USD",
                    "confidence": 0.9,
                    "priority_score": 90,
                    "selected_option_id": None,
                    "execution_status": "not_started",
                }
            ],
            "decisions": [
                {
                    "id": 2,
                    "workspace_id": WORKSPACE_ID,
                    "status": "open",
                    "created_at": timestamp,
                    "kpis": [],
                }
            ],
            "control_room_item_events": [
                {
                    "id": 11,
                    "workspace_id": WORKSPACE_ID,
                    "item_id": ITEM_ID,
                    "event_type": "detected",
                    "created_at": timestamp,
                    "metadata": {},
                }
            ],
            "intelligence_runs": [
                {
                    "id": 21,
                    "run_ref": "run-1",
                    "tenant_id": TENANT_ID,
                    "workspace_id": WORKSPACE_ID,
                    "owner_user_id": 7,
                    "status": "completed",
                    "started_at": timestamp,
                    "metadata": {},
                }
            ],
        }
        self.mutation_attempts: list[str] = []
        self.scope_calls: list[tuple[str, str]] = []

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return deepcopy(self.tables)

    @staticmethod
    def _sql(query: str) -> str:
        return " ".join(str(query).split())

    def _reject_mutation(self, query: str) -> str:
        statement = self._sql(query)
        if _MUTATING_SQL.search(statement):
            self.mutation_attempts.append(statement)
            raise AssertionError(f"mutation attempted from GET: {statement[:120]}")
        return statement

    async def execute(self, query: str, *args):
        statement = self._reject_mutation(query)
        if statement.upper().startswith("SELECT SET_CONFIG"):
            self.scope_calls.append((str(args[0]), str(args[1])))
        return None

    async def fetch(self, query: str, *args):
        statement = self._reject_mutation(query)
        if "FROM cartridge_installations ci" in statement:
            return [
                {
                    "cartridge_id": "sap_hcm",
                    "installation_status": "ready",
                    "current_step": "ready",
                    "error_message": None,
                    "ready_at": self.tables["intelligence_runs"][0]["started_at"],
                    "label": "SAP HCM",
                    "category": "hr",
                }
            ]
        if "FROM control_room_items" in statement:
            rows = self.tables["control_room_items"]
            if "item_id = ANY" in statement and len(args) > 1:
                allowed = {str(value) for value in args[1]}
                rows = [row for row in rows if row["item_id"] in allowed]
            if "decision_id = ANY" in statement and len(args) > 1:
                allowed = {int(value) for value in args[1]}
                rows = [row for row in rows if row.get("decision_id") in allowed]
            return deepcopy(rows)
        if "FROM decisions" in statement:
            return deepcopy(self.tables["decisions"])
        if "FROM decision_actions" in statement:
            return []
        if "FROM control_room_item_events" in statement:
            return deepcopy(self.tables["control_room_item_events"])
        if "FROM intelligence_runs" in statement:
            return deepcopy(self.tables["intelligence_runs"])
        return []

    async def fetchrow(self, query: str, *args):
        statement = self._reject_mutation(query)
        if "FROM intelligence_runs" in statement:
            return deepcopy(self.tables["intelligence_runs"][0])
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *_args):
        statement = self._reject_mutation(query)
        if "COUNT" in statement.upper():
            return 0
        return None

    async def executemany(self, query: str, *_args):
        self.mutation_attempts.append(self._sql(query))
        raise AssertionError("executemany attempted from GET")

    async def copy_records_to_table(self, *_args, **_kwargs):
        self.mutation_attempts.append("COPY_RECORDS_TO_TABLE")
        raise AssertionError("copy attempted from GET")

    async def copy_to_table(self, *_args, **_kwargs):
        self.mutation_attempts.append("COPY_TO_TABLE")
        raise AssertionError("copy attempted from GET")

    async def copy_from_table(self, *_args, **_kwargs):
        self.mutation_attempts.append("COPY_FROM_TABLE")
        raise AssertionError("copy attempted from GET")

    def add(self, *_args):
        self.mutation_attempts.append("ADD")
        raise AssertionError("ORM add attempted from GET")

    def delete(self, *_args):
        self.mutation_attempts.append("DELETE")
        raise AssertionError("ORM delete attempted from GET")

    async def flush(self):
        self.mutation_attempts.append("FLUSH")
        raise AssertionError("ORM flush attempted from GET")

    async def commit(self):
        self.mutation_attempts.append("COMMIT")
        raise AssertionError("commit attempted from GET")


class ConcurrencyProbe:
    def __init__(self) -> None:
        self.enabled = False
        self.active_requests: set[str] = set()
        self.max_in_flight = 0
        self.release = asyncio.Event()

    async def checkpoint(self) -> None:
        if not self.enabled:
            return
        request_id = _REQUEST_ID.get()
        self.active_requests.add(request_id)
        self.max_in_flight = max(self.max_in_flight, len(self.active_requests))
        if len(self.active_requests) >= 2:
            self.release.set()
        await asyncio.wait_for(self.release.wait(), timeout=2)
        await asyncio.sleep(0.01)
        self.active_requests.discard(request_id)


def build_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user = USER
        token = _REQUEST_ID.set(request.headers.get("x-purity-request", "sequential"))
        try:
            return await call_next(request)
        finally:
            _REQUEST_ID.reset(token)

    app.include_router(routes.router)
    app.dependency_overrides[require_authenticated] = lambda: USER
    return app
