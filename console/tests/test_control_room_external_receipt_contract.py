from __future__ import annotations

from copy import deepcopy

from app.services.control_room.business_action_replay import action_reservation_contract
from app.services.control_room.business_external_receipt_contract import (
    receipt_contract_matches,
    reservation_authority_audit_valid,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_eligibility_provenance,
)
from control_room_surface_fixtures import business_item


def _item(entity_id: str = "employee-a") -> dict:
    return business_item(
        entity_id=entity_id,
        decision_id=42,
        status="approved",
        execution_status="dry_run_validated",
        metadata={
            "connection": {
                "base_url": "https://hcm.example",
                "writeback_path": "/review/a",
            }
        },
    )


def _authorization() -> dict:
    return {
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "effective_permissions": ["control_room.write", "control_room.execute"],
    }


def _persisted_row(item: dict) -> dict:
    metadata = {
        **dict(item.get("metadata") or {}),
        **{
            key: item[key]
            for key in (
                "data_status",
                "detected_at",
                "evidence_refs",
                "metric_type",
                "observed_value",
                "population_count",
            )
            if key in item
        },
        DECISION_PROVENANCE_KEY: workflow_eligibility_provenance(
            item,
            stage=WorkflowStage.APPROVED,
            workspace_id=str(item["workspace_id"]),
            decision_id=int(item["decision_id"]),
        ),
    }
    fields = {
        "owner_user_id",
        "domain",
        "source_dataset",
        "title",
        "severity",
        "status",
        "decision_id",
        "entity_kind",
        "entity_id",
        "entity_label",
        "anomaly_type",
        "impact_estimate",
        "impact_currency",
        "confidence",
        "priority_score",
        "selected_option_id",
        "execution_status",
        "first_seen_at",
        "last_seen_at",
        "resolved_at",
        "dismissed_at",
    }
    return {
        **{key: item.get(key) for key in fields},
        "tenant_id": item["tenant_id"],
        "workspace_id": item["workspace_id"],
        "item_id": item["id"],
        "item_kind": item["kind"],
        "cartridge_id": item["cartridge"],
        "metadata": metadata,
    }


def _contract(item: dict, payload: dict | None = None) -> dict:
    return action_reservation_contract(
        workspace_id=str(item["workspace_id"]),
        item=item,
        template_id="prepare_hcm_org_review",
        operation="execute",
        authorization_contract=_authorization(),
        input_payload=payload,
    )


def _receipt(item: dict, contract: dict, payload: dict | None = None) -> dict:
    authority_audit = {
        "version": "control-room-authority-audit/v1",
        "binding_id": "signed-binding-id",
        "key_id": "active-key-id",
        "issued_at": "2026-07-26T12:00:00Z",
        "expires_at": "2026-07-26T12:15:00Z",
        "observation_fingerprint": contract["fingerprint"],
        "execution_target_digest": contract["execution_target_digest"],
        "template_contract_digest": contract["template_contract_digest"],
        "input_payload_digest": contract["input_payload_digest"],
    }
    return {
        "item_id": item["id"],
        "decision_id": item["decision_id"],
        "action_type": contract["template_id"],
        "input": payload or {},
        "metadata": {
            "reservation_contract": contract,
            "authority_audit": authority_audit,
        },
        "execution_result": {
            "executed": True,
            "local_projection_status": "pending_reconciliation",
        },
    }


def test_reconciliation_rejects_changed_target_with_same_observation():
    original = _item()
    changed = deepcopy(original)
    changed["metadata"]["connection"]["writeback_path"] = "/review/b"

    assert business_observation_fingerprint(
        original
    ) == business_observation_fingerprint(changed)
    assert not receipt_contract_matches(
        _receipt(changed, _contract(original)),
        _persisted_row(changed),
        str(changed["workspace_id"]),
    )


def test_reconciliation_restores_signed_transient_payload_context():
    item = _item()
    item["threshold_state"] = "critical"
    payload = {"impact": {"priority_score": 92}}
    contract = _contract(item, payload)
    persisted = _persisted_row(item)

    assert "threshold_state" not in persisted
    assert receipt_contract_matches(
        _receipt(item, contract, payload),
        persisted,
        str(item["workspace_id"]),
    )


def test_reconciliation_rejects_changed_input_payload():
    item = _item()
    contract = _contract(item, {"impact": {"priority_score": 10}})

    assert not receipt_contract_matches(
        _receipt(item, contract, {"impact": {"priority_score": 11}}),
        _persisted_row(item),
        str(item["workspace_id"]),
    )


def test_reconciliation_rejects_changed_template_contract_digest():
    item = _item()
    contract = _contract(item)
    contract["template_contract_digest"] = "0" * 64

    assert not receipt_contract_matches(
        _receipt(item, contract),
        _persisted_row(item),
        str(item["workspace_id"]),
    )


def test_reconciliation_rejects_missing_or_changed_authority_audit():
    item = _item()
    contract = _contract(item)
    receipt = _receipt(item, contract)
    receipt["metadata"].pop("authority_audit")
    assert not receipt_contract_matches(
        receipt, _persisted_row(item), str(item["workspace_id"])
    )


def test_receipt_production_revalidates_stored_authority_audit():
    item = _item()
    contract = _contract(item)
    receipt = _receipt(item, contract)
    expected = receipt["metadata"]["authority_audit"]
    assert reservation_authority_audit_valid(receipt, expected)
    assert not reservation_authority_audit_valid(
        receipt, {**expected, "key_id": "other-key"}
    )

    receipt = _receipt(item, contract)
    receipt["metadata"]["authority_audit"]["binding_id"] = ""
    assert not receipt_contract_matches(
        receipt, _persisted_row(item), str(item["workspace_id"])
    )


def test_completed_reconciliation_requires_executed_provenance():
    item = _item()
    contract = _contract(item)
    receipt = _receipt(item, contract)
    receipt["execution_result"]["local_projection_status"] = "completed"
    persisted = _persisted_row(item)
    persisted["execution_status"] = "executed"
    persisted["metadata"][DECISION_PROVENANCE_KEY] = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.EXECUTED,
        workspace_id=str(item["workspace_id"]),
        decision_id=int(item["decision_id"]),
    )

    assert receipt_contract_matches(receipt, persisted, str(item["workspace_id"]))
    receipt["execution_result"]["local_projection_status"] = "pending_reconciliation"
    assert not receipt_contract_matches(
        receipt,
        persisted,
        str(item["workspace_id"]),
    )
