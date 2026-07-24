from __future__ import annotations

from app.schemas.control_room_diagnostic_enums import (
    DiagnosticInstallationStatus,
    DiagnosticItemKind,
    DiagnosticItemStatus,
    DiagnosticReadinessStatus,
    DiagnosticSourceStatus,
)


_READINESS_ALIASES = {
    "complete": DiagnosticReadinessStatus.READY,
    "gold_ready": DiagnosticReadinessStatus.READY,
    "materialized": DiagnosticReadinessStatus.READY,
    "ok": DiagnosticReadinessStatus.READY,
    "success": DiagnosticReadinessStatus.READY,
    "metadata_ready": DiagnosticReadinessStatus.PARTIAL,
    "near": DiagnosticReadinessStatus.PARTIAL,
    "benchmark_internal": DiagnosticReadinessStatus.PARTIAL,
    "not_ready": DiagnosticReadinessStatus.PENDING,
    "permission_denied": DiagnosticReadinessStatus.NO_PERMISSION,
    "schema_only": DiagnosticReadinessStatus.INVALID_SCHEMA,
    "failed": DiagnosticReadinessStatus.ERROR,
    "failure": DiagnosticReadinessStatus.ERROR,
}


def _enum_or_unknown(enum_type, value: object):
    text = str(value or "").strip().lower()
    try:
        return enum_type(text)
    except ValueError:
        return enum_type.UNKNOWN


def normalize_source_status(value: object) -> DiagnosticSourceStatus:
    text = str(value or "").strip().lower()
    aliases = {
        "permission_denied": DiagnosticSourceStatus.NO_PERMISSION,
        "schema_only": DiagnosticSourceStatus.INVALID_SCHEMA,
    }
    return aliases.get(text) or _enum_or_unknown(DiagnosticSourceStatus, text)


def normalize_readiness_status(value: object) -> DiagnosticReadinessStatus:
    text = str(value or "").strip().lower()
    return _READINESS_ALIASES.get(text) or _enum_or_unknown(
        DiagnosticReadinessStatus,
        text,
    )


def normalize_item_kind(value: object) -> DiagnosticItemKind:
    return _enum_or_unknown(DiagnosticItemKind, value)


def normalize_item_status(value: object) -> DiagnosticItemStatus:
    return _enum_or_unknown(DiagnosticItemStatus, value)


def normalize_installation_status(value: object) -> DiagnosticInstallationStatus:
    text = str(value or "").strip().lower()
    if text == "active":
        return DiagnosticInstallationStatus.READY
    return _enum_or_unknown(DiagnosticInstallationStatus, text)


def evaluated_count_state(
    source_status: DiagnosticSourceStatus,
    readiness: DiagnosticReadinessStatus,
) -> bool:
    return source_status in {
        DiagnosticSourceStatus.OK,
        DiagnosticSourceStatus.EMPTY,
    } and readiness in {
        DiagnosticReadinessStatus.READY,
        DiagnosticReadinessStatus.EMPTY,
    }


__all__ = (
    "DiagnosticInstallationStatus",
    "DiagnosticItemKind",
    "DiagnosticItemStatus",
    "DiagnosticReadinessStatus",
    "DiagnosticSourceStatus",
    "evaluated_count_state",
    "normalize_installation_status",
    "normalize_item_kind",
    "normalize_item_status",
    "normalize_readiness_status",
    "normalize_source_status",
)
