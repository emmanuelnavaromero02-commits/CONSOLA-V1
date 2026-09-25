from __future__ import annotations

import os
from typing import Any

from .successfactors_foundation_fallbacks import FOUNDATION_GOLD_FALLBACK_SQL
from .successfactors_talent_core_fallbacks import TALENT_CORE_FALLBACK_SQL
from .successfactors_talent_empty_fallbacks import TALENT_EMPTY_FALLBACK_SQL
from .successfactors_talent_readfree_fallbacks import TALENT_READFREE_EMPTY_SQL
from .successfactors_talent_runtime_fallbacks import TALENT_RUNTIME_FALLBACK_SQL


_MISSING_DEPENDENCY_MARKERS = (
    "no files found",
    "source_files_missing",
    "dependency_not_materialized",
    "missing_materialized_dependencies",
    "404 (not found)",
    "(http 404)",
)

_INFRA_FAILURE_MARKERS = (
    "list-type=2",
    "(http 400)",
    "(http 401)",
    "(http 403)",
    "(http 408)",
    "(http 416)",
    "(http 429)",
    "(http 500)",
    "(http 502)",
    "(http 503)",
    "(http 504)",
    "connection refused",
    "timed out",
    "could not establish connection",
    "failed to read connection",
    "name or service not known",
)

TALENT_GOLD_FALLBACK_SQL = {
    **TALENT_CORE_FALLBACK_SQL,
    **TALENT_RUNTIME_FALLBACK_SQL,
    **TALENT_EMPTY_FALLBACK_SQL,
}

EMPLOYEE_CENTRAL_FALLBACK_SQL: dict[str, str] = {
    "sap_successfactors_employees_anomalies": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS anomaly_type,
    NULL::VARCHAR AS severity,
    NULL::VARCHAR AS details,
    CURRENT_TIMESTAMP AS detected_at
WHERE FALSE
""",
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
    **EMPLOYEE_CENTRAL_FALLBACK_SQL,
}


def is_missing_successfactors_dependency_error(exc: Exception | Any) -> bool:
    text = " ".join(
        str(part) for part in getattr(exc, "args", ()) or (str(exc),)
    ).lower()
    if any(marker in text for marker in _INFRA_FAILURE_MARKERS):
        return False
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


def readfree_empty_dataset_for_successfactors(
    ds: dict[str, Any], exc: Exception | Any
) -> dict[str, Any] | None:
    name = str(ds.get("name") or "")
    sql = TALENT_READFREE_EMPTY_SQL.get(name)
    if not sql or not is_missing_successfactors_dependency_error(exc):
        return None
    return {
        **ds,
        "sql_def": sql.strip(),
        "sources": [],
        "description": (
            str(ds.get("description") or "").strip()
            + " Proyeccion vacia read-free: el tenant no expone las fuentes de talento."
        ).strip(),
    }


def _strict_fallback_enabled() -> bool:
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
        return True


def annotate_operational_fallback(
    dataset_name: str,
    result: dict[str, Any],
    original_error: str,
) -> dict[str, Any]:
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
