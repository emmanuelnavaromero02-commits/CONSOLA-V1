"""Studio API routes backed by real platform services.

These endpoints are the canonical ``/api/studio/*`` surface used by the
legacy :8000 Studio UI. When a downstream system has no data yet, the endpoint
returns an explicit empty result with source metadata instead of pretending the
feature worked.
"""
from __future__ import annotations

import html
import os
import re
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated
from app.security import get_internal_api_key
from app.services import cartridge_service, dag_templates, mcp_registry, studio_assistant, studio_entities
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/studio", tags=["Studio"])
require_studio_read = require_permission("studio.read")
require_studio_write = require_permission("studio.write")

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _key_for(server: str) -> str:
    pair = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        return pair
    return get_internal_api_key()


def _hdr_for(server: str) -> dict[str, str]:
    return {"x-api-key": _key_for(server), "x-internal-service": "console"}


def _rls_user_context(user: dict | None) -> dict[str, Any]:
    if not user:
        return {}
    role = user.get("role")
    return {
        "id": user.get("id"),
        "email": user.get("email", ""),
        "name": user.get("name") or user.get("email", ""),
        "role": role,
        "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
        "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        "workspace_role": user.get("workspace_role"),
        "_trusted_admin": role == "admin",
    }


def _clean_identifier(value: str, *, label: str) -> str:
    ident = (value or "").strip()
    if not _IDENT_RE.fullmatch(ident):
        raise HTTPException(400, f"Invalid {label}: use letters, numbers and underscores only")
    return ident


async def _optional_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _refinement_invoke(tool: str, args: dict[str, Any], *, timeout: int = 60) -> dict:
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=timeout) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json={"tool": tool, "args": args},
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    return response.json()


async def _refinement_datasets() -> list[dict]:
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=15) as client:
        response = await client.get(f"{REFINEMENT_URL}/datasets")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    payload = response.json()
    datasets = payload.get("datasets") if isinstance(payload, dict) else []
    return datasets if isinstance(datasets, list) else []


async def _rag_sources() -> list[dict]:
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=10) as client:
        response = await client.get(f"{MCP_INFRA_URL.rstrip('/')}/rag/sources")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    payload = response.json()
    sources = payload.get("sources") or payload.get("results") or []
    return sources if isinstance(sources, list) else []


def _svg_for_graph(nodes: list[dict], edges: list[dict]) -> str:
    width = max(760, len(nodes) * 150)
    height = 360
    positions: dict[str, tuple[int, int]] = {}
    lanes = {"cartridge": 40, "entity": 130, "dag": 220, "dataset": 310}
    counters: dict[str, int] = {}
    for node in nodes:
        kind = node.get("kind", "entity")
        counters[kind] = counters.get(kind, 0) + 1
        x = 40 + (counters[kind] - 1) * 180
        positions[node["id"]] = (x, lanes.get(kind, 130))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Inter,Arial,sans-serif;font-size:12px} .node{fill:#fff;stroke:#2563eb;stroke-width:1.5}.edge{stroke:#64748b;stroke-width:1.2;marker-end:url(#arrow)}</style>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#64748b"/></marker></defs>',
    ]
    for edge in edges:
        src = positions.get(edge.get("source"))
        dst = positions.get(edge.get("target"))
        if not src or not dst:
            continue
        parts.append(
            f'<line class="edge" x1="{src[0] + 120}" y1="{src[1] + 20}" '
            f'x2="{dst[0]}" y2="{dst[1] + 20}"/>'
        )
    for node in nodes:
        x, y = positions[node["id"]]
        label = html.escape(str(node.get("label") or node["id"])[:28])
        parts.append(f'<rect class="node" x="{x}" y="{y}" rx="6" width="120" height="40"/>')
        parts.append(f'<text x="{x + 10}" y="{y + 25}">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _dataset_table_name(ds: dict) -> str:
    layer = (ds.get("layer") or "").strip().lower()
    name = _clean_identifier(str(ds.get("name") or ""), label="dataset name")
    if layer in {"gold", "master"}:
        return f"{layer}_{name}"
    return name


def _extract_entities_from_spec(content: str) -> list[dict]:
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"Spec YAML/JSON inválido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "Spec must be a YAML/JSON object")

    entities: list[dict] = []
    raw_entities = parsed.get("entities")
    if isinstance(raw_entities, dict):
        for name, cfg in raw_entities.items():
            cfg = cfg if isinstance(cfg, dict) else {}
            entities.append({"entity": str(name), **cfg})
    elif isinstance(raw_entities, list):
        for item in raw_entities:
            if isinstance(item, str):
                entities.append({"entity": item})
            elif isinstance(item, dict):
                name = item.get("entity") or item.get("name") or item.get("id")
                if name:
                    entities.append({"entity": str(name), **item})

    paths = parsed.get("paths")
    if isinstance(paths, dict):
        for path in paths:
            segments = [
                s for s in str(path).strip("/").split("/")
                if s and not (s.startswith("{") and s.endswith("}"))
            ]
            if segments:
                name = re.sub(r"[^A-Za-z0-9_]", "_", segments[-1]).strip("_")
                if name:
                    entities.append({"entity": name, "description": f"Imported from path {path}"})

    schemas = ((parsed.get("components") or {}).get("schemas") or {})
    if isinstance(schemas, dict):
        for name in schemas:
            entities.append({"entity": str(name), "description": "Imported from OpenAPI schema"})

    seen: set[str] = set()
    out: list[dict] = []
    for item in entities:
        entity = _clean_identifier(str(item.get("entity") or item.get("name") or ""), label="entity")
        if entity in seen:
            continue
        seen.add(entity)
        out.append({
            "entity": entity,
            "display_name": item.get("display_name") or item.get("title") or entity,
            "mode": item.get("mode") or "full",
            "primary_key": item.get("primary_key") or item.get("id_field") or "",
            "dag_id": item.get("dag_id") or "",
            "trigger_type": item.get("trigger_type") or "manual",
            "cron_expression": item.get("cron_expression") or "",
            "description": item.get("description") or "",
            "enabled": bool(item.get("enabled", True)),
        })
    return out


@router.get("/dag-graph")
async def dag_graph(
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    manifest = await cartridge_service.get_cartridge(cartridge)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")

    nodes = [{"id": f"cartridge:{cartridge}", "kind": "cartridge", "label": manifest["name"]}]
    edges: list[dict] = []
    for entity in manifest.get("entities") or []:
        entity_id = f"entity:{entity['entity']}"
        nodes.append({"id": entity_id, "kind": "entity", "label": entity.get("display_name") or entity["entity"]})
        edges.append({"source": f"cartridge:{cartridge}", "target": entity_id})
        if entity.get("dag_id"):
            dag_id = f"dag:{entity['dag_id']}"
            if not any(n["id"] == dag_id for n in nodes):
                nodes.append({"id": dag_id, "kind": "dag", "label": entity["dag_id"]})
            edges.append({"source": entity_id, "target": dag_id})

    try:
        for ds in await _refinement_datasets():
            if ds.get("cartridge") and ds.get("cartridge") != cartridge:
                continue
            ds_id = f"dataset:{ds.get('layer')}:{ds.get('name')}"
            nodes.append({"id": ds_id, "kind": "dataset", "label": f"{ds.get('layer')}:{ds.get('name')}"})
            for source in ds.get("sources") or []:
                parts = str(source).split("/")
                if len(parts) >= 3 and parts[0] == "raw" and parts[1] == cartridge:
                    edges.append({"source": f"entity:{parts[2]}", "target": ds_id})
    except Exception:
        pass

    return {"format": "svg", "svg": _svg_for_graph(nodes, edges), "nodes": nodes, "edges": edges}


@router.post("/dag-deploy", dependencies=[Depends(require_csrf)])
async def dag_deploy(request: Request, user: dict = Depends(require_authenticated)):
    body = await _optional_json(request)
    cartridge = body.get("cartridge") or body.get("cartridge_id") or "replicon"
    entity = body.get("entity") or "Entity"
    dag_id = body.get("dag_id")
    code = body.get("code")
    if not code and body.get("template_id"):
        code = dag_templates.get_code(body["template_id"], cartridge=cartridge, entity=entity)
        dag_id = dag_id or f"{cartridge}_{entity}_full"
    if not dag_id or not code:
        return {
            "status": "needs_input",
            "message": "dag_id and code are required, or provide template_id + cartridge + entity",
            "dag_id": dag_id,
        }
    result = await mcp_registry.invoke("infra", "airflow_create_dag", {
        "dag_id": _clean_identifier(dag_id, label="dag_id"),
        "code": code,
        "cartridge_id": cartridge,
        "description": body.get("description"),
    })
    if isinstance(result, dict) and result.get("error"):
        return {"status": "failed", "dag_id": dag_id, "error": result["error"]}
    return {"status": "deployed", "dag_id": dag_id, "result": result}


@router.get("/templates")
async def templates(user: dict = Depends(require_authenticated)):
    return {"templates": dag_templates.get_all()}


@router.get("/entities", dependencies=[Depends(require_studio_read)])
async def entities_list(
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    if not await cartridge_service.get_cartridge(cartridge):
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")
    entities = await studio_entities.list_entities(cartridge=cartridge)
    return {"cartridge": cartridge, "entities": entities, "total": len(entities)}


@router.post("/entities/upload", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def entities_upload(request: Request, user: dict = Depends(require_authenticated)):
    content_type = request.headers.get("content-type", "")
    cartridge = request.query_params.get("cartridge") or "replicon"
    filename = "spec.yaml"
    content = ""

    if "multipart/form-data" in content_type:
        form = await request.form()
        cartridge = str(form.get("cartridge") or form.get("cartridge_id") or cartridge)
        uploaded = form.get("file") or form.get("spec")
        if uploaded is not None and hasattr(uploaded, "read"):
            raw = await uploaded.read()
            filename = getattr(uploaded, "filename", None) or filename
            content = raw.decode("utf-8", errors="replace")
    else:
        body = await _optional_json(request)
        cartridge = body.get("cartridge") or body.get("cartridge_id") or cartridge
        filename = body.get("filename") or filename
        content = body.get("content") or body.get("yaml") or body.get("spec") or ""

    if not content.strip():
        return {"accepted": False, "accepted_count": 0, "error": "spec content is required"}
    if not await cartridge_service.get_cartridge(cartridge):
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")

    spec_key = cartridge_service.upload_spec(cartridge, filename, content)
    try:
        result = await studio_entities.upload_spec(content, user, default_cartridge=cartridge)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "accepted": not result["errors"],
        "accepted_count": len(result["created"]),
        "uploaded": spec_key,
        "entities": [e["name"] for e in result["created"]],
        "created": result["created"],
        "errors": result["errors"],
    }


@router.post("/entity", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def entity(request: Request, user: dict = Depends(require_authenticated)):
    body = await _optional_json(request)
    cartridge = body.get("cartridge") or body.get("cartridge_id") or "replicon"
    entity_name = body.get("entity") or body.get("name")
    if not entity_name:
        return {"created": False, "error": "entity is required"}
    entity_name = _clean_identifier(str(entity_name), label="entity")
    spec = body.get("spec") if isinstance(body.get("spec"), dict) else {}
    spec = {
        **spec,
        "name": spec.get("name") or entity_name,
        "cartridge": spec.get("cartridge") or cartridge,
        "fields": spec.get("fields") or body.get("fields") or [{"name": body.get("primary_key") or "id", "type": "string", "primary_key": True}],
        "display_name": body.get("display_name") or body.get("title") or entity_name,
        "mode": body.get("mode") or "full",
        "primary_key": body.get("primary_key") or "",
        "dag_id": body.get("dag_id") or "",
        "trigger_type": body.get("trigger_type") or "manual",
        "cron_expression": body.get("cron_expression") or "",
        "description": body.get("description") or "",
        "enabled": body.get("enabled", True),
    }
    try:
        created = await studio_entities.create_entity(entity_name, cartridge, spec, user)
    except (ValueError, studio_entities.DuplicateEntityError, studio_entities.UnknownCartridgeError) as exc:
        raise studio_entities.to_http_error(exc) from exc
    return {"created": True, "entity_id": entity_name, "entity": created}


async def _layer_preview(layer: str, request: Request, user: dict) -> dict:
    layer = layer.lower()
    limit = min(max(int(request.query_params.get("limit", "20")), 1), 200)
    cartridge = request.query_params.get("cartridge") or "replicon"
    requested = request.query_params.get("dataset")
    datasets = await _refinement_datasets()
    candidates = [
        ds for ds in datasets
        if (ds.get("layer") or "").lower() == layer
        and (not cartridge or not ds.get("cartridge") or ds.get("cartridge") == cartridge)
    ]
    if requested:
        candidates = [ds for ds in candidates if ds.get("name") == requested]
    if not candidates:
        return {
            "layer": layer,
            "columns": [],
            "rows": [],
            "total": 0,
            "available": False,
            "reason": f"No {layer} datasets registered for cartridge {cartridge}",
        }
    ds = candidates[0]
    result = await _refinement_invoke(
        "query_dataset",
        {"name": ds["name"], "limit": limit, "user_context": _rls_user_context(user)},
        timeout=60,
    )
    rows = result.get("data") or result.get("rows") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    else:
        schema = await _refinement_invoke("get_schema", {"name": ds["name"]}, timeout=30)
        columns = [f.get("name") for f in schema.get("fields", []) if f.get("name")]
    return {
        "layer": layer,
        "dataset": ds["name"],
        "columns": columns,
        "rows": rows if isinstance(rows, list) else [],
        "total": len(rows) if isinstance(rows, list) else 0,
        "source": "refinement.query_dataset",
    }


@router.get("/silver/preview")
async def silver_preview(request: Request, user: dict = Depends(require_authenticated)):
    return await _layer_preview("silver", request, user)


@router.get("/gold/preview")
async def gold_preview(request: Request, user: dict = Depends(require_authenticated)):
    return await _layer_preview("gold", request, user)


@router.get("/master/preview")
async def master_preview(request: Request, user: dict = Depends(require_authenticated)):
    return await _layer_preview("master", request, user)


@router.post("/superset/dataset", dependencies=[Depends(require_csrf)])
async def superset_dataset(request: Request, user: dict = Depends(require_authenticated)):
    body = await _optional_json(request)
    database_id = body.get("database_id")
    table_name = body.get("table_name")
    schema = body.get("schema") or "public"

    if not table_name:
        gold = [ds for ds in await _refinement_datasets() if (ds.get("layer") or "").lower() == "gold"]
        if not gold:
            return {"created": False, "available": False, "error": "No Gold datasets available for Superset"}
        table_name = _dataset_table_name(gold[0])

    if not database_id:
        databases = await mcp_registry.invoke("infra", "superset_list_databases", {})
        dbs = databases.get("databases", []) if isinstance(databases, dict) else []
        match = next((db for db in dbs if db.get("name") in {"modecissions_gold", "Postgres Gold"}), None)
        match = match or (dbs[0] if dbs else None)
        if not match:
            return {"created": False, "available": False, "error": "No Superset database connection registered"}
        database_id = match["id"]

    existing = await mcp_registry.invoke("infra", "superset_list_datasets", {})
    for ds in existing.get("datasets", []) if isinstance(existing, dict) else []:
        if ds.get("name") == table_name and ds.get("schema") == schema:
            return {"created": False, "dataset_id": ds.get("id"), "table": table_name, "schema": schema, "existing": True}

    result = await mcp_registry.invoke("infra", "superset_create_dataset", {
        "database_id": int(database_id),
        "table_name": table_name,
        "schema": schema,
    })
    if isinstance(result, dict) and result.get("error"):
        return {"created": False, "error": result["error"], "table": table_name, "schema": schema}
    return {"created": True, "dataset_id": result.get("dataset_id"), "table": table_name, "schema": schema, "result": result}


@router.get("/semantic")
async def semantic(
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    manifest = await cartridge_service.get_cartridge(cartridge)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")
    return {
        "cartridge": cartridge,
        "server": manifest,
        "entities": manifest.get("entities") or [],
        "vocabulary": (manifest.get("semantic_model") or {}).get("vocabulary") or [],
        "relations": [],
    }


@router.get("/rag")
async def rag(user: dict = Depends(require_authenticated)):
    sources = await _rag_sources()
    return {"sources": sources, "corpus": sources, "docs_indexed": len(sources) if isinstance(sources, list) else 0}


@router.post("/assistant", dependencies=[Depends(require_csrf)])
async def assistant(request: Request, user: dict = Depends(require_authenticated)):
    body = await _optional_json(request)
    message = (body.get("message") or body.get("prompt") or "").strip()
    if not message:
        return {"reply": "Escribe una instrucción para el asistente de Studio.", "session_id": body.get("session_id")}
    cartridge_id = body.get("cartridge_id") or body.get("cartridge") or "replicon"
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    result = await studio_assistant.chat(
        message=message,
        history=body.get("history", []),
        step=int(body.get("step") or 1),
        manifest=manifest,
        actor_role=user.get("workspace_role") or user.get("role"),
    )
    result["session_id"] = body.get("session_id")
    return result
