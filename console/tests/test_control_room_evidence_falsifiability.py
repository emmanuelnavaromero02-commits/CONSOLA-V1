from __future__ import annotations

import pytest

from app.services import control_room_service
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


def _item(**overrides):
    return {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap_successfactors",
        "cartridge": "sap_successfactors",
        "tenant_id": "tenant-11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        **overrides,
    }


@pytest.mark.parametrize(
    "reference",
    [
        "looks plausible",
        "note:parece-valido",
        "arbitrary/evidence-17",
    ],
)
def test_narrative_and_arbitrary_scalar_references_are_rejected(reference):
    assert has_evidence(_item(evidence_refs=[reference])) is False


@pytest.mark.parametrize(
    "reference",
    [
        "gold_metrics:22222222-2222-2222-2222-222222222222",
        "gold_metrics/tenant-11111111-1111-1111-1111-111111111111",
        "s3://bucket/22222222-2222-2222-2222-222222222222",
    ],
)
def test_prefixed_or_path_admin_ids_are_not_evidence(reference):
    assert has_evidence(_item(evidence_refs=[reference])) is False


def test_structured_evidence_source_must_match_item_source():
    evidence = {
        "type": "dataset_row",
        "source_dataset": "gold_other",
        "source_record_id": "record-17",
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


def test_typed_evidence_source_cannot_match_by_prefix():
    evidence = {
        "type": "dataset_row",
        "source_dataset": "gold_metrics:forged",
        "source_record_id": "record-17",
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


def test_untyped_structured_evidence_source_cannot_match_by_prefix():
    evidence = {
        "source_dataset": "gold_metrics:forged",
        "source_record_id": "record-17",
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


def test_short_administrative_id_cannot_be_embedded_as_record_evidence():
    evidence = {
        "type": "dataset_row",
        "source_dataset": "gold_metrics",
        "source_record_id": "record-7",
    }

    assert has_evidence(_item(owner_user_id=7, evidence_refs=[evidence])) is False


@pytest.mark.parametrize(
    "reference",
    [
        "https://evil.example/fabricated/17",
        "s3://unrelated-bucket/fabricated/17",
        "gs://unrelated-bucket/fabricated/17",
    ],
)
def test_unbound_external_uri_is_not_evidence(reference):
    assert has_evidence(_item(evidence_refs=[reference])) is False


def test_unknown_structured_evidence_type_is_rejected():
    evidence = {
        "type": "narrative_note",
        "source_dataset": "gold_metrics",
        "source_record_id": "record-17",
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


def test_fabricated_source_record_id_is_not_server_verified_evidence():
    evidence = {
        "type": "dataset_row",
        "source_dataset": "gold_metrics",
        "source_record_id": f"record-{'0' * 64}",
        "observed_at": "2026-07-20T10:00:00Z",
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {"type": "dataset_row", "source_dataset": "gold_metrics"},
        {
            "type": "dataset_row",
            "source_dataset": "gold_metrics",
            "source_record_id": "N/A",
        },
    ],
)
def test_empty_incomplete_or_placeholder_evidence_is_rejected(evidence):
    assert has_evidence(_item(evidence_refs=[evidence])) is False


def test_runtime_evidence_is_typed_source_bound_and_accepted():
    source_row = {
        "employee_id": "employee-17",
        "anomaly_type": "anomaly",
        "detected_at": "2026-07-20T10:00:00Z",
    }
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row=source_row,
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )

    reference = fields["evidence_refs"][0]
    assert reference["source_record_id"] == "record-employee-17"
    assert reference["source_locator"] == {
        "relation": "gold_metrics",
        "field": "employee_id",
        "value": "employee-17",
    }
    assert reference["source_row_hash"]
    assert reference["server_attestation"]
    assert has_evidence(_item(**fields)) is True


def test_runtime_attestation_cannot_be_reused_for_a_fabricated_record_id():
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row={"employee_id": "employee-17"},
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )
    reference = fields["evidence_refs"][0]
    forged = {**reference, "source_record_id": "employee-forged"}

    assert has_evidence(_item(evidence_refs=[forged])) is False


def test_runtime_attestation_cannot_be_rebound_by_unsigned_source_alias():
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row={"employee_id": "employee-17"},
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )
    reference = {**fields["evidence_refs"][0], "dataset": "gold_other"}

    assert (
        has_evidence(_item(source_dataset="gold_other", evidence_refs=[reference]))
        is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant-other"),
        ("workspace_id", "33333333-3333-3333-3333-333333333333"),
        ("source_system", "other-system"),
        ("cartridge", "other-cartridge"),
    ],
)
def test_runtime_attestation_cannot_cross_context_with_same_dataset(field, value):
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row={"employee_id": "employee-17"},
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )

    assert (
        has_evidence(
            _item(
                **{field: value},
                evidence_refs=fields["evidence_refs"],
            )
        )
        is False
    )


def test_missing_signing_key_fails_closed_without_breaking_runtime_reads(monkeypatch):
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row={"employee_id": "employee-17"},
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", raising=False)

    assert has_evidence(_item(**fields)) is False

    source = control_room_service.ControlRoomSource(
        dataset="gold_metrics",
        cartridge="sap_successfactors",
        domain="People",
        module_label="Workforce",
        entity_kind="Employee",
        entity_id_field="employee_id",
        entity_label_field="employee_name",
    )
    item = control_room_service._base_item(
        source,
        {
            "employee_id": "employee-17",
            "employee_name": "Employee 17",
            "detected_at": "2026-07-20T10:00:00Z",
        },
        "anomaly",
        "employee-17",
        "Employee 17",
    )

    assert item.get("evidence_refs") in (None, [])
    assert has_evidence(item) is False


def test_matching_legacy_scalar_reference_remains_narrowly_compatible():
    assert (
        has_evidence(
            _item(
                tenant_id=None,
                workspace_id=None,
                evidence_refs=["gold_metrics:record-17"],
            )
        )
        is True
    )


def test_legacy_scalar_source_must_match_item_source():
    assert has_evidence(_item(evidence_refs=["gold_other:record-17"])) is False
