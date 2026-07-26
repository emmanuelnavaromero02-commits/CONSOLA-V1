from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.control_room.business_action_binding import valid_action_template_id
from app.services.control_room.business_action_capability import (
    business_experience_template_allowed,
)
from app.services.control_room.business_action_digest import canonical_action_bytes
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_evidence_signing import (
    active_evidence_signing_key_id,
    sign_control_room_evidence,
    verify_control_room_evidence,
)
from app.services.control_room.business_execution_target import execution_target_digest
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from app.services.control_room.business_template_contract import (
    template_contract_digest,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
)


Clock = Callable[[], datetime]
ACTION_BINDINGS_FIELD = "explicit_action_bindings"
ACTION_BINDING_VERSION = "control-room-action-binding/v2"
ACTION_BINDING_PURPOSE = ACTION_BINDING_VERSION
ACTION_BINDING_PRODUCER = "control_room_action_policy"
ACTION_BINDING_TTL_SECONDS = 900
MAX_EXPLICIT_ACTION_BINDINGS = 8
_BINDING_KEYS = frozenset(
    {
        "version",
        "policy_version",
        "item_id",
        "template_id",
        "tenant_id",
        "workspace_id",
        "observation_fingerprint",
        "execution_target_digest",
        "template_contract_digest",
        "issued_at",
        "expires_at",
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

    @property
    def execution_target_digest(self) -> str:
        return str(self.values["execution_target_digest"])

    @property
    def template_contract_digest(self) -> str:
        return str(self.values["template_contract_digest"])

    @property
    def expires_at(self) -> str:
        return str(self.values["expires_at"])


def _now(clock: Clock | None) -> datetime:
    value = clock() if clock is not None else datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("action binding clock must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _valid_window(value: Mapping[str, Any], *, now: datetime) -> bool:
    issued_at = _parse_timestamp(value.get("issued_at"))
    expires_at = _parse_timestamp(value.get("expires_at"))
    return bool(
        issued_at
        and expires_at
        and issued_at <= now < expires_at
        and expires_at - issued_at == timedelta(seconds=ACTION_BINDING_TTL_SECONDS)
    )


def _text(item: Mapping[str, Any], *keys: str) -> str:
    metadata = item.get("metadata")
    for source in (item, metadata if isinstance(metadata, Mapping) else {}):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _item_id(item: Mapping[str, Any]) -> str:
    value = item.get("id") or item.get("item_id")
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("action binding item id is invalid")
    return value


def _binding_payload(
    item: Mapping[str, Any],
    template_id: str,
    *,
    issued_at: str,
    expires_at: str,
) -> dict[str, Any]:
    item_id = _item_id(item)
    for field in ("entity_kind", "entity_id"):
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"action binding source is missing {field}")
    tenant_id = _text(item, "tenant_id")
    workspace_id = _text(item, "workspace_id")
    dataset = _text(item, "source_dataset", "dataset", "gold_table")
    system = _text(item, "source_system")
    cartridge = _text(item, "cartridge", "cartridge_id")
    template = ACTION_TEMPLATES.get(template_id)
    if not (
        valid_action_template_id(template_id)
        and tenant_id
        and workspace_id
        and dataset
        and system
        and cartridge
        and template
        and classify_business_item(item).eligible
        and has_evidence(item)
        and business_experience_template_allowed(
            source_cartridge=cartridge, template=template
        )
    ):
        raise ValueError("action binding source is incomplete")
    return {
        "version": ACTION_BINDING_VERSION,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "item_id": item_id,
        "template_id": template_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "observation_fingerprint": business_observation_fingerprint(item),
        "execution_target_digest": execution_target_digest(item, template),
        "template_contract_digest": template_contract_digest(template),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "source": {"dataset": dataset, "system": system, "cartridge": cartridge},
        "provenance": {
            "producer": ACTION_BINDING_PRODUCER,
            "evidence": "verified_business_observation",
        },
    }


def issue_explicit_action_binding(
    item: Mapping[str, Any], *, template_id: str, clock: Clock | None = None
) -> dict[str, Any]:
    """Issue one server-owned binding; presentation code only verifies it."""

    issued = _now(clock)
    payload = _binding_payload(
        item,
        template_id,
        issued_at=_timestamp(issued),
        expires_at=_timestamp(issued + timedelta(seconds=ACTION_BINDING_TTL_SECONDS)),
    )
    key_id = active_evidence_signing_key_id()
    binding_id = sign_control_room_evidence(
        canonical_action_bytes(payload), purpose=ACTION_BINDING_PURPOSE, key_id=key_id
    )
    return {**payload, "attestation_key_id": key_id, "binding_id": binding_id}


def attach_explicit_action_binding(
    item: Mapping[str, Any], *, template_id: str, clock: Clock | None = None
) -> dict[str, Any]:
    bound = dict(item)
    metadata = item.get("metadata")
    clean_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    existing = clean_metadata.get(ACTION_BINDINGS_FIELD)
    values = (
        [dict(value) for value in existing if isinstance(value, Mapping)]
        if isinstance(existing, list)
        else []
    )
    values = [value for value in values if value.get("template_id") != template_id]
    if len(values) >= MAX_EXPLICIT_ACTION_BINDINGS:
        raise ValueError("explicit action binding limit exceeded")
    binding = issue_explicit_action_binding(bound, template_id=template_id, clock=clock)
    clean_metadata[ACTION_BINDINGS_FIELD] = [*values, binding]
    bound["metadata"] = clean_metadata
    return bound


def verified_explicit_action_bindings(
    item: Mapping[str, Any], *, clock: Clock | None = None
) -> tuple[VerifiedActionBinding, ...]:
    metadata = item.get("metadata")
    raw = metadata.get(ACTION_BINDINGS_FIELD) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, list) or len(raw) > MAX_EXPLICIT_ACTION_BINDINGS:
        return ()
    now = _now(clock)
    verified: list[VerifiedActionBinding] = []
    for value in raw:
        if (
            not isinstance(value, Mapping)
            or set(value) != _BINDING_KEYS
            or not _valid_window(value, now=now)
        ):
            continue
        try:
            expected = _binding_payload(
                item,
                str(value.get("template_id") or ""),
                issued_at=str(value.get("issued_at") or ""),
                expires_at=str(value.get("expires_at") or ""),
            )
        except ValueError:
            continue
        payload = {key: value.get(key) for key in expected}
        key_id = value.get("attestation_key_id")
        signature = value.get("binding_id")
        if canonical_action_bytes(payload) != canonical_action_bytes(expected):
            continue
        if not (
            isinstance(key_id, str)
            and isinstance(signature, str)
            and verify_control_room_evidence(
                canonical_action_bytes(expected),
                purpose=ACTION_BINDING_PURPOSE,
                key_id=key_id,
                signature=signature,
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
    "ACTION_BINDING_TTL_SECONDS",
    "ACTION_BINDING_VERSION",
    "MAX_EXPLICIT_ACTION_BINDINGS",
    "VerifiedActionBinding",
    "attach_explicit_action_binding",
    "issue_explicit_action_binding",
    "verified_explicit_action_bindings",
)
