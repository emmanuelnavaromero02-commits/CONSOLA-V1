"""T2b — watermarks OData v2 a prueba de /Date(ms)/ (patron portado de
sap_successfactors, la referencia evolucionada de la casa).

El bug (auditoria 2026-08-17, alto): el filtro incremental se construia como
string entrecomillado (f"{field} gt '{watermark}'") y el retroceso de 5 min
usaba fromisoformat con un except silencioso que persistia el valor crudo.
SAP OData v2 serializa Edm.DateTime como '/Date(1699999999000)/': el
watermark guardado quedaba en ese formato, el siguiente filtro lo mandaba
como texto (Gateway lo rechaza con 400 o compara como string) y el filtro
cliente comparaba strings de formatos mezclados — incrementales rotos o mal
filtrados en silencio. De paso, el literal tipado elimina la inyeccion de
segundo orden por comillas (el valor jamas se interpola crudo).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

SAP_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def watermark_value_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    if SAP_DATE_RE.match(text):
        return "sap_date_ms"
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return "numeric"
    if re.match(r"^\d{4}-\d{2}-\d{2}([T\s]\d{2}:\d{2}:\d{2})?", text):
        return "iso8601"
    return "unknown"


def parse_watermark_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = SAP_DATE_RE.match(text)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        try:
            number = float(text)
            # SAP suele mandar milisegundos; acepta segundos para epochs
            # chicos y no convertir watermarks viejos en anio 53900.
            if abs(number) > 9_999_999_999:
                number = number / 1000
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        normalized = text.replace("Z", "+00:00").replace(" ", "T")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def odata_datetime_literal(value: Any) -> str | None:
    """Literal Edm.DateTime para $filter de OData v2 (NetWeaver Gateway).

    Jamas mandar /Date(ms)/ crudo como string entrecomillado. Un watermark
    futuro (> ahora + 5 min) se trata como corrupto: mejor full snapshot que
    perder filas contra un reloj envenenado."""
    parsed = parse_watermark_datetime(value)
    if parsed is None:
        return None
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        return None
    return "datetime'" + parsed.strftime("%Y-%m-%dT%H:%M:%S") + "'"


def normalized_watermark(value: Any, *, backoff_minutes: int = 0) -> str | None:
    """ISO8601 UTC canonico para PERSISTIR (con retroceso opcional).

    Devuelve None si el valor no parsea: el llamador conserva el watermark
    anterior en vez de envenenar la tabla con un crudo — fail-closed."""
    parsed = parse_watermark_datetime(value)
    if parsed is None:
        return None
    if backoff_minutes:
        parsed = parsed - timedelta(minutes=backoff_minutes)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
