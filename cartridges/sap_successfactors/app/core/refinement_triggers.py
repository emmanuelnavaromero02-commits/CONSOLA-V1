from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from app.core.request_context import refinement_security_context
# Fuente unica de verdad de los ordenes de datasets gold SF (ver dataset_orders.py
# + app/config/gold_dataset_orders.json). NO redefinir como literales aqui.
from app.core.dataset_orders import (
    SUCCESSFACTORS_GOLD_FOUNDATION_ORDER,
    SUCCESSFACTORS_GOLD_TALENT_ORDER,
    SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER,
)


# How long a caller waits for refinement to materialize one dataset.
#
# This was 300 s on the two silver paths and 600 s on the gold loop -- which
# also materializes silver datasets, so the same work had two different budgets
# depending on which entry point reached it. Nothing in the code justified the
# split.
#
# 300 s is below the floor for a large entity. Measured 2026-09-22 on a host
# identical to production: SuccessFactors EmployeeTime is 14.53 GB of raw
# parquet in the materialized scope, read from S3 at ~34 MB/s, so the read
# alone cannot finish in under ~430 s. The budget was shorter than the work,
# and the caller recorded "partial" for a materialization the server was still
# running -- and, being shielded, would go on to complete and publish.
#
# One name, one value, the larger of the two that were already in use.
MATERIALIZE_TIMEOUT_SECONDS = 600

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")


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
        # Same reason as the gold loop below: refinement only accepts the
        # purpose-bound runtime envelope from the airflow perimeter, so the
        # entity's conventional silver is materialized through /mcp/invoke
        # instead of /refresh-by-source.
        name = f"sap_successfactors_{entity.strip().lower()}_latest"
        async with httpx.AsyncClient(timeout=MATERIALIZE_TIMEOUT_SECONDS) as client:
            response = await _runtime_materialize_request(
                client, name, api_key=api_key, security_context=security_context
            )
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = {"text": response.text[:300]}
        if response.status_code >= 400:
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
    async with httpx.AsyncClient(timeout=MATERIALIZE_TIMEOUT_SECONDS) as client:
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


def _runtime_materialize_request(
    client: "httpx.AsyncClient",
    name: str,
    *,
    api_key: str,
    security_context: dict | None,
):
    """Materialize one dataset through the refinement runtime (v2) route.

    Refinement routes every trusted ``source=airflow`` context through the
    purpose-bound hmac-v2 runtime validation, so the plain signed-context
    ``/datasets/{name}/refresh`` call the cartridge container uses is
    rejected there. From the Airflow workers we take the same route the
    dataset_refresh_chain already takes: an ``/mcp/invoke`` materialize
    with the envelope minted by runtime_security_context (importable only
    inside the Airflow container, hence the local import).
    """
    import secrets as _secrets

    from runtime_security_context import build_materialize_context

    scoped = security_context or {}
    ctx = build_materialize_context(
        tenant_id=str(scoped.get("tenant_id") or ""),
        workspace_id=str(scoped.get("workspace_id") or ""),
        cartridge_id="sap_successfactors",
        dataset_name=name,
        run_id=_secrets.token_hex(16),
    )
    return client.post(
        f"{REFINEMENT_URL}/mcp/invoke",
        headers={"x-api-key": api_key, "x-internal-service": "airflow"},
        json={"tool": "materialize", "args": {"name": name}, "security_context": ctx},
    )


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
    async with httpx.AsyncClient(timeout=MATERIALIZE_TIMEOUT_SECONDS) as client:
        for name in silver_datasets:
            try:
                if use_runtime_route:
                    response = await _runtime_materialize_request(
                        client, name, api_key=api_key, security_context=security_context
                    )
                else:
                    response = await client.post(
                        f"{REFINEMENT_URL}/datasets/{quote(name, safe='')}/refresh",
                        headers=headers,
                    )
                try:
                    payload = response.json()
                except Exception:
                    payload = {"text": response.text[:300]}
                if response.status_code >= 400:
                    silver_results.append(
                        {
                            "name": name,
                            "status": "error",
                            "status_code": response.status_code,
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
                if use_runtime_route:
                    response = await _runtime_materialize_request(
                        client, name, api_key=api_key, security_context=security_context
                    )
                else:
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
