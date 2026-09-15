"""Mission 5: attested scheduled-monitor alerts on ``/control-room``.

``collect_surface_snapshot`` rebuilds the Control Room surface live from Gold on
every request; persisted rows only overlay state onto items Gold produced. A
monitor alert is not a Gold row, so until now it could never appear on the
screen, however real it was.

This loader adds exactly the persisted monitor alerts console itself attested
(``evidence_tickets``), rebuilt from scratch rather than from agent-written
metadata: identity and scope from the row's columns, the measured value from
the analysis evidence mcp-infra stored, and the evidence from the ticket. Each
candidate then goes through the ordinary eligibility check, so an alert whose
persisted value no longer matches what console signed simply does not show.

Nothing here writes, and any failure yields an empty result: Control Room must
render without monitor alerts rather than fail because of them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services import auth
from app.services.control_room.business_access import (
    can_read_workspace_wide,
    owner_scope_id,
    workspace_scope,
)
from app.services.control_room.business_cartridge_scope import (
    filter_business_cartridge_items,
)
from app.services.control_room.business_experience_copy import (
    MAX_STRUCTURAL_IDENTITY_LENGTH,
    visible_business_copy,
)
from app.services.control_room.business_projection import filter_business_items
from app.services.control_room.business_surface_identity import (
    resolve_bounded_business_surface_identity,
)
from app.services.control_room.evidence_tickets import (
    MONITOR_ITEM_KIND,
    MONITOR_METRIC_NAME,
    MONITOR_METRIC_TYPE,
    resolve_monitor_evidence,
)
from app.services.db_scope import run_with_db_scope
from app.services.intelligence.alert_narrative import (
    evidence_note,
    published_narrative,
)

logger = logging.getLogger(__name__)

VISIBLE_STATUSES = ("open", "in_review", "decision_created", "approved")
MAX_MONITOR_ALERTS = 50
FALLBACK_TITLE = "Alerta del monitor programado"

_ALERTS_SQL = """
SELECT tenant_id::text AS tenant_id,
       workspace_id::text AS workspace_id,
       item_id, cartridge_id, domain, source_dataset, title, severity,
       status, entity_label, metadata, last_seen_at
  FROM control_room_items
 WHERE workspace_id = $1::uuid
   AND tenant_id = $2::uuid
   AND item_kind = 'agent_alert'
   AND status = ANY($3::text[])
   AND ($4::bigint IS NULL OR owner_user_id = $4::bigint)
 ORDER BY last_seen_at DESC, item_id DESC
 LIMIT $5
"""


@dataclass(frozen=True)
class MonitorAlertSurface:
    items: tuple[Mapping[str, Any], ...] = ()
    narratives: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row["metadata"]
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, Mapping) else {}


def _signal_count(metadata: Mapping[str, Any]) -> int | None:
    analysis = metadata.get("analysis_evidence")
    metrics = analysis.get("metrics") if isinstance(analysis, Mapping) else None
    value = metrics.get("signal_count") if isinstance(metrics, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _candidate(
    row: Mapping[str, Any], reference: Mapping[str, Any], signal_count: int
) -> dict[str, Any]:
    cartridge = str(row["cartridge_id"] or "").strip()
    source_dataset = str(row["source_dataset"] or "").strip()
    return {
        "id": str(row["item_id"] or "").strip(),
        "kind": MONITOR_ITEM_KIND,
        "tenant_id": str(row["tenant_id"] or "").strip(),
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "domain": str(row["domain"] or "").strip(),
        "cartridge": cartridge,
        "cartridge_id": cartridge,
        "source_system": cartridge,
        "source_dataset": source_dataset,
        "module_id": source_dataset,
        "title": str(row["title"] or "").strip(),
        "severity": str(row["severity"] or "medium").strip().lower(),
        "status": str(row["status"] or "open").strip(),
        "entity_label": row["entity_label"],
        "metric_name": MONITOR_METRIC_NAME,
        "metric_type": MONITOR_METRIC_TYPE,
        "observed_value": signal_count,
        "observation_date": str(reference.get("observed_at") or "").strip(),
        "evidence_refs": [dict(reference)],
    }


def _with_visible_title(item: dict[str, Any]) -> dict[str, Any]:
    # A monitor title embeds the agent name and the wisdom-bit id. When the
    # public copy rules refuse it, the fact would vanish; a fixed title keeps
    # the attested fact on screen without publishing the refused text.
    identity = resolve_bounded_business_surface_identity(
        item, max_length=MAX_STRUCTURAL_IDENTITY_LENGTH
    )
    if identity is None:
        return item
    if visible_business_copy(item, identity, item.get("title"), max_length=240):
        return item
    return {**item, "title": FALLBACK_TITLE}


def project_monitor_alerts(
    rows: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    user: Mapping[str, Any] | None,
) -> MonitorAlertSurface:
    items: list[dict[str, Any]] = []
    narratives: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_id = str(row["item_id"] or "").strip()
        references = evidence.get(item_id) or ()
        metadata = _metadata(row)
        signal_count = _signal_count(metadata)
        if not references or signal_count is None:
            continue
        for reference in references:
            candidate = _with_visible_title(_candidate(row, reference, signal_count))
            eligible = filter_business_items([candidate])
            if not eligible:
                continue
            items.append(eligible[0])
            narrative = published_narrative(
                domain=row["domain"],
                severity=row["severity"],
                metadata=metadata,
                item_id=item_id,
                tenant_id=str(row["tenant_id"] or "").strip(),
                workspace_id=str(row["workspace_id"] or "").strip(),
            )
            narratives[item_id] = {
                **narrative,
                "evidence_note": evidence_note(
                    signal_count, reference.get("observed_at")
                ),
            }
            break
    scoped = filter_business_cartridge_items(items, dict(user or {}))
    kept = {str(item.get("id") or "") for item in scoped}
    return MonitorAlertSurface(
        items=tuple(scoped),
        narratives={key: value for key, value in narratives.items() if key in kept},
    )


async def load_attested_monitor_alerts(
    user: Mapping[str, Any] | None,
) -> MonitorAlertSurface:
    try:
        tenant_id, workspace_id = workspace_scope(user)
        if not tenant_id or not workspace_id:
            return MonitorAlertSurface()
        workspace_wide = can_read_workspace_wide(user)
        owner_id = None if workspace_wide else owner_scope_id(user)
        if not workspace_wide and owner_id is None:
            return MonitorAlertSurface()
        pool = await auth.pool()

        async def _load(
            conn: Any, _tenant_id: str | None, _workspace_id: str
        ) -> tuple[list[Any], dict[str, list[dict[str, Any]]]]:
            rows = list(
                await conn.fetch(
                    _ALERTS_SQL,
                    workspace_id,
                    tenant_id,
                    list(VISIBLE_STATUSES),
                    owner_id,
                    MAX_MONITOR_ALERTS,
                )
            )
            alerts = [
                {
                    "item_id": row["item_id"],
                    "agent_id": _metadata(row).get("agent_id"),
                    "agent_run_id": _metadata(row).get("agent_run_id"),
                }
                for row in rows
            ]
            evidence = await resolve_monitor_evidence(
                conn, tenant_id=tenant_id, workspace_id=workspace_id, alerts=alerts
            )
            return rows, evidence

        rows, evidence = await run_with_db_scope(pool, dict(user or {}), _load)
    except Exception as exc:  # noqa: BLE001 - Control Room renders without them
        logger.warning(
            "control_room.monitor_alerts.unavailable error_code=%s",
            type(exc).__name__,
        )
        return MonitorAlertSurface()
    return project_monitor_alerts(rows, evidence, user=user)


__all__ = (
    "FALLBACK_TITLE",
    "MAX_MONITOR_ALERTS",
    "MonitorAlertSurface",
    "VISIBLE_STATUSES",
    "load_attested_monitor_alerts",
    "project_monitor_alerts",
)
