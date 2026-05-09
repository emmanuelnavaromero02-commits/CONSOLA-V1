"""
MODecissionsPaaS — Refinement Engine
Servicio transversal: cualquier cartucho deposita en Bronze, el engine
transforma a Silver/Gold con términos de negocio y trazabilidad de lineage.
"""
from __future__ import annotations

import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Depends

from app.duckdb_engine import DuckDBEngine
from app.dataset_store import DatasetStore
from app.llm_sql import generate_sql
from app.security import get_internal_api_key

DATASETS_DIR = Path("/app/datasets")
engine = DuckDBEngine()
store  = DatasetStore(DATASETS_DIR)
DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _postgres_dsn() -> str:
    return _normalize_postgres_dsn(os.environ.get("DATABASE_URL", ""))


def _validate_dataset_name(name: str) -> None:
    if not DATASET_NAME_RE.fullmatch(name or ""):
        raise HTTPException(400, "Invalid dataset name")


def _migrate_yaml_datasets():
    """One-time migration: import YAML dataset definitions into Postgres if missing."""
    import yaml
    yaml_dir = DATASETS_DIR
    if not yaml_dir.exists():
        return
    for f in yaml_dir.glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not data or not data.get("name"):
                continue
            existing = store.get_dataset(data["name"])
            if existing:
                continue  # already in Postgres
            store.save_dataset({
                "name":        data["name"],
                "layer":       data.get("layer", "silver"),
                "cartridge":   data.get("cartridge", "replicon"),
                "sources":     data.get("sources", []),
                "sql":         data.get("sql", data.get("sql_def", "")),
                "description": data.get("description", ""),
            })
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.setup()
    _migrate_yaml_datasets()
    _seed_catalog_from_existing()
    _seed_relationships()
    yield

INTERNAL_API_KEY = get_internal_api_key()
def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    # Validate the key and that the caller explicitly declares itself
    if not x_internal_service or x_internal_service not in ["console", "workspace", "refinement", "mcp-infra", "airflow", "replicon"]:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key or not secrets.compare_digest(x_api_key, INTERNAL_API_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")

app = FastAPI(title="MODecissionsPaaS Refinement", lifespan=lifespan)


# ── MCP tools (consumidas por la consola y el LLM) ────────────────────────────

@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
async def mcp_tools():
    return {"tools": [

        # ── Bronze discovery ──────────────────────────────────────────────────
        {
            "name": "list_sources",
            "description": (
                "Lista todas las fuentes disponibles en Bronze (MinIO). "
                "Devuelve rutas del tipo raw/{cartridge}/{entity}."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_source_partitions",
            "description": (
                "Devuelve las particiones disponibles (load_date, batch_id) de una fuente Bronze. "
                "Úsala ANTES de save_dataset para conocer la fecha más reciente y obtener "
                "sql_latest — un SQL listo con el filtro correcto a la última carga."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string",
                               "description": "e.g. 'raw/replicon/TimeEntry'"},
                },
                "required": ["source"],
            },
        },
        {
            "name": "preview_source",
            "description": "Muestra schema y filas de muestra de una fuente Bronze.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "limit":  {"type": "integer", "default": 5},
                },
                "required": ["source"],
            },
        },

        # ── SQL generation & preview ──────────────────────────────────────────
        {
            "name": "generate_transform",
            "description": (
                "Usa LLM para generar SQL de transformación dado una descripción en lenguaje "
                "natural y las fuentes Bronze. Renombra columnas a términos de negocio. "
                "Siempre hacer preview_transform antes de save_dataset."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string",
                                    "description": "Qué debe contener el dataset en términos de negocio"},
                    "sources":     {"type": "array", "items": {"type": "string"},
                                    "description": "Fuentes bronze a usar, e.g. ['raw/replicon/User']"},
                    "cartridge":   {"type": "string",
                                    "description": "ID del cartucho origen, e.g. 'replicon'"},
                },
                "required": ["description", "sources"],
            },
        },
        {
            "name": "preview_transform",
            "description": (
                "Ejecuta un SQL en DuckDB y devuelve schema + muestra de filas SIN materializar. "
                "Úsala para validar el SQL antes de guardar."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql":     {"type": "string"},
                    "limit":   {"type": "integer", "default": 20},
                    "sources": {"type": "array", "items": {"type": "string"}, "default": []},
                },
                "required": ["sql"],
            },
        },

        # ── Dataset lifecycle ─────────────────────────────────────────────────
        {
            "name": "save_dataset",
            "description": (
                "Guarda la definición de un dataset Silver o Gold. "
                "layer='silver': analítico — Parquet en MinIO. "
                "layer='master': maestro pequeño — Parquet + tabla Postgres. "
                "layer='gold': agregación — tabla Postgres. "
                "Incluye cartridge, source_load_date y column_mapping para trazabilidad."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":             {"type": "string",
                                         "description": "Nombre único del dataset, e.g. 'empleados_activos'"},
                    "description":      {"type": "string"},
                    "sql":              {"type": "string"},
                    "layer":            {"type": "string", "enum": ["silver", "master", "gold"]},
                    "sources":          {"type": "array", "items": {"type": "string"}},
                    "cartridge":        {"type": "string",
                                         "description": "ID del cartucho origen, e.g. 'replicon'"},
                    "column_mapping":   {"type": "object",
                                         "description": "Mapeo de columnas origen a términos de negocio"},
                    "source_load_date": {"type": "string",
                                         "description": "Fecha de la partición bronze usada (YYYY-MM-DD)"},
                    "source_batch_id":  {"type": "string"},
                },
                "required": ["name", "sql", "layer"],
            },
        },
        {
            "name": "materialize",
            "description": (
                "Ejecuta el SQL del dataset guardado y escribe el resultado en Silver/Gold. "
                "Registra automáticamente el lineage en silver_lineage."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "list_datasets",
            "description": "Lista todos los datasets Silver/Gold definidos con su estado.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_schema",
            "description": "Devuelve schema (campos y tipos) de un dataset definido.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "query_dataset",
            "description": "Consulta un dataset Silver/Gold con filtros opcionales.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":    {"type": "string"},
                    "filters": {"type": "object"},
                    "limit":   {"type": "integer", "default": 100},
                },
                "required": ["name"],
            },
        },

        # ── Schema discovery (para el asistente IA) ──────────────────────────
        {
            "name": "describe_source",
            "description": (
                "Devuelve el schema completo (columnas y tipos) de una fuente Bronze "
                "junto con una muestra de filas. Útil para entender qué datos hay "
                "antes de escribir un SQL de transformación. "
                "source: ruta bronze, e.g. 'raw/replicon/TimeEntry'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "limit":  {"type": "integer", "default": 3},
                },
                "required": ["source"],
            },
        },
        {
            "name": "describe_silver",
            "description": (
                "Devuelve el schema (columnas y tipos) de un dataset Silver/Master "
                "ya materializado leyendo su Parquet en MinIO. "
                "Más rápido que get_schema porque no re-ejecuta el SQL fuente. "
                "name: nombre del dataset, e.g. 'replicon_timeentry_latest'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string"},
                    "limit": {"type": "integer", "default": 3,
                              "description": "Filas de muestra (0 = solo schema)"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "list_datasets_with_schemas",
            "description": (
                "Devuelve todos los datasets registrados (silver/master/gold) con sus "
                "columnas. Llama esto primero para entender el modelo de datos completo "
                "antes de diseñar un dataset Gold o escribir SQL analítico."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "layer":     {"type": "string",
                                  "description": "Filtrar por capa: silver|master|gold (vacío=todos)"},
                    "cartridge": {"type": "string",
                                  "description": "Filtrar por cartucho, e.g. 'replicon'"},
                },
            },
        },

        # ── Semantic catalog ──────────────────────────────────────────────────
        {
            "name": "get_data_catalog",
            "description": (
                "Devuelve el catálogo semántico completo: schema con descripciones de negocio "
                "y relaciones entre datasets. UNA sola llamada reemplaza list_datasets_with_schemas "
                "+ múltiples describe_silver. Úsala como primer paso antes de generar cualquier SQL analítico. "
                "Filtra por layer, cartridge o tags para reducir el contexto."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "layer":     {"type": "string",
                                  "description": "Filtrar por capa: silver|master|gold"},
                    "cartridge": {"type": "string",
                                  "description": "Filtrar por cartucho, e.g. 'replicon'"},
                    "tags":      {"type": "array", "items": {"type": "string"},
                                  "description": "Filtrar por tags, e.g. ['pnl', 'tiempo']"},
                    "datasets":  {"type": "array", "items": {"type": "string"},
                                  "description": "Lista específica de dataset names a incluir"},
                },
            },
        },
        {
            "name": "upsert_catalog_entries",
            "description": (
                "Agrega o actualiza descripciones semánticas, tags y flags en data_catalog. "
                "Úsala para enriquecer el catálogo con contexto de negocio que no se puede "
                "inferir automáticamente del schema."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "Lista de entradas a upsert",
                        "items": {
                            "type": "object",
                            "properties": {
                                "dataset":     {"type": "string"},
                                "column_name": {"type": "string"},
                                "description": {"type": "string"},
                                "tags":        {"type": "array", "items": {"type": "string"}},
                                "is_key":      {"type": "boolean"},
                                "is_metric":   {"type": "boolean"},
                                "example_values": {"type": "array"},
                            },
                            "required": ["dataset", "column_name"],
                        },
                    },
                },
                "required": ["entries"],
            },
        },
        {
            "name": "register_relationship",
            "description": (
                "Registra una relación (JOIN) entre columnas de dos datasets. "
                "Úsala para documentar cómo se unen las tablas del modelo. "
                "El catálogo devuelve estas relaciones junto con el schema para "
                "que el LLM pueda generar JOINs correctos sin adivinar."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "from_dataset": {"type": "string", "description": "Dataset origen"},
                    "from_column":  {"type": "string", "description": "Columna origen"},
                    "to_dataset":   {"type": "string", "description": "Dataset destino"},
                    "to_column":    {"type": "string", "description": "Columna destino"},
                    "join_hint":    {"type": "string", "default": "LEFT",
                                     "description": "Tipo de JOIN sugerido: LEFT|INNER|COALESCE"},
                    "description":  {"type": "string",
                                     "description": "Descripción del significado de la relación"},
                    "transform":    {"type": "string",
                                     "description": "Transformación necesaria al hacer JOIN, e.g. "
                                                    "'CAST(TRY_CAST(from AS DOUBLE) AS BIGINT)::VARCHAR'"},
                },
                "required": ["from_dataset", "from_column", "to_dataset", "to_column"],
            },
        },

        # ── Analytic Apps ─────────────────────────────────────────────────────
        {
            "name": "publish_app",
            "description": (
                "Publica una aplicación analítica HTML generada. El HTML puede usar "
                "fetch('/api/data/{dataset}') para obtener datos del lakehouse en JSON. "
                "Devuelve la URL pública de la app: /apps/{name}. "
                "Úsala después de generar el HTML completo y auto-contenido. "
                "IMPORTANTE: pasa cartridge_id para que la app viaje en el ZIP de export "
                "del cartucho (si la app usa datasets de un cartucho específico)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":         {"type": "string",
                                     "description": "Slug de la app, e.g. 'pnl_revenue_manager'"},
                    "title":        {"type": "string",
                                     "description": "Título descriptivo de la app"},
                    "html":         {"type": "string",
                                     "description": "HTML completo de la app (incluyendo <style> y <script>)"},
                    "description":  {"type": "string",
                                     "description": "Descripción corta de qué muestra la app"},
                    "cartridge_id": {"type": "string",
                                     "description": "Cartridge al que pertenece la app, e.g. 'replicon'. "
                                                    "Determina con qué cartucho viaja en export/import."},
                },
                "required": ["name", "title", "html"],
            },
        },
        {
            "name": "list_apps",
            "description": "Lista todas las aplicaciones analíticas publicadas con sus datasets_used.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_app_details",
            "description": (
                "Devuelve la metadata completa de una app: title, description, "
                "cartridge_id, visibility, datasets_used (los datasets que la app "
                "consume vía /api/data/<name>). Úsala antes de explicar o modificar "
                "una app para saber con qué datos trabaja."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Slug de la app"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_app_html",
            "description": (
                "Devuelve el HTML completo (source) de una app publicada. "
                "USAR SIEMPRE antes de modificar una app: edita el HTML retornado "
                "y vuelve a publicarlo con publish_app. NUNCA generes HTML desde "
                "cero cuando el usuario pida un cambio sobre una app existente."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Slug de la app"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "delete_app",
            "description": "Elimina una aplicación analítica publicada por su slug.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Slug de la app a eliminar"},
                },
                "required": ["name"],
            },
        },

        # ── Dataset delete ────────────────────────────────────────────────────
        {
            "name": "delete_dataset",
            "description": (
                "Elimina un dataset: borra su registro de Postgres, el Parquet de MinIO "
                "(silver/master) y la tabla Postgres correspondiente (master/gold)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },

        # ── Lineage ───────────────────────────────────────────────────────────
        {
            "name": "get_lineage",
            "description": (
                "Devuelve el historial de materializaciones de un dataset: "
                "qué SQL se usó, qué partición bronze fue la fuente, cuántas filas, cuándo."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "Nombre del dataset silver/gold"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["name"],
            },
        },
    ]}


@app.post("/mcp/invoke", dependencies=[Depends(verify_api_key)])
async def mcp_invoke(body: dict):
    tool = body.get("tool")
    args = body.get("args", {})

    if tool == "list_sources":
        return {"sources": engine.list_sources()}

    if tool == "get_source_partitions":
        return engine.get_source_partitions(args["source"])

    if tool == "preview_source":
        return engine.preview_source(args["source"], args.get("limit", 5))

    if tool == "generate_transform":
        schemas = {s: engine.get_source_schema(s) for s in args["sources"]}
        sql, explanation = await generate_sql(args["description"], schemas)
        return {"sql": sql, "explanation": explanation, "cartridge": args.get("cartridge")}

    if tool == "preview_transform":
        return engine.preview_sql(args["sql"], args.get("limit", 20), args.get("sources"), args.get("user_context"))

    if tool == "save_dataset":
        store.save_dataset(args)
        return {"saved": True, "name": args["name"]}

    if tool == "delete_dataset":
        ds_name = args["name"]
        _validate_dataset_name(ds_name)
        # Block deletion if any published app references this dataset (best-effort
        # via substring scan of the HTML — apps fetch via /api/data/<dataset>).
        try:
            blockers = _pg_exec(
                "SELECT name FROM analytic_apps WHERE html LIKE %s",
                (f"%/api/data/{ds_name}%",), fetch=True,
            ) or []
            if blockers and not args.get("force"):
                return {
                    "error": f"dataset '{ds_name}' is used by {len(blockers)} app(s): "
                             + ", ".join(b["name"] for b in blockers[:5])
                             + (" …" if len(blockers) > 5 else "")
                             + ". Delete those apps first, or pass force=true to override.",
                    "blocking_apps": [b["name"] for b in blockers],
                }
        except Exception:
            pass
        info = store.delete_dataset(ds_name)
        if not info.get("deleted"):
            raise HTTPException(404, info.get("error", "not found"))
        layer     = info["layer"]
        cartridge = info["cartridge"]
        name      = info["name"]
        _validate_dataset_name(name)
        steps     = []
        # Delete MinIO Parquet for silver / master
        if layer in ("silver", "master"):
            try:
                from minio import Minio
                mc = Minio(
                    engine.minio_endpoint,
                    access_key=engine.minio_access,
                    secret_key=engine.minio_secret,
                    secure=engine.minio_secure,
                )
                obj_path = f"silver/{cartridge}/{name}/data.parquet"
                mc.remove_object(engine.minio_bucket, obj_path)
                steps.append(f"parquet deleted: s3://{engine.minio_bucket}/{obj_path}")
            except Exception as exc:
                steps.append(f"parquet not found or already deleted: {exc}")
        # Drop Postgres table for master / gold (both live in postgres_gold)
        if layer in ("master", "gold"):
            prefix = "master" if layer == "master" else "gold"
            table  = f"{prefix}_{name}"
            try:
                import psycopg2
                dsn  = engine.pg_gold_url.replace("postgresql+psycopg2://", "postgresql://")
                conn = psycopg2.connect(dsn)
                with conn.cursor() as cur:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}"')
                conn.commit()
                conn.close()
                steps.append(f"table dropped: {table}")
            except Exception as exc:
                steps.append(f"table drop failed: {exc}")
        return {"deleted": True, "name": name, "layer": layer, "steps": steps}

    if tool == "materialize":
        ds = store.get_dataset(args["name"])
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        result = engine.materialize(ds)
        store.update_refresh(args["name"], result["row_count"])
        return result

    if tool == "list_datasets":
        return {"datasets": store.list_datasets()}

    if tool == "get_schema":
        ds = store.get_dataset(args["name"])
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        return engine.get_dataset_schema(ds)

    if tool == "query_dataset":
        ds = store.get_dataset(args["name"])
        if not ds:
            raise HTTPException(404, f"Dataset '{args['name']}' not found")
        return engine.query_dataset(ds, args.get("filters", {}), args.get("limit", 100), args.get("user_context"))

    if tool == "get_lineage":
        return _get_lineage(args["name"], args.get("limit", 10))

    if tool == "describe_source":
        source = args["source"]
        limit  = args.get("limit", 3)
        schema = engine.get_source_schema(source)
        preview = engine.preview_source(source, limit)
        return {
            "source":  source,
            "fields":  schema.get("fields", []),
            "sample":  preview.get("data", []),
            "error":   schema.get("error") or preview.get("error"),
        }

    if tool == "describe_silver":
        name  = args["name"]
        _validate_dataset_name(name)
        limit = args.get("limit", 3)
        ds    = store.get_dataset(name)
        if not ds:
            raise HTTPException(404, f"Dataset '{name}' not found")
        cartridge = ds.get("cartridge", "unknown")
        _validate_dataset_name(cartridge)
        parquet   = f"s3://{engine.minio_bucket}/silver/{cartridge}/{name}/data.parquet"
        try:
            with engine._duckdb_lock:
                con = engine._conn()
                schema_rows = con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0"
                ).fetchall()
                fields = [{"name": r[0], "type": r[1]} for r in schema_rows]
                sample = []
                if limit > 0:
                    cols = [r[0] for r in schema_rows]
                    rows = con.execute(
                        f"SELECT * FROM read_parquet('{parquet}') LIMIT {limit}"
                    ).fetchall()
                    sample = [dict(zip(cols, row)) for row in rows]
            return {"name": name, "layer": ds["layer"], "cartridge": cartridge,
                    "parquet": parquet, "fields": fields, "sample": sample}
        except Exception as exc:
            return {"name": name, "error": str(exc),
                    "hint": "Dataset might not be materialized yet — run materialize first"}

    if tool == "list_datasets_with_schemas":
        layer_filter     = args.get("layer")
        cartridge_filter = args.get("cartridge")
        all_ds = store.list_datasets()
        result = []
        for ds_meta in all_ds:
            if layer_filter and ds_meta["layer"] != layer_filter:
                continue
            if cartridge_filter and ds_meta["cartridge"] != cartridge_filter:
                continue
            entry = {
                "name":      ds_meta["name"],
                "layer":     ds_meta["layer"],
                "cartridge": ds_meta["cartridge"],
                "sources":   ds_meta["sources"],
                "row_count": ds_meta["row_count"],
                "fields":    [],
            }
            ds_full = store.get_dataset(ds_meta["name"])
            if ds_full:
                schema = engine.get_dataset_schema(ds_full)
                entry["fields"] = schema.get("fields", [])
                if schema.get("error"):
                    entry["schema_error"] = schema["error"]
            result.append(entry)
        return {"datasets": result}

    if tool == "get_data_catalog":
        return _get_data_catalog(
            layer=args.get("layer"),
            cartridge=args.get("cartridge"),
            tags=args.get("tags"),
            datasets=args.get("datasets"),
        )

    if tool == "upsert_catalog_entries":
        return _upsert_catalog_entries(args["entries"])

    if tool == "register_relationship":
        return _register_relationship(args)

    if tool == "publish_app":
        return _publish_app(args)

    if tool == "list_apps":
        return _list_apps()

    if tool == "get_app_details":
        return _get_app_details(args)

    if tool == "get_app_html":
        return _get_app_html(args)

    if tool == "delete_app":
        return _delete_app(args)

    raise HTTPException(400, f"Unknown tool: {tool}")


def _get_lineage(name: str, limit: int) -> dict:
    try:
        import psycopg2
        conn = psycopg2.connect(_postgres_dsn())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT silver_name, cartridge_id, source_entity,
                       source_load_date, source_batch_id, layer,
                       row_count, storage_uri, created_by, created_at
                FROM silver_lineage
                WHERE silver_name = %s
                ORDER BY created_at DESC LIMIT %s
            """, (name, min(limit, 50)))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            if r.get("source_load_date"):
                r["source_load_date"] = str(r["source_load_date"])
        return {"name": name, "lineage": rows}
    except Exception as exc:
        return {"name": name, "error": str(exc)}


def _pg_exec(query: str, params=None, fetch=False):
    import psycopg2
    conn = psycopg2.connect(_postgres_dsn())
    result = None
    with conn.cursor() as cur:
        cur.execute(query, params)
        if fetch:
            cols   = [d[0] for d in cur.description]
            result = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.commit()
    conn.close()
    return result


def _get_data_catalog(
    layer: str | None = None,
    cartridge: str | None = None,
    tags: list[str] | None = None,
    datasets: list[str] | None = None,
) -> dict:
    import json as _json
    conditions = ["1=1"]
    params: list = []
    if layer:
        conditions.append("c.layer = %s"); params.append(layer)
    if cartridge:
        conditions.append("c.cartridge = %s"); params.append(cartridge)
    if tags:
        conditions.append("c.tags && %s"); params.append(tags)
    if datasets:
        conditions.append("c.dataset = ANY(%s)"); params.append(datasets)

    where = " AND ".join(conditions)
    cols = _pg_exec(
        f"""SELECT c.dataset, c.layer, c.cartridge, c.column_name,
                   c.data_type, c.description, c.example_values,
                   c.tags, c.is_key, c.is_metric
            FROM data_catalog c
            WHERE {where}
            ORDER BY c.dataset, c.column_name""",
        params, fetch=True,
    ) or []

    # Group by dataset
    datasets_out: dict = {}
    ds_names: set = set()
    for row in cols:
        dn = row["dataset"]
        ds_names.add(dn)
        if dn not in datasets_out:
            # get dataset description from store
            ds_meta = store.get_dataset(dn)
            datasets_out[dn] = {
                "layer":       row["layer"],
                "cartridge":   row["cartridge"],
                "description": ds_meta.get("description", "") if ds_meta else "",
                "columns":     [],
            }
        ev = row["example_values"]
        if isinstance(ev, str):
            try:
                ev = _json.loads(ev)
            except Exception:
                ev = None
        datasets_out[dn]["columns"].append({
            "name":           row["column_name"],
            "type":           row["data_type"],
            "description":    row["description"] or "",
            "tags":           row["tags"] or [],
            "is_key":         row["is_key"],
            "is_metric":      row["is_metric"],
            "example_values": ev,
        })

    # Fetch relevant relationships
    rels: list = []
    if ds_names:
        rels = _pg_exec(
            """SELECT from_dataset, from_column, to_dataset, to_column,
                      join_hint, description, transform
               FROM data_relationships
               WHERE from_dataset = ANY(%s) OR to_dataset = ANY(%s)
               ORDER BY from_dataset, from_column""",
            [list(ds_names), list(ds_names)], fetch=True,
        ) or []

    return {"datasets": datasets_out, "relationships": rels}


def _upsert_catalog_entries(entries: list[dict]) -> dict:
    import json as _json
    import psycopg2
    conn = psycopg2.connect(_postgres_dsn())
    updated = 0
    with conn.cursor() as cur:
        for e in entries:
            ev = e.get("example_values")
            cur.execute("""
                INSERT INTO data_catalog
                    (dataset, layer, cartridge, column_name, data_type, description,
                     example_values, tags, is_key, is_metric, updated_at)
                SELECT %s, COALESCE(d.layer,'silver'), COALESCE(d.cartridge,''),
                       %s, '', %s,
                       %s::jsonb, %s, %s, %s, NOW()
                FROM (SELECT layer, cartridge FROM datasets WHERE name=%s
                      UNION ALL SELECT 'silver','') d LIMIT 1
                ON CONFLICT (dataset, column_name) DO UPDATE
                    SET description    = COALESCE(NULLIF(EXCLUDED.description,''), data_catalog.description),
                        example_values = COALESCE(EXCLUDED.example_values, data_catalog.example_values),
                        tags           = CASE WHEN EXCLUDED.tags != '{}' THEN EXCLUDED.tags
                                              ELSE data_catalog.tags END,
                        is_key         = COALESCE(EXCLUDED.is_key,   data_catalog.is_key),
                        is_metric      = COALESCE(EXCLUDED.is_metric, data_catalog.is_metric),
                        updated_at     = NOW()
            """, (
                e["dataset"],
                e["column_name"],
                e.get("description", ""),
                _json.dumps(ev) if ev is not None else None,
                e.get("tags", []),
                e.get("is_key"),
                e.get("is_metric"),
                e["dataset"],
            ))
            updated += 1
    conn.commit()
    conn.close()
    return {"updated": updated}


def _register_relationship(args: dict) -> dict:
    _pg_exec("""
        INSERT INTO data_relationships
            (from_dataset, from_column, to_dataset, to_column, join_hint, description, transform)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (from_dataset, from_column, to_dataset, to_column) DO UPDATE
            SET join_hint   = EXCLUDED.join_hint,
                description = EXCLUDED.description,
                transform   = EXCLUDED.transform
    """, (
        args["from_dataset"],
        args["from_column"],
        args["to_dataset"],
        args["to_column"],
        args.get("join_hint", "LEFT"),
        args.get("description", ""),
        args.get("transform"),
    ))
    return {"registered": True,
            "relation": f"{args['from_dataset']}.{args['from_column']} → {args['to_dataset']}.{args['to_column']}"}


def _ensure_apps_table():
    _pg_exec("""
        CREATE TABLE IF NOT EXISTS analytic_apps (
            name         TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            html         TEXT NOT NULL,
            description  TEXT,
            cartridge_id TEXT,
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    _pg_exec("ALTER TABLE analytic_apps ADD COLUMN IF NOT EXISTS cartridge_id TEXT")


def _publish_app(args: dict) -> dict:
    name  = (args.get("name")  or "").strip()
    title = (args.get("title") or "").strip()
    html  = args.get("html") or ""
    if not name:
        return {"error": "name is required (slug snake_case, e.g. 'pnl_revenue_manager')"}
    if not title:
        return {"error": "title is required (human-readable app title)"}
    if not html or not html.strip():
        return {"error": "html is required — full self-contained HTML of the app, "
                         "including <style> and <script>. Generate the complete page "
                         "before calling publish_app."}
    if len(html) < 200:
        return {"error": f"html looks too short ({len(html)} chars) — provide the "
                         "complete page, not a placeholder."}
    visibility = args.get("visibility") if args.get("visibility") in ("private", "shared") else "private"
    # Auto-extract dataset names referenced via /api/data/<name>
    import re as _re
    datasets_used = sorted(set(_re.findall(r"/api/data/([a-zA-Z_][a-zA-Z0-9_]*)", html)))
    _ensure_apps_table()
    # Make sure column exists (older deploys may not have it)
    _pg_exec("ALTER TABLE analytic_apps ADD COLUMN IF NOT EXISTS datasets_used TEXT[]")
    _pg_exec("""
        INSERT INTO analytic_apps (name, title, html, description, cartridge_id,
                                   created_by_id, visibility, datasets_used, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (name) DO UPDATE
            SET title         = EXCLUDED.title,
                html          = EXCLUDED.html,
                description   = EXCLUDED.description,
                cartridge_id  = COALESCE(EXCLUDED.cartridge_id, analytic_apps.cartridge_id),
                created_by_id = COALESCE(EXCLUDED.created_by_id, analytic_apps.created_by_id),
                visibility    = EXCLUDED.visibility,
                datasets_used = EXCLUDED.datasets_used,
                updated_at    = NOW()
    """, (name, title, html, args.get("description", ""), args.get("cartridge_id"),
          args.get("created_by_id"), visibility, datasets_used))
    return {"published": True, "name": name,
            "cartridge_id": args.get("cartridge_id"),
            "visibility": visibility,
            "datasets_used": datasets_used,
            "url": f"/apps/{name}"}


def _get_app_details(args: dict) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    rows = _pg_exec(
        "SELECT name, title, description, cartridge_id, visibility, datasets_used, updated_at "
        "FROM analytic_apps WHERE name = %s",
        (name,), fetch=True,
    ) or []
    if not rows:
        return {"error": f"app '{name}' not found"}
    r = dict(rows[0])
    if r.get("updated_at"):
        r["updated_at"] = r["updated_at"].isoformat()
    r["url"] = f"/apps/{r['name']}"
    return r


def _get_app_html(args: dict) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    rows = _pg_exec(
        "SELECT name, title, description, cartridge_id, visibility, datasets_used, html "
        "FROM analytic_apps WHERE name = %s",
        (name,), fetch=True,
    ) or []
    if not rows:
        return {"error": f"app '{name}' not found"}
    return dict(rows[0])


def _list_apps() -> dict:
    try:
        rows = _pg_exec(
            "SELECT name, title, description, datasets_used, updated_at "
            "FROM analytic_apps ORDER BY updated_at DESC",
            fetch=True,
        ) or []
        for r in rows:
            if r.get("updated_at"):
                r["updated_at"] = r["updated_at"].isoformat()
            r["url"] = f"/apps/{r['name']}"
        return {"apps": rows}
    except Exception:
        return {"apps": []}


def _delete_app(args: dict) -> dict:
    name = args["name"]
    rows = _pg_exec(
        "DELETE FROM analytic_apps WHERE name=%s RETURNING name",
        (name,),
        fetch=True,
    ) or []
    if not rows:
        return {"deleted": False, "name": name, "error": "App not found"}
    return {"deleted": True, "name": name}


def _seed_catalog_from_existing() -> int:
    """
    Seed data_catalog with schema from already-materialized datasets.
    Only inserts rows where (dataset, column_name) doesn't exist yet.
    """
    seeded = 0
    try:
        all_ds = store.list_datasets()
        for meta in all_ds:
            name      = meta["name"]
            layer     = meta.get("layer", "silver")
            cartridge = meta.get("cartridge", "")
            try:
                _validate_dataset_name(name)
                if cartridge:
                    _validate_dataset_name(cartridge)
            except HTTPException:
                continue
            ds_full   = store.get_dataset(name)
            col_map   = ds_full.get("column_mapping", {}) if ds_full else {}

            # Get schema
            if layer in ("silver", "master"):
                parquet = (
                    f"s3://{engine.minio_bucket}/silver/{cartridge}/{name}/data.parquet"
                )
                try:
                    with engine._duckdb_lock:
                        con   = engine._conn()
                        rows  = con.execute(
                            f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0"
                        ).fetchall()
                    fields = [{"name": r[0], "type": r[1]} for r in rows]
                except Exception:
                    continue
            elif layer == "gold":
                try:
                    _validate_dataset_name(name)
                    import psycopg2
                    conn = psycopg2.connect(_normalize_postgres_dsn(
                        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
                    ))
                    with conn.cursor() as cur:
                        cur.execute(f"""
                            SELECT column_name, data_type
                            FROM information_schema.columns
                            WHERE table_name = 'gold_{name}'
                            ORDER BY ordinal_position
                        """)
                        fields = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
                    conn.close()
                except Exception:
                    continue
            else:
                continue

            engine._update_catalog(name, layer, cartridge, fields, col_map)
            seeded += len(fields)
    except Exception:
        pass
    return seeded


def _seed_relationships() -> int:
    """Register known Replicon model relationships if not already present."""
    relationships = [
        # TimeEntry → Proyectos
        ("replicon_timeentry_latest", "projectcode",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Proyecto del tiempo reportado — Project Type, Revenue Manager, % avance", None),
        ("replicon_timeentry_latest", "projectcode",
         "replicon_project_latest", "code",
         "LEFT", "Datos del proyecto: valor de contrato, cliente, estado", None),
        # TimeEntry → Personas
        ("replicon_timeentry_latest", "userid",
         "replicon_resourceallocation_latest", "userid",
         "LEFT", "Tarifa de facturación del consultor en este proyecto",
         "br.userid = te.userid AND br.projectcode = te.projectcode"),
        ("replicon_timeentry_latest", "username",
         "empleados_maestro", "usuario",
         "LEFT", "Costo/hora y tipo de contrato del consultor",
         "LOWER(TRIM(em.usuario)) = LOWER(TRIM(te.username))"),
        # ResourceAllocation → Proyectos
        ("replicon_resourceallocation_latest", "projectcode",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Proyecto de la asignación de recurso", None),
        ("replicon_resourceallocation_latest", "projectcode",
         "replicon_project_latest", "code",
         "LEFT", "Valor de contrato del proyecto asignado", None),
        # Progress History → Proyectos
        ("project_progress_history", "project_code",
         "replicon_project_latest", "code",
         "LEFT", "Valor de contrato para calcular revenue FPP por incremento de avance", None),
        ("project_progress_history", "project_code",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Revenue Manager y tipo de proyecto para el historial de avance", None),
        # Facturación → Proyectos (Project Code viene como float string: '29630595633.0')
        ("replicon_projectbilling_curated", "Project Code",
         "replicon_project_latest", "code",
         "LEFT", "Proyecto de la factura — normalizar Project Code: CAST(TRY_CAST(TRY_CAST(\"Project Code\" AS DOUBLE) AS BIGINT) AS VARCHAR)",
         "CAST(TRY_CAST(TRY_CAST(b.\"Project Code\" AS DOUBLE) AS BIGINT) AS VARCHAR) = p.code"),
        # BillingItem → Proyectos
        ("replicon_billingitem_latest", "projectcode",
         "replicon_project_latest", "code",
         "LEFT", "Proyecto del item de facturación", None),
        ("replicon_billingitem_latest", "projectcode",
         "replicon_project_detail_curated", "Project Code",
         "LEFT", "Revenue Manager y tipo del proyecto facturado", None),
    ]
    seeded = 0
    for rel in relationships:
        try:
            _pg_exec("""
                INSERT INTO data_relationships
                    (from_dataset, from_column, to_dataset, to_column,
                     join_hint, description, transform)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (from_dataset, from_column, to_dataset, to_column) DO NOTHING
            """, rel)
            seeded += 1
        except Exception:
            pass
    return seeded


# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/datasets", dependencies=[Depends(verify_api_key)])
async def list_datasets():
    return {"datasets": store.list_datasets()}


@app.get("/datasets/{name}/definition", dependencies=[Depends(verify_api_key)])
async def dataset_definition(name: str):
    ds = store.get_dataset(name)
    if not ds:
        raise HTTPException(404)
    return ds


@app.get("/datasets/{name}/schema", dependencies=[Depends(verify_api_key)])
async def dataset_schema(name: str):
    ds = store.get_dataset(name)
    if not ds:
        raise HTTPException(404)
    return engine.get_dataset_schema(ds)


@app.get("/datasets/{name}/data", dependencies=[Depends(verify_api_key)])
async def dataset_data(name: str, limit: int = 100):
    ds = store.get_dataset(name)
    if not ds:
        raise HTTPException(404)
    return engine.query_dataset(ds, {}, limit)


@app.post("/datasets/{name}/refresh", dependencies=[Depends(verify_api_key)])
async def refresh_dataset(name: str):
    ds = store.get_dataset(name)
    if not ds:
        raise HTTPException(404)
    result = engine.materialize(ds)
    store.update_refresh(name, result["row_count"])
    return result


@app.post("/refresh-by-source", dependencies=[Depends(verify_api_key)])
async def refresh_by_source(body: dict):
    """
    Re-materializa todos los datasets Silver/Master cuyas fuentes incluyen
    la ruta Bronze indicada. Llamado automáticamente por el job_runner
    tras completar una extracción.

    Body: {"source": "raw/replicon/TimeEntry"}
    """
    source = body.get("source", "").strip()
    if not source:
        raise HTTPException(400, "source is required")

    all_ds   = store.list_datasets()
    matched  = [d for d in all_ds if source in (d.get("sources") or [])]
    results  = []

    for meta in matched:
        ds = store.get_dataset(meta["name"])
        if not ds or ds.get("layer") == "gold":
            continue  # gold depende de Silver, no de Bronze directamente
        try:
            result = engine.materialize(ds)
            store.update_refresh(meta["name"], result["row_count"])
            results.append({"name": meta["name"], "status": "ok",
                             "row_count": result["row_count"],
                             "storage_uri": result["storage_uri"]})
        except Exception as exc:
            results.append({"name": meta["name"], "status": "error", "error": str(exc)})

    return {"source": source, "refreshed": len(results), "results": results}
