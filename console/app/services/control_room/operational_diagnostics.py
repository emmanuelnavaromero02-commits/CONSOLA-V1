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
from app.services.control_room.business_projection import (
    diagnostic_items,
    filter_business_items,
    strip_business_fields,
)
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.diagnostic_states import (
    DiagnosticReadinessStatus,
    DiagnosticSourceStatus,
    evaluated_count_state,
    normalize_installation_status,
    normalize_item_kind,
    normalize_item_status,
    normalize_readiness_status,
    normalize_source_status,
)
from app.services.control_room.surface_snapshot import (
    SurfaceSnapshot,
    validate_snapshot_scope,
)
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    contains_public_technical_data,
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
            raw = values.get(key)
            if not isinstance(raw, str):
                continue
            value = raw.strip()
            if value:
                return value
    return ""


def _public_text(row: Mapping[str, object], *keys: str) -> str:
    containers = [row]
    for name in ("details", "metadata"):
        nested = row.get(name)
        if isinstance(nested, Mapping):
            containers.append(nested)
    for values in containers:
        for key in keys:
            raw = values.get(key)
            if contains_public_technical_data(raw) or not isinstance(raw, str):
                continue
            value = raw.strip()
            if value and not contains_public_technical_copy(value):
                return value
    return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    sanitized: list[str] = []
    for item in value:
        if contains_public_technical_data(item) or not isinstance(item, str):
            continue
        redacted = redact_diagnostic_value(item)
        if (
            isinstance(redacted, str)
            and redacted.strip()
            and not contains_public_technical_copy(redacted)
        ):
            sanitized.append(redacted[:500])
    return sanitized


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


def _optional_readiness(value: str) -> DiagnosticReadinessStatus | None:
    return normalize_readiness_status(value) if value else None


def _optional_source_status(value: str) -> DiagnosticSourceStatus | None:
    return normalize_source_status(value) if value else None


def _count(
    row: Mapping[str, object],
    *,
    source_status: DiagnosticSourceStatus,
    readiness: DiagnosticReadinessStatus | None,
    checked_at: datetime | None,
) -> int | None:
    if "count" not in row:
        return None
    value = row.get("count")
    if type(value) is not int or value < 0:
        return None
    if value > 0:
        return value
    if (
        checked_at is not None
        and readiness is not None
        and evaluated_count_state(source_status, readiness)
    ):
        return 0
    return None


def _source(raw: Mapping[str, object]) -> DiagnosticSource | None:
    row = _mapping(raw)
    cartridge = _text(row, "cartridge", "connector_id")
    dataset = _text(row, "dataset", "source_dataset")
    if not cartridge or not dataset:
        return None
    status = normalize_source_status(_text(row, "status"))
    readiness = _optional_readiness(_text(row, "data_readiness"))
    checked_at = _utc_datetime(row.get("checked_at"))
    return DiagnosticSource(
        domain=_public_text(row, "domain")[:200] or None,
        status=status,
        data_readiness=readiness,
        count=_count(
            row,
            source_status=status,
            readiness=readiness,
            checked_at=checked_at,
        ),
        operationally_ready=row.get("operationally_ready") is True,
        checked_at=checked_at,
        reason=_public_text(row, "readiness_reason", "reason")[:500] or None,
        blockers=_string_list(row.get("readiness_blockers")),
        warnings=_string_list(row.get("contract_warnings")),
        error=_controlled_error(row, "error", message="Source query failed"),
    )


def _diagnostic_item(raw: Mapping[str, object]) -> DiagnosticItem:
    row = _mapping(raw)
    status = _text(row, "status")
    data_status = _text(row, "data_status")
    readiness_status = _text(row, "readiness_status")
    source_status = _text(row, "source_status")
    return DiagnosticItem(
        kind=normalize_item_kind(_text(row, "kind", "item_kind")),
        title=_public_text(row, "title")[:240] or "Technical diagnostic",
        domain=_public_text(row, "domain")[:200] or None,
        status=normalize_item_status(status) if status else None,
        data_status=_optional_readiness(data_status),
        readiness_status=_optional_readiness(readiness_status),
        source_status=_optional_source_status(source_status),
        observed_at=_utc_datetime(row.get("checked_at") or row.get("observed_at")),
        error=_controlled_error(row, "error", message="Diagnostic error reported"),
    )


def _installation(raw: Mapping[str, object]) -> DiagnosticInstallation | None:
    row = _mapping(raw)
    cartridge_id = _text(row, "cartridge_id")
    if not cartridge_id:
        return None
    return DiagnosticInstallation(
        status=normalize_installation_status(
            _text(row, "installation_status", "status")
        ),
        label=_public_text(row, "label")[:200] or None,
        category=_public_text(row, "category")[:120] or None,
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
    sources.sort(key=lambda row: row.model_dump_json(exclude_none=True))
    unsectioned = [
        strip_business_fields(item)
        for item in filter_business_items(snapshot.items)
        if resolve_business_surface_identity(item) is None
    ]
    technical = [
        *snapshot.diagnostics,
        *diagnostic_items(snapshot.items),
        *unsectioned,
    ]
    items: list[DiagnosticItem] = []
    seen_items: set[tuple[str, str, str]] = set()
    for raw in technical:
        item = _diagnostic_item(raw)
        raw_id = raw.get("id") or raw.get("item_id")
        identity = (
            item.kind,
            raw_id.strip() if isinstance(raw_id, str) else "",
            item.title,
        )
        if identity in seen_items:
            continue
        seen_items.add(identity)
        items.append(item)
    items.sort(
        key=lambda row: (
            row.domain or "",
            row.kind,
            row.title,
            row.model_dump_json(exclude_none=True),
        )
    )
    installations = [
        value
        for raw in snapshot.installations
        if (value := _installation(raw)) is not None
    ]
    installations.sort(key=lambda row: row.model_dump_json(exclude_none=True))
    return ControlRoomDiagnosticsResponse(
        schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        sources=sources,
        diagnostic_items=items,
        installations=installations,
    )


__all__ = ("build_operational_diagnostics", "redact_diagnostic_value")
