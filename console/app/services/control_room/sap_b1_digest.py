from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from app.domains.agentops.sap_b1_monitors import SAP_B1_SEMAFORO_MONITOR_SPEC
from app.domains.agentops.domain_monitor_support import DomainMonitorSpec
from app.services import email_service
from app.services.db_scope import scoped_db_for_user

MAX_RECIPIENTS = 20
STALE_CLAIM_MINUTES = 30
_EMAIL_RE = re.compile(r"^[^@\s,;=<>\"']+@[^@\s,;=<>\"']+\.[^@\s,;=<>\"']+$")
_COLORS = {
    "rojo": ("#f85149", "Rojo"),
    "amarillo": ("#d29922", "Amarillo"),
    "verde": ("#3fb950", "Verde"),
    "sin_datos": ("#6e7681", "Sin datos"),
}
_ORDER = {"rojo": 0, "amarillo": 1, "sin_datos": 2, "verde": 3}

CLAIM_SQL = """
    INSERT INTO sap_b1_digest_deliveries (tenant_id, workspace_id, local_date, recipients, agent_run_id)
    VALUES ($1::uuid, $2::uuid, $3::date, $4, $5)
    ON CONFLICT (tenant_id, workspace_id, local_date) DO UPDATE
       SET status = 'sending',
           recipients = EXCLUDED.recipients,
           delivered = 0,
           agent_run_id = EXCLUDED.agent_run_id,
           claimed_at = clock_timestamp(),
           finished_at = NULL
     WHERE sap_b1_digest_deliveries.status = 'failed'
        OR (sap_b1_digest_deliveries.status = 'sending'
            AND sap_b1_digest_deliveries.claimed_at < clock_timestamp() - make_interval(mins => $6))
    RETURNING local_date
"""

FINISH_SQL = """
    UPDATE sap_b1_digest_deliveries
       SET status = CASE WHEN $4 > 0 THEN 'sent' ELSE 'failed' END,
           delivered = $4,
           finished_at = clock_timestamp()
     WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid AND local_date = $3::date
"""

Sender = Callable[[str, str, str, str | None], Awaitable[bool]]


RECIPIENTS_SQL = """
    SELECT email FROM sap_b1_digest_recipients
     WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid
     ORDER BY email
     LIMIT $3
"""
ADD_RECIPIENT_SQL = """
    INSERT INTO sap_b1_digest_recipients (tenant_id, workspace_id, email, added_by)
    VALUES ($1::uuid, $2::uuid, $3, $4)
    ON CONFLICT (tenant_id, workspace_id, email) DO NOTHING
    RETURNING email
"""
REMOVE_RECIPIENT_SQL = """
    DELETE FROM sap_b1_digest_recipients
     WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid AND email = $3
    RETURNING email
"""
COUNT_RECIPIENTS_SQL = """
    SELECT COUNT(*) FROM sap_b1_digest_recipients WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid
"""


def normalize_email(value: Any) -> str | None:
    email = str(value or "").strip().lower()
    return email if 6 <= len(email) <= 254 and _EMAIL_RE.match(email) else None


async def recipients(conn: Any, tenant_id: str | None, workspace_id: str) -> list[str]:
    rows = await conn.fetch(RECIPIENTS_SQL, tenant_id, workspace_id, MAX_RECIPIENTS)
    return [str(row["email"]) for row in rows]


async def add_recipient(conn: Any, tenant_id: str | None, workspace_id: str, email: str, user_id: Any) -> bool:
    if (await conn.fetchval(COUNT_RECIPIENTS_SQL, tenant_id, workspace_id) or 0) >= MAX_RECIPIENTS:
        raise ValueError(f"no more than {MAX_RECIPIENTS} recipients")
    added_by = int(user_id) if str(user_id or "").isdigit() else None
    return await conn.fetchrow(ADD_RECIPIENT_SQL, tenant_id, workspace_id, email, added_by) is not None


async def remove_recipient(conn: Any, tenant_id: str | None, workspace_id: str, email: str) -> bool:
    return await conn.fetchrow(REMOVE_RECIPIENT_SQL, tenant_id, workspace_id, email) is not None


def local_now(spec: DomainMonitorSpec, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(spec.tz))


def in_schedule_window(spec: DomainMonitorSpec, now: datetime | None = None) -> bool:
    minute, hour = spec.cron.split()[:2]
    local = local_now(spec, now)
    return local.hour == int(hour) and local.minute >= int(minute)


def _areas(payload: dict[str, Any]) -> list[dict[str, Any]]:
    areas = [area for area in (payload.get("areas") or []) if isinstance(area, dict)]
    return sorted(areas, key=lambda area: (_ORDER.get(str(area.get("color")), 9), str(area.get("label") or "")))


def render(payload: dict[str, Any], local_date: date) -> tuple[str, str, str]:
    areas = _areas(payload)
    red = sum(1 for area in areas if area.get("color") == "rojo")
    missing = sum(1 for area in areas if area.get("color") == "sin_datos")
    if red:
        headline = f"{red} área{'s' if red != 1 else ''} en rojo"
    elif missing:
        headline = f"sin rojos, {missing} área{'s' if missing != 1 else ''} sin datos"
    else:
        headline = "todo en verde"
    subject = f"Semáforo SAP Business One {local_date.isoformat()}: {headline}"
    blocks, lines = [], [subject, ""]
    for area in areas:
        color, label = _COLORS.get(str(area.get("color")), _COLORS["sin_datos"])
        title = str(area.get("label") or area.get("metric") or "")
        period = f" · {area['period']}" if area.get("period") else ""
        findings = [str(item) for item in (area.get("findings") or [])]
        extra = int(area.get("findings_total") or 0) - len(findings)
        if not findings and area.get("reason"):
            findings = [str(area["reason"])]
        items = "".join(f"<li>{html.escape(item)}</li>" for item in findings)
        if extra > 0:
            items += f"<li>y {extra} más en la consola</li>"
        listing = f'<ul style="margin:6px 0 0 18px;padding:0">{items}</ul>' if items else ""
        blocks.append(
            f'<tr><td style="padding:10px 12px;vertical-align:top;white-space:nowrap">'
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:5px;background:{color}"></span> '
            f'<b>{html.escape(label)}</b></td>'
            f'<td style="padding:10px 12px"><b>{html.escape(title)}</b>{html.escape(period)}'
            f"{listing}</td></tr>"
        )
        lines.append(f"[{label}] {title}{period}")
        lines.extend(f"  - {item}" for item in findings)
        if extra > 0:
            lines.append(f"  - y {extra} más en la consola")
    body = (
        '<!DOCTYPE html><html><body style="font-family:Helvetica,Arial,sans-serif;background:#0d1117;'
        'color:#e6edf3;padding:24px"><div style="max-width:680px;margin:0 auto;background:#161b22;'
        'border:1px solid #30363d;border-radius:6px;padding:24px">'
        f'<h2 style="margin:0 0 4px;font-size:17px">{html.escape(subject)}</h2>'
        '<div style="color:#8b949e;font-size:12px;margin-bottom:16px">Solo recomendaciones: nada se escribe en '
        'Business One.</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:13px">{"".join(blocks)}</table>'
        "</div></body></html>"
    )
    return subject, body, "\n".join(lines)


async def maybe_send_digest(
    user: dict[str, Any],
    payload: dict[str, Any],
    *,
    pool: Any,
    now: datetime | None = None,
    send: Sender | None = None,
    spec: DomainMonitorSpec = SAP_B1_SEMAFORO_MONITOR_SPEC,
) -> dict[str, Any]:
    if payload.get("wisdom_bit_id") != spec.wisdom_bit_id:
        return {"sent": False, "reason": "not_semaforo"}
    if not payload.get("evidence_handle"):
        return {"sent": False, "reason": "unverified_run"}
    if not in_schedule_window(spec, now):
        return {"sent": False, "reason": "outside_schedule"}
    local_date = local_now(spec, now).date()
    subject, body, text = render(payload, local_date)
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        to = await recipients(conn, tenant_id, workspace_id)
        if not to:
            return {"sent": False, "reason": "no_recipients"}
        claimed = await conn.fetchrow(
            CLAIM_SQL, tenant_id, workspace_id, local_date, len(to),
            str(user.get("agent_run_id") or "") or None, STALE_CLAIM_MINUTES,
        )
    if claimed is None:
        return {"sent": False, "reason": "already_sent"}
    deliver = send or email_service.send_email
    delivered = 0
    for recipient in to:
        if await deliver(recipient, subject, body, text):
            delivered += 1
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        await conn.execute(FINISH_SQL, tenant_id, workspace_id, local_date, delivered)
    return {"sent": delivered > 0, "recipients": len(to), "delivered": delivered, "local_date": local_date.isoformat()}


__all__ = (
    "MAX_RECIPIENTS",
    "add_recipient",
    "in_schedule_window",
    "maybe_send_digest",
    "normalize_email",
    "recipients",
    "remove_recipient",
    "render",
)
