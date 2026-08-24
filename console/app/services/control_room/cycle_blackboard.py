"""E5b — la pizarra del ciclo: UNA lectura de lo que la máquina hizo y aprendió.

Los cuadernos ya existían, cada uno por su lado: pipeline_runs (qué corrió el
ciclo autónomo), intelligence_signals (qué encontró), control_room_lessons
(qué reglas aprendió el copiloto) y calibration_observations (cómo se
movieron las creencias con cada outcome — ahora automático vía E5a). Esta
pizarra los compone en una sola superficie scoped por workspace, para que el
siguiente ciclo, el copiloto y el dueño lean el MISMO estado sin recorrer
cuatro tablas.

Integrar, no reconstruir: cero tablas nuevas, cero escritores nuevos — solo
la lectura unificada que faltaba (el "Evoluciona" visible de la demo).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import auth
from app.services.db_scope import scoped_db_for_user

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

_CYCLES_SQL = """
SELECT run_id, cartridge_id, entity, status,
       started_at, record_count,
       extra->>'transition' AS transition,
       extra->>'target' AS target
  FROM pipeline_runs
 WHERE workspace_id::text = $1
   AND extra->>'transition' IS NOT NULL
 ORDER BY started_at DESC NULLS LAST
 LIMIT $2
"""

_SIGNALS_SQL = """
SELECT signal_id, metric, severity, signal_subtype, created_at
  FROM intelligence_signals
 WHERE workspace_id::text = $1
 ORDER BY created_at DESC
 LIMIT $2
"""

_LESSONS_SQL = """
SELECT item_id, cartridge_id, anomaly_type, rule, confidence, created_at
  FROM control_room_lessons
 WHERE workspace_id::text = $1
 ORDER BY created_at DESC
 LIMIT $2
"""

_CALIBRATION_SQL = """
SELECT calibration_group, source_type, source_id, provenance_status, created_at
  FROM calibration_observations
 WHERE workspace_id::text = $1
 ORDER BY created_at DESC
 LIMIT $2
"""


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif value is None or isinstance(value, (str, int, float, bool)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


async def read_blackboard(user: dict, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Compone la pizarra del workspace: ciclos, señales, lecciones y
    movimientos de calibración — todo reciente, todo scoped, nada inventado
    (una sección sin filas es una lista vacía, jamás un relleno)."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        cycles = await conn.fetch(_CYCLES_SQL, workspace_id, limit)
        signals = await conn.fetch(_SIGNALS_SQL, workspace_id, limit)
        lessons = await conn.fetch(_LESSONS_SQL, workspace_id, limit)
        calibration = await conn.fetch(_CALIBRATION_SQL, workspace_id, limit)
    return {
        "workspace_id": workspace_id,
        "limit": limit,
        "cycles": [_jsonable(dict(row)) for row in cycles],
        "signals": [_jsonable(dict(row)) for row in signals],
        "lessons": [_jsonable(dict(row)) for row in lessons],
        "calibration": [_jsonable(dict(row)) for row in calibration],
    }
