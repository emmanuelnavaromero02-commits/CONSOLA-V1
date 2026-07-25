from enum import StrEnum


class DiagnosticSourceStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    INVALID_SCHEMA = "invalid_schema"
    BLOCKED = "blocked"
    NO_PERMISSION = "no_permission"
    UNKNOWN = "unknown"


class DiagnosticReadinessStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    STALE = "stale"
    STUB = "stub"
    EMPTY = "empty"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_SCOPE = "invalid_scope"
    BLOCKED = "blocked"
    NO_PERMISSION = "no_permission"
    INSUFFICIENT_DATA = "insufficient_data"
    PENDING = "pending"
    ERROR = "error"
    UNKNOWN = "unknown"


class DiagnosticItemKind(StrEnum):
    SOURCE_STATE = "source_state"
    ANOMALY = "anomaly"
    CONTROL_ITEM = "control_item"
    INTELLIGENCE_SIGNAL = "intelligence_signal"
    AGENT_ALERT = "agent_alert"
    DIAGNOSTIC = "diagnostic"
    UNKNOWN = "unknown"


class DiagnosticItemStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    DECISION_CREATED = "decision_created"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class DiagnosticInstallationStatus(StrEnum):
    REQUESTED = "requested"
    INSTALLING = "installing"
    PENDING_CONNECTION = "pending_connection"
    WAITING_CREDENTIALS = "waiting_credentials"
    READY = "ready"
    FAILED = "failed"
    PAUSED = "paused"
    REVOKED = "revoked"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


__all__ = (
    "DiagnosticInstallationStatus",
    "DiagnosticItemKind",
    "DiagnosticItemStatus",
    "DiagnosticReadinessStatus",
    "DiagnosticSourceStatus",
)
