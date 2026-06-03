from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import audit_service, auth
from app.services.intelligence.contracts import contract_sources, load_contracts
from app.services.intelligence.utils import json_dumps, public_json, workspace_scope


SAFE_SOURCE_TYPES = {
    "company_calendar",
    "public_holidays",
    "weather",
    "fx_rates",
    "market_notes",
}


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("id") or source.get("source_id") or source.get("type") or "external").strip()


def _source_type(source: dict[str, Any]) -> str:
    return str(source.get("type") or source.get("source_type") or "market_notes").strip()


def _source_config(source: dict[str, Any]) -> dict[str, Any]:
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    inline = {key: value for key, value in source.items() if key not in {"id", "source_id", "type", "source_type", "enabled"}}
    return {**inline, **config}


def _matches(value: Any, target: str) -> bool:
    text = str(value or "").strip()
    return not text or text == "*" or text == target


def _period_month(period_key: str) -> str:
    return period_key[:7] if len(period_key) >= 7 else period_key


def _manual_findings(source: dict[str, Any], signal: dict[str, Any]) -> list[dict[str, Any]]:
    config = _source_config(source)
    findings = config.get("findings") or config.get("events") or []
    if not isinstance(findings, list):
        return []
    matched: list[dict[str, Any]] = []
    signal_period = str(signal.get("period_key") or "")
    signal_month = _period_month(signal_period)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if not _matches(finding.get("entity_id"), str(signal.get("entity_id") or "")):
            continue
        if not _matches(finding.get("entity_kind"), str(signal.get("entity_kind") or "")):
            continue
        period = str(finding.get("period_key") or finding.get("date") or finding.get("month") or "")
        if period and period != signal_period and _period_month(period) != signal_month:
            continue
        matched.append(finding)
    return matched


def _calendar_evidence(source: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    findings = _manual_findings(source, signal)
    title = "Calendario operativo revisado"
    if findings:
        title = str(findings[0].get("title") or findings[0].get("name") or title)
    strength = max([float(item.get("strength") or 0.35) for item in findings], default=0.20)
    return {
        "source_type": "external",
        "source_ref": _source_id(source),
        "query_text": None,
        "data": {
            "source_type": _source_type(source),
            "title": title,
            "period_key": signal.get("period_key"),
            "findings": public_json(findings[:5]),
            "status": "matched" if findings else "checked",
        },
        "supports_hypothesis": "external_event_correlation" if findings else "calendar_seasonality",
        "strength": round(min(0.90, strength), 2),
        "metadata": {"external": True, "ttl_seconds": source.get("ttl_seconds") or 86400},
    }


def _unavailable_evidence(source: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    source_type = _source_type(source)
    return {
        "source_type": "external",
        "source_ref": _source_id(source),
        "query_text": None,
        "data": {
            "source_type": source_type,
            "period_key": signal.get("period_key"),
            "status": "external_unavailable",
            "reason": "source_not_configured",
        },
        "supports_hypothesis": "external_unavailable",
        "strength": 0.05,
        "metadata": {"external": True, "ttl_seconds": source.get("ttl_seconds") or 3600},
    }


def build_external_evidence(
    contract: dict[str, Any],
    metric: dict[str, Any],
    signal: dict[str, Any],
    sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    configured = [*contract_sources(contract, metric), *(sources or [])]
    for source in configured:
        if not isinstance(source, dict) or source.get("enabled") is False:
            continue
        source_type = _source_type(source)
        if source_type not in SAFE_SOURCE_TYPES:
            continue
        if source_type in {"company_calendar", "market_notes", "public_holidays"}:
            items.append(_calendar_evidence(source, signal))
        else:
            items.append(_unavailable_evidence(source, signal))
    return items


async def list_sources(user: dict) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT id, source_id, source_type, cartridge_id, metric, enabled,
               config, ttl_seconds, last_run_at, last_status, metadata,
               created_at, updated_at
          FROM external_intelligence_sources
         WHERE workspace_id = $1
         ORDER BY cartridge_id NULLS LAST, metric NULLS LAST, source_id
        """,
        workspace_id,
    )
    configured = []
    for contract in load_contracts():
        for metric in contract.get("metrics", []) if isinstance(contract.get("metrics"), list) else []:
            if not isinstance(metric, dict):
                continue
            for source in contract_sources(contract, metric):
                configured.append(
                    {
                        "source_id": _source_id(source),
                        "source_type": _source_type(source),
                        "cartridge_id": contract.get("cartridge"),
                        "metric": metric.get("id"),
                        "enabled": source.get("enabled", True),
                        "config": _source_config(source),
                        "source": "contract",
                    }
                )
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "sources": [_normalize_source_row(row) for row in rows],
        "contract_sources": configured,
    }


async def patch_source(user: dict, source_id: str, body: dict[str, Any]) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    source_type = str(body.get("source_type") or body.get("type") or "market_notes")
    if source_type not in SAFE_SOURCE_TYPES:
        source_type = "market_notes"
    enabled = bool(body.get("enabled", True))
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    ttl_seconds = int(body.get("ttl_seconds") or 86400)
    cartridge_id = str(body.get("cartridge_id") or "").strip() or None
    metric = str(body.get("metric") or "").strip() or None
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO external_intelligence_sources (
            tenant_id, workspace_id, source_id, source_type, cartridge_id,
            metric, enabled, config, ttl_seconds, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb)
        ON CONFLICT (workspace_id, source_id, cartridge_id, metric) DO UPDATE
        SET source_type = EXCLUDED.source_type,
            enabled = EXCLUDED.enabled,
            config = EXCLUDED.config,
            ttl_seconds = EXCLUDED.ttl_seconds,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING *
        """,
        tenant_id,
        workspace_id,
        source_id,
        source_type,
        cartridge_id,
        metric,
        enabled,
        json_dumps(config),
        ttl_seconds,
        json_dumps({"updated_by": user.get("email")}),
    )
    await audit_service.record_event(
        user.get("id"),
        user.get("email"),
        "intelligence.external_source.update",
        "workspace",
        workspace_id,
        metadata={"source_id": source_id, "source_type": source_type, "cartridge_id": cartridge_id, "metric": metric},
    )
    return {"source": _normalize_source_row(row)}


async def run_sources(user: dict, body: dict[str, Any] | None = None) -> dict[str, Any]:
    _, workspace_id = workspace_scope(user)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=int((body or {}).get("ttl_seconds") or 86400))
    source_id = str((body or {}).get("source_id") or "manual_context")
    source_type = str((body or {}).get("source_type") or "market_notes")
    findings = (body or {}).get("findings") if isinstance((body or {}).get("findings"), list) else []
    pool = await auth.pool()
    cached = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        await pool.execute(
            """
            INSERT INTO external_evidence_cache (
                tenant_id, workspace_id, source_id, source_type, entity_kind,
                entity_id, period_key, data, strength, expires_at, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11::jsonb)
            ON CONFLICT (workspace_id, source_id, entity_kind, entity_id, period_key) DO UPDATE
            SET data = EXCLUDED.data,
                strength = EXCLUDED.strength,
                expires_at = EXCLUDED.expires_at,
                metadata = EXCLUDED.metadata,
                created_at = NOW()
            """,
            user.get("active_tenant_id") or user.get("tenant_id"),
            workspace_id,
            source_id,
            source_type,
            str(finding.get("entity_kind") or "*"),
            str(finding.get("entity_id") or "*"),
            str(finding.get("period_key") or finding.get("date") or finding.get("month") or "*"),
            json_dumps(finding),
            float(finding.get("strength") or 0.50),
            expires_at,
            json_dumps({"loaded_by": user.get("email")}),
        )
        cached += 1
    await audit_service.record_event(
        user.get("id"),
        user.get("email"),
        "intelligence.external_source.run",
        "workspace",
        workspace_id,
        metadata={"source_id": source_id, "source_type": source_type, "cached": cached},
    )
    return {"source_id": source_id, "source_type": source_type, "cached": cached, "expires_at": expires_at.isoformat()}


def _normalize_source_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    return public_json(data)
