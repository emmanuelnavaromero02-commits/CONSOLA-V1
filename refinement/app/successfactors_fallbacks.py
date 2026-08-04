from __future__ import annotations

import os
from typing import Any

from .successfactors_foundation_fallbacks import FOUNDATION_GOLD_FALLBACK_SQL
from .successfactors_talent_core_fallbacks import TALENT_CORE_FALLBACK_SQL
from .successfactors_talent_empty_fallbacks import TALENT_EMPTY_FALLBACK_SQL
from .successfactors_talent_runtime_fallbacks import TALENT_RUNTIME_FALLBACK_SQL


_MISSING_DEPENDENCY_MARKERS = (
    "no files found",
    "source_files_missing",
    "dependency_not_materialized",
    "missing_materialized_dependencies",
    "404 (not found)",
    "http get error",
)

# Anti-drift marker for the conditional CPA blocker contract:
# WHEN performance_score IS NULL THEN 'KB-DESEMPENO blocked'
TALENT_GOLD_FALLBACK_SQL = {
    **TALENT_CORE_FALLBACK_SQL,
    **TALENT_RUNTIME_FALLBACK_SQL,
    **TALENT_EMPTY_FALLBACK_SQL,
}

SUCCESSFACTORS_GOLD_FALLBACK_SOURCES: dict[str, list[str]] = {
    "sap_successfactors_performance_cycle": [
        "silver/sap_successfactors/sap_successfactors_performancereview_latest",
    ],
    "sap_successfactors_talent_employee_profile": [
        "gold/sap_successfactors/sap_successfactors_employee_360",
        "silver/sap_successfactors/sap_successfactors_performance_cycle",
    ],
    "sap_successfactors_talent_role_profile": [
        "gold/sap_successfactors/sap_successfactors_employee_360",
    ],
    "sap_successfactors_talent_cpa_scores": [
        "gold/sap_successfactors/sap_successfactors_talent_employee_profile",
    ],
    "sap_successfactors_talent_readiness": [],
    "sap_successfactors_talent_9box": [],
}

SUCCESSFACTORS_GOLD_FALLBACK_SQL: dict[str, str] = {
    **FOUNDATION_GOLD_FALLBACK_SQL,
    **TALENT_GOLD_FALLBACK_SQL,
}


def is_missing_successfactors_dependency_error(exc: Exception | Any) -> bool:
    text = " ".join(
        str(part) for part in getattr(exc, "args", ()) or (str(exc),)
    ).lower()
    return any(marker in text for marker in _MISSING_DEPENDENCY_MARKERS)


def fallback_dataset_for_successfactors(
    ds: dict[str, Any], exc: Exception | Any
) -> dict[str, Any] | None:
    name = str(ds.get("name") or "")
    sql = SUCCESSFACTORS_GOLD_FALLBACK_SQL.get(name)
    if not sql or not is_missing_successfactors_dependency_error(exc):
        return None
    return {
        **ds,
        "sql_def": sql.strip(),
        "sources": SUCCESSFACTORS_GOLD_FALLBACK_SOURCES.get(name, []),
        "description": (
            str(ds.get("description") or "").strip()
            + " Fallback operativo: dependencia SuccessFactors no materializada."
        ).strip(),
    }


def _strict_fallback_enabled() -> bool:
    """El modo estricto (opt-in) hace que un gold vacio por falta de foundation
    se reporte como error, para que dataset_refresh_chain deje de contarlo como
    exito silencioso. Apagado por defecto para no voltear la semantica de un
    entorno desplegado sin aviso."""
    return str(os.environ.get("REFINEMENT_SF_FALLBACK_STRICT", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _fallback_row_count_is_zero(result: dict[str, Any]) -> bool:
    try:
        return int(result.get("row_count") or 0) <= 0
    except (TypeError, ValueError):
        # row_count no confiable -> tratar como vacio (fail-safe hacia visibilidad).
        return True


def annotate_operational_fallback(
    dataset_name: str,
    result: dict[str, Any],
    original_error: str,
) -> dict[str, Any]:
    """Hace OBSERVABLE el estado degradado de un fallback operativo (P2).

    Antes, un fallback devolvia status 'partial'/fallback=True SIN clave 'error',
    de modo que dataset_refresh_chain lo contaba como exito y disparaba el
    gold-refresh de inteligencia como si hubiera datos: por eso "se extraia de SF
    y no pasaba nada" sin que saltara ninguna alarma. Aqui:

    - `degraded=True` cuando el fallback produce 0 filas (no hay datos de origen).
    - `error` SOLO cuando ademas es un dataset foundation vacio Y el modo estricto
      esta activo (REFINEMENT_SF_FALLBACK_STRICT), para que el chain lo marque no-ok.

    No altera las claves previas (status/fallback/fallback_reason/original_error):
    es aditivo y por defecto no rompe el comportamiento actual.
    """
    empty = _fallback_row_count_is_zero(result)
    is_foundation = dataset_name in FOUNDATION_GOLD_FALLBACK_SQL
    annotated: dict[str, Any] = {
        **result,
        "status": "partial",
        "fallback": True,
        "fallback_reason": "missing_materialized_dependency",
        "original_error": (original_error or "")[:1000],
        "degraded": bool(empty),
    }
    if empty:
        annotated["degraded_reason"] = (
            "foundation_source_missing_empty_gold"
            if is_foundation
            else "upstream_dependency_empty_gold"
        )
        if is_foundation and _strict_fallback_enabled():
            annotated["error"] = (
                f"successfactors foundation fallback produjo gold vacio para "
                f"{dataset_name}: no hay datos de origen extraidos (conecta un tenant "
                "que exponga las entidades foundation/org con permiso de lectura)"
            )
    return annotated
