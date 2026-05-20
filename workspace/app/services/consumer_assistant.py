"""
Consumer assistant — answers business questions by combining the semantic
catalog, RAG retrieval, and queries against GOLD datasets.

Constraints (vs the builder assistant):
  - Tool whitelist (no cartridge/dataset lifecycle operations beyond promoting to gold)
  - save_dataset is forced to layer='gold' regardless of model output
  - Never deletes, never edits cartridges, never touches pipeline config
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.middleware.request_id import request_id_var
from app.services import llm_client

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
MCP_INFRA_URL  = os.environ.get("MCP_INFRA_URL",  "http://mcp-infra:8010")

# ── Tool whitelist (server_id → set of allowed tool names) ──────────────────
# Read-only by design: the business user can EXPLORE and ASK, never create or
# modify pipelines/datasets/apps. Anything that would require new infra is
# routed to the admin via request_admin_help.
ALLOWED_TOOLS = {
    "refinement": {
        "get_data_catalog",
        "list_datasets",
        "describe_silver",
        "get_schema",
        "query_dataset",
        "preview_transform",
        "list_apps",
        "get_app_details",
    },
    "mcp-infra": {
        "search_rag",
        "list_rag_sources",
        "cartridge_search_term",
        "request_admin_help",
    },
}

SERVER_URLS = {
    "refinement": REFINEMENT_URL,
    "mcp-infra":  MCP_INFRA_URL,
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _headers_for(server_id: str) -> dict[str, str]:
    key = ""
    key_env = ""
    if server_id == "mcp-infra":
        key_env = "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA"
    elif server_id == "refinement":
        key_env = "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT"
    if key_env:
        key = os.environ.get(key_env, "")
        if not key and _is_production():
            raise RuntimeError(f"Missing {key_env}; legacy fallback disabled in production")
    if not key and not _is_production():
        key = os.environ.get("INTERNAL_API_KEY", "")
    headers = {"x-api-key": key, "x-internal-service": "workspace"} if key else {}
    rid = request_id_var.get()
    if rid:
        headers["x-request-id"] = rid
    return headers


def _security_context(user: dict | None) -> dict:
    if not user:
        return {
            "trusted": False,
            "source": "workspace",
            "permissions": [],
            "allowed_cartridges": [],
            "allowed_buckets": [],
            "allowed_prefixes": [],
        }
    role = (user or {}).get("role") or "workspace_user"
    admin = role in {"admin", "owner", "super_admin"}
    allowed_cartridges = (user or {}).get("allowed_cartridges") or (["*"] if admin else [])
    return {
        "trusted": True,
        "source": "workspace",
        "user_id": (user or {}).get("id"),
        "email": (user or {}).get("email", ""),
        "role": role,
        "workspace_role": (user or {}).get("workspace_role"),
        "tenant_id": (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id"),
        "workspace_id": (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id"),
        "permissions": ["datasets.read", "cartridges.read", "apps.read", "workspace.access"],
        "allowed_cartridges": allowed_cartridges,
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": (
            ["raw/", "silver/", "gold/", "uploads/", "cartridges/"]
            if admin
            else [f"{layer}/{cart}/" for cart in allowed_cartridges for layer in ("raw", "silver", "gold", "uploads", "cartridges")]
        ),
        "_trusted_admin": admin,
    }


def _payload(tool: str, args: dict, user: dict | None = None) -> dict:
    return {"tool": tool, "args": args, "security_context": _security_context(user)}


SYSTEM_BASE = """Eres el asistente de ΩMEGA by EPIUSE para usuarios de negocio.

## Tu rol
Respondes preguntas analíticas consultando los datos GOLD ya publicados y los
documentos cargados en el RAG. **NO** construyes pipelines, **NO** creas ni
modificas datasets, **NO** publicas dashboards, **NO** modificas el modelo.
Si la pregunta no se puede resolver con lo que ya existe, escalas al admin
con `request_admin_help` — nunca improvises infra nueva.

## Datos disponibles
- **Modelo de datos** (catálogo semántico): inyectado abajo, contiene datasets,
  columnas, descripciones de negocio y relaciones.
- **GOLD tables**: en Postgres analítico (alias DuckDB `pggold.gold_<dataset>`).
  Son las tablas listas para consumo: dimensiones (dim_*), hechos (fact_*),
  agregados y KPIs. Todo lo que antes vivía en MASTER ahora es gold.
- **RAG**: usa `search_rag` para preguntas de definición/proceso/política/contexto
  de negocio (NO para datos numéricos). El RAG tiene **dos tipos** de fuentes:
  - `kinds=["document"]` → reportes, políticas, manuales y notas que el equipo
    sube manualmente (úsalo SIEMPRE para preguntas de negocio).
  - `kinds=["schema"]` → metadatos auto-indexados de cada dataset (SQL del gold,
    columnas y descripciones). Útil cuando preguntan cómo se calcula un campo.
  - Sin `kinds` → busca en todo (last resort).

## Reglas de oro

1. **Definición o proceso de negocio** → SIEMPRE `search_rag` con
   `kinds=["document"]` ANTES de responder. Si no encuentras nada, intenta sin
   filtro. Si el usuario pregunta cómo se calcula un campo del dataset, usa
   `kinds=["schema"]`.
2. **Pregunta numérica/agregada** → consulta GOLD con `query_dataset` o
   `preview_transform` (SQL libre sobre `pggold.*`).
3. **Los datos NO están en ningún GOLD ni en el RAG** → escala al admin con
   `request_admin_help`. NO inventes datasets, NO publiques apps, NO crees
   pipelines. Tu trabajo es responder lo que ya existe; lo demás es del admin.
4. **NUNCA** llames `save_dataset`, `materialize`, `publish_app`, `delete_*`
   ni ningún tool de creación/edición. Esos tools NO están en tu whitelist —
   intentarlos solo desperdicia turnos.

## Apps analíticas existentes

`list_apps` te muestra qué dashboards ya publicó el equipo. Puedes mencionar
al usuario el link de un app existente si responde a la pregunta:
`[Ver dashboard](/apps/<name>)`. **No creas, no modificas** — si el usuario
quiere una visualización que no existe, escalas con `request_admin_help`
describiendo qué app/visualización necesita.

## Escalación al admin — request_admin_help

Úsalo cuando:
- La pregunta requiere un dataset que no existe en GOLD ni en silver/parquet.
- La pregunta requiere un cálculo que no está expuesto en ningún campo y
  combinarlo desde GOLD no es viable.
- La pregunta requiere documentación / política que no está en el RAG.

Antes de escalar:
1. Verifica honestamente con `get_data_catalog`, `list_datasets`, `search_rag`
   sin filtro — confirma que NO existe el dato.
2. Llama `request_admin_help` con:
   - `user_question`: la pregunta literal del usuario.
   - `why_unanswerable`: por qué no pudiste (qué dataset/tool falta).
   - `what_is_needed`: descripción concreta (ej. "extractor de Replicon entity
     X" o "nueva tabla GOLD que cruce A y B" o "subir el manual de procesos").
3. Al usuario respóndele: "He enviado tu solicitud al equipo admin —
   recibirás respuesta en cuanto el dataset/herramienta esté disponible."

**Resultados de tools grandes son automáticamente truncados** antes de volver
a ti — si ves `_truncated` en una respuesta, significa que pediste demasiado;
re-formula con `limit` bajo o usa `get_schema` en su lugar.

## Flujo recomendado

1. Lee el catálogo (ya inyectado).
2. Si la pregunta tiene componente de negocio (no técnico) → `search_rag`
   con `kinds=["document"]`.
3. Decide qué tabla GOLD necesitas. Si no existe → `request_admin_help`.
4. Si existe → `query_dataset` / `preview_transform` para el cálculo.
5. Si pidieron una visualización nueva → menciona apps existentes con
   `list_apps`; si ninguna sirve, `request_admin_help`.
6. Responde en el idioma del usuario, formato directo, sin jerga técnica innecesaria.
"""


# ── Tool discovery + invocation ─────────────────────────────────────────────

_tools_cache: list[dict] | None = None
_tools_map_cache: dict[str, str] = {}
_tools_ts: float = 0.0
_TOOLS_TTL = 300


async def _discover_tools() -> tuple[list[dict], dict[str, str]]:
    """Fetch tools from each MCP server, filter by whitelist, return as
    (tool_list, tool_name → server_id)."""
    global _tools_cache, _tools_map_cache, _tools_ts
    if _tools_cache and (time.time() - _tools_ts) < _TOOLS_TTL:
        return _tools_cache, _tools_map_cache

    tools: list[dict] = []
    server_map: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for srv_id, base_url in SERVER_URLS.items():
            allow = ALLOWED_TOOLS.get(srv_id, set())
            try:
                r = await c.get(f"{base_url}/mcp/tools", headers=_headers_for(srv_id))
                r.raise_for_status()
                data = r.json()
            except Exception:
                continue
            for t in data.get("tools", []):
                if t["name"] not in allow:
                    continue
                full_name = f"{srv_id}__{t['name']}"
                tools.append({
                    "name":         full_name,
                    "description":  t.get("description", ""),
                    "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
                })
                server_map[full_name] = srv_id
    _tools_cache = tools
    _tools_map_cache = server_map
    _tools_ts = time.time()
    return tools, server_map


async def _raw_invoke(server_id: str, tool: str, args: dict, user: dict | None = None) -> Any:
    base = SERVER_URLS.get(server_id)
    if not base:
        return {"error": f"unknown server: {server_id}"}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{base}/mcp/invoke", json=_payload(tool, args, user), headers=_headers_for(server_id))
    try:
        return r.json()
    except Exception:
        return {"error": f"non-JSON response: {r.text[:300]}"}


def _make_user_aware_invoke(user: dict | None):
    """Returns an invoke_tool closure that:
      - prefixes user-created datasets/apps with `wk_<uid>_`
      - stamps `created_by_id` on save_dataset / publish_app
      - forces layer='gold' on save_dataset
      - defaults app visibility to 'private'
    """
    uid = user.get("id") if user else None
    prefix = f"wk_{uid}_" if uid else ""

    async def invoke(server_id: str, tool: str, args: dict) -> Any:
        if tool not in ALLOWED_TOOLS.get(server_id, set()):
            return {"error": f"tool_not_allowed: {server_id}__{tool}"}
        if tool == "save_dataset":
            # Force gold layer (consumer never creates lower-layer models)
            if args.get("layer") != "gold":
                args = {**args, "layer": "gold"}
            if uid is not None:
                name = (args.get("name") or "").strip()
                if name and not name.startswith(prefix):
                    args = {**args, "name": prefix + name, "created_by_id": uid}
                else:
                    args = {**args, "created_by_id": uid}

        elif tool == "publish_app":
            if uid is not None:
                name = (args.get("name") or "").strip()
                if name and not name.startswith(prefix):
                    args = {**args, "name": prefix + name, "created_by_id": uid}
                else:
                    args = {**args, "created_by_id": uid}
                args.setdefault("visibility", "private")

        elif tool == "materialize":
            # The model passes the dataset name; if it forgot the prefix, add it.
            if uid is not None:
                name = (args.get("name") or "").strip()
                if name and not name.startswith(prefix) and not _looks_official(name):
                    args = {**args, "name": prefix + name}

        return await _raw_invoke(server_id, tool, args, user)

    return invoke


# Heuristic: official datasets we never want to prefix even if model forgets the wk_ form.
# (Mostly belt-and-suspenders — users can't normally reach these names anyway.)
_OFFICIAL_PREFIXES = ("replicon_", "pnl_", "consultor_", "costo_", "empleados_", "project_")


def _looks_official(name: str) -> bool:
    return name.startswith(_OFFICIAL_PREFIXES) and not name.startswith("wk_")


# ── Catalog context (cached) ────────────────────────────────────────────────

_catalog_text_by_scope: dict[str, str] = {}
_catalog_ts:   float = 0.0
_CATALOG_TTL = int(os.environ.get("CATALOG_TTL_SECONDS", "3600"))


def _format_catalog(data: dict) -> str:
    lines = ["## Modelo de datos — Data Catalog", ""]
    lines.append("Tablas Postgres analítico (DuckDB alias `pggold`):"
                 " `pggold.gold_<name>` (incluye dim_* y fact_*)")
    lines.append("Parquet silver (lake): `s3://lakehouse/silver/<cartridge>/<name>/data.parquet`")
    lines.append("")
    datasets = data.get("datasets", {})
    for ds_name, ds in datasets.items():
        layer  = ds.get("layer", "")
        desc   = (ds.get("description") or "")[:140]
        lines.append(f"### {ds_name}  [{layer}]" + (f"\n{desc}" if desc else ""))
        for col in ds.get("columns", []):
            flags = ""
            if col.get("is_key"):    flags += " [KEY]"
            if col.get("is_metric"): flags += " [MTR]"
            tags = ",".join(col.get("tags") or [])
            tag_s = f" ({tags})" if tags else ""
            lines.append(f"  {col['name']} {col.get('type','')+flags}: "
                         f"{col.get('description','')}{tag_s}")
        lines.append("")
    rels = data.get("relationships") or []
    if rels:
        lines.append("## Relaciones (joins)")
        for r in rels:
            lines.append(
                f"  {r['from_dataset']}.{r['from_column']} → "
                f"{r.get('join_hint','LEFT')} JOIN {r['to_dataset']}.{r['to_column']}"
            )
        lines.append("")
    return "\n".join(lines)


def _scope_cache_key(user: dict | None) -> str:
    sec = _security_context(user)
    return "|".join([
        str(sec.get("user_id") or ""),
        str(sec.get("workspace_id") or ""),
        ",".join(sec.get("allowed_prefixes") or []),
    ])


async def _catalog_context(user: dict | None) -> str:
    global _catalog_ts
    key = _scope_cache_key(user)
    if key in _catalog_text_by_scope and (time.time() - _catalog_ts) < _CATALOG_TTL:
        return _catalog_text_by_scope[key]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                             json=_payload("get_data_catalog", {}, user),
                             headers=_headers_for("refinement"))
            r.raise_for_status()
            _catalog_text_by_scope[key] = _format_catalog(r.json())
            _catalog_ts   = time.time()
    except Exception:
        pass
    return _catalog_text_by_scope.get(key, "")


_hints_text_by_scope: dict[str, str] = {}
_hints_ts:   float = 0.0
_HINTS_TTL = 300


async def _cartridge_hints_block(user: dict | None) -> str:
    """Concatenate `assistant_hints` from every registered cartridge so the
    workspace assistant honors cartridge-specific rules across the catalog."""
    global _hints_ts
    key = _scope_cache_key(user)
    if key in _hints_text_by_scope and (time.time() - _hints_ts) < _HINTS_TTL:
        return _hints_text_by_scope[key]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{MCP_INFRA_URL}/mcp/invoke",
                             json=_payload(
                                 "postgres_execute_query",
                                 {"sql": "SELECT id, COALESCE(assistant_hints,'') AS hints "
                                         "FROM cartridges WHERE assistant_hints IS NOT NULL "
                                         "AND length(assistant_hints) > 0"},
                                 user,
                             ),
                             headers=_headers_for("mcp-infra"))
            data = (r.json().get("result") or r.json()).get("rows") or []
        sections = []
        for row in data:
            h = (row.get("hints") or "").strip()
            if not h:
                continue
            sections.append(f"## Cartucho `{row.get('id','?')}`\n{h}")
        if sections:
            _hints_text_by_scope[key] = "\n\n<hints_cartuchos>\n" + "\n\n".join(sections) + "\n</hints_cartuchos>"
        else:
            _hints_text_by_scope[key] = ""
        _hints_ts = time.time()
    except Exception:
        pass
    return _hints_text_by_scope.get(key, "")


# ── Public entry ────────────────────────────────────────────────────────────

async def chat(message: str, history: list[dict], user: dict | None = None,
               on_event=None) -> dict:
    catalog_ctx = await _catalog_context(user)
    user_ctx    = _user_context_block(user) if user else ""
    hints_ctx   = await _cartridge_hints_block(user)
    system      = SYSTEM_BASE + user_ctx + ("\n\n" + catalog_ctx if catalog_ctx else "") + hints_ctx
    tools, server_map = await _discover_tools()

    messages = list(history)
    messages.append({"role": "user", "content": message})

    invoke = _make_user_aware_invoke(user)
    reply, viewer_urls, full_msgs = await llm_client.chat(
        system=system,
        messages=messages,
        tools=tools,
        invoke_tool=invoke,
        tool_server_map=server_map,
        on_event=on_event,
    )
    return {"reply": reply, "viewer_urls": viewer_urls, "messages": full_msgs}


def _user_context_block(user: dict) -> str:
    uid = user.get("id")
    return f"""

## Contexto del usuario

- ID: {uid}
- Email: {user.get("email", "?")}
- Tu prefijo personal de datasets/apps: `wk_{uid}_`

## Reglas de naming y crecimiento

- Cuando crees datasets nuevos (`save_dataset`) o publiques apps (`publish_app`),
  el sistema **automáticamente** prefija el nombre con `wk_{uid}_` y registra
  tu `created_by_id`. NO necesitas añadir el prefijo manualmente.
- **Quota suave**: máximo 5 datasets `wk_{uid}_*` activos y 5 apps `wk_{uid}_*`
  activas a la vez. Si llegas al tope, primero borra alguno con
  `delete_dataset` o `delete_app` y luego crea el nuevo.
- Antes de crear un dataset nuevo, **verifica con `list_datasets`** si ya
  existe uno equivalente (tuyo o oficial) — reutilizar es mejor que duplicar.
- Apps nuevas son `visibility='private'` por defecto: solo tú las ves en la
  galería. Si quieres compartirla con el equipo, dilo explícitamente al usuario
  para que lo decida (no decidas tú).
"""


def invalidate_caches():
    """Force the next chat() to re-fetch the catalog and tool list."""
    global _catalog_ts, _hints_ts, _tools_cache, _tools_ts
    _catalog_text_by_scope.clear()
    _catalog_ts = 0.0
    _hints_text_by_scope.clear()
    _hints_ts = 0.0
    _tools_cache = None
    _tools_ts = 0.0
