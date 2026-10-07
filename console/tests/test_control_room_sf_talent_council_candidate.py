from __future__ import annotations

from typing import Any

from app.services import control_room_service
from app.services.control_room.business_council_view import (
    _display,
    system_suggestion_match,
)
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_item_persistence import _prepared_rows
from app.services.control_room.business_runtime_evidence import (
    runtime_scope_binding,
    verified_runtime_row_reference,
)
from app.services.control_room.business_source_scope import INVALID_SCOPE_STATUS
from app.services.control_room.business_state_rows import state_rows
from app.services.control_room.business_workflow_quarantine import (
    workflow_columns_unlinked,
)


TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
FOREIGN_WORKSPACE = "33333333-3333-4333-8333-333333333333"
USER = {
    "id": 7,
    "role": "user",
    "tenant_id": TENANT,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "workspace_role": "workspace_admin",
    "allowed_cartridges": ["sap_successfactors"],
}
SIGNAL_ROW = {
    "signal_id": "talent_role_requirements_missing",
    "signal_type": "pipeline",
    "severity": "medium",
    "affected_count": 42,
    "title": "Requisitos de rol pendientes",
    "recommendation": "Validar Position y entidades de skills.",
    "status": "recommendation_only",
    "generated_at": "2026-07-05T03:27:32Z",
    "readiness_status": "partial",
    "source_row_count": 10,
    "materialized_at": "2026-07-05T03:27:32Z",
    "blockers": "[]",
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
}


def _source() -> Any:
    return next(
        source
        for source in control_room_service._all_sources()
        if source.dataset == "sap_successfactors_talent_signals"
    )


def _live_item(**updates: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    control_room_service._append_normalized_source_rows(
        items, _source(), [{**SIGNAL_ROW, **updates}], {}, USER
    )
    (item,) = items
    return item


def _persisted_row(item: dict[str, Any]) -> dict[str, Any]:
    rows = state_rows(
        [item],
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        owner_user_id=USER["id"],
        impact_builder=control_room_service._impact_for_item,
        metadata_builder=control_room_service._metadata_for_item,
        diagnostic_builder=control_room_service._diagnostic_metadata,
    )
    (row,) = _prepared_rows(rows)
    return {"status": "open", "decision_id": None, **row}


def _dataset_row_refs(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ref
        for ref in item.get("evidence_refs") or []
        if isinstance(ref, dict) and ref.get("type") == "dataset_row"
    ]


def test_scoped_talent_signal_carries_workspace_and_signed_row_evidence():
    item = _live_item()

    assert (item["tenant_id"], item["workspace_id"]) == (TENANT, WORKSPACE)
    assert item["data_status"] == "gold_ready"
    (ref,) = _dataset_row_refs(item)
    assert ref["scope_binding"] == runtime_scope_binding(TENANT, WORKSPACE)
    assert ref["source_record_id"] == "record-talent_role_requirements_missing"
    assert verified_runtime_row_reference(ref)
    assert classify_business_item(item).eligible
    assert has_evidence(item)

    legacy_only = {**item, "evidence_refs": []}
    assert not has_evidence(legacy_only)
    assert (
        classify_business_item(legacy_only).reason
        == EligibilityReason.MISSING_LINEAGE
    )


def test_persisted_talent_signal_is_business_row_without_preselected_option():
    item = _live_item()
    row = _persisted_row(item)

    assert "selected_option_id" not in item
    assert [option["option_id"] for option in item["intelligence"]["options"]] == [
        "review_talent_signal",
        "monitor_talent_signal",
    ]
    assert row["selected_option_id"] is None
    assert row["metadata"]["data_status"] == "gold_ready"
    assert row["metadata"]["business_eligibility_fingerprint"]
    assert (row["tenant_id"], row["workspace_id"]) == (TENANT, WORKSPACE)
    assert workflow_columns_unlinked(row)

    refreshed = control_room_service._with_omega(item)
    assert refreshed["selected_option_id"] is None
    assert refreshed["omega"]["options"]
    assert not any(option["selected"] for option in refreshed["omega"]["options"])
    refreshed_row = _persisted_row(refreshed)
    assert refreshed_row["selected_option_id"] is None
    assert workflow_columns_unlinked(refreshed_row)
    assert (
        system_suggestion_match(
            item, refreshed_row, tenant_id=TENANT, workspace_id=WORKSPACE
        )
        is not None
    )


def test_talent_signal_becomes_system_council_candidate_with_title():
    item = _live_item()
    row = _persisted_row(item)

    match = system_suggestion_match(item, row, tenant_id=TENANT, workspace_id=WORKSPACE)
    assert match is not None
    fingerprint, digest = match
    assert fingerprint == row["metadata"]["business_eligibility_fingerprint"]
    assert digest
    display = _display(item, None)
    assert display is not None
    assert display["title"] == "Requisitos de rol pendientes"
    assert display["severity"] == "medium"

    chosen = {**row, "selected_option_id": "review_talent_signal"}
    assert (
        system_suggestion_match(item, chosen, tenant_id=TENANT, workspace_id=WORKSPACE)
        is None
    )


def test_foreign_workspace_row_fails_closed_as_invalid_scope():
    for updates in ({"workspace_id": FOREIGN_WORKSPACE}, {"tenant_id": None, "workspace_id": None}):
        item = _live_item(**updates)

        assert item["data_status"] == INVALID_SCOPE_STATUS
        assert item["details"]["scope_validation"] == "source_scope_mismatch"
        assert _dataset_row_refs(item) == []
        assert not has_evidence(item)
        verdict = classify_business_item(item)
        assert not verdict.eligible
        assert verdict.reason == EligibilityReason.TECHNICAL_STATE
        row = _persisted_row(item)
        assert row["metadata"]["data_status"] == INVALID_SCOPE_STATUS
        assert (
            system_suggestion_match(
                item, row, tenant_id=TENANT, workspace_id=WORKSPACE
            )
            is None
        )


def test_without_signing_key_the_scoped_signal_is_not_business_eligible(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", raising=False)
    item = _live_item()

    assert (item["tenant_id"], item["workspace_id"]) == (TENANT, WORKSPACE)
    assert _dataset_row_refs(item) == []
    assert not has_evidence(item)
    verdict = classify_business_item(item)
    assert not verdict.eligible
    assert verdict.reason == EligibilityReason.MISSING_LINEAGE
    row = _persisted_row(item)
    assert (
        system_suggestion_match(item, row, tenant_id=TENANT, workspace_id=WORKSPACE)
        is None
    )
