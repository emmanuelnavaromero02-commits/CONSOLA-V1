from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.control_room.business_runtime_evidence import (
    canonical_runtime_row_reference,
    runtime_scope_binding,
)
from app.services.control_room.business_source_scope import (
    scoped_runtime_evidence_fields,
    scoped_source_row,
)
from app.services.db_scope import scoped_db

logger = logging.getLogger(__name__)

TICKET_TTL_SECONDS = 86_400

SCHEDULED_CONTEXT_SOURCE = "agent_runner"
MONITOR_ITEM_KIND = "agent_alert"
MONITOR_METRIC_TYPE = "count"
MONITOR_METRIC_NAME = "Senales del monitor"
LOCATOR_FIELD = "item_id"
DEFAULT_MONITOR_WISDOM_BIT = "WB-TALENTO"
MAX_RESOLVE_ITEMS = 200
MAX_TICKETS_PER_ITEM = 3
_HANDLE_BYTES = 16

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/=-]{1,240}$")

EVIDENCE_EVENTS = frozenset(
    {
        "minted",
        "resolved",
        "not_scheduled",
        "lease_missing",
        "lease_invalid",
        "duplicate_lease",
        "not_monitor",
        "identity_invalid",
        "no_signal",
        "scope_invalid",
        "sign_failed",
        "binding_mismatch",
        "unavailable",
    }
)
_QUIET_EVENTS = frozenset({"minted", "resolved"})

_COUNTERS: Counter[str] = Counter()


class _Refused(Exception):

    def __init__(self, reason: str, **fields: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


def _record(event: str, **fields: object) -> None:
    _COUNTERS[event] += 1
    detail = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
    level = logging.INFO if event in _QUIET_EVENTS else logging.WARNING
    logger.log(level, "control_room.evidence.%s %s", event, detail)


def record_evidence_event(event: str, **fields: object) -> None:
    _record(event if event in EVIDENCE_EVENTS else "unavailable", **fields)


def evidence_counters() -> dict[str, int]:
    return {event: _COUNTERS[event] for event in sorted(EVIDENCE_EVENTS)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _uuid_text(value: Any) -> str | None:
    try:
        return str(uuid.UUID(_text(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class MonitorAlertIdentity:
    item_id: str
    alert_type: str
    entity_key: str
    source_dataset: str
    wisdom_bit_id: str


def monitor_alert_item_id(
    *,
    agent_id: str,
    workspace_id: str,
    alert_type: str,
    entity_key: str,
    source_dataset: str,
) -> str:
    raw = json.dumps(
        {
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "alert_type": alert_type,
            "entity_key": entity_key,
            "source_dataset": source_dataset,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"agent_alert:{digest}"


def monitor_alert_identity(
    *,
    agent_id: str,
    workspace_id: str,
    cartridge_id: str,
    slug: str,
    contract: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> MonitorAlertIdentity | None:
    wisdom_bit_id = str(
        contract.get("wisdom_bit_id") or payload.get("wisdom_bit_id") or "wisdom_bit"
    )
    alert_type = str(contract.get("alert_type") or "wisdombit_monitor").strip()
    source_dataset = str(contract.get("dataset") or "agent_monitor").strip()
    entity_key = str(
        contract.get("dedup_key") or f"{cartridge_id}:{slug}:{wisdom_bit_id}"
    ).strip()
    if not all(
        _SAFE_ID_RE.fullmatch(value) for value in (alert_type, source_dataset, entity_key)
    ):
        return None
    return MonitorAlertIdentity(
        item_id=monitor_alert_item_id(
            agent_id=_text(agent_id),
            workspace_id=_text(workspace_id),
            alert_type=alert_type,
            entity_key=entity_key,
            source_dataset=source_dataset,
        ),
        alert_type=alert_type,
        entity_key=entity_key,
        source_dataset=source_dataset,
        wisdom_bit_id=wisdom_bit_id,
    )


def monitor_signal_count(payload: Mapping[str, Any]) -> int:
    signals = payload.get("signals")
    if isinstance(signals, Mapping):
        try:
            return int(signals.get("count") or len(signals.get("items") or []))
        except Exception:  # noqa: BLE001 - mirrors the runtime's own fallback
            return 0
    if isinstance(signals, list):
        return len(signals)
    return 0


def monitor_business_observation(
    *, item_id: str, signal_count: int, observation_date: str
) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": MONITOR_ITEM_KIND,
        "metric_name": MONITOR_METRIC_NAME,
        "metric_type": MONITOR_METRIC_TYPE,
        "observed_value": signal_count,
        "observation_date": observation_date,
    }


_LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '2s'"

_ASSERT_LEASE_SQL = (
    "SELECT assert_scheduled_effect_authority("
    "$1::bigint, $2::bigint, $3::uuid, $4::uuid, $5::uuid)"
)

_LOAD_AGENT_SQL = """
SELECT cartridge_id, slug, extra
  FROM agents
 WHERE id = $1::uuid
   AND tenant_id = $2::uuid
   AND workspace_id = $3::uuid
   AND is_active = TRUE
"""

_INSERT_TICKET_SQL = """
INSERT INTO control_room_evidence_tickets (
    handle, tenant_id, workspace_id, item_id,
    agent_id, agent_run_id, schedule_run_id, fencing_token,
    internal_service, security_context_source,
    wisdom_bit_id, source_dataset, source_system, cartridge,
    source_record_id, locator_field, locator_value, source_row_hash,
    attestation_key_id, business_binding_fingerprint,
    reference, expires_at
)
VALUES (
    $1, $2::uuid, $3::uuid, $4,
    $5::uuid, $6, $7, $8,
    $9, $10,
    $11, $12, $13, $14,
    $15, $16, $17, $18,
    $19, $20,
    $21::jsonb, clock_timestamp() + make_interval(secs => $22)
)
ON CONFLICT (schedule_run_id, fencing_token) DO NOTHING
RETURNING handle
"""


async def _mint(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    agent_run_id: str | None,
    schedule_run_id: int,
    fencing_token: int,
    internal_service: str,
    requested_wisdom_bit_id: str,
    requested_cartridge_id: str,
    payload: Mapping[str, Any],
) -> tuple[str, str, str]:
    await conn.execute(_LOCK_TIMEOUT_SQL)
    try:
        await conn.execute(
            _ASSERT_LEASE_SQL,
            schedule_run_id,
            fencing_token,
            tenant_id,
            workspace_id,
            agent_id,
        )
    except Exception as exc:  # noqa: BLE001 - stale lease, wrong token, no run
        raise _Refused("lease_invalid", error_code=type(exc).__name__) from None

    agent = await conn.fetchrow(_LOAD_AGENT_SQL, agent_id, tenant_id, workspace_id)
    if agent is None:
        raise _Refused("not_monitor")
    contract = _json_mapping(_json_mapping(agent["extra"]).get("monitor"))
    if not contract:
        raise _Refused("not_monitor")
    cartridge_id = _text(agent["cartridge_id"])
    expected_wisdom_bit = _text(
        contract.get("wisdom_bit_id") or DEFAULT_MONITOR_WISDOM_BIT
    ).upper()
    if _text(requested_wisdom_bit_id).upper() != expected_wisdom_bit:
        raise _Refused("identity_invalid", mismatch="wisdom_bit")
    if _text(requested_cartridge_id).replace("-", "_") != cartridge_id.replace("-", "_"):
        raise _Refused("identity_invalid", mismatch="cartridge")
    identity = monitor_alert_identity(
        agent_id=agent_id,
        workspace_id=workspace_id,
        cartridge_id=cartridge_id,
        slug=_text(agent["slug"]),
        contract=contract,
        payload=payload,
    )
    if identity is None or not cartridge_id:
        raise _Refused("identity_invalid", mismatch="item")
    signal_count = monitor_signal_count(payload)
    if signal_count <= 0:
        raise _Refused("no_signal", item_id=identity.item_id)

    observation_date = datetime.now(UTC).date().isoformat()
    observation = monitor_business_observation(
        item_id=identity.item_id,
        signal_count=signal_count,
        observation_date=observation_date,
    )
    row = scoped_source_row(
        {
            **observation,
            LOCATOR_FIELD: identity.item_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "wisdom_bit_id": identity.wisdom_bit_id,
        },
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not row.scope_valid:
        raise _Refused("scope_invalid", item_id=identity.item_id)
    fields = scoped_runtime_evidence_fields(
        row,
        source_dataset=identity.source_dataset,
        source_system=cartridge_id,
        cartridge=cartridge_id,
        locator_field=LOCATOR_FIELD,
        observed_at=observation_date,
        business_observation=observation,
    )
    refs = fields.get("evidence_refs")
    reference = refs[-1] if isinstance(refs, Sequence) and refs else None
    if not isinstance(reference, Mapping):
        raise _Refused("sign_failed", item_id=identity.item_id)

    locator = _json_mapping(reference.get("source_locator"))
    binding = _json_mapping(reference.get("business_binding"))
    key_id = _text(reference.get("attestation_key_id"))
    handle = await conn.fetchval(
        _INSERT_TICKET_SQL,
        secrets.token_hex(_HANDLE_BYTES),
        tenant_id,
        workspace_id,
        identity.item_id,
        agent_id,
        agent_run_id,
        schedule_run_id,
        fencing_token,
        internal_service,
        SCHEDULED_CONTEXT_SOURCE,
        identity.wisdom_bit_id,
        identity.source_dataset,
        cartridge_id,
        cartridge_id,
        _text(reference.get("source_record_id")),
        _text(locator.get("field")),
        _text(locator.get("value")),
        _text(reference.get("source_row_hash")),
        key_id,
        _text(binding.get("fingerprint")),
        json.dumps(dict(reference), ensure_ascii=False, sort_keys=True),
        float(TICKET_TTL_SECONDS),
    )
    if not handle:
        raise _Refused("duplicate_lease", item_id=identity.item_id)
    return str(handle), identity.item_id, key_id


async def mint_monitor_evidence_ticket(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: Any,
    agent_run_id: Any,
    schedule_run_id: int,
    fencing_token: int,
    internal_service: str,
    security_context_source: str,
    requested_wisdom_bit_id: str,
    requested_cartridge_id: str,
    payload: Mapping[str, Any],
) -> str | None:
    tenant = _uuid_text(tenant_id)
    workspace = _uuid_text(workspace_id)
    agent = _uuid_text(agent_id)
    base = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "agent_id": agent,
        "schedule_run_id": schedule_run_id,
    }
    if security_context_source != SCHEDULED_CONTEXT_SOURCE:
        _record("not_scheduled", **base)
        return None
    if not tenant or not workspace or not agent:
        _record("scope_invalid", **base)
        return None
    try:
        async with scoped_db(pool, tenant, workspace) as conn:
            handle, item_id, key_id = await _mint(
                conn,
                tenant_id=tenant,
                workspace_id=workspace,
                agent_id=agent,
                agent_run_id=_text(agent_run_id) or None,
                schedule_run_id=int(schedule_run_id),
                fencing_token=int(fencing_token),
                internal_service=_text(internal_service) or "unknown",
                requested_wisdom_bit_id=_text(requested_wisdom_bit_id),
                requested_cartridge_id=_text(requested_cartridge_id),
                payload=payload,
            )
    except _Refused as refused:
        _record(refused.reason, **base, **refused.fields)
        return None
    except Exception as exc:  # noqa: BLE001 - attestation must not break the monitor
        _record("unavailable", error_code=type(exc).__name__, **base)
        return None
    _record(
        "minted",
        handle=handle,
        item_id=item_id,
        agent_run_id=_text(agent_run_id),
        internal_service=_text(internal_service),
        attestation_key_id=key_id,
        **base,
    )
    return handle


_RESOLVE_SQL = """
SELECT ranked.item_id, ranked.handle, ranked.reference
  FROM (
        SELECT t.item_id, t.handle, t.reference,
               row_number() OVER (
                   PARTITION BY t.item_id
                   ORDER BY (t.agent_run_id IS NOT DISTINCT FROM alert.agent_run_id) DESC,
                            t.minted_at DESC
               ) AS rank
          FROM control_room_evidence_tickets AS t
          JOIN unnest($3::text[], $4::uuid[], $5::text[])
               AS alert(item_id, agent_id, agent_run_id)
            ON t.item_id = alert.item_id
           AND t.agent_id = alert.agent_id
         WHERE t.tenant_id = $1::uuid
           AND t.workspace_id = $2::uuid
           AND t.expires_at > clock_timestamp()
       ) AS ranked
 WHERE ranked.rank <= $6
 ORDER BY ranked.item_id, ranked.rank
"""


def _verified_reference(
    reference: Mapping[str, Any], *, item_id: str, tenant_id: str, workspace_id: str
) -> dict[str, Any] | None:
    canonical = canonical_runtime_row_reference(reference)
    if canonical is None:
        return None
    locator = canonical.get("source_locator")
    if (
        not isinstance(locator, Mapping)
        or locator.get("field") != LOCATOR_FIELD
        or locator.get("value") != item_id
        or canonical.get("scope_binding")
        != runtime_scope_binding(tenant_id, workspace_id)
    ):
        return None
    return canonical


async def resolve_monitor_evidence(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    alerts: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    tenant = _uuid_text(tenant_id)
    workspace = _uuid_text(workspace_id)
    if not tenant or not workspace:
        return {}
    item_ids: list[str] = []
    agent_ids: list[str] = []
    run_ids: list[str | None] = []
    for alert in alerts[:MAX_RESOLVE_ITEMS]:
        item_id = _text(alert.get("item_id"))
        agent = _uuid_text(alert.get("agent_id"))
        if item_id and agent:
            item_ids.append(item_id)
            agent_ids.append(agent)
            run_ids.append(_text(alert.get("agent_run_id")) or None)
    if not item_ids:
        return {}
    rows = await conn.fetch(
        _RESOLVE_SQL,
        tenant,
        workspace,
        item_ids,
        agent_ids,
        run_ids,
        MAX_TICKETS_PER_ITEM,
    )
    resolved: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item_id = _text(row["item_id"])
        candidates = resolved.setdefault(item_id, [])
        if len(candidates) >= MAX_TICKETS_PER_ITEM:
            continue
        reference = _verified_reference(
            _json_mapping(row["reference"]),
            item_id=item_id,
            tenant_id=tenant,
            workspace_id=workspace,
        )
        if reference is None:
            _record(
                "binding_mismatch",
                handle=_text(row["handle"]),
                item_id=item_id,
                tenant_id=tenant,
                workspace_id=workspace,
            )
            continue
        candidates.append(reference)
        _COUNTERS["resolved"] += 1
    return {item_id: refs for item_id, refs in resolved.items() if refs}


__all__ = (
    "DEFAULT_MONITOR_WISDOM_BIT",
    "EVIDENCE_EVENTS",
    "LOCATOR_FIELD",
    "MONITOR_ITEM_KIND",
    "MONITOR_METRIC_NAME",
    "MONITOR_METRIC_TYPE",
    "MonitorAlertIdentity",
    "SCHEDULED_CONTEXT_SOURCE",
    "TICKET_TTL_SECONDS",
    "evidence_counters",
    "mint_monitor_evidence_ticket",
    "monitor_alert_identity",
    "monitor_alert_item_id",
    "monitor_business_observation",
    "monitor_signal_count",
    "record_evidence_event",
    "resolve_monitor_evidence",
)
