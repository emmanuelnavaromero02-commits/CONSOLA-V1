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
import textwrap
import zipfile
from datetime import datetime, timezone

import asyncpg

_DATABASE_URL = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+psycopg2://", "postgresql://")
    .replace("postgresql+asyncpg://", "postgresql://")
)
_POOL: asyncpg.Pool | None = None

_MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT",   "minio:9000")
_MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minio")
_MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY")
_MINIO_BUCKET     = os.environ.get("MINIO_BUCKET",     "lakehouse")
_MINIO_SECURE     = os.environ.get("MINIO_SECURE", "false").lower() == "true"


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
        _POOL = await asyncpg.create_pool(_DATABASE_URL, min_size=1, max_size=4)
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
    from minio import Minio
    return Minio(
        _MINIO_ENDPOINT,
        access_key=_MINIO_ACCESS_KEY,
        secret_key=_MINIO_SECRET_KEY,
        secure=_MINIO_SECURE,
    )


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
            "SELECT id, name, version, description, pattern, category, bronze_path "
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
        dags = await conn.fetch(
            "SELECT dag_id, file, description, trigger, params "
            "FROM cartridge_dags WHERE cartridge_id=$1 ORDER BY dag_id",
            cartridge_id,
        )
        entities = await conn.fetch(
            "SELECT entity, display_name, mode, primary_key, dag_id, "
            "       trigger_type, cron_expression, description, enabled "
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
        "bronze_path": row["bronze_path"] or "",
        "connections": [dict(r) for r in connections],
        "dags":        [dict(r) for r in dags],
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
    Add or update fields in entity_config.
    Only the kwargs provided are written; existing columns not in kwargs are preserved.
    """
    allowed = {"display_name", "mode", "primary_key", "dag_id",
               "trigger_type", "cron_expression", "description", "enabled"}
    fields  = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return

    conn = await _pg()
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM entity_config WHERE cartridge_id=$1 AND entity=$2",
            cartridge_id, entity,
        )
        if exists:
            set_parts  = [f"{k}=${i+3}" for i, k in enumerate(fields)]
            set_clause = ", ".join(set_parts)
            values     = list(fields.values())
            await conn.execute(
                f"UPDATE entity_config SET {set_clause} "
                f"WHERE cartridge_id=$1 AND entity=$2",
                cartridge_id, entity, *values,
            )
        else:
            await conn.execute(
                """
                INSERT INTO entity_config
                    (cartridge_id, entity, display_name, mode, primary_key,
                     dag_id, trigger_type, cron_expression, description, enabled)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
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
    c   = _minio()
    _ensure_bucket(c)
    key = f"cartridges/{cartridge_id}/specs/{filename}"
    raw = content.encode("utf-8")
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
    finally:
        await conn.close()

    manifest["knowledge_bits"] = [dict(r) for r in kb_rows]
    manifest["custom_tools"]   = [dict(r) for r in custom_rows]
    manifest["analytic_apps"]  = [dict(r) for r in app_rows]

    files: dict[str, bytes] = {}
    files["config/seed.sql"] = _generate_seed_sql(manifest).encode("utf-8")

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


async def import_cartridge(zip_bytes: bytes) -> dict:
    """
    Import a cartridge from a previously exported ZIP.
      1. Run config/seed.sql against the DB (cartridges + all related tables)
      2. Write dags/*.py to /opt/airflow/dags/ so Airflow picks them up
      3. Upload specs/* and other extras to MinIO under cartridges/{id}/
    """
    import os, pathlib, re

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        if "config/seed.sql" not in names:
            raise ValueError("ZIP must contain config/seed.sql")

        sql = z.read("config/seed.sql").decode("utf-8")

        m = re.search(r"INSERT INTO cartridges[^V]*VALUES\s*\(\s*'([^']+)'", sql, re.DOTALL)
        cartridge_id = m.group(1) if m else None
        if not cartridge_id:
            raise ValueError("Could not parse cartridge_id from seed.sql")

        # 1 · Apply seed
        conn = await _pg()
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

        # 2 · DAG files → Airflow dags directory (via mcp-infra, which has the mount)
        import httpx
        mcp_infra_url = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
        dag_files_written: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for name in names:
                    if not name.startswith("dags/") or not name.endswith(".py"):
                        continue
                    fname  = pathlib.Path(name).name
                    dag_id = fname[:-3]
                    code   = z.read(name).decode("utf-8")
                    r = await client.post(
                        f"{mcp_infra_url}/mcp/invoke",
                        json={"tool": "airflow_create_dag",
                              "args": {"dag_id": dag_id, "code": code,
                                       "cartridge_id": cartridge_id}},
                    )
                    if r.status_code < 400:
                        dag_files_written.append(fname)
        except Exception:
            pass

        # 3 · Supplementary files (specs etc.) → MinIO under cartridges/{id}/
        spec_files_written: list[str] = []
        try:
            c = _minio()
            _ensure_bucket(c)
            for name in names:
                if name == "config/seed.sql" or name.startswith("dags/"):
                    continue
                raw = z.read(name)
                key = f"cartridges/{cartridge_id}/{name}"
                c.put_object(_MINIO_BUCKET, key, io.BytesIO(raw), len(raw))
                spec_files_written.append(name)
        except Exception:
            pass

    result = await get_cartridge(cartridge_id) or {"imported": True, "id": cartridge_id}
    result["import_summary"] = {
        "dag_files":  dag_files_written,
        "spec_files": spec_files_written,
    }
    return result


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
            "INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)",
            "VALUES",
        ]
        rows = manifest["dags"]
        for i, d in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            params = str(d.get("params") or "[]")
            lines.append(
                f"    ({_q(cid)}, {_q(d['dag_id'])}, {_q(d.get('file'))}, "
                f"{_q(d.get('description'))}, {_q(d.get('trigger','on-demand'))}, "
                f"'{params.replace(chr(39), chr(39)+chr(39))}'){sep}"
            )
        lines += ["ON CONFLICT (cartridge_id, dag_id) DO NOTHING;", ""]

    if manifest.get("entities"):
        lines += [
            "-- ── Entities ────────────────────────────────────────────────────────────────",
            "INSERT INTO entity_config",
            "    (cartridge_id, entity, display_name, mode, primary_key, dag_id,",
            "     trigger_type, cron_expression, description, enabled)",
            "VALUES",
        ]
        rows = manifest["entities"]
        for i, e in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            lines.append(
                f"    ({_q(cid)}, {_q(e['entity'])}, {_q(e.get('display_name',''))}, "
                f"{_q(e.get('mode','full'))}, {_q(e.get('primary_key'))}, "
                f"{_q(e.get('dag_id'))}, {_q(e.get('trigger_type','manual'))}, "
                f"{_q(e.get('cron_expression'))}, {_q(e.get('description',''))}, TRUE){sep}"
            )
        lines += ["ON CONFLICT (cartridge_id, entity) DO NOTHING;", ""]

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

    return "\n".join(lines)
