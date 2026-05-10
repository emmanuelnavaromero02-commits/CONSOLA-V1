"""
Studio Assistant
================
AI assistant specialized for cartridge construction.
Uses the same LLM + MCP machinery as the Monitor assistant,
but with a different system prompt that is:
  - Action-oriented (generate code, deploy DAGs, test connections)
  - Cartridge-context-aware (receives current manifest)
  - Step-aware (knows which wizard step is active)
"""
from __future__ import annotations

from typing import Callable

from app.services import mcp_registry, llm_client

# ── Step metadata (aligned with studio.html nav) ──────────────────────────────

STEP_LABELS = {
    1: "RESUMEN",
    2: "DAGS",
    3: "ENTIDADES",
    4: "REFINAR",
    5: "ANALYTICS",
    6: "IA SEMÁNTICA",
    7: "RAG",
}


# Tool slimming: per-step allow-list. Reduces tool count from 60 to ~10–20 per
# step, drastically improving model focus and accuracy. Names are bare (no
# server prefix); "*" suffix is a glob-prefix match. _common applies to all steps.
STEP_TOOLS: dict[int | str, set[str]] = {
    "_common": {
        # Cartridge discovery — needed everywhere
        "list_cartridges", "cartridge_get_manifest",
        "cartridge_list_entities", "cartridge_get_schema",
        "cartridge_search_term", "cartridge_get_semantic",
        # Vault — credentials may be needed in any step
        "vault_*",
    },
    # Note: cartridge_sync_semantic_to_rag is exposed in step 6 (semantic editing).
    1: {  # RESUMEN — overview, manifest editing
        "cartridge_list_jobs", "cartridge_list_kbs",
        "minio_list_cartridge_specs", "minio_read_spec", "minio_upload_spec",
    },
    2: {  # DAGS
        "airflow_*",
        "dag_save_source", "dag_get_source",
        "watermark_get", "watermark_set", "pipeline_run_save",
        "cartridge_extract", "cartridge_extract_all",
        "cartridge_get_run_logs", "cartridge_get_job_status", "cartridge_list_jobs",
    },
    3: {  # ENTIDADES
        "cartridge_preview", "cartridge_extract", "cartridge_extract_all",
        "cartridge_get_run_logs", "cartridge_get_job_status", "cartridge_list_jobs",
        "minio_*",
        "list_entities", "rename_entity", "update_entity", "get_entity_logs",
        "watermark_get",
    },
    4: {  # REFINAR (Silver/Gold)
        "list_sources", "preview_source", "get_source_partitions",
        "generate_transform", "preview_transform",
        "save_dataset", "materialize", "list_datasets",
        "get_schema", "query_dataset", "describe_source", "describe_silver",
        "list_datasets_with_schemas", "delete_dataset", "get_lineage",
        "cartridge_run_kb", "cartridge_query_kb", "cartridge_list_kbs",
        "postgres_*",
    },
    5: {  # ANALYTICS (Superset + Apps)
        "superset_*",
        "publish_app", "list_apps", "delete_app", "get_app_details", "get_app_html",
        "query_dataset", "list_datasets", "list_datasets_with_schemas",
        "postgres_list_tables",
    },
    6: {  # IA SEMÁNTICA (vocabulary)
        "get_data_catalog", "upsert_catalog_entries", "register_relationship",
        "list_datasets_with_schemas", "describe_silver",
        "postgres_execute_query", "postgres_execute_ddl",
        "cartridge_sync_semantic_to_rag",  # re-embed glossary after edits
    },
    7: {  # RAG
        "search_rag", "ingest_document", "list_rag_sources",
    },
}

ANALYST_READ_ONLY_EXACT = {
    "airflow_get_run_status",
    "airflow_get_task_logs",
    "airflow_list_dag_runs",
    "airflow_list_dags",
    "airflow_list_task_instances",
    "cartridge_get_job_status",
    "cartridge_get_manifest",
    "cartridge_get_run_logs",
    "cartridge_get_schema",
    "cartridge_get_semantic",
    "cartridge_list_entities",
    "cartridge_list_jobs",
    "cartridge_list_kbs",
    "cartridge_preview",
    "cartridge_search_term",
    "cartridge_query_kb",
    "dag_get_source",
    "describe_silver",
    "describe_source",
    "get_app_details",
    "get_app_html",
    "get_data_catalog",
    "get_entity_logs",
    "get_lineage",
    "get_schema",
    "get_source_partitions",
    "list_apps",
    "list_cartridges",
    "list_datasets",
    "list_datasets_with_schemas",
    "list_entities",
    "list_rag_sources",
    "list_sources",
    "minio_list_cartridge_specs",
    "minio_read_spec",
    "postgres_execute_query",
    "postgres_get_sample",
    "postgres_get_table_schema",
    "postgres_list_schemas",
    "postgres_list_tables",
    "preview_source",
    "preview_transform",
    "query_dataset",
    "search_rag",
    "watermark_get",
}


def _bare_tool_name(tool_name: str) -> str:
    return tool_name.split("__", 1)[-1]


def is_tool_allowed_for_role(role: str | None, tool_name: str) -> bool:
    """Server-side Studio tool policy. Analysts are read/query/preview only."""
    if (role or "").lower() != "analyst":
        return True
    bare = _bare_tool_name(tool_name)
    return bare in ANALYST_READ_ONLY_EXACT


def _matches_pattern(bare_name: str, allowed: set[str]) -> bool:
    if bare_name in allowed:
        return True
    for p in allowed:
        if p.endswith("*") and bare_name.startswith(p[:-1]):
            return True
    return False


def filter_tools_for_step(tools: list[dict], step: int) -> list[dict]:
    """Return only tools relevant to the active step + common ones."""
    allowed = STEP_TOOLS["_common"] | STEP_TOOLS.get(step, set())
    if not allowed:
        return tools
    out = []
    for t in tools:
        # tool name format: "<server>__<bare>" e.g. "infra__cartridge_search_term"
        bare = t["name"].split("__", 1)[-1]
        if _matches_pattern(bare, allowed):
            out.append(t)
    return out

STEP_INSTRUCTIONS = {
    1: """\
Step RESUMEN — visión general del cartucho activo.
- Overview: cartridge_get_manifest(id).
- Significado de un término: cartridge_search_term(id, query).
- Listar cartuchos: list_cartridges().
- Crear/importar: indica al usuario que use el botón "Importar ZIP" o pide datos para crear uno.
""",
    2: """\
Step DAGS — gestión de DAGs de Airflow del cartucho.
- Lista DAGs: airflow_list_dags() (filtra por nombre/tag del cartucho).
- Código fuente existente: dag_get_source(cartridge_id, dag_id).
- Crear/actualizar: airflow_create_dag(dag_id, code, cartridge_id) + dag_save_source.
- Disparar: airflow_trigger_dag(dag_id).
- Estado y logs: airflow_get_run_status, airflow_list_dag_runs, airflow_get_task_logs.
- Si el usuario subió un spec (OpenAPI/WSDL/OData), léelo con minio_read_spec y genera DAG.
""",
    3: """\
Step ENTIDADES — qué objetos extraer del sistema origen y cómo procesarlos.

CONSULTAR:
- list_entities(cartridge_id) → estado actual: nombre, modo, dag_id, último run.

EDITAR:
- rename_entity(cartridge_id, old_name, new_name): actualiza entity_config, watermarks,
  pipeline_runs, silver_lineage en una transacción. Bronze histórico queda en el path viejo.
- update_entity → cambia mode, display_name, dag_id, trigger_type, cron_expression.
- update_entity también CREA si no existe.

EXTRAER:
- cartridge_extract(cartridge_id, entity, mode) — una entidad.
- cartridge_extract_all(cartridge_id, mode) — todas en paralelo.
- cartridge_get_job_status(run_id), cartridge_get_run_logs(run_id) para seguir.

DIAGNÓSTICO:
- get_entity_logs(cartridge_id, entity): error de pipeline_runs + logs Airflow en una sola llamada.

ESTRATEGIA: full = todo siempre. incremental = sólo nuevos desde último watermark.
""",
    4: """\
Step REFINAR — Silver y Gold con Refinement Engine.

FLUJO:
1. EXPLORAR: list_sources, preview_source(name), get_source_partitions(name).
2. SQL: generate_transform(description, sources, cartridge) ó pásalo escrito directamente.
3. PREVIEW siempre: preview_transform(sql, limit=20). Si falla, corrige y repite.
4. GUARDAR/MATERIALIZAR:
   - save_dataset(name, sql, layer, sources, cartridge, description)
     · silver → Parquet (s3://lakehouse/silver/{cartridge}/{name})
     · gold   → tabla en postgres_gold: gold_{name} (alias DuckDB: pggold; visible en Superset)
     · master → Parquet + tabla en postgres_gold: master_{name} (alias DuckDB: pggold)
   - materialize(name) ejecuta y escribe.
5. VERIFICAR: get_schema(name), query_dataset(name), get_lineage(name).

PATRONES SQL COMUNES (silver):
- Última partición: SELECT * FROM read_parquet('...load_date=*/...', hive_partitioning=true)
  WHERE load_date = (SELECT MAX(load_date) FROM read_parquet(...))
- Incremental dedup: ROW_NUMBER() OVER (PARTITION BY id ORDER BY load_date DESC).

KBs y catálogo: cartridge_list_kbs, cartridge_run_kb, cartridge_query_kb.
""",
    5: """\
Step ANALYTICS — Superset + apps HTML.

REGLAS DE ORO (no negociables):
1. NUNCA pegues HTML/JS/CSS en el chat. El código va directo a `publish_app`.
   La respuesta al usuario es 1–3 líneas: qué hiciste y la URL `/apps/<name>`.
2. ANTES de cualquier cambio a una app, llama `list_apps` y luego `get_app_html(name)`.
   Edita el HTML retornado y vuelve a `publish_app` con el mismo `name` (sobrescribe).
   PROHIBIDO regenerar desde cero si la app ya existe.
3. Si el usuario pide algo que sería una app nueva (no existe en `list_apps`),
   PREGUNTA antes de crear: confirma nombre, dataset(s) y propósito en una sola línea.
4. POR DEFAULT publica al terminar. No muestres "borrador" ni código previo —
   genera, valida con `get_app_html` si quieres revisar internamente, y publica.

SUPERSET:
- postgres_list_tables(gold=true) → tablas Gold disponibles.
- superset_list_databases → database_id de modecissions_gold.
- superset_create_dataset → desde una tabla Gold.
- superset_create_chart (bar, line, big_number, pie, table…).
- superset_create_dashboard → agrupa gráficos.

APPS HTML — flujo obligatorio:
1. `list_apps()` → ¿existe ya `<name>`?
2a. SI EXISTE: `get_app_html(name)` → edita el HTML retornado → `publish_app` (mismo name).
2b. NO EXISTE: confirma con el usuario nombre + datasets en 1 línea → al confirmar,
    `list_datasets_with_schemas` (si lo necesitas) → `publish_app(name, title, html, cartridge_id)`.
3. Confirma con la URL `/apps/<name>`. NO pegues HTML en la respuesta.

Tools:
- list_apps, get_app_details(name), get_app_html(name), publish_app, delete_app.
- list_datasets / list_datasets_with_schemas → conoce los datasets disponibles.
- El HTML auto-contenido usa `fetch('/api/data/{dataset}')`.
""",
    6: """\
Step IA SEMÁNTICA — vocabulario de negocio (semantic_terms + data_catalog).
- Inventario actual: get_data_catalog(cartridge_id).
- Inserta/actualiza términos: upsert_catalog_entries(...).
- Relaciones entre datasets: register_relationship(...).
- Para términos puros de glosario (no atados a columna): usa herramientas
  estructuradas de catálogo; postgres_execute_ddl no ejecuta DML.
- Insumo: list_datasets_with_schemas, describe_silver.

IMPORTANTE — DESPUÉS de cualquier edit a semantic_terms o data_catalog:
- Llama `cartridge_sync_semantic_to_rag(cartridge_id)` para re-embedar el vocabulario.
- Esto mantiene la búsqueda semántica fresca; sin sync, las preguntas en lenguaje
  natural seguirán encontrando la versión vieja del glosario.
""",
    7: """\
Step RAG — base de conocimiento del cartucho.
- list_rag_sources() → ver qué documentos están ingeridos.
- ingest_document(name, content, description) → ingiere texto plano.
  Para PDFs el usuario los sube por la UI directamente; ahí se hace el extract.
- search_rag(query, top_k) → búsqueda semántica antes de responder preguntas
  sobre los documentos. SIEMPRE buscá antes de afirmar/negar contenido.
""",
}

# ── System prompt builder ──────────────────────────────────────────────────────

def _build_system_static() -> str:
    """Static system prompt — same across cartridges, steps and turns. Cacheable.
    Structure follows the "Lost in the Middle" mitigation: rules anchored at the
    very top (primacy), tool catalog as the dense body, recency reminder at end.
    """
    return f"""\
<rol>
Eres el asistente constructor de cartuchos en MODecissions Studio.
Un cartucho es un conector portable que define: conexión al origen, extracción de
entidades, refinamiento Bronze→Silver→Gold, publicación de dashboards y vocabulario
de negocio.
</rol>

<reglas_criticas>
NUNCA inventes información de un cartucho. SIEMPRE consulta tools antes de afirmar o negar.

Flujo OBLIGATORIO ante cualquier pregunta sobre un cartucho:

  1. IDENTIFICA cartridge_id:
     · Si [CONTEXTO ACTUAL] tiene "id: <x>" → usa ese id.
     · Si dice "ningún cartucho seleccionado" o el usuario menciona un cartucho por nombre
       → primera acción: `infra__list_cartridges()`.

  2. CONSULTA con la tool correcta (NUNCA respondas de memoria):
     · "qué significa X"        → `infra__cartridge_search_term(cartridge_id, X)`
     · "qué entidades hay"      → `infra__cartridge_list_entities(cartridge_id)`
     · "qué KBs hay"            → `infra__cartridge_list_kbs(cartridge_id)`
     · "vocabulario completo"   → `infra__cartridge_get_semantic(cartridge_id)`
     · "últimos jobs"           → `infra__cartridge_list_jobs(cartridge_id)`
     · "manifest completo"      → `infra__cartridge_get_manifest(cartridge_id)`

  3. SINTETIZA con datos REALES de la tool:
     · Si la tool NO devolvió matches, di "No está definido en este cartucho".
     · NUNCA digas "no encontré" sin haber llamado primero la tool correspondiente.

Restricciones globales:
- PROHIBIDO suponer que un término no existe sin haber llamado `cartridge_search_term`.
- PROHIBIDO pedir el cartridge_id al usuario si puedes obtenerlo con `list_cartridges`.
- PROHIBIDO devolver respuesta vacía: si una tool falla, diagnostica con los logs.
- PROHIBIDO pegar código (HTML, SQL, JS, Python, YAML) en el chat. El código se
  aplica con la tool correspondiente (`publish_app`, `save_dataset`, `dag_save_source`,
  `postgres_execute_ddl`, etc.). La respuesta al usuario describe QUÉ hiciste y
  el resultado (URL, nombre, conteo), NO muestra el código generado.
- Para MODIFICAR algo existente: primero LEE su estado actual con la tool de get
  correspondiente (`get_app_html`, `dag_get_source`, `cartridge_get_manifest`),
  edita ese contenido, y aplica con la tool de save. NUNCA regeneres desde cero.
</reglas_criticas>

<herramientas>
Las herramientas relevantes para el step actual están disponibles como funciones —
sus nombres, parámetros y descripciones son visibles directamente en tu API de tools.
Convención de prefijos:
  · infra__*       → Airflow, MinIO, PostgreSQL, Superset, Vault, Cartridge tools, RAG
  · refinement__*  → Bronze → Silver → Gold (DuckDB, dataset save/materialize)
  · studio_ops__*  → operaciones específicas de cartuchos (rename_entity, get_entity_logs)
  · monitoring__*  → deeplinks a vistas del UI

Sólo se te exponen en cada turno las tools del step activo + las comunes
(list_cartridges, cartridge_search_term, cartridge_get_manifest, etc.).
</herramientas>

<notas>
- Sé proactivo: ejecuta las acciones, no sólo describas qué hacer.
- Después de cada cambio significativo, sugiere PATCH /studio/cartridges/{{id}} para persistir.
- Si una acción falla, diagnostica con los logs y propone una corrección concreta.
- Responde siempre en el idioma del usuario.
- El [CONTEXTO ACTUAL] trae sólo IDs y conteos — los DETALLES los obtienes vía tools.
</notas>

<recordatorio_final>
ANTES de responder cualquier pregunta sobre un cartucho:
  · ¿Llamé `list_cartridges` o tengo el id en [CONTEXTO ACTUAL]?  Si no → llámalo.
  · ¿Llamé la tool específica de la pregunta (search_term / list_entities / etc.)?  Si no → llámala.
  · ¿Mi respuesta cita datos reales de las tools, no de mi memoria?  Si no → corrígela.
NO respondas hasta que las 3 sean SÍ.
</recordatorio_final>
"""


def _build_dynamic_context(step: int, manifest: dict | None) -> str:
    """Per-turn context — only counts and IDs. The LLM uses tools for details."""
    step_label = STEP_LABELS.get(step, f"Paso {step}")
    step_instr = STEP_INSTRUCTIONS.get(step, "")

    if not manifest:
        header = "(ningún cartucho seleccionado — pide al usuario que elija uno o cree uno nuevo)"
    else:
        ents  = len(manifest.get("entities") or [])
        dags  = len(manifest.get("dags") or [])
        conns = len(manifest.get("connections") or [])
        vocab = len((manifest.get("semantic_model") or {}).get("vocabulary") or [])
        header = (
            f"id:           {manifest.get('id', '?')}\n"
            f"name:         {manifest.get('name', '?')}\n"
            f"version:      {manifest.get('version', '?')}\n"
            f"description:  {(manifest.get('description') or '').strip()[:160]}\n"
            f"pattern:      {manifest.get('pattern', '?')}\n"
            f"connections:  {conns}\n"
            f"entities:     {ents}\n"
            f"dags:         {dags}\n"
            f"vocabulary:   {vocab} términos"
        )
    return f"""\
[CONTEXTO ACTUAL]
─── CARTUCHO ─────────────────────
{header}
─── PASO ACTIVO ──────────────────
{step_label}: {step_instr.strip()}
──────────────────────────────────"""


# ── Main chat handler ─────────────────────────────────────────────────────────

async def chat(
    message: str,
    history: list[dict],
    step: int = 1,
    manifest: dict | None = None,
    on_event: Callable | None = None,
    actor_role: str | None = None,
) -> dict:
    servers = await mcp_registry.list_servers()
    tools:           list[dict]       = []
    tool_server_map: dict[str, str]   = {}

    for server in servers:
        if not server.get("healthy"):
            continue
        for t in (server.get("tools") or []):
            full_name = f"{server['id']}__{t['name']}"
            tools.append({
                "name":         full_name,
                "description":  f"[{server['name']}] {t.get('description', '')}",
                "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
            })
            tool_server_map[full_name] = server["id"]

    # Tool slimming: only expose tools relevant to the active step + common ones.
    # Reduces ~60 tools to 10–20 per call, sharply improving LLM accuracy.
    tools = filter_tools_for_step(tools, step)
    tools = [t for t in tools if is_tool_allowed_for_role(actor_role, t["name"])]

    # Keep full history (including tool call/result blocks) so the model
    # remembers what tools it already ran and what they returned.
    # Per-turn dynamic context (manifest + step) is prepended to the latest user
    # message so the static system stays cacheable across cartridges/steps.
    messages = list(history)
    context = _build_dynamic_context(step, manifest)
    messages.append({"role": "user", "content": f"{context}\n\n---\n\n{message}"})

    system = _build_system_static()

    async def _invoke_tool(srv: str, tool: str, args: dict):
        full_name = f"{srv}__{tool}"
        if not is_tool_allowed_for_role(actor_role, full_name):
            return {"error": "Forbidden: analyst role is limited to read, inspect, query and preview tools"}
        return await mcp_registry.invoke(srv, tool, args)

    reply, viewer_urls, full_msgs = await llm_client.chat(
        system=system,
        messages=messages,
        tools=tools,
        invoke_tool=_invoke_tool,
        tool_server_map=tool_server_map,
        on_event=on_event,
    )

    # full_msgs already contains the complete conversation including tool calls.
    # The frontend stores this and sends it back on the next turn so the model
    # has full context (no more cycling "I'll do X" without knowing it already tried).
    return {"reply": reply, "viewer_urls": viewer_urls, "messages": full_msgs}
