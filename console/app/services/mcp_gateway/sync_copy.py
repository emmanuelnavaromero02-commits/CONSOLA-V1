from __future__ import annotations

from app.services.mcp_gateway.errors import REASON_COPY


UNKNOWN = "Sin información"
STATUS_COPY: dict[str, str] = {
    "queued": "En cola",
    "running": "En curso",
    "success": "Completada",
    "partial": "Completada con advertencias",
    "failed": "Fallida",
    "blocked": "Bloqueada",
    "skipped": "Omitida",
    "skipped_explicit": "Omitida por configuración",
}
STEP_STATUS_COPY: dict[str, str] = {
    "queued": "En cola",
    "running": "En curso",
    "success": "Completado",
    "partial": "Con advertencias",
    "failed": "Falló",
    "blocked": "Bloqueado",
    "skipped": "Omitido",
    "skipped_explicit": "Omitido por configuración",
}
STEP_LABELS: dict[str, str] = {
    "connection": "Conexión con la fuente",
    "bronze": "Extracción de datos de origen",
    "silver_gold": "Modelado de tablas",
    "control_room": "Actualización del Control Room",
    "agents_intelligence": "Agentes e inteligencia",
}
AUTOMATION_STATE_COPY: dict[str, str] = {
    "active": "Activa",
    "paused_by_operator": "Pausada por un operador",
    "paused_manual": "En pausa hasta su próxima ejecución manual",
    "unavailable": "No disponible",
}
AUTOMATION_KIND_COPY: dict[str, str] = {"manual": "Manual", "scheduled": "Programada"}


def status_copy(status: object) -> str:
    return STATUS_COPY.get(str(status or ""), UNKNOWN)


def step_status_copy(status: object) -> str:
    return STEP_STATUS_COPY.get(str(status or ""), UNKNOWN)


def step_label(step_id: object) -> str:
    return STEP_LABELS.get(str(step_id or ""), UNKNOWN)


def reason_copy(reason: object) -> str | None:
    if not isinstance(reason, str) or not reason:
        return None
    return REASON_COPY.get(reason)


def card_title(status: object) -> str:
    value = str(status or "")
    if value == "success":
        return "Sincronización completa"
    if value == "partial":
        return "Completada con advertencias"
    if value == "failed":
        return "La sincronización falló"
    if value == "blocked":
        return "Sincronización bloqueada"
    if value in {"skipped", "skipped_explicit"}:
        return "Sincronización omitida"
    if value in {"queued", "running"}:
        return "Sincronización en curso"
    return "Estado de la sincronización"


__all__ = (
    "AUTOMATION_KIND_COPY",
    "AUTOMATION_STATE_COPY",
    "STATUS_COPY",
    "STEP_LABELS",
    "STEP_STATUS_COPY",
    "card_title",
    "reason_copy",
    "status_copy",
    "step_label",
    "step_status_copy",
)
