"""E5a — outcome → calibración AUTOMÁTICA (el ciclo Evoluciona, sin manos).

Hasta ahora cada outcome evaluado requería un POST /calibration/observe
manual: el motor bayesiano (calibration.py, completo desde antes) solo
aprendía si alguien se acordaba de avisarle. Este autopiloto cierra el lazo:
tras cada ciclo autónomo (run_intelligence en modo gold_refresh) barre los
prediction_outcomes evaluados que aún no tienen observación verificada y los
observa por la MISMA ruta autoritativa de siempre (observation_service.observe
reconstruye la verdad del servidor; el payload es solo source_type+source_id,
así que aquí no se afirma nada que la base no respalde).

Doctrina intacta:
- La elegibilidad espeja EXACTAMENTE las condiciones del trigger de autoridad
  (99zzf): hit/miss, evaluación completa, señal observed con horizonte y
  procedencia — lo que no cumple queda PENDIENTE con conteo visible, jamás se
  fuerza ni se fabrica.
- Idempotente por construcción: observe() re-usa la idempotency_key del
  outcome; un replay devuelve la observación existente.
- Best-effort aislado: un outcome que falla no detiene a los demás, y el
  autopiloto JAMÁS tumba el run de inteligencia que lo invoca.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import calibration_observation_service, engine_policy

logger = logging.getLogger(__name__)

MAX_OUTCOMES_PER_SWEEP = 25

# Espejo de las condiciones del trigger de autoridad (99zzf) + "sin
# observación verificada todavía". Solo IDs: la verdad la reconstruye observe().
_PENDING_SQL = """
SELECT outcome.id::text AS outcome_id
  FROM prediction_outcomes outcome
  JOIN intelligence_signals signal
    ON signal.workspace_id = outcome.workspace_id
   AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
   AND signal.signal_id = outcome.signal_id
 WHERE outcome.workspace_id = $1
   AND outcome.evaluation_status IN ('hit', 'miss')
   AND outcome.evaluation_rule_version IS NOT NULL
   AND outcome.evaluated_at IS NOT NULL
   AND outcome.evaluated_by IS NOT NULL
   AND signal.signal_subtype = 'observed'
   AND signal.prediction_horizon_days BETWEEN 1 AND 3650
   AND NULLIF(btrim(signal.metadata->>'source_system'), '') IS NOT NULL
   AND NULLIF(btrim(signal.metadata->>'source_dataset'), '') IS NOT NULL
   AND NULLIF(btrim(signal.metadata->>'evidence_pack_id'), '') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
         FROM calibration_observations obs
        WHERE obs.workspace_id = outcome.workspace_id
          AND obs.source_type = 'prediction_outcome'
          AND obs.source_id = outcome.id::text
          AND obs.provenance_status = 'verified'
   )
 ORDER BY outcome.evaluated_at ASC
 LIMIT $2
"""


async def observe_pending_outcomes(
    user: dict, *, limit: int = MAX_OUTCOMES_PER_SWEEP
) -> dict[str, Any]:
    """Barre y observa los outcomes evaluados pendientes del workspace.

    Devuelve un resumen exacto (contados, observados, fallidos con razón) —
    cero error silencioso. Nunca lanza: el llamador (el ciclo) sigue vivo."""
    if not engine_policy.math_engines_enabled():
        return {"status": "paused", "reason": engine_policy.PAUSED_REASON}
    limit = max(1, min(int(limit or MAX_OUTCOMES_PER_SWEEP), 200))
    summary: dict[str, Any] = {
        "pending_found": 0,
        "observed": 0,
        "failed": [],
    }
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        rows = await conn.fetch(_PENDING_SQL, workspace_id, limit)
    outcome_ids = [str(row["outcome_id"]) for row in rows]
    summary["pending_found"] = len(outcome_ids)
    for outcome_id in outcome_ids:
        try:
            await calibration_observation_service.observe(
                user,
                {"source_type": "prediction_outcome", "source_id": outcome_id},
            )
            summary["observed"] += 1
        except Exception as exc:  # noqa: BLE001 — aislar por outcome
            detail = str(getattr(exc, "detail", exc))[:200]
            summary["failed"].append({"outcome_id": outcome_id, "reason": detail})
            logger.warning(
                "calibration autopilot: outcome %s no observado: %s",
                outcome_id,
                detail,
            )
    return summary


async def run_best_effort(user: dict) -> dict[str, Any] | None:
    """Envoltura para el ciclo: cualquier fallo se reporta como None + log,
    jamás interrumpe el run de inteligencia."""
    try:
        return await observe_pending_outcomes(user)
    except Exception as exc:  # noqa: BLE001
        logger.warning("calibration autopilot sweep failed: %s", exc)
        return None
