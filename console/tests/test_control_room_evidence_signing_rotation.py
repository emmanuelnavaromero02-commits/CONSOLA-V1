from __future__ import annotations

import json

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
    verified_runtime_row_reference,
)


CURRENT_KEY = "control-room-current-evidence-key-material-0001"
PREVIOUS_KEY = "control-room-previous-evidence-key-material-0001"
SECURITY_KEY = "shared-security-context-key-material-00000001"


def _configure_current(monkeypatch, *, key_id: str, key: str) -> None:
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", key_id)
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", key)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS", raising=False)


def _reference() -> dict:
    fields = runtime_row_evidence_fields(
        source_dataset="gold_people",
        source_system="sap_hcm",
        cartridge="sap_hcm",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        source_row={"item_id": "business-1"},
        locator_field="item_id",
        observed_at="2026-07-22T10:00:00Z",
    )
    return fields.get("evidence_refs", [{}])[-1]


def test_dedicated_key_and_key_id_are_required_fail_closed(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SECURITY_KEY)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", raising=False)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS", raising=False)

    assert _reference() == {}

    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", CURRENT_KEY)
    assert _reference() == {}


def test_short_or_shared_dedicated_key_fails_closed(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SECURITY_KEY)
    _configure_current(monkeypatch, key_id="current", key="too-short")
    assert _reference() == {}

    _configure_current(monkeypatch, key_id="current", key=SECURITY_KEY)
    assert _reference() == {}


def test_attestation_signs_purpose_and_active_key_id(monkeypatch):
    _configure_current(monkeypatch, key_id="evidence-2026-07", key=CURRENT_KEY)

    reference = _reference()

    assert reference["attestation_key_id"] == "evidence-2026-07"
    assert reference["attestation_purpose"] == "control-room-runtime-evidence-v1"
    assert verified_runtime_row_reference(reference) is True
    assert (
        verified_runtime_row_reference(
            {**reference, "attestation_key_id": "evidence-forged"}
        )
        is False
    )
    assert (
        verified_runtime_row_reference(
            {**reference, "attestation_purpose": "security-context"}
        )
        is False
    )


def test_previous_key_rotation_and_removal(monkeypatch):
    _configure_current(monkeypatch, key_id="evidence-old", key=PREVIOUS_KEY)
    old_reference = _reference()

    _configure_current(monkeypatch, key_id="evidence-current", key=CURRENT_KEY)
    monkeypatch.setenv(
        "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS",
        json.dumps({"evidence-old": PREVIOUS_KEY}),
    )
    current_reference = _reference()

    assert current_reference["attestation_key_id"] == "evidence-current"
    assert verified_runtime_row_reference(old_reference) is True
    assert verified_runtime_row_reference(current_reference) is True

    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS")
    assert verified_runtime_row_reference(old_reference) is False
    assert verified_runtime_row_reference(current_reference) is True


def test_security_context_key_alone_cannot_fabricate_evidence(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SECURITY_KEY)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", raising=False)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS", raising=False)

    fabricated = _reference()

    assert fabricated == {}
