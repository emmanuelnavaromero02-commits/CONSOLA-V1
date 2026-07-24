from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_action_reservation import effective_action_key
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_source_scope import scoped_source_row
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    WorkflowStage,
    business_observation_fingerprint,
    persistence_metadata,
    workflow_eligibility_provenance,
)

# fmt: off
USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
    "allowed_cartridges": ["sap_hcm", "sap_s4hana", "sap_successfactors", "replicon"],
    "_effective_permissions": ["control_room.read", "control_room.write", "control_room.execute"],
}
# fmt: on

SAMPLE_ROWS = {"pnl_mensual": []}

for _source in control_room_service._all_sources():  # noqa: SLF001
    SAMPLE_ROWS.setdefault(_source.dataset, [])


def _successful_command_tag(query: object, *_args: object) -> str:
    sql = " ".join(str(query).split()).upper()
    for command in ("INSERT", "UPDATE", "DELETE"):
        if sql.startswith(command) or f" {command} " in f" {sql} ":
            return f"{command} 0 1" if command == "INSERT" else f"{command} 1"
    return "SELECT 1"


def _enable_successful_writes(mock_pool: AsyncMock) -> None:
    mock_pool.execute = AsyncMock(side_effect=_successful_command_tag)


def _authoritative_item_row(item: dict) -> dict:
    metadata = persistence_metadata(
        {**item, "metadata": business_policy_metadata(item.get("metadata"), item)}
    )
    metadata[DECISION_PROVENANCE_KEY] = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.APPROVED,
        workspace_id="workspace-A",
        decision_id=int(item["decision_id"]),
        option_id=item.get("selected_option_id"),
    )
    return {
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "owner_user_id": 7,
        "item_id": item["id"],
        "cartridge_id": item.get("cartridge"),
        "domain": item.get("domain"),
        "source_dataset": item.get("source_dataset"),
        "item_kind": item.get("kind") or item.get("item_kind"),
        "title": item.get("title"),
        "severity": item.get("severity"),
        "status": item.get("status"),
        "decision_id": item.get("decision_id"),
        "entity_kind": item.get("entity_kind"),
        "entity_id": item.get("entity_id"),
        "entity_label": item.get("entity_label"),
        "anomaly_type": item.get("anomaly_type"),
        "metadata": metadata,
        "impact_estimate": item.get("impact_estimate"),
        "impact_currency": item.get("impact_currency"),
        "confidence": item.get("confidence"),
        "priority_score": item.get("priority_score"),
        "selected_option_id": item.get("selected_option_id"),
        "execution_status": item.get("execution_status") or "not_started",
        "first_seen_at": datetime(2026, 5, 20, 9, 0, 0),
        "last_seen_at": datetime(2026, 5, 20, 10, 0, 0),
        "resolved_at": None,
        "dismissed_at": None,
    }


async def finance_fetcher(dataset: str, user: dict | None, _limit: int) -> list[dict]:
    rows = {key: list(value) for key, value in SAMPLE_ROWS.items()}
    rows["pnl_mensual"] = [
        {
            "mes": "2026-05-01",
            "revenue_manager": "RM Norte",
            "proyecto": "P-LOW",
            "project_name": "Omega Low Margin",
            "revenue_usd": 100000,
            "facturacion_mes_usd": 76000,
            "wip_usd": 6000,
            "costo_total": 95000,
            "margen_bruto_usd": 5000,
            "margen_bruto_pct": 5,
        }
    ]
    context = user or USER
    source = next(
        item
        for item in control_room_service._all_sources()  # noqa: SLF001
        if item.dataset == dataset
    )
    scoped = []
    for row in rows[dataset]:
        source_row = {
            **row,
            "tenant_id": context["tenant_id"],
            "workspace_id": context["active_workspace_id"],
        }
        projected = scoped_source_row(
            source_row,
            tenant_id=context["tenant_id"],
            workspace_id=context["active_workspace_id"],
        )
        observed_at = str(source_row.get("mes") or "")
        if observed_at:
            projected.update(
                runtime_row_evidence_fields(
                    source_dataset=dataset,
                    source_system=source.cartridge,
                    cartridge=source.cartridge,
                    tenant_id=context["tenant_id"],
                    workspace_id=context["active_workspace_id"],
                    source_row=source_row,
                    locator_field=source.entity_id_field,
                    observed_at=observed_at,
                    business_observation=projected,
                )
            )
        scoped.append(projected)
    return scoped


def _executed_item(item: dict, *, status: str = "approved") -> dict:
    return control_room_service._with_omega(  # noqa: SLF001
        {
            **item,
            "decision_id": 42,
            "status": status,
            "execution_status": "dry_run_validated",
        }
    )


def _dry_run_action_run_row(item: dict) -> dict:
    return {
        "id": 44,
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "item_id": item["id"],
        "decision_id": item.get("decision_id") or 42,
        "legacy_execution_id": 22,
        "action_type": "create_followup_task",
        "adapter_name": "internal_followup_task",
        "mode": "dry_run",
        "status": "dry_run_completed",
        "risk_level": "low",
        "requires_approval": True,
        "approval_status": "implicit_internal_beta",
        "idempotency_key": f"dry_run:{item['id']}:create_followup_task:42",
        "actor_id": 7,
        "actor_email": "ops@example.com",
        "input": {},
        "dry_run_result": {"ok": True, "validated": True},
        "execution_result": {},
        "side_effect": {},
        "error_code": None,
        "error_message": None,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 10, 1, 0),
        "updated_at": datetime(2026, 5, 20, 10, 1, 1),
        "completed_at": datetime(2026, 5, 20, 10, 1, 1),
    }


def _execution_fetchrow_router(item: dict, *, execution_status: str = "executed"):
    key = effective_action_key(
        workspace_id="workspace-A",
        item=item,
        template_id="create_followup_task",
        operation="execute",
        provided="idem-1",
    )
    contract = {
        "version": 1,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "workspace_id": "workspace-A",
        "item_id": item["id"],
        "fingerprint": business_observation_fingerprint(item),
        "decision_id": item.get("decision_id"),
        "template_id": "create_followup_task",
        "operation": "execute",
        "authorization": execution_authorization_contract(USER),
    }
    pending = {
        "id": 55,
        "status": "pending",
        "idempotency_key": key,
        "metadata": {"reservation_contract": contract},
        "execution_result": {"ok": True, "target": "decision_actions"},
    }

    def route(query: object, *_args: object):
        sql = " ".join(str(query).split()).upper()
        if "FROM CONTROL_ROOM_ITEMS" in sql and "FOR UPDATE" in sql:
            return _authoritative_item_row(item)
        if "MODE = 'DRY_RUN'" in sql:
            return _dry_run_action_run_row(item)
        if sql.startswith("INSERT INTO ACTION_RUNS"):
            return pending
        if "FROM ACTION_RUNS" in sql and "FOR UPDATE" in sql:
            return {**pending, "status": "pending"}
        if sql.startswith("UPDATE ACTION_RUNS"):
            return {**pending, "status": "completed"}
        if "FROM DECISIONS" in sql:
            return {"id": 42}
        if sql.startswith("INSERT INTO DECISION_ACTIONS"):
            return {"id": 101, "decision_id": 42}
        if sql.startswith("INSERT INTO CONTROL_ROOM_ACTION_EXECUTIONS"):
            return {
                "id": 33,
                "workspace_id": "workspace-A",
                "item_id": item["id"],
                "template_id": "create_followup_task",
                "mode": "execute_live",
                "status": execution_status,
                "payload": {"idempotency_key": "idem-1"},
                "result": {"ok": True},
                "error": None,
                "actor_email": "ops@example.com",
                "created_at": datetime(2026, 5, 20, 10, 2, 0),
                "completed_at": datetime(2026, 5, 20, 10, 2, 1),
            }
        return None

    return route


@pytest.mark.asyncio
async def test_execute_live_rejects_resolved_terminal_item(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    base_item = (
        await control_room_service._collect_items(  # noqa: SLF001
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"][0]
    item = _executed_item(base_item, status="resolved")
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_execution_fetchrow_router(item, execution_status="blocked")
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="create_followup_task",
                confirm_execute=True,
                idempotency_key="idem-1",
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 409
    assert audit_event.await_args.kwargs["metadata"]["reason"] == "terminal_item"
