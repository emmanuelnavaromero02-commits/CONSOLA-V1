"""
Consumer assistant — answers business questions by combining the semantic
catalog, RAG retrieval, and queries against GOLD datasets.

Constraints (vs the builder assistant):
  - Tool whitelist (no cartridge/dataset lifecycle operations beyond promoting to gold)
  - save_dataset is forced to layer='gold' regardless of model output
  - Never deletes, never edits cartridges, never touches silver/master config
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.services import llm_client

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
MCP_INFRA_URL  = os.environ.get("MCP_INFRA_URL",  "http://mcp-infra:8010")

# ── Tool whitelist (server_id → set of allowed tool names) ──────────────────
ALLOWED_TOOLS = {
    "refinement": {
        "get_data_catalog",
        "list_datasets",
        "describe_silver",
        "get_schema",
        "query_dataset",
        "preview_transform",
        "save_dataset",
        "materialize",
        "publish_app",
        "list_apps",
        "get_app_details",
        "get_app_html",
        "generate_transform",
    },
    "mcp-infra": {
        "search_rag",
        "list_rag_sources",
        "cartridge_search_term",
    },
}

SERVER_URLS = {
    "refinement": REFINEMENT_URL,
    "mcp-infra":  MCP_INFRA_URL,
}


SYSTEM_BASE = """Eres el asistente de MODecissions para usuarios de negocio.

## Tu rol
Respondes preguntas analíticas, generas dashboards y consultas datos GOLD ya
publicados. NO construyes pipelines ni configuras cartuchos: eso es trabajo del
equipo Studio.

## Datos disponibles
- **Modelo de datos** (catálogo semántico): inyectado abajo, contiene datasets,
  columnas, descripciones de negocio y relaciones.
- **GOLD tables**: en Postgres analítico (alias DuckDB `pggold.gold_<dataset>`).
  Son las tablas listas para consumo (KPIs, agregaciones, hechos).
- **MASTER tables**: dimensiones pequeñas (`pggold.master_<dataset>`) — úsalas
  para joins y descripciones.
- **RAG**: usa `search_rag` para preguntas de definición/proceso/política/contexto
  de negocio (NO para datos numéricos).

## Reglas de oro

1. **Definición o proceso de negocio** → SIEMPRE `search_rag` ANTES de responder.
2. **Pregunta numérica/agregada** → consulta GOLD con `query_dataset` o
   `preview_transform` (SQL libre sobre `pggold.*`).
3. **Datos no están en GOLD pero sí en silver/parquet** → crea un dataset GOLD
   con `save_dataset` (layer='gold') y luego `materialize`. Avisa al usuario:
   "Voy a publicar el dataset gold `<nombre>` para responder esto."
4. **Datos no están en silver/parquet** → escala: dile al usuario que el equipo
   Studio debe crear el dataset bronze/silver primero. NO intentes crearlo tú.
5. **NUNCA** llames `save_dataset` con layer='silver' o 'master' — solo 'gold'.
6. **NUNCA** llames `delete_*`. Los datos son del equipo, no tuyos.

## Dashboards / apps analíticas

Si el usuario pide un dashboard, reporte o visualización interactiva:

1. **NO consultes los datos** con `query_dataset` para construir el dashboard.
   Los datos los carga la app en tiempo de ejecución vía `fetch('/api/data/<dataset>')`.
   - Para conocer las columnas: usa el catálogo (ya inyectado arriba) o
     `describe_silver(name)` / `get_schema(name)` — devuelven solo schema.
   - Solo si necesitas validar 1-2 valores reales para el cálculo:
     `query_dataset(name, limit=3)` (NUNCA limit alto).

2. Genera HTML auto-contenido con Chart.js (CDN) y tema oscuro.
   - Datos: `fetch('/api/data/<dataset>')` devuelve array de rows.
   - Filtros: `fetch('/api/data/<dataset>/options?columns=col1,col2')`.
   - KPIs, tablas, charts. Formato USD `$X,XXX.XX`, % `XX.XX%`.
   - Mantén el HTML conciso (≤500 líneas). Usa estilos inline mínimos y los
     defaults de Chart.js cuando sea suficiente — no re-implementes un sistema
     de diseño desde cero.

3. Llama `publish_app(name, title, html, description)` con el HTML COMPLETO
   en `html`. **NUNCA** llames `publish_app` sin `html` o con un placeholder.
   Si tu output va a ser muy largo, simplifica el dashboard antes de publicar.
   Devuelve `{url: "/apps/<name>"}`.

   **Cuando el usuario pida MODIFICAR una app existente** (cambiar un campo,
   ajustar un cálculo, agregar un filtro): SIEMPRE empieza con
   `get_app_html(name)` para leer el HTML actual, edita SOLO lo que pidió,
   y vuelve a publicar con `publish_app`. NUNCA regeneres una app desde cero
   cuando ya existe — perderías el trabajo del usuario y los detalles que
   no estaban en tu memoria.

4. Preséntalo al usuario como link clickeable Markdown.

**Resultados de tools grandes son automáticamente truncados** antes de volver
a ti — si ves `_truncated` en una respuesta, significa que pediste demasiado;
re-formula con `limit` bajo o usa `get_schema` en su lugar.

## Flujo recomendado

1. Lee el catálogo (ya inyectado).
2. Si la pregunta tiene componente de negocio (no técnico) → `search_rag`.
3. Decide qué tabla GOLD necesitas. Si no existe pero el silver sí → `save_dataset` + `materialize`.
4. `query_dataset` o `preview_transform` para el cálculo.
5. Si pidieron visualización → genera HTML + `publish_app`.
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
                r = await c.get(f"{base_url}/mcp/tools")
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


async def _raw_invoke(server_id: str, tool: str, args: dict) -> Any:
    base = SERVER_URLS.get(server_id)
    if not base:
        return {"error": f"unknown server: {server_id}"}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{base}/mcp/invoke", json={"tool": tool, "args": args})
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
        if tool == "save_dataset":
            # Force gold layer (consumer never creates silver/master)
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

        return await _raw_invoke(server_id, tool, args)

    return invoke


# Heuristic: official datasets we never want to prefix even if model forgets the wk_ form.
# (Mostly belt-and-suspenders — users can't normally reach these names anyway.)
_OFFICIAL_PREFIXES = ("replicon_", "pnl_", "consultor_", "costo_", "empleados_", "project_")


def _looks_official(name: str) -> bool:
    return name.startswith(_OFFICIAL_PREFIXES) and not name.startswith("wk_")


# ── Catalog context (cached) ────────────────────────────────────────────────

_catalog_text: str = ""
_catalog_ts:   float = 0.0
_CATALOG_TTL = int(os.environ.get("CATALOG_TTL_SECONDS", "3600"))


def _format_catalog(data: dict) -> str:
    lines = ["## Modelo de datos — Data Catalog", ""]
    lines.append("Tablas Postgres analítico (DuckDB alias `pggold`):"
                 " `pggold.gold_<name>`, `pggold.master_<name>`")
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


async def _catalog_context() -> str:
    global _catalog_text, _catalog_ts
    if _catalog_text and (time.time() - _catalog_ts) < _CATALOG_TTL:
        return _catalog_text
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                             json={"tool": "get_data_catalog", "args": {}})
            r.raise_for_status()
            _catalog_text = _format_catalog(r.json())
            _catalog_ts   = time.time()
    except Exception:
        pass
    return _catalog_text


# ── Public entry ────────────────────────────────────────────────────────────

async def chat(message: str, history: list[dict], user: dict | None = None,
               on_event=None) -> dict:
    catalog_ctx = await _catalog_context()
    user_ctx    = _user_context_block(user) if user else ""
    system      = SYSTEM_BASE + user_ctx + ("\n\n" + catalog_ctx if catalog_ctx else "")
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
    global _catalog_text, _catalog_ts, _tools_cache, _tools_ts
    _catalog_text = ""
    _catalog_ts = 0.0
    _tools_cache = None
    _tools_ts = 0.0
