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

from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from app.services import audit_service, mcp_registry, llm_client
from app.services.tool_manifest import classify_tool


STUDIO_TOOLS_WHITELIST = {
    "introspect_source",
    "generate_dag_code",
    "validate_dag_code",
    "create_full_cartridge",
    "list_cartridges",
    "cartridge_get_manifest",
    "cartridge_list_entities",
    "cartridge_get_schema",
    "cartridge_search_term",
    "cartridge_get_semantic",
    "cartridge_list_jobs",
    "cartridge_list_kbs",
    "minio_list_cartridge_specs",
    "minio_read_spec",
    "minio_upload_spec",
    "airflow_list_dags",
    "airflow_create_dag",
    "airflow_trigger_dag",
    "airflow_get_run_status",
    "airflow_get_task_logs",
    "airflow_list_dag_runs",
    "dag_save_source",
    "dag_get_source",
    "watermark_get",
    "cartridge_preview",
    "cartridge_extract",
    "cartridge_extract_all",
    "cartridge_get_run_logs",
    "cartridge_get_job_status",
    "list_entities",
    "rename_entity",
    "update_entity",
    "get_entity_logs",
    "list_sources",
    "preview_source",
    "get_source_partitions",
    "generate_transform",
    "preview_transform",
    "save_dataset",
    "materialize",
    "list_datasets",
    "get_schema",
    "query_dataset",
    "describe_source",
    "describe_silver",
    "delete_dataset",
    "list_datasets_with_schemas",
    "get_lineage",
    "cartridge_run_kb",
    "cartridge_query_kb",
    "postgres_execute_query",
    "postgres_execute_ddl",
    "postgres_list_tables",
    "superset_list_databases",
    "superset_create_dataset",
    "superset_list_datasets",
    "publish_app",
    "list_apps",
    "delete_app",
    "get_app_details",
    "get_app_html",
    "get_data_catalog",
    "upsert_catalog_entries",
    "register_relationship",
    "cartridge_sync_semantic_to_rag",
    "search_rag",
    "ingest_document",
    "list_rag_sources",
}

STUDIO_LOCAL_SERVER_ID = "studio"
LocalToolHandler = Callable[[dict[str, Any], dict | None], Awaitable[Any]]
_LOCAL_TOOLS: dict[str, dict[str, Any]] = {}
_LOCAL_TOOL_HANDLERS: dict[str, LocalToolHandler] = {}


def register_local_tool(
    name: str,
    *,
    description: str,
    input_schema: dict[str, Any],
    handler: LocalToolHandler,
) -> None:
    """Register a Studio-native tool exposed through the assistant tool loop."""
    _LOCAL_TOOLS[name] = {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }
    _LOCAL_TOOL_HANDLERS[name] = handler


def _local_tools() -> list[dict[str, Any]]:
    return list(_LOCAL_TOOLS.values())


async def _invoke_local_tool(tool: str, args: dict[str, Any], user: dict | None) -> Any:
    handler = _LOCAL_TOOL_HANDLERS.get(tool)
    if handler is None:
        raise HTTPException(404, f"Studio local tool '{tool}' is not registered")
    return await handler(args or {}, user)

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
        "create_full_cartridge",
        "cartridge_list_jobs", "cartridge_list_kbs",
        "minio_list_cartridge_specs", "minio_read_spec", "minio_upload_spec",
    },
    2: {  # DAGS
        "airflow_*",
        "introspect_source",
        "generate_dag_code",
        "validate_dag_code",
        "dag_save_source", "dag_get_source",
        "cartridge_get_run_logs", "cartridge_get_job_status",
    },
    3: {  # ENTIDADES
        "cartridge_preview", "cartridge_extract", "cartridge_extract_all",
        "introspect_source",
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
    "generate_dag_code",
    "get_lineage",
    "get_schema",
    "get_source_partitions",
    "introspect_source",
    "validate_dag_code",
    "list_apps",
    "list_cartridges",
    "list_datasets",
    "list_datasets_with_schemas",
    "list_entities",
    "list_rag_sources",
    "list_sources",
    "minio_list_cartridge_specs",
    "minio_read_spec",
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


_SENSITIVE_ARG_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "password",
    "secret",
    "token",
)


def _scrub_tool_args(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(fragment in key_text for fragment in _SENSITIVE_ARG_FRAGMENTS):
                out[key] = "***"
            else:
                out[key] = _scrub_tool_args(item)
        return out
    if isinstance(value, list):
        return [_scrub_tool_args(item) for item in value]
    return value


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


def filter_tools_by_whitelist(tools: list[dict], whitelist: set[str] | None) -> list[dict]:
    if not whitelist:
        return tools
    return [t for t in tools if _bare_tool_name(t["name"]) in whitelist]

STEP_INSTRUCTIONS = {
    1: """\
Step RESUMEN — visión general del cartucho activo.
- Overview: cartridge_get_manifest(id).
- Significado de un término: cartridge_search_term(id, query).
- Listar cartuchos: list_cartridges().
- Crear cartucho completo: usa `studio__create_full_cartridge` cuando el usuario ya
  entregó nombre, descripción, entidades, DAGs, KBs/agentes/hints. No uses la creación
  mínima si hay metadatos completos; esta tool valida integridad y seed.sql.
- Importar ZIP: indica al usuario que use el botón "Importar ZIP".
""",
    2: """\
Step DAGS — gestión de DAGs de Airflow del cartucho.
- Lista DAGs: airflow_list_dags() (filtra por nombre/tag del cartucho).
- Código fuente existente: dag_get_source(cartridge_id, dag_id).
- Crear DAG dinámico: primero llama `studio__introspect_source(cartridge_id)`;
  después llama `studio__generate_dag_code(cartridge_id, entity_name, schema)`
  usando el schema devuelto; luego llama silenciosamente
  `studio__validate_dag_code(code)` antes de mostrar el código. Presenta el
  Python resultante al usuario sólo si la validación pasa.
- Si `validate_dag_code` falla, captura el `stderr`, vuelve a llamar
  `studio__generate_dag_code` pasando ese error como contexto si está disponible,
  y revalida. Máximo 2 intentos; si ambos fallan, avisa el error detallado.
- No uses plantillas con EDIT_HERE para crear DAGs nuevos.
- Crear/actualizar: airflow_create_dag(dag_id, code, cartridge_id) + dag_save_source.
  v1.43.2 (Frontend R2): airflow_create_dag está deshabilitado fuera
  de modo desarrollo — fallará con PermissionError en producción.
  Si el entorno NO es development, NO sugieras esta tool: indica al
  operador que use el pipeline de despliegue (CI/CD) o la UI de
  Airflow directamente. Puedes consultar el modo via /api/system/info.
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
Tu ámbito es SOLO Studio. Si el usuario pide algo fuera de diseñar entidades,
DAGs, datasets, Superset, semántica o RAG de Studio, redirígelo al copiloto global.
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
     · Paso 2 o 3 sin metadatos → `studio__introspect_source(cartridge_id)`
     · Crear DAG en Paso 2     → `studio__introspect_source`,
                                  `studio__generate_dag_code`,
                                  `studio__validate_dag_code`

  3. SINTETIZA con datos REALES de la tool:
     · Si la tool NO devolvió matches, di "No está definido en este cartucho".
     · NUNCA digas "no encontré" sin haber llamado primero la tool correspondiente.

Restricciones globales:
- PROHIBIDO suponer que un término no existe sin haber llamado `cartridge_search_term`.
- PROHIBIDO pedir el cartridge_id al usuario si puedes obtenerlo con `list_cartridges`.
- PROHIBIDO pedir YAML al usuario en Paso 2 o 3 sin intentar primero
  `studio__introspect_source(cartridge_id)`; sólo pide YAML si esa introspección falla
  o el esquema devuelto no contiene metadatos suficientes.
- Si el usuario pide crear un DAG en el Paso 2, primero llama a
  `studio__introspect_source` para obtener el esquema, luego usa
  `studio__generate_dag_code` con ese esquema y ejecuta silenciosamente
  `studio__validate_dag_code` antes de presentar el código resultante al usuario.
  Esta es la excepción al bloqueo global de pegar código: puedes mostrar el
  Python validado generado por `generate_dag_code`.
- Si el DAG no pasa validación tras 2 intentos, NO entregues código incompleto:
  informa el `stderr` de validación y qué faltó corregir.
- PROHIBIDO responder con plantillas que contengan EDIT_HERE; el DAG generado
  debe estar validado y listo para revisión/despliegue.
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
  · studio__*      → herramientas locales de Studio, como introspección de fuente
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
    actor_user: dict | None = None,
    tools_whitelist: set[str] | None = None,
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

    for t in _local_tools():
        full_name = f"{STUDIO_LOCAL_SERVER_ID}__{t['name']}"
        tools.append({
            "name": full_name,
            "description": f"[Studio] {t.get('description', '')}",
            "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
        })
        tool_server_map[full_name] = STUDIO_LOCAL_SERVER_ID

    # Tool slimming: only expose tools relevant to the active step + common ones.
    # Reduces ~60 tools to 10–20 per call, sharply improving LLM accuracy.
    tools = filter_tools_for_step(tools, step)
    tools = filter_tools_by_whitelist(tools, tools_whitelist or STUDIO_TOOLS_WHITELIST)
    tools = [t for t in tools if is_tool_allowed_for_role(actor_role, t["name"])]

    # Keep full history (including tool call/result blocks) so the model
    # remembers what tools it already ran and what they returned.
    # Per-turn dynamic context (manifest + step) is prepended to the latest user
    # message so the static system stays cacheable across cartridges/steps.
    messages = list(history)
    context = _build_dynamic_context(step, manifest)
    messages.append({"role": "user", "content": f"{context}\n\n---\n\n{message}"})

    system = _build_system_static()
    hints = (manifest or {}).get("assistant_hints", "").strip()
    if hints:
        system = (
            f"{system}\n\n"
            f"<hints_cartucho>\n"
            f"Instrucciones específicas del cartucho `{manifest.get('id', '?')}`. "
            f"Tómalas como complemento de las reglas globales; si entran en conflicto, "
            f"ganan las globales.\n\n"
            f"{hints}\n"
            f"</hints_cartucho>"
        )

    async def _invoke_tool(srv: str, tool: str, args: dict):
        full_name = f"{srv}__{tool}"
        bare_name = _bare_tool_name(full_name)
        if bare_name not in (tools_whitelist or STUDIO_TOOLS_WHITELIST):
            await audit_service.record_event(
                user_id=(actor_user or {}).get("id"),
                email=(actor_user or {}).get("email"),
                action="studio.assistant.tool_denied",
                resource_type="mcp_tool",
                resource_id=full_name,
                status="forbidden",
                metadata={"server": srv, "tool": tool, "reason": "outside_studio_scope"},
                tool_name=full_name,
                tool_args=_scrub_tool_args(args or {}),
                tool_result_status="forbidden",
                risk_level="write",
            )
            return {"error": f"Forbidden: tool {tool} is outside Studio scope"}
        if not is_tool_allowed_for_role(actor_role, full_name):
            await audit_service.record_event(
                user_id=(actor_user or {}).get("id"),
                email=(actor_user or {}).get("email"),
                action="studio.assistant.tool_denied",
                resource_type="mcp_tool",
                resource_id=full_name,
                status="forbidden",
                metadata={"server": srv, "tool": tool, "reason": "role_policy"},
                tool_name=full_name,
                tool_args=_scrub_tool_args(args or {}),
                tool_result_status="forbidden",
                risk_level=classify_tool(bare_name)["risk_level"],
            )
            return {"error": "Forbidden: analyst role is limited to read, inspect, query and preview tools"}
        risk = classify_tool(bare_name)["risk_level"]
        try:
            if srv == STUDIO_LOCAL_SERVER_ID:
                result = await _invoke_local_tool(tool, args, actor_user)
            else:
                result = await mcp_registry.invoke(srv, tool, args, user=actor_user)
        except HTTPException as exc:
            message = exc.detail if isinstance(exc.detail, str) else f"HTTP {exc.status_code}"
            detail_payload = exc.detail if isinstance(exc.detail, dict) else None
            await audit_service.record_event(
                user_id=(actor_user or {}).get("id"),
                email=(actor_user or {}).get("email"),
                action="studio.assistant.tool_call",
                resource_type="mcp_tool",
                resource_id=full_name,
                status="error",
                metadata={"server": srv, "tool": tool, "status_code": exc.status_code},
                tool_name=full_name,
                tool_args=_scrub_tool_args(args or {}),
                tool_result_status="error",
                risk_level=risk,
            )
            if detail_payload is not None:
                return {
                    "error": str(detail_payload.get("error") or message),
                    "status_code": exc.status_code,
                    "details": detail_payload,
                }
            return {"error": str(message), "status_code": exc.status_code}
        status = "error" if isinstance(result, dict) and result.get("error") else "success"
        await audit_service.record_event(
            user_id=(actor_user or {}).get("id"),
            email=(actor_user or {}).get("email"),
            action="studio.assistant.tool_call",
            resource_type="mcp_tool",
            resource_id=full_name,
            status=status,
            metadata={"server": srv, "tool": tool},
            tool_name=full_name,
            tool_args=_scrub_tool_args(args or {}),
            tool_result_status=status,
            risk_level=risk,
        )
        return result

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
