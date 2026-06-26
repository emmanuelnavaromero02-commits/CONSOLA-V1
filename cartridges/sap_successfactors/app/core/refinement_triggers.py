from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from app.core.request_context import refinement_security_context


REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")

SUCCESSFACTORS_GOLD_FOUNDATION_ORDER = [
    "sap_successfactors_employee_360",
    "sap_successfactors_org_structure",
    "sap_successfactors_headcount_by_location",
    "sap_successfactors_headcount_by_department",
    "sap_successfactors_headcount_by_company",
    "sap_successfactors_manager_hierarchy",
]

SUCCESSFACTORS_GOLD_TALENT_ORDER = [
    "sap_successfactors_talent_employee_profile",
    "sap_successfactors_talent_role_profile",
    "sap_successfactors_talent_mobility_history",
    "sap_successfactors_talent_cpa_scores",
    "sap_successfactors_talent_readiness",
    "sap_successfactors_talent_9box",
    "sap_successfactors_talent_9box_operational",
    "sap_successfactors_talent_retention_risk",
    "sap_successfactors_talent_promotion_alignment",
    "sap_successfactors_talent_calibration_sensitivity",
    "sap_successfactors_talent_role_fit_assignments",
    "sap_successfactors_talent_action_candidates",
    "sap_successfactors_talent_signals",
]


def _refinement_auth() -> tuple[str, str]:
    airflow_key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT", "")
    if airflow_key:
        return airflow_key, "airflow"
    cartridge_key = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", "")
    if cartridge_key:
        return cartridge_key, "cartridge-sap_successfactors"
    if os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}:
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy, "cartridge-sap_successfactors"
    raise RuntimeError("Missing INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT or INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT")


def _refinement_security_context_for_service(
    security_context: dict | None,
    internal_service: str,
) -> dict[str, Any]:
    source = "airflow" if internal_service == "airflow" else "cartridge-sap_successfactors"
    return refinement_security_context(security_context, source=source)


async def trigger_silver_refresh(entity: str, security_context: dict | None = None) -> dict[str, Any]:
    source = f"raw/sap_successfactors/{entity}"
    api_key, internal_service = _refinement_auth()
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/refresh-by-source",
            headers={
                "x-api-key": api_key,
                "x-internal-service": internal_service,
            },
            json={
                "source": source,
                "allow_partial": True,
                "security_context": _refinement_security_context_for_service(
                    security_context,
                    internal_service,
                ),
            },
        )
    response.raise_for_status()
    payload = response.json()
    errors = [r for r in payload.get("results", []) if r.get("status") == "error"]
    if (
        errors
        or payload.get("error")
        or payload.get("status") in {"partial", "skipped"}
        or int(payload.get("refreshed") or 0) <= 0
    ):
        return {"status": "partial", **payload}
    return {"status": "success", **payload}


def successfactors_gold_datasets_for_target(target: str = "all") -> list[str]:
    target = str(target or "all").strip().lower()
    if target == "foundation":
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)
    if target == "talent":
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) + list(SUCCESSFACTORS_GOLD_TALENT_ORDER)
    return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) + list(SUCCESSFACTORS_GOLD_TALENT_ORDER)


async def trigger_successfactors_gold_refresh(
    target: str = "all",
    security_context: dict | None = None,
) -> dict[str, Any]:
    datasets = successfactors_gold_datasets_for_target(target)
    api_key, internal_service = _refinement_auth()
    headers = {
        "x-api-key": api_key,
        "x-internal-service": internal_service,
        "x-security-context": json.dumps(
            _refinement_security_context_for_service(
                security_context,
                internal_service,
            ),
            ensure_ascii=False,
        ),
    }
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=600) as client:
        for name in datasets:
            try:
                response = await client.post(
                    f"{REFINEMENT_URL}/datasets/{quote(name, safe='')}/refresh",
                    headers=headers,
                )
                try:
                    payload = response.json()
                except Exception:
                    payload = {"text": response.text[:300]}
                if response.status_code >= 400:
                    results.append(
                        {
                            "name": name,
                            "status": "error",
                            "status_code": response.status_code,
                            "error": payload,
                        }
                    )
                    continue
                results.append(
                    {
                        "name": name,
                        "status": "ok",
                        "row_count": payload.get("row_count"),
                        "storage_uri": payload.get("storage_uri"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "name": name,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    ok = sum(1 for item in results if item.get("status") == "ok")
    return {
        "status": "success" if ok == len(results) else "partial" if ok else "failed",
        "target": target,
        "materialized": ok,
        "total": len(results),
        "results": results,
    }
