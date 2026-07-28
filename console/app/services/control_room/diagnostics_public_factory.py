from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from app.schemas.control_room_surfaces import (
    DIAGNOSTICS_SCHEMA_VERSION,
    ControlRoomDiagnosticsResponse,
    DiagnosticInstallation,
    DiagnosticItem,
    DiagnosticSource,
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
from app.services.control_room.diagnostics_public_copy import (
    DIAGNOSTICS_ENDPOINT,
    DIAGNOSTIC_ITEM_ERROR_FIELD,
    DIAGNOSTIC_ITEM_TITLE_FIELD,
    INSTALLATION_ERROR_FIELD,
    SOURCE_ERROR_FIELD,
    _DIAGNOSTIC_ERROR_COPY_ID,
    _INSTALLATION_ERROR_COPY_ID,
    _SOURCE_QUERY_FAILED_COPY_ID,
    _TECHNICAL_DIAGNOSTIC_COPY_ID,
    _resolve_server_copy,
)
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    contains_public_technical_data,
)


@dataclass(frozen=True, slots=True)
class RawDiagnosticsDraft:
    generated_at: datetime
    sources: tuple[Mapping[str, object], ...] = ()
    diagnostic_items: tuple[Mapping[str, object], ...] = ()
    installations: tuple[Mapping[str, object], ...] = ()


def _mapping(value: object) -> Mapping[str, object]:
    redacted = redact_diagnostic_value(value)
    return redacted if isinstance(redacted, Mapping) else {}


def _containers(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    containers = [row]
    for name in ("details", "metadata"):
        nested = row.get(name)
        if isinstance(nested, Mapping):
            containers.append(nested)
    return containers


def _text(row: Mapping[str, object], *keys: str) -> str:
    for values in _containers(row):
        for key in keys:
            raw = values.get(key)
            if not isinstance(raw, str):
                continue
            value = raw.strip()
            if value:
                return value
    return ""


def _public_text(row: Mapping[str, object], *keys: str) -> str:
    for values in _containers(row):
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


def _server_copy(copy_id: object, field: str) -> str:
    literal = _resolve_server_copy(copy_id, DIAGNOSTICS_ENDPOINT, field)
    if literal is None:
        raise RuntimeError("diagnostics server copy is not registered")
    return literal


def _controlled_error(
    row: Mapping[str, object],
    *keys: str,
    copy_id: object,
    field: str,
) -> str | None:
    return _server_copy(copy_id, field) if _text(row, *keys) else None


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
        error=_controlled_error(
            row,
            "error",
            copy_id=_SOURCE_QUERY_FAILED_COPY_ID,
            field=SOURCE_ERROR_FIELD,
        ),
    )


def _diagnostic_item(raw: Mapping[str, object]) -> DiagnosticItem:
    row = _mapping(raw)
    status = _text(row, "status")
    data_status = _text(row, "data_status")
    readiness_status = _text(row, "readiness_status")
    source_status = _text(row, "source_status")
    title = _public_text(row, "title")[:240]
    return DiagnosticItem(
        kind=normalize_item_kind(_text(row, "kind", "item_kind")),
        title=title
        or _server_copy(
            _TECHNICAL_DIAGNOSTIC_COPY_ID,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
        ),
        domain=_public_text(row, "domain")[:200] or None,
        status=normalize_item_status(status) if status else None,
        data_status=_optional_readiness(data_status),
        readiness_status=_optional_readiness(readiness_status),
        source_status=_optional_source_status(source_status),
        observed_at=_utc_datetime(row.get("checked_at") or row.get("observed_at")),
        error=_controlled_error(
            row,
            "error",
            copy_id=_DIAGNOSTIC_ERROR_COPY_ID,
            field=DIAGNOSTIC_ITEM_ERROR_FIELD,
        ),
    )


def _installation(raw: Mapping[str, object]) -> DiagnosticInstallation | None:
    row = _mapping(raw)
    if not _text(row, "cartridge_id"):
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
            copy_id=_INSTALLATION_ERROR_COPY_ID,
            field=INSTALLATION_ERROR_FIELD,
        ),
    )


def build_diagnostics_response(
    draft: RawDiagnosticsDraft,
) -> ControlRoomDiagnosticsResponse:
    sources = [value for raw in draft.sources if (value := _source(raw))]
    sources.sort(key=lambda row: row.model_dump_json(exclude_none=True))
    items: list[DiagnosticItem] = []
    seen_items: set[tuple[str, str, str]] = set()
    for raw in draft.diagnostic_items:
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
        for raw in draft.installations
        if (value := _installation(raw)) is not None
    ]
    installations.sort(key=lambda row: row.model_dump_json(exclude_none=True))
    return ControlRoomDiagnosticsResponse(
        schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        generated_at=draft.generated_at,
        sources=sources,
        diagnostic_items=items,
        installations=installations,
    )


__all__ = ("RawDiagnosticsDraft", "build_diagnostics_response")
