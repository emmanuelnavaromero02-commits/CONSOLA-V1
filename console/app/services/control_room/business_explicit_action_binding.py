from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_action_binding import (
    valid_action_item_id,
    valid_action_template_id,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_evidence_signing import (
    active_evidence_signing_key_id,
    sign_control_room_evidence,
    verify_control_room_evidence,
)
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
)


ACTION_BINDINGS_FIELD = "explicit_action_bindings"
ACTION_BINDING_VERSION = "control-room-action-binding/v1"
ACTION_BINDING_PURPOSE = ACTION_BINDING_VERSION
ACTION_BINDING_PRODUCER = "control_room_action_policy"
_BINDING_KEYS = frozenset(
    {
        "version",
        "policy_version",
        "item_id",
        "template_id",
        "tenant_id",
        "workspace_id",
        "observation_fingerprint",
        "source",
        "provenance",
        "attestation_key_id",
        "binding_id",
    }
)


@dataclass(frozen=True)
class VerifiedActionBinding:
    values: dict[str, Any]

    @property
    def binding_id(self) -> str:
        return str(self.values["binding_id"])

    @property
    def template_id(self) -> str:
        return str(self.values["template_id"])

    def public_values(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.values.items()
            if key != "attestation_key_id"
        }


def _canonical(values: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(values), separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _text(item: Mapping[str, Any], *keys: str) -> str:
    metadata = item.get("metadata")
    for source in (item, metadata if isinstance(metadata, Mapping) else {}):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _binding_payload(item: Mapping[str, Any], template_id: str) -> dict[str, Any]:
    item_id = _text(item, "id", "item_id")
    tenant_id = _text(item, "tenant_id")
    workspace_id = _text(item, "workspace_id")
    dataset = _text(item, "source_dataset", "dataset", "gold_table")
    system = _text(item, "source_system")
    cartridge = _text(item, "cartridge", "cartridge_id")
    template = ACTION_TEMPLATES.get(template_id)
    if not (
        valid_action_item_id(item_id)
        and valid_action_template_id(template_id)
        and tenant_id
        and workspace_id
        and dataset
        and system
        and cartridge
        and template
        and classify_business_item(item).eligible
        and has_evidence(item)
    ):
        raise ValueError("action binding source is incomplete")
    if str(template.get("cartridge_id") or "") not in {"platform", cartridge}:
        raise ValueError("action template does not match the source cartridge")
    return {
        "version": ACTION_BINDING_VERSION,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "item_id": item_id,
        "template_id": template_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "observation_fingerprint": business_observation_fingerprint(item),
        "source": {
            "dataset": dataset,
            "system": system,
            "cartridge": cartridge,
        },
        "provenance": {
            "producer": ACTION_BINDING_PRODUCER,
            "evidence": "verified_business_observation",
        },
    }


def issue_explicit_action_binding(
    item: Mapping[str, Any], *, template_id: str
) -> dict[str, Any]:
    """Issue a binding for an authorized backend producer.

    Presentation and command layers must only verify bindings and never call this
    function while resolving an action.
    """

    payload = _binding_payload(item, template_id)
    key_id = active_evidence_signing_key_id()
    binding_id = sign_control_room_evidence(
        _canonical(payload), purpose=ACTION_BINDING_PURPOSE, key_id=key_id
    )
    return {
        **payload,
        "attestation_key_id": key_id,
        "binding_id": binding_id,
    }


def attach_explicit_action_binding(
    item: Mapping[str, Any], *, template_id: str
) -> dict[str, Any]:
    bound = dict(item)
    metadata = item.get("metadata")
    clean_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    binding = issue_explicit_action_binding(bound, template_id=template_id)
    existing = clean_metadata.get(ACTION_BINDINGS_FIELD)
    values = (
        [dict(value) for value in existing if isinstance(value, Mapping)]
        if isinstance(existing, list)
        else []
    )
    values = [value for value in values if value.get("template_id") != template_id]
    clean_metadata[ACTION_BINDINGS_FIELD] = [*values, binding]
    bound["metadata"] = clean_metadata
    return bound


def verified_explicit_action_bindings(
    item: Mapping[str, Any],
) -> tuple[VerifiedActionBinding, ...]:
    metadata = item.get("metadata")
    raw = metadata.get(ACTION_BINDINGS_FIELD) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, list):
        return ()
    verified: list[VerifiedActionBinding] = []
    for value in raw:
        if not isinstance(value, Mapping) or set(value) != _BINDING_KEYS:
            continue
        template_id = value.get("template_id")
        try:
            expected = _binding_payload(item, str(template_id or ""))
        except ValueError:
            continue
        payload = {key: value.get(key) for key in expected}
        if _canonical(payload) != _canonical(expected):
            continue
        key_id = value.get("attestation_key_id")
        binding_id = value.get("binding_id")
        if not (
            isinstance(key_id, str)
            and isinstance(binding_id, str)
            and verify_control_room_evidence(
                _canonical(expected),
                purpose=ACTION_BINDING_PURPOSE,
                key_id=key_id,
                signature=binding_id,
            )
        ):
            continue
        verified.append(VerifiedActionBinding(dict(value)))
    return tuple(
        sorted(verified, key=lambda value: (value.template_id, value.binding_id))
    )


__all__ = (
    "ACTION_BINDINGS_FIELD",
    "ACTION_BINDING_PRODUCER",
    "ACTION_BINDING_VERSION",
    "VerifiedActionBinding",
    "attach_explicit_action_binding",
    "issue_explicit_action_binding",
    "verified_explicit_action_bindings",
)
