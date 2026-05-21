"""
Cartridge Service — PostgreSQL as source of truth.

All cartridge configuration (header, connections, DAGs, entities, semantic
vocabulary) lives in the database.  MinIO is used only for supplementary files
(OpenAPI specs, generated code, DAG source files).

Export = ZIP with:
  config/seed.sql   — generated from DB, re-runnable on any installation
  dags/*.py         — DAG source files from MinIO cartridges/{id}/dags/
  specs/*           — spec files from MinIO cartridges/{id}/specs/

Import = run seed.sql + store supplementary files in MinIO.
"""
from __future__ import annotations

import io
import os
import re
import textwrap
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import asyncpg

from app.security import get_internal_api_key
from app.services.security_context import build_security_context

_DATABASE_URL = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+psycopg2://", "postgresql://")
    .replace("postgresql+asyncpg://", "postgresql://")
)
_POOL: asyncpg.Pool | None = None

_MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT",   "minio:9000")
_MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minio")
_MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
_MINIO_BUCKET     = os.environ.get("MINIO_BUCKET",     "lakehouse")
_MINIO_SECURE     = os.environ.get("MINIO_SECURE", "false").lower() == "true"
_MAX_IMPORT_ZIP_BYTES = int(os.environ.get("CARTRIDGE_IMPORT_MAX_BYTES", str(25 * 1024 * 1024)))
_MAX_IMPORT_UNCOMPRESSED_BYTES = int(
    os.environ.get("CARTRIDGE_IMPORT_MAX_UNCOMPRESSED_BYTES", str(50 * 1024 * 1024))
)
_MAX_IMPORT_MEMBER_BYTES = int(
    os.environ.get("CARTRIDGE_IMPORT_MAX_MEMBER_BYTES", str(10 * 1024 * 1024))
)
_MAX_IMPORT_MEMBERS = int(os.environ.get("CARTRIDGE_IMPORT_MAX_MEMBERS", "500"))
_ALLOWED_SEED_TABLES = {
    "cartridges",
    "cartridge_connections",
    "cartridge_dags",
    "entity_config",
    "semantic_terms",
    "kb_config",
    "mcp_custom_tools",
    "analytic_apps",
    "agents",
}
_FORBIDDEN_SEED_SQL = re.compile(
    r"\b(drop|truncate|delete|copy|create\s+extension|create\s+function|"
    r"create\s+procedure|do\s+\$|grant|revoke|alter\s+system|attach|dblink|"
    r"foreign\s+server|foreign\s+table)\b|\\",
    re.IGNORECASE,
)
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_cartridge_id(value: str) -> str:
    value = (value or "").strip()
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError("invalid cartridge_id")
    return value


def _validate_plain_filename(value: str) -> str:
    value = (value or "").strip()
    if not _SAFE_FILENAME_RE.fullmatch(value):
        raise ValueError("invalid filename")
    return value


def _mcp_infra_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA")
    if not key and os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}:
        raise RuntimeError("Missing INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA; legacy fallback disabled in production")
    if not key:
        key = get_internal_api_key()
    return {
        "x-api-key": key,
        "x-internal-service": "console",
    }


def _mcp_infra_payload(tool: str, args: dict, actor_user: dict | None = None) -> dict:
    payload = {"tool": tool, "args": args}
    if actor_user is not None:
        payload["security_context"] = build_security_context(actor_user)
    return payload


# ── DB connection ─────────────────────────────────────────────────────────────

class _PooledConnection:
    def __init__(self, db_pool: asyncpg.Pool, conn):
        self._pool = db_pool
        self._conn = conn
        self._released = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self) -> None:
        if not self._released:
            self._released = True
            await self._pool.release(self._conn)


async def pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        _POOL = await asyncpg.create_pool(_DATABASE_URL, min_size=1, max_size=4, command_timeout=10)
    return _POOL


async def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


async def _pg():
    db_pool = await pool()
    return _PooledConnection(db_pool, await db_pool.acquire())


# ── MinIO ─────────────────────────────────────────────────────────────────────

def _minio():
    if _MINIO_SECURE and "amazonaws.com" in _MINIO_ENDPOINT and not _MINIO_SECRET_KEY:
        return _BotoS3ObjectStore(_MINIO_BUCKET)
    from minio import Minio
    return Minio(
        _MINIO_ENDPOINT,
        access_key=_MINIO_ACCESS_KEY,
        secret_key=_MINIO_SECRET_KEY,
        secure=_MINIO_SECURE,
    )


class _BotoS3ObjectStore:
    """Small adapter for AWS S3 with instance-profile credentials.

    The rest of this service uses the MinIO client's tiny object-store surface;
    this adapter keeps production AWS from requiring static access keys.
    """

    def __init__(self, default_bucket: str):
        import boto3
        self._default_bucket = default_bucket
        self._client = boto3.client("s3")

    def bucket_exists(self, bucket: str) -> bool:
        try:
            self._client.head_bucket(Bucket=bucket)
            return True
        except Exception:
            return False

    def make_bucket(self, bucket: str) -> None:
        self._client.create_bucket(Bucket=bucket)

    def put_object(self, bucket: str, key: str, data, length: int, content_type: str | None = None):
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=bucket, Key=key, Body=data.read(length), **extra)

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = True):
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield SimpleNamespace(
                    object_name=obj["Key"],
                    size=obj.get("Size", 0),
                    last_modified=obj.get("LastModified"),
                )

    def get_object(self, bucket: str, key: str):
        obj = self._client.get_object(Bucket=bucket, Key=key)
        return obj["Body"]


def _ensure_bucket(c) -> None:
    if not c.bucket_exists(_MINIO_BUCKET):
        c.make_bucket(_MINIO_BUCKET)


# ── Public API ────────────────────────────────────────────────────────────────

async def get_cartridge(cartridge_id: str) -> dict | None:
    """
    Return full cartridge config assembled from DB tables.
    Returns None if the cartridge is not registered.
    """
    conn = await _pg()
    try:
        row = await conn.fetchrow(
            "SELECT id, name, version, description, pattern, category, bronze_path, "
            "       COALESCE(assistant_hints, '') AS assistant_hints "
            "FROM cartridges WHERE id=$1",
            cartridge_id,
        )
        if not row:
            return None

        connections = await conn.fetch(
            "SELECT conn_id, description, auth_type, poll_strategy, enabled "
            "FROM cartridge_connections WHERE cartridge_id=$1 ORDER BY conn_id",
            cartridge_id,
        )
        # DAGs del cartucho + DAGs compartidos del cartucho 'platform'
        # (file_ingest, entity_scheduler, agent_runner). dag_role discrimina
        # worker (asignable a entidad) vs orchestrator / utility.
        dags = await conn.fetch(
            "SELECT cartridge_id, dag_id, file, description, trigger, params, "
            "       COALESCE(dag_params_example, '{}'::jsonb) AS dag_params_example, "
            "       COALESCE(dag_role, 'worker') AS dag_role "
            "FROM cartridge_dags "
            "WHERE cartridge_id = $1 OR (cartridge_id = 'platform' AND $1 <> 'platform') "
            "ORDER BY (cartridge_id = 'platform'), dag_id",
            cartridge_id,
        )
        entities = await conn.fetch(
            "SELECT entity, display_name, mode, primary_key, dag_id, "
            "       trigger_type, cron_expression, description, enabled, "
            "       COALESCE(dag_params, '{}'::jsonb) AS dag_params "
            "FROM entity_config WHERE cartridge_id=$1 AND enabled=TRUE ORDER BY entity",
            cartridge_id,
        )
        vocab = await conn.fetch(
            "SELECT term, definition, maps_to "
            "FROM semantic_terms WHERE cartridge_id=$1 ORDER BY term",
            cartridge_id,
        )
    finally:
        await conn.close()

    return {
        "id":          row["id"],
        "name":        row["name"],
        "version":     row["version"],
        "description": row["description"] or "",
        "pattern":     row["pattern"],
        "category":    row["category"],
        "bronze_path":     row["bronze_path"] or "",
        "assistant_hints": row["assistant_hints"] or "",
        "connections": [dict(r) for r in connections],
        "dags": [
            {
                **{k: v for k, v in dict(r).items() if k != "dag_params_example"},
                "dag_params_example": _entity_dag_params(r["dag_params_example"]),
            }
            for r in dags
        ],
        "entities": [
            {
                "entity":          r["entity"],
                "display_name":    r["display_name"] or "",
                "mode":            r["mode"],
                "primary_key":     r["primary_key"] or "",
                "dag_id":          r["dag_id"] or "",
                "trigger_type":    r["trigger_type"] or "manual",
                "cron_expression": r["cron_expression"] or "",
                "description":     r["description"] or "",
                "enabled":         r["enabled"],
                "dag_params":      _entity_dag_params(r["dag_params"]),
            }
            for r in entities
        ],
        "semantic_model": {
            "vocabulary": [
                {"term": r["term"], "definition": r["definition"], "maps_to": r["maps_to"]}
                for r in vocab
            ]
        },
    }


async def list_cartridges() -> list[dict]:
    """List all registered cartridges with summary info."""
    conn = await _pg()
    try:
        rows = await conn.fetch(
            "SELECT c.id, c.name, c.version, c.description, c.pattern, c.category, "
            "       COUNT(e.entity) AS entity_count "
            "FROM cartridges c "
            "LEFT JOIN entity_config e ON e.cartridge_id = c.id AND e.enabled = TRUE "
            "GROUP BY c.id, c.name, c.version, c.description, c.pattern, c.category "
            "ORDER BY c.name"
        )
    finally:
        await conn.close()

    return [
        {
            "id":          r["id"],
            "name":        r["name"],
            "version":     r["version"],
            "description": (r["description"] or "").strip(),
            "pattern":     r["pattern"],
            "entities":    r["entity_count"],
            "source":      "database",
        }
        for r in rows
    ]


async def create_cartridge(cartridge_id: str, name: str, description: str = "") -> dict:
    """Register a new cartridge. Raises if it already exists."""
    conn = await _pg()
    try:
        existing = await conn.fetchrow(
            "SELECT id FROM cartridges WHERE id=$1", cartridge_id
        )
        if existing:
            raise ValueError(f"Cartridge '{cartridge_id}' already exists")
        await conn.execute(
            "INSERT INTO cartridges (id, name, description) VALUES ($1, $2, $3)",
            cartridge_id, name, description,
        )
    finally:
        await conn.close()
    return await get_cartridge(cartridge_id)


async def update_cartridge(cartridge_id: str, updates: dict) -> dict:
    """Update top-level cartridge fields."""
    allowed = {"name", "version", "description", "pattern", "category", "bronze_path"}
    fields  = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return await get_cartridge(cartridge_id)

    conn = await _pg()
    try:
        set_clause = ", ".join(f"{k}=${i+2}" for i, k in enumerate(fields))
        values     = list(fields.values())
        await conn.execute(
            f"UPDATE cartridges SET {set_clause}, updated_at=NOW() WHERE id=$1",
            cartridge_id, *values,
        )
    finally:
        await conn.close()
    return await get_cartridge(cartridge_id)


async def upsert_entity(cartridge_id: str, entity: str, **kwargs) -> None:
    """
    Add or update fields in entity_config. `cron_expression`/`trigger_type`
    here are the source of truth per entity — the `entity_scheduler` DAG
    reads them and fires the entity's DAG via Airflow's REST API. The DAG
    source's `schedule_interval` only matters when it isn't None; in that
    case `airflow_create_dag` mirrors it onto every entity that points at
    the DAG.
    """
    import json as _json
    allowed = {"display_name", "mode", "primary_key", "dag_id",
               "trigger_type", "cron_expression", "description", "enabled",
               "dag_params"}
    fields  = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return

    # dag_params is jsonb — accept dict and serialize, or pass string through
    if "dag_params" in fields and not isinstance(fields["dag_params"], str):
        fields["dag_params"] = _json.dumps(fields["dag_params"] or {})

    conn = await _pg()
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM entity_config WHERE cartridge_id=$1 AND entity=$2",
            cartridge_id, entity,
        )
        if exists:
            set_parts = []
            values    = []
            for i, (k, v) in enumerate(fields.items()):
                cast = "::jsonb" if k == "dag_params" else ""
                set_parts.append(f"{k}=${i+3}{cast}")
                values.append(v)
            await conn.execute(
                f"UPDATE entity_config SET {', '.join(set_parts)} "
                f"WHERE cartridge_id=$1 AND entity=$2",
                cartridge_id, entity, *values,
            )
        else:
            await conn.execute(
                """
                INSERT INTO entity_config
                    (cartridge_id, entity, display_name, mode, primary_key,
                     dag_id, trigger_type, cron_expression, description, enabled,
                     dag_params)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
                """,
                cartridge_id, entity,
                fields.get("display_name"),
                fields.get("mode", "full"),
                fields.get("primary_key"),
                fields.get("dag_id"),
                fields.get("trigger_type", "manual"),
                fields.get("cron_expression"),
                fields.get("description", ""),
                fields.get("enabled", True),
                fields.get("dag_params", "{}"),
            )
    finally:
        await conn.close()


async def rename_entity(cartridge_id: str, old_name: str, new_name: str) -> None:
    """
    Rename an entity across all tables that reference it.
    Runs as a single transaction: entity_config, entity_watermarks, pipeline_runs, silver_lineage.
    """
    conn = await _pg()
    try:
        async with conn.transaction():
            # entity_config (PK — must go first)
            await conn.execute(
                "UPDATE entity_config SET entity=$3 "
                "WHERE cartridge_id=$1 AND entity=$2",
                cartridge_id, old_name, new_name,
            )
            # watermarks
            await conn.execute(
                "UPDATE entity_watermarks SET entity_name=$3 "
                "WHERE cartridge_id=$1 AND entity_name=$2",
                cartridge_id, old_name, new_name,
            )
            # pipeline run history
            await conn.execute(
                "UPDATE pipeline_runs SET entity=$3 "
                "WHERE cartridge_id=$1 AND entity=$2",
                cartridge_id, old_name, new_name,
            )
            # silver lineage
            await conn.execute(
                "UPDATE silver_lineage SET source_entity=$3 "
                "WHERE cartridge_id=$1 AND source_entity=$2",
                cartridge_id, old_name, new_name,
            )
    finally:
        await conn.close()


async def delete_entity(cartridge_id: str, entity: str) -> None:
    """
    Delete an entity from entity_config and its watermarks.
    Pipeline run history is kept for auditing.
    """
    conn = await _pg()
    try:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM entity_config WHERE cartridge_id=$1 AND entity=$2",
                cartridge_id, entity,
            )
            await conn.execute(
                "DELETE FROM entity_watermarks WHERE cartridge_id=$1 AND entity_name=$2",
                cartridge_id, entity,
            )
    finally:
        await conn.close()


# ── Supplementary files (MinIO) ────────────────────────────────────────────────

def upload_spec(cartridge_id: str, filename: str, content: str) -> str:
    cartridge_id = _validate_cartridge_id(cartridge_id)
    filename = _validate_plain_filename(filename)
    c   = _minio()
    _ensure_bucket(c)
    key = f"cartridges/{cartridge_id}/specs/{filename}"
    raw = content.encode("utf-8")
    if len(raw) > _MAX_IMPORT_MEMBER_BYTES:
        raise ValueError("spec upload too large")
    c.put_object(_MINIO_BUCKET, key, io.BytesIO(raw), len(raw), content_type="text/plain")
    return key


def upload_code(cartridge_id: str, filename: str, content: str) -> str:
    c   = _minio()
    _ensure_bucket(c)
    key = f"cartridges/{cartridge_id}/{filename}"
    raw = content.encode("utf-8")
    c.put_object(_MINIO_BUCKET, key, io.BytesIO(raw), len(raw), content_type="text/plain")
    return key


def list_specs(cartridge_id: str) -> list[str]:
    c      = _minio()
    prefix = f"cartridges/{cartridge_id}/specs/"
    try:
        objs = c.list_objects(_MINIO_BUCKET, prefix=prefix, recursive=True)
        return [o.object_name.replace(prefix, "") for o in objs]
    except Exception:
        return []


# ── Export / Import ────────────────────────────────────────────────────────────

async def export_cartridge(cartridge_id: str) -> bytes:
    """
    Export cartridge as a ZIP:
      config/seed.sql  — generated from DB (cartridges, connections, dags,
                         entities, semantic_terms, kb_config, mcp_custom_tools)
      dags/*.py        — DAG source code from cartridge_dags.source_code
                         (falls back to /registry/cartridges/{id}/dags then to
                          /opt/airflow/dags for any *.py prefixed with cartridge_id)
      specs/*          — spec files from MinIO
    Self-contained and re-importable on a fresh installation.
    """
    manifest = await get_cartridge(cartridge_id)
    if not manifest:
        raise ValueError(f"Cartridge '{cartridge_id}' not found")

    # ── Pull additional tables (kb_config, mcp_custom_tools, dag sources) ──
    conn = await _pg()
    try:
        kb_rows = await conn.fetch(
            "SELECT kb_id, name, description, sql, pg_table, output_path "
            "FROM kb_config WHERE cartridge_id=$1 AND enabled=TRUE ORDER BY kb_id",
            cartridge_id,
        )
        custom_rows = await conn.fetch(
            "SELECT name, description, tool_type, config "
            "FROM mcp_custom_tools WHERE cartridge_id=$1 AND enabled=TRUE ORDER BY name",
            cartridge_id,
        )
        app_rows = await conn.fetch(
            "SELECT name, title, html, description "
            "FROM analytic_apps WHERE cartridge_id=$1 ORDER BY name",
            cartridge_id,
        )
        dag_rows = await conn.fetch(
            "SELECT dag_id, file, source_code FROM cartridge_dags "
            "WHERE cartridge_id=$1 AND source_code IS NOT NULL ORDER BY dag_id",
            cartridge_id,
        )
        hints_row = await conn.fetchrow(
            "SELECT assistant_hints FROM cartridges WHERE id=$1",
            cartridge_id,
        )
        agent_rows = await conn.fetch(
            "SELECT slug, name, description, instructions, personality, "
            "       allowed_tools, rag_filter, extra, model, max_tokens, "
            "       temperature, is_active "
            "FROM agents WHERE cartridge_id=$1 ORDER BY slug",
            cartridge_id,
        )
    finally:
        await conn.close()

    manifest["knowledge_bits"]  = [dict(r) for r in kb_rows]
    manifest["custom_tools"]    = [dict(r) for r in custom_rows]
    manifest["analytic_apps"]   = [dict(r) for r in app_rows]
    manifest["assistant_hints"] = (hints_row["assistant_hints"] if hints_row else None) or ""
    manifest["agents"]          = [dict(r) for r in agent_rows]

    files: dict[str, bytes] = {}
    files["config/seed.sql"] = _generate_seed_sql(manifest).encode("utf-8")

    # ── Hints (cartridge-specific instructions surfaced to the assistant) ──
    if manifest["assistant_hints"].strip():
        files["hints/assistant.md"] = manifest["assistant_hints"].encode("utf-8")

    # ── Agents: emit one YAML per agent for human inspection. The SQL block
    #    in seed.sql is what actually loads them on import; YAMLs are docs.
    for a in manifest.get("agents") or []:
        slug = a.get("slug", "agent")
        files[f"agents/{slug}.yaml"] = _agent_to_yaml(a).encode("utf-8")

    # ── DAG sources: prefer cartridge_dags.source_code (DB) ───────────────
    seen_dag_files: set[str] = set()
    for r in dag_rows:
        fname = r["file"] or f"{r['dag_id']}.py"
        files[f"dags/{fname}"] = r["source_code"].encode("utf-8")
        seen_dag_files.add(fname)

    # ── Fallback: filesystem (any DAG not already captured from DB) ───────
    import pathlib
    for base in (
        pathlib.Path(f"/registry/cartridges/{cartridge_id}/dags"),
        pathlib.Path("/opt/airflow/dags"),
    ):
        if not base.exists():
            continue
        for fp in base.glob("*.py"):
            if fp.name in seen_dag_files:
                continue
            # only pick up DAGs that look like they belong to this cartridge
            if base.name == "dags" and not fp.name.startswith(f"{cartridge_id}_"):
                continue
            files[f"dags/{fp.name}"] = fp.read_bytes()
            seen_dag_files.add(fp.name)

    # ── Specs and other supplementary files from MinIO ────────────────────
    try:
        c      = _minio()
        prefix = f"cartridges/{cartridge_id}/"
        for obj in c.list_objects(_MINIO_BUCKET, prefix=prefix, recursive=True):
            name = obj.object_name.replace(prefix, "")
            if name.startswith("dags/") or name.endswith("seed.sql"):
                continue
            files[name] = c.get_object(_MINIO_BUCKET, obj.object_name).read()
    except Exception:
        pass

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    buf.seek(0)
    return buf.read()


async def import_cartridge(zip_bytes: bytes, actor_user: dict | None = None) -> dict:
    """
    Import a cartridge from a previously exported ZIP.
      1. Run config/seed.sql against the DB (cartridges + all related tables)
      2. Write dags/*.py to /opt/airflow/dags/ so Airflow picks them up
      3. Upload specs/* and other extras to MinIO under cartridges/{id}/
    """
    import pathlib

    if len(zip_bytes or b"") > _MAX_IMPORT_ZIP_BYTES:
        raise ValueError(f"ZIP too large (max {_MAX_IMPORT_ZIP_BYTES} bytes)")

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        infos = z.infolist()
        _validate_import_zip_members(infos)
        names = [info.filename for info in infos]
        if "config/seed.sql" not in names:
            raise ValueError("ZIP must contain config/seed.sql")

        sql = z.read("config/seed.sql").decode("utf-8")
        _validate_seed_sql(sql)

        m = re.search(r"INSERT INTO cartridges[^V]*VALUES\s*\(\s*'([^']+)'", sql, re.DOTALL)
        cartridge_id = m.group(1) if m else None
        if not cartridge_id:
            raise ValueError("Could not parse cartridge_id from seed.sql")
        cartridge_id = _validate_cartridge_id(cartridge_id)

        allow_dag_import = (
            os.environ.get("ALLOW_CARTRIDGE_DAG_IMPORT", "").strip().lower() in {"1", "true", "yes"}
            or os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}
        )
        dag_names = [name for name in names if name.startswith("dags/") and name.endswith(".py")]
        if dag_names and not allow_dag_import:
            raise ValueError("DAG import is disabled in production")

        # Apply seed and dependent metadata in one DB transaction. External
        # side effects still happen in MCP/MinIO, but a later DAG/import
        # failure cannot leave the cartridge seed half-applied in Postgres.
        dag_files_written: list[str] = []
        spec_files_written: list[str] = []
        extra_names = [name for name in names if name != "config/seed.sql" and not name.startswith("dags/")]
        conn = await _pg()
        try:
            async with conn.transaction():
                await conn.execute(sql)

                # 1b · Cartridge-specific assistant hints (optional file)
                if "hints/assistant.md" in names:
                    hints = z.read("hints/assistant.md").decode("utf-8")
                    await conn.execute(
                        "UPDATE cartridges SET assistant_hints=$2 WHERE id=$1",
                        cartridge_id, hints,
                    )

                # 2 · Supplementary files (specs etc.) → MinIO under cartridges/{id}/
                if extra_names:
                    c = _minio()
                    _ensure_bucket(c)
                    for name in extra_names:
                        raw = z.read(name)
                        key = f"cartridges/{cartridge_id}/{name}"
                        c.put_object(_MINIO_BUCKET, key, io.BytesIO(raw), len(raw))
                        spec_files_written.append(name)

                # 3 · DAG files → Airflow dags directory (via mcp-infra, which has the mount)
                import httpx
                mcp_infra_url = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
                async with httpx.AsyncClient(headers=_mcp_infra_headers(), timeout=30) as client:
                    for name in dag_names:
                        fname  = pathlib.Path(name).name
                        dag_id = fname[:-3]
                        code   = z.read(name).decode("utf-8")
                        r = await client.post(
                            f"{mcp_infra_url}/mcp/invoke",
                            json=_mcp_infra_payload(
                                "airflow_create_dag",
                                {"dag_id": dag_id, "code": code, "cartridge_id": cartridge_id},
                                actor_user,
                            ),
                        )
                        if r.status_code >= 400:
                            raise ValueError(f"DAG import failed for {fname}: {r.text[:300]}")
                        dag_files_written.append(fname)
        finally:
            await conn.close()

    result = await get_cartridge(cartridge_id) or {"imported": True, "id": cartridge_id}
    result["import_summary"] = {
        "dag_files":  dag_files_written,
        "spec_files": spec_files_written,
    }
    return result


def _validate_import_zip_members(members: list[object]) -> None:
    if len(members) > _MAX_IMPORT_MEMBERS:
        raise ValueError(f"ZIP contains too many files (max {_MAX_IMPORT_MEMBERS})")

    total_uncompressed = 0
    seen: set[str] = set()
    for member in members:
        name = getattr(member, "filename", str(member))
        size = int(getattr(member, "file_size", 0) or 0)
        normalized = name.replace("\\", "/")
        if normalized in seen:
            raise ValueError(f"duplicate ZIP member: {name}")
        seen.add(normalized)
        if normalized.startswith("/") or "/../" in f"/{normalized}" or normalized in {"..", "."}:
            raise ValueError(f"unsafe ZIP path: {name}")
        if normalized.endswith("/"):
            continue
        if size > _MAX_IMPORT_MEMBER_BYTES:
            raise ValueError(f"ZIP member too large: {name}")
        total_uncompressed += size
        if total_uncompressed > _MAX_IMPORT_UNCOMPRESSED_BYTES:
            raise ValueError("ZIP uncompressed payload too large")
        allowed = (
            normalized == "config/seed.sql"
            or normalized == "hints/assistant.md"
            or normalized.startswith("dags/") and normalized.endswith(".py")
            or normalized.startswith("specs/")
            or normalized.startswith("agents/")
            or normalized.startswith("apps/")
            or normalized.startswith("datasets/")
            or normalized.startswith("hints/")
        )
        if not allowed:
            raise ValueError(f"unexpected ZIP member: {name}")


def _validate_seed_sql(sql: str) -> None:
    if "\x00" in sql or _FORBIDDEN_SEED_SQL.search(sql or ""):
        raise ValueError("seed.sql contains forbidden SQL")
    cleaned = "\n".join(
        line for line in (sql or "").splitlines()
        if not line.lstrip().startswith("--")
    )
    statements = _split_sql_statements(cleaned)
    for statement in statements:
        normalized = re.sub(r"\s+", " ", statement).strip()
        lower = normalized.lower()
        insert_match = re.match(r"insert\s+into\s+([a-z_][a-z0-9_]*)\b", lower)
        if insert_match:
            table = insert_match.group(1)
            if table not in _ALLOWED_SEED_TABLES:
                raise ValueError(f"seed.sql cannot insert into {table}")
            if re.search(r"\bselect\b", lower):
                raise ValueError("seed.sql INSERT must use literal VALUES, not SELECT")
            continue
        if re.match(r"update\s+cartridges\s+set\s+assistant_hints\s*=", lower):
            if not re.search(r"\bwhere\s+id\s*=", lower):
                raise ValueError("assistant_hints update must target a single cartridge id")
            continue
        if re.match(r"alter\s+table\s+analytic_apps\s+add\s+column\s+if\s+not\s+exists\s+cartridge_id\s+text$", lower):
            continue
        if lower.startswith("on conflict") or lower.startswith("values"):
            # These should be part of an INSERT statement; if our simple
            # splitter sees them alone, fail closed rather than guessing.
            raise ValueError("seed.sql has malformed statement boundary")
        raise ValueError("seed.sql contains unsupported statement")


def _split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    in_single = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        buf.append(ch)
        if ch == "'":
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                buf.append(sql[i + 1])
                i += 2
                continue
            in_single = not in_single
        elif ch == ";" and not in_single:
            statement = "".join(buf[:-1]).strip()
            if statement:
                statements.append(statement)
            buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _entity_dag_params(v) -> dict:
    """Normalize JSON-ish DAG params to a dict for the JSON API."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    import json as _json
    try:
        return _json.loads(v)
    except Exception:
        return {}


# ── Agent YAML emitter (human-readable companion to the SQL block) ───────────

def _agent_to_yaml(a: dict) -> str:
    import json as _json

    def _scalar(v) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v)
        # Quote unless safe bare scalar
        if s == "" or any(c in s for c in ":#\n\"'\\") or s[0] in " -?":
            return _json.dumps(s, ensure_ascii=False)
        return s

    def _multiline(field: str, text: str) -> list[str]:
        if not text:
            return [f"{field}: \"\""]
        out = [f"{field}: |"]
        for line in text.splitlines() or [""]:
            out.append(f"  {line}")
        return out

    lines = [
        f"# Agent: {a.get('name','')}",
        f"slug: {_scalar(a.get('slug',''))}",
        f"name: {_scalar(a.get('name',''))}",
        f"description: {_scalar(a.get('description',''))}",
        f"model: {_scalar(a.get('model','claude-sonnet-4-6'))}",
        f"max_tokens: {int(a.get('max_tokens') or 8192)}",
        f"temperature: {float(a.get('temperature') if a.get('temperature') is not None else 0.4)}",
        f"is_active: {'true' if a.get('is_active', True) else 'false'}",
    ]
    lines += _multiline("instructions", a.get("instructions") or "")
    lines += _multiline("personality",  a.get("personality")  or "")
    at = a.get("allowed_tools") or []
    if isinstance(at, str):
        try:
            at = _json.loads(at)
        except Exception:
            at = []
    lines.append("allowed_tools:")
    for t in at:
        lines.append(f"  - {t}")
    rf = a.get("rag_filter") or {}
    if isinstance(rf, str):
        try:
            rf = _json.loads(rf)
        except Exception:
            rf = {}
    lines.append("rag_filter: " + _json.dumps(rf, ensure_ascii=False))
    ex = a.get("extra") or {}
    if isinstance(ex, str):
        try:
            ex = _json.loads(ex)
        except Exception:
            ex = {}
    lines.append("extra: " + _json.dumps(ex, ensure_ascii=False))
    return "\n".join(lines) + "\n"


# ── SQL generator ─────────────────────────────────────────────────────────────

def _q(v) -> str:
    """Quote a Python value as a SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _generate_seed_sql(manifest: dict) -> str:
    import json as _json

    cid  = manifest["id"]
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"-- MODecissions Cartridge: {manifest['name']} — seed configuration",
        f"-- Generated: {now}",
        f"-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.",
        "",
        "-- ── Cartridge header ──────────────────────────────────────────────────────────",
        "INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)",
        "VALUES (",
        f"    {_q(cid)},",
        f"    {_q(manifest['name'])},",
        f"    {_q(manifest['version'])},",
        f"    {_q(manifest['description'])},",
        f"    {_q(manifest['pattern'])},",
        f"    {_q(manifest['category'])},",
        f"    {_q(manifest['bronze_path'])}",
        ")",
        "ON CONFLICT (id) DO UPDATE",
        "    SET name=EXCLUDED.name, version=EXCLUDED.version,",
        "        description=EXCLUDED.description, updated_at=NOW();",
        "",
    ]

    hints = (manifest.get("assistant_hints") or "").strip()
    if hints:
        lines += [
            "-- ── Assistant hints ─────────────────────────────────────────────────────────",
            f"UPDATE cartridges SET assistant_hints = {_q(hints)} WHERE id = {_q(cid)};",
            "",
        ]

    if manifest.get("connections"):
        lines += [
            "-- ── Connections ──────────────────────────────────────────────────────────────",
            "INSERT INTO cartridge_connections (cartridge_id, conn_id, description, auth_type, poll_strategy)",
            "VALUES",
        ]
        rows = manifest["connections"]
        for i, c in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            lines.append(
                f"    ({_q(cid)}, {_q(c['conn_id'])}, {_q(c.get('description'))}, "
                f"{_q(c.get('auth_type','bearer_token'))}, {_q(c.get('poll_strategy'))}){sep}"
            )
        lines += ["ON CONFLICT (cartridge_id, conn_id) DO NOTHING;", ""]

    if manifest.get("dags"):
        lines += [
            "-- ── DAGs ────────────────────────────────────────────────────────────────────",
            "INSERT INTO cartridge_dags",
            "    (cartridge_id, dag_id, file, description, trigger, params, dag_params_example)",
            "VALUES",
        ]
        rows = manifest["dags"]
        for i, d in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            params = str(d.get("params") or "[]")
            dag_params_example = _json.dumps(d.get("dag_params_example") or {}, ensure_ascii=False)
            lines.append(
                f"    ({_q(cid)}, {_q(d['dag_id'])}, {_q(d.get('file'))}, "
                f"{_q(d.get('description'))}, {_q(d.get('trigger','on-demand'))}, "
                f"'{params.replace(chr(39), chr(39)+chr(39))}', "
                f"{_q(dag_params_example)}::jsonb){sep}"
            )
        lines += [
            "ON CONFLICT (cartridge_id, dag_id) DO UPDATE",
            "    SET file=EXCLUDED.file, description=EXCLUDED.description,",
            "        trigger=EXCLUDED.trigger, params=EXCLUDED.params,",
            "        dag_params_example=EXCLUDED.dag_params_example;",
            "",
        ]

    if manifest.get("entities"):
        lines += [
            "-- ── Entities ────────────────────────────────────────────────────────────────",
            "INSERT INTO entity_config",
            "    (cartridge_id, entity, display_name, mode, primary_key, dag_id,",
            "     trigger_type, cron_expression, description, enabled, dag_params)",
            "VALUES",
        ]
        rows = manifest["entities"]
        for i, e in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            dag_params = _json.dumps(e.get("dag_params") or {}, ensure_ascii=False)
            lines.append(
                f"    ({_q(cid)}, {_q(e['entity'])}, {_q(e.get('display_name',''))}, "
                f"{_q(e.get('mode','full'))}, {_q(e.get('primary_key'))}, "
                f"{_q(e.get('dag_id'))}, {_q(e.get('trigger_type','manual'))}, "
                f"{_q(e.get('cron_expression'))}, {_q(e.get('description',''))}, TRUE, "
                f"{_q(dag_params)}::jsonb){sep}"
            )
        lines += [
            "ON CONFLICT (cartridge_id, entity) DO UPDATE",
            "    SET display_name=EXCLUDED.display_name, mode=EXCLUDED.mode,",
            "        primary_key=EXCLUDED.primary_key, dag_id=EXCLUDED.dag_id,",
            "        trigger_type=EXCLUDED.trigger_type, cron_expression=EXCLUDED.cron_expression,",
            "        description=EXCLUDED.description, enabled=EXCLUDED.enabled,",
            "        dag_params=EXCLUDED.dag_params;",
            "",
        ]

    vocab = (manifest.get("semantic_model") or {}).get("vocabulary") or []
    if vocab:
        lines += [
            "-- ── Semantic vocabulary ─────────────────────────────────────────────────────",
            "INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)",
            "VALUES",
        ]
        for i, v in enumerate(vocab):
            sep = "," if i < len(vocab) - 1 else ""
            lines.append(
                f"    ({_q(cid)}, {_q(v['term'])}, {_q(v.get('definition'))}, "
                f"{_q(v.get('maps_to'))}){sep}"
            )
        lines += ["ON CONFLICT (cartridge_id, term) DO NOTHING;", ""]

    if manifest.get("knowledge_bits"):
        lines += [
            "-- ── Knowledge Bits ──────────────────────────────────────────────────────────",
            "INSERT INTO kb_config (cartridge_id, kb_id, name, description, sql, pg_table, output_path, enabled)",
            "VALUES",
        ]
        rows = manifest["knowledge_bits"]
        for i, k in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            lines.append(
                f"    ({_q(cid)}, {_q(k['kb_id'])}, {_q(k.get('name'))}, "
                f"{_q(k.get('description'))}, {_q(k.get('sql'))}, "
                f"{_q(k.get('pg_table'))}, {_q(k.get('output_path'))}, TRUE){sep}"
            )
        lines += ["ON CONFLICT (cartridge_id, kb_id) DO NOTHING;", ""]

    if manifest.get("custom_tools"):
        import json as _json
        lines += [
            "-- ── Custom MCP tools ────────────────────────────────────────────────────────",
            "INSERT INTO mcp_custom_tools (cartridge_id, name, description, tool_type, config, enabled)",
            "VALUES",
        ]
        rows = manifest["custom_tools"]
        for i, t in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            cfg = t.get("config")
            cfg_str = cfg if isinstance(cfg, str) else _json.dumps(cfg or {})
            lines.append(
                f"    ({_q(cid)}, {_q(t['name'])}, {_q(t.get('description'))}, "
                f"{_q(t['tool_type'])}, {_q(cfg_str)}::jsonb, TRUE){sep}"
            )
        lines += ["ON CONFLICT DO NOTHING;", ""]

    if manifest.get("analytic_apps"):
        lines += [
            "-- ── Analytic Apps (HTML dashboards) ─────────────────────────────────────────",
            "-- ensure column exists before insert (safe on fresh installs)",
            "ALTER TABLE analytic_apps ADD COLUMN IF NOT EXISTS cartridge_id TEXT;",
            "",
            "INSERT INTO analytic_apps (name, title, html, description, cartridge_id, updated_at)",
            "VALUES",
        ]
        rows = manifest["analytic_apps"]
        for i, a in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            lines.append(
                f"    ({_q(a['name'])}, {_q(a.get('title'))}, {_q(a.get('html'))}, "
                f"{_q(a.get('description'))}, {_q(cid)}, NOW()){sep}"
            )
        lines += [
            "ON CONFLICT (name) DO UPDATE",
            "    SET title=EXCLUDED.title, html=EXCLUDED.html,",
            "        description=EXCLUDED.description, cartridge_id=EXCLUDED.cartridge_id,",
            "        updated_at=NOW();",
            "",
        ]

    if manifest.get("agents"):
        import json as _json
        lines += [
            "-- ── Agents (mind=cartridge, body=platform) ─────────────────────────────────",
            "INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,",
            "                    allowed_tools, rag_filter, extra, model, max_tokens, temperature,",
            "                    is_active)",
            "VALUES",
        ]
        rows = manifest["agents"]
        for i, a in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            at = a.get("allowed_tools") or []
            rf = a.get("rag_filter")    or {}
            ex = a.get("extra")         or {}
            at_s = at if isinstance(at, str) else _json.dumps(at)
            rf_s = rf if isinstance(rf, str) else _json.dumps(rf)
            ex_s = ex if isinstance(ex, str) else _json.dumps(ex)
            lines.append(
                f"    ({_q(cid)}, {_q(a['slug'])}, {_q(a['name'])}, "
                f"{_q(a.get('description',''))}, {_q(a.get('instructions',''))}, "
                f"{_q(a.get('personality',''))}, {_q(at_s)}::jsonb, "
                f"{_q(rf_s)}::jsonb, {_q(ex_s)}::jsonb, {_q(a.get('model','claude-sonnet-4-6'))}, "
                f"{int(a.get('max_tokens') or 8192)}, "
                f"{float(a.get('temperature') if a.get('temperature') is not None else 0.4)}, "
                f"{'TRUE' if a.get('is_active', True) else 'FALSE'}){sep}"
            )
        lines += [
            "ON CONFLICT (cartridge_id, slug) DO UPDATE",
            "    SET name=EXCLUDED.name, description=EXCLUDED.description,",
            "        instructions=EXCLUDED.instructions, personality=EXCLUDED.personality,",
            "        allowed_tools=EXCLUDED.allowed_tools, rag_filter=EXCLUDED.rag_filter,",
            "        extra=EXCLUDED.extra, model=EXCLUDED.model,",
            "        max_tokens=EXCLUDED.max_tokens, temperature=EXCLUDED.temperature,",
            "        is_active=EXCLUDED.is_active, updated_at=NOW();",
            "",
        ]

    return "\n".join(lines)
