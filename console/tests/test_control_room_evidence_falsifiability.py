from __future__ import annotations

import pytest

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
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        entity_id="employee-17",
        item_type="anomaly",
        observed_at="2026-07-20T10:00:00Z",
    )

    reference = fields["evidence_refs"][0]
    assert reference == {
        "type": "dataset_row",
        "source_dataset": "gold_metrics",
        "source_record_id": reference["source_record_id"],
        "observed_at": "2026-07-20T10:00:00Z",
    }
    assert reference["source_record_id"].startswith("record-")
    assert has_evidence(_item(**fields)) is True


def test_matching_legacy_scalar_reference_remains_narrowly_compatible():
    assert has_evidence(_item(evidence_refs=["gold_metrics:record-17"])) is True


def test_legacy_scalar_source_must_match_item_source():
    assert has_evidence(_item(evidence_refs=["gold_other:record-17"])) is False
