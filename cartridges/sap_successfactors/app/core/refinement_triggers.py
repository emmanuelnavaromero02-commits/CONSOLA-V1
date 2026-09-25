from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from app.core.request_context import refinement_security_context
from app.core.dataset_orders import (
    SUCCESSFACTORS_GOLD_FOUNDATION_ORDER,
    SUCCESSFACTORS_GOLD_TALENT_ORDER,
    SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER,
)


REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
RUNTIME_REQUEST_TIMEOUT_SECONDS = 60
RUNTIME_JOB_DEADLINE_SECONDS = 3 * 3600


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
    if internal_service == "airflow":
        name = f"sap_successfactors_{entity.strip().lower()}_latest"
        async with httpx.AsyncClient(timeout=RUNTIME_REQUEST_TIMEOUT_SECONDS) as client:
            status_code, payload = await _runtime_materialize(
                client,
                name,
                api_key=api_key,
                security_context=security_context,
                phase="entity-silver",
            )
        if status_code >= 400:
            return {
                "status": "partial",
                "source": source,
                "refreshed": 0,
                "results": [{"name": name, "status": "error", "error": payload}],
            }
        return {
            "status": "success",
            "source": source,
            "refreshed": 1,
            "results": [{"name": name, "status": "ok", **payload}],
        }
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


def successfactors_curated_silver_datasets_for_target(target: str = "all") -> list[str]:
    target = str(target or "all").strip().lower()
    if target == "foundation":
        return []
    return list(SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER)


async def _runtime_materialize(
    client: "httpx.AsyncClient",
    name: str,
    *,
    api_key: str,
    security_context: dict | None,
    phase: str,
) -> tuple[int, Any]:
    import secrets as _secrets

    from runtime_security_context import build_materialize_context
    from service_job_client import ServiceJobError, ambient_idempotency_key, run_service_job_async

    scoped = security_context or {}

    def body() -> dict[str, Any]:
        return {
            "tool": "materialize",
            "args": {"name": name},
            "security_context": build_materialize_context(
                tenant_id=str(scoped.get("tenant_id") or ""),
                workspace_id=str(scoped.get("workspace_id") or ""),
                cartridge_id="sap_successfactors",
                dataset_name=name,
                run_id=_secrets.token_hex(16),
            ),
        }

    try:
        payload = await run_service_job_async(
            client,
            f"{REFINEMENT_URL}/mcp/invoke",
            headers={"x-api-key": api_key, "x-internal-service": "airflow"},
            key=ambient_idempotency_key(
                "sap_successfactors",
                phase,
                name,
                scoped.get("tenant_id"),
                scoped.get("workspace_id"),
            ),
            json=body,
            deadline_seconds=RUNTIME_JOB_DEADLINE_SECONDS,
        )
    except ServiceJobError as exc:
        return exc.status_code or 502, exc.result if exc.result is not None else {"error": str(exc)}
    except httpx.HTTPStatusError as exc:
        try:
            return exc.response.status_code, exc.response.json()
        except Exception:  # noqa: BLE001
            return exc.response.status_code, {"text": exc.response.text[:300]}
    return 200, payload if isinstance(payload, dict) else {"result": payload}


async def _refresh_dataset(
    client: "httpx.AsyncClient",
    name: str,
    *,
    use_runtime_route: bool,
    api_key: str,
    headers: dict[str, str],
    security_context: dict | None,
    phase: str,
) -> tuple[int, Any]:
    if use_runtime_route:
        return await _runtime_materialize(
            client, name, api_key=api_key, security_context=security_context, phase=phase
        )
    response = await client.post(
        f"{REFINEMENT_URL}/datasets/{quote(name, safe='')}/refresh",
        headers=headers,
    )
    try:
        return response.status_code, response.json()
    except Exception:  # noqa: BLE001
        return response.status_code, {"text": response.text[:300]}


async def trigger_successfactors_gold_refresh(
    target: str = "all",
    security_context: dict | None = None,
) -> dict[str, Any]:
    silver_datasets = successfactors_curated_silver_datasets_for_target(target)
    datasets = successfactors_gold_datasets_for_target(target)
    api_key, internal_service = _refinement_auth()
    use_runtime_route = internal_service == "airflow"
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
    silver_results: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    timeout = RUNTIME_REQUEST_TIMEOUT_SECONDS if use_runtime_route else 600
    async with httpx.AsyncClient(timeout=timeout) as client:
        for name in silver_datasets:
            try:
                status_code, payload = await _refresh_dataset(
                    client,
                    name,
                    use_runtime_route=use_runtime_route,
                    api_key=api_key,
                    headers=headers,
                    security_context=security_context,
                    phase="curated-silver",
                )
                if status_code >= 400:
                    silver_results.append(
                        {
                            "name": name,
                            "status": "error",
                            "status_code": status_code,
                            "error": payload,
                        }
                    )
                    continue
                silver_results.append(
                    {
                        "name": name,
                        "status": "ok",
                        "row_count": payload.get("row_count"),
                        "storage_uri": payload.get("storage_uri"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                silver_results.append(
                    {
                        "name": name,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        for name in datasets:
            try:
                status_code, payload = await _refresh_dataset(
                    client,
                    name,
                    use_runtime_route=use_runtime_route,
                    api_key=api_key,
                    headers=headers,
                    security_context=security_context,
                    phase="gold",
                )
                if status_code >= 400:
                    results.append(
                        {
                            "name": name,
                            "status": "error",
                            "status_code": status_code,
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
    silver_ok = sum(1 for item in silver_results if item.get("status") == "ok")
    return {
        "status": "success" if ok == len(results) else "partial" if ok else "failed",
        "target": target,
        "materialized": ok,
        "total": len(results),
        "results": results,
        "silver_status": (
            "success"
            if silver_results and silver_ok == len(silver_results)
            else "partial"
            if silver_ok
            else "failed"
            if silver_results
            else "skipped"
        ),
        "silver_materialized": silver_ok,
        "silver_total": len(silver_results),
        "silver_results": silver_results,
    }
