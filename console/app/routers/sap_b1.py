from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from app.services import audit_service, auth
from app.services.control_room import sap_b1_digest
from app.services.control_room.api import _internal_headers
from app.services.db_scope import scoped_db_for_user
from app.services.csrf import require_csrf
from app.services.permissions import require_permission
from app.services.security_context import build_security_context
from app.services.service_urls import service_url

CARTRIDGE = "sap_b1"
CONNECTION_ID = "default"
PARAMETERS_FIELD = "business_parameters"
MAX_PARAMETERS_BYTES = 256 * 1024
MAX_FINANCE_BYTES = 5 * 1024 * 1024
DAGS = ("sap_b1_refresh", "dataset_refresh_chain", "agent_runner")
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
FINANCE_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

router = APIRouter(prefix="/api/sap-b1", tags=["sap_b1"])


def _cartridge_url() -> str:
    return service_url("SAP_B1_URL", "http://sap-b1:8206", "http://127.0.0.1:8206").rstrip("/")


def _vault_url() -> str:
    return service_url("VAULT_URL", "http://vault:8300", "http://127.0.0.1:8300").rstrip("/")


def _signed_context(user: dict) -> dict[str, Any]:
    ctx = build_security_context(user)
    if not ctx.get("trusted") or not ctx.get("tenant_id") or not ctx.get("workspace_id"):
        raise HTTPException(403, "active tenant/workspace is required")
    return ctx


def _vault_headers(user: dict) -> dict[str, str]:
    return {**_internal_headers("VAULT"), "x-security-context": json.dumps(_signed_context(user), ensure_ascii=False)}


def _email_transport() -> str:
    provider = os.environ.get("EMAIL_PROVIDER", "smtp").strip().lower()
    if provider == "ses":
        return "ses"
    host = os.environ.get("SMTP_HOST", "mailhog").strip().strip('"').lower()
    return "sin_configurar" if host in {"", "mailhog", "localhost", "127.0.0.1"} else "smtp"


async def _cartridge(method: str, path: str, *, user: dict | None = None, body: dict | None = None,
                     timeout: httpx.Timeout = TIMEOUT) -> httpx.Response:
    payload = dict(body or {})
    if user is not None:
        payload["security_context"] = _signed_context(user)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(method, f"{_cartridge_url()}{path}", headers=_internal_headers("CARTRIDGE"),
                                    json=payload if method != "GET" else None)


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail or f"sap_b1 respondió {response.status_code}")[:500]


async def _connection(user: dict) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(f"{_vault_url()}/connections/{CARTRIDGE}/{quote(CONNECTION_ID, safe='')}",
                                    headers=_vault_headers(user))
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise HTTPException(502, "Vault request failed")
    data = response.json()
    return data if isinstance(data, dict) else None


def _parameters_text(connection: dict[str, Any] | None) -> str:
    if not connection:
        return ""
    fields = connection.get("fields") if isinstance(connection.get("fields"), dict) else {}
    for container in (fields, connection):
        for key in (PARAMETERS_FIELD, "sap_b1_business_parameters", "SAP_B1_BUSINESS_PARAMETERS"):
            value = container.get(key)
            if isinstance(value, str):
                return value
    return ""


async def _catalog() -> list[dict[str, Any]]:
    response = await _cartridge("GET", "/business-parameters/catalog")
    if response.status_code >= 400:
        raise HTTPException(502, _detail(response))
    return list(response.json().get("parameters") or [])


async def _validate(text: str) -> dict[str, Any]:
    response = await _cartridge("POST", "/business-parameters/validate", body={"spec": text})
    if response.status_code == 422:
        raise HTTPException(422, _detail(response))
    if response.status_code >= 400:
        raise HTTPException(502, _detail(response))
    return response.json()


def _completeness(catalog: list[dict[str, Any]], parsed: list[dict[str, Any]]) -> dict[str, Any]:
    set_keys = {item.get("key") for item in parsed if item.get("kind") in ("threshold", "setting")}
    missing = [item["key"] for item in catalog if item.get("default") is None and item["key"] not in set_keys]
    defaulted = [item["key"] for item in catalog if item.get("default") is not None and item["key"] not in set_keys]
    return {"keys_total": len(catalog), "keys_set": len(set_keys & {item["key"] for item in catalog}),
            "missing": missing, "using_default": defaulted,
            "branches": sum(1 for item in parsed if item.get("kind") == "branch"),
            "accounts": sum(1 for item in parsed if item.get("kind") == "account")}


async def _dag_states(user: dict) -> list[dict[str, Any]]:
    from app.services import mcp_registry

    try:
        result = await mcp_registry.invoke("infra", "airflow_list_dags", {}, user=user)
    except Exception:  # noqa: BLE001
        return [{"dag_id": dag_id, "present": None, "paused": None} for dag_id in DAGS]
    raw = result.get("dags") if isinstance(result, dict) else result
    by_id = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            by_id[str(item.get("dag_id") or item.get("id") or "")] = item
    return [
        {"dag_id": dag_id, "present": dag_id in by_id,
         "paused": bool(by_id[dag_id].get("is_paused", by_id[dag_id].get("paused"))) if dag_id in by_id else None}
        for dag_id in DAGS
    ]


@router.get("/overview", dependencies=[Depends(require_permission("datasets.read"))])
async def overview(user: dict = Depends(require_permission("datasets.read"))) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        installed = await conn.fetchval(
            "SELECT status FROM cartridge_installations WHERE cartridge_id = $1 AND workspace_id = $2::uuid "
            "ORDER BY updated_at DESC LIMIT 1", CARTRIDGE, workspace_id)
        recipients = await sap_b1_digest.recipients(conn, tenant_id, workspace_id)
        last = await conn.fetchrow(
            "SELECT local_date, status, delivered, recipients FROM sap_b1_digest_deliveries "
            "WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid ORDER BY local_date DESC LIMIT 1",
            tenant_id, workspace_id)
    connection = await _connection(user)
    text = _parameters_text(connection)
    parameters: dict[str, Any] = {"loaded": bool(text.strip())}
    try:
        catalog = await _catalog()
        parsed = (await _validate(text))["parameters"] if text.strip() else []
        parameters.update(_completeness(catalog, parsed), valid=True)
    except HTTPException as exc:
        parameters.update(valid=False, error=str(exc.detail)[:300])
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            status_response = await client.get(
                f"{_cartridge_url()}/connector/status",
                headers={**_internal_headers("CARTRIDGE"),
                         "x-security-context": json.dumps(_signed_context(user), ensure_ascii=False)})
        connector = status_response.json() if status_response.status_code < 400 else {"present": False, "error": _detail(status_response)}
    except httpx.HTTPError:
        connector = {"present": False, "error": "el cartucho sap_b1 no respondió"}
    return {
        "installed": installed,
        "connection": {"present": connection is not None},
        "parameters": parameters,
        "connector": connector,
        "dags": await _dag_states(user),
        "digest": {
            "recipients": len(recipients),
            "transport": _email_transport(),
            "last": {"local_date": last["local_date"].isoformat(), "status": last["status"],
                     "delivered": last["delivered"], "recipients": last["recipients"]} if last else None,
        },
    }


@router.get("/indicators", dependencies=[Depends(require_permission("datasets.read"))])
async def indicators(user: dict = Depends(require_permission("datasets.read"))) -> dict[str, Any]:
    response = await _cartridge("GET", "/indicators")
    if response.status_code >= 400:
        raise HTTPException(502, _detail(response))
    return response.json()


@router.get("/mapping", dependencies=[Depends(require_permission("datasets.read"))])
async def mapping(user: dict = Depends(require_permission("datasets.read"))) -> dict[str, Any]:
    from app.services.seed_packaged_catalog import dataset_files, load_packaged_manifest
    from app.services.seed_packaged_datasets import _EXPECTED_CATALOG_DIGEST, _EXPECTED_CATALOG_FILES, _REGISTRY

    response = await _cartridge("GET", "/entities")
    if response.status_code >= 400:
        raise HTTPException(502, _detail(response))
    entities = response.json().get("entities") or []
    try:
        manifest = load_packaged_manifest(dataset_files(_REGISTRY, expected_files=_EXPECTED_CATALOG_FILES,
                                                        expected_digest=_EXPECTED_CATALOG_DIGEST))
        datasets = manifest.get(CARTRIDGE, [])
    except (OSError, ValueError):
        datasets = []
    used_by: dict[str, list[str]] = {}
    for dataset in datasets:
        for source in dataset.get("sources") or []:
            parts = str(source).split("/")
            if len(parts) == 3 and parts[0] == "raw" and parts[1] == CARTRIDGE:
                used_by.setdefault(parts[2], []).append(dataset["name"])
    rows = []
    for entity in entities:
        name = entity.get("entity")
        rows.append({
            "entity": name,
            "business_name": entity.get("business_name"),
            "description": entity.get("description"),
            "mode": entity.get("mode"),
            "date_field": entity.get("date_field"),
            "primary_key": entity.get("primary_key"),
            "fields": list(entity.get("select_fields") or []),
            "datasets": sorted(used_by.get(name, [])),
        })
    return {"entities": rows}


@router.get("/business-parameters", dependencies=[Depends(require_permission("control_room.write"))])
async def get_business_parameters(user: dict = Depends(require_permission("control_room.write"))) -> dict[str, Any]:
    text = _parameters_text(await _connection(user))
    catalog = await _catalog()
    parsed = (await _validate(text))["parameters"] if text.strip() else []
    return {"text": text, "parameters": parsed, "catalog": catalog, **_completeness(catalog, parsed)}


@router.put(
    "/business-parameters",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def put_business_parameters(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("control_room.write")),
) -> dict[str, Any]:
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_PARAMETERS_BYTES:
        raise HTTPException(422, "text must be a string of at most 256 KB")
    validated = await _validate(text)
    connection = await _connection(user) or {}
    fields = dict(connection.get("fields") or {}) if isinstance(connection.get("fields"), dict) else {}
    fields[PARAMETERS_FIELD] = text
    stored = {key: value for key, value in connection.items() if key not in {"conn_id", "fields"}}
    stored["fields"] = fields
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.put(f"{_vault_url()}/connections/{CARTRIDGE}/{quote(CONNECTION_ID, safe='')}",
                                    headers=_vault_headers(user), json=stored)
    if response.status_code >= 400:
        raise HTTPException(502, "Vault request failed")
    refresh = await _cartridge("POST", "/business-parameters/refresh", user=user)
    await audit_service.record_event(
        user_id=user.get("id"), email=user.get("email"), action="sap_b1.business_parameters.update",
        resource_type="vault_connection", resource_id=f"{CARTRIDGE}/{CONNECTION_ID}",
        status="success" if refresh.status_code < 400 else "partial",
        metadata={"parameters": validated.get("count"), "refresh_status": refresh.status_code},
    )
    return {"saved": True, "count": validated.get("count"), "refreshed": refresh.status_code < 400,
            "refresh_error": None if refresh.status_code < 400 else _detail(refresh)}


@router.post(
    "/finance-runs",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def post_finance_run(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("control_room.write")),
) -> dict[str, Any]:
    csv = body.get("csv") if isinstance(body, dict) else None
    if not isinstance(csv, str) or not csv.strip() or len(csv.encode("utf-8")) > MAX_FINANCE_BYTES:
        raise HTTPException(422, "csv must be a non-empty string of at most 5 MB")
    response = await _cartridge("POST", "/finance-runs", user=user, body={"csv": csv}, timeout=FINANCE_TIMEOUT)
    if response.status_code == 422:
        raise HTTPException(422, _detail(response))
    if response.status_code >= 400:
        raise HTTPException(502, _detail(response))
    result = response.json()
    await audit_service.record_event(
        user_id=user.get("id"), email=user.get("email"), action="sap_b1.finance_run.upload",
        resource_type="dataset_input", resource_id=f"{CARTRIDGE}/FinanceManualRun",
        status="success", metadata={"rows": result.get("rows"), "companies": result.get("companies")},
    )
    return {"rows": result.get("rows"), "indicators": result.get("indicators"), "companies": result.get("companies")}


@router.get("/recipients", dependencies=[Depends(require_permission("control_room.write"))])
async def list_recipients(user: dict = Depends(require_permission("control_room.write"))) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        emails = await sap_b1_digest.recipients(conn, tenant_id, workspace_id)
    return {"recipients": emails, "max": sap_b1_digest.MAX_RECIPIENTS, "transport": _email_transport()}


@router.post(
    "/recipients",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def add_recipient(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("control_room.write")),
) -> dict[str, Any]:
    email = sap_b1_digest.normalize_email(body.get("email") if isinstance(body, dict) else None)
    if not email:
        raise HTTPException(422, "email is not valid")
    pool = await auth.pool()
    try:
        async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
            added = await sap_b1_digest.add_recipient(conn, tenant_id, workspace_id, email, user.get("id"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await audit_service.record_event(
        user_id=user.get("id"), email=user.get("email"), action="sap_b1.digest_recipient.add",
        resource_type="sap_b1_digest_recipient", resource_id=email, status="success" if added else "noop",
    )
    return {"added": added, "email": email}


@router.delete(
    "/recipients/{email}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def remove_recipient(email: str, user: dict = Depends(require_permission("control_room.write"))) -> dict[str, Any]:
    clean = sap_b1_digest.normalize_email(email)
    if not clean:
        raise HTTPException(422, "email is not valid")
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        removed = await sap_b1_digest.remove_recipient(conn, tenant_id, workspace_id, clean)
    if not removed:
        raise HTTPException(404, "recipient not found")
    await audit_service.record_event(
        user_id=user.get("id"), email=user.get("email"), action="sap_b1.digest_recipient.remove",
        resource_type="sap_b1_digest_recipient", resource_id=clean, status="success",
    )
    return {"removed": True, "email": clean}
