from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time

from app.schemas.control_room_surfaces import (
    DIAGNOSTICS_SCHEMA_VERSION,
    ControlRoomDiagnosticsResponse,
    DiagnosticInstallation,
    DiagnosticItem,
    DiagnosticSource,
)
from app.services.control_room.business_projection import diagnostic_items
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.surface_snapshot import (
    SurfaceSnapshot,
    validate_snapshot_scope,
)


def _mapping(value: object) -> Mapping[str, object]:
    redacted = redact_diagnostic_value(value)
    return redacted if isinstance(redacted, Mapping) else {}


def _text(row: Mapping[str, object], *keys: str) -> str:
    containers = [row]
    for name in ("details", "metadata"):
        nested = row.get(name)
        if isinstance(nested, Mapping):
            containers.append(nested)
    for values in containers:
        for key in keys:
            value = str(values.get(key) or "").strip()
            if value:
                return value
    return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item)[:500] for item in value if str(item).strip()]


def _utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min, tzinfo=UTC)
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _controlled_error(
    row: Mapping[str, object],
    *keys: str,
    message: str,
) -> str | None:
    return message if _text(row, *keys) else None


def _source(raw: Mapping[str, object]) -> DiagnosticSource | None:
    row = _mapping(raw)
    cartridge = _text(row, "cartridge", "connector_id")
    dataset = _text(row, "dataset", "source_dataset")
    if not cartridge or not dataset:
        return None
    try:
        count = max(0, int(row.get("count") or 0))
    except (TypeError, ValueError):
        count = 0
    return DiagnosticSource(
        cartridge=cartridge[:120],
        dataset=dataset[:200],
        module=_text(row, "module")[:200] or None,
        domain=_text(row, "domain")[:200] or None,
        status=_text(row, "status")[:80] or "unknown",
        data_readiness=_text(row, "data_readiness")[:80] or None,
        count=count,
        operationally_ready=row.get("operationally_ready") is True,
        checked_at=_utc_datetime(row.get("checked_at")),
        reason=_text(row, "readiness_reason", "reason")[:500] or None,
        blockers=_string_list(row.get("readiness_blockers")),
        warnings=_string_list(row.get("contract_warnings")),
        error=_controlled_error(row, "error", message="Source query failed"),
    )


def _diagnostic_item(raw: Mapping[str, object]) -> DiagnosticItem:
    row = _mapping(raw)
    return DiagnosticItem(
        kind=_text(row, "kind", "item_kind")[:80] or "diagnostic",
        title=_text(row, "title")[:240] or "Technical diagnostic",
        cartridge=_text(row, "cartridge", "connector_id")[:120] or None,
        dataset=_text(row, "source_dataset", "dataset")[:200] or None,
        module=_text(row, "module")[:200] or None,
        domain=_text(row, "domain")[:200] or None,
        status=_text(row, "status")[:80] or None,
        data_status=_text(row, "data_status")[:80] or None,
        readiness_status=_text(row, "readiness_status")[:80] or None,
        source_status=_text(row, "source_status")[:80] or None,
        observed_at=_utc_datetime(row.get("checked_at") or row.get("observed_at")),
        error=_controlled_error(row, "error", message="Diagnostic error reported"),
    )


def _installation(raw: Mapping[str, object]) -> DiagnosticInstallation | None:
    row = _mapping(raw)
    cartridge_id = _text(row, "cartridge_id")
    if not cartridge_id:
        return None
    return DiagnosticInstallation(
        cartridge_id=cartridge_id[:120],
        status=_text(row, "installation_status", "status")[:80] or "unknown",
        current_step=_text(row, "current_step")[:200] or None,
        label=_text(row, "label")[:200] or None,
        category=_text(row, "category")[:120] or None,
        ready_at=_utc_datetime(row.get("ready_at")),
        error=_controlled_error(
            row,
            "error_message",
            "error",
            message="Installation error reported",
        ),
    )


def build_operational_diagnostics(
    snapshot: SurfaceSnapshot,
) -> ControlRoomDiagnosticsResponse:
    validate_snapshot_scope(snapshot)
    sources = [value for raw in snapshot.sources if (value := _source(raw))]
    sources.sort(key=lambda row: (row.cartridge, row.dataset))
    technical = [*snapshot.diagnostics, *diagnostic_items(snapshot.items)]
    items: list[DiagnosticItem] = []
    seen_items: set[tuple[str, str, str]] = set()
    for raw in technical:
        item = _diagnostic_item(raw)
        identity = (
            item.kind,
            str(raw.get("id") or raw.get("item_id") or ""),
            item.title,
        )
        if identity in seen_items:
            continue
        seen_items.add(identity)
        items.append(item)
    items.sort(key=lambda row: (row.cartridge or "", row.dataset or "", row.title))
    installations = [
        value
        for raw in snapshot.installations
        if (value := _installation(raw)) is not None
    ]
    installations.sort(key=lambda row: row.cartridge_id)
    return ControlRoomDiagnosticsResponse(
        schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        scope=snapshot.scope,
        sources=sources,
        diagnostic_items=items,
        installations=installations,
    )


__all__ = ("build_operational_diagnostics", "redact_diagnostic_value")
