"""
MCP Infrastructure Server
=========================
Single FastAPI service that exposes tools for Airflow, MinIO, PostgreSQL and Superset
via the standard MCP contract:

  GET  /mcp/tools          → { tools: [{name, description, input_schema}] }
  POST /mcp/invoke         → { tool, args } → { result } | { error }
  GET  /health             → { status }   (public; tool count intentionally redacted)
"""
from __future__ import annotations
import os
import re
import secrets

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel

from app import registry
from app.security import get_internal_api_key
# Sprint v1.41.1 — structured JSON logs so request_id correlates here too.
from app.logging_config import setup_logging  # noqa: E402

setup_logging(service_name="mcp-infra")

# ── Import tool modules so decorators register themselves ──────────────────────
import app.tools.airflow     # noqa: F401
import app.tools.admin_request  # noqa: F401
import app.tools.agents      # noqa: F401
import app.tools.cartridges  # noqa: F401
import app.tools.minio       # noqa: F401
import app.tools.pipeline    # noqa: F401
import app.tools.postgres    # noqa: F401
import app.tools.rag         # noqa: F401
import app.tools.superset    # noqa: F401
import app.tools.vault       # noqa: F401

# ── App ────────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager
from app.rag.store import get_pool as _rag_get_pool


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        await _rag_get_pool()
    except Exception:
        pass  # RAG is optional — server starts even if pgvector is not ready
    yield

INTERNAL_API_KEY = get_internal_api_key()  # legacy fallback, still accepted
_RAG_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")

# Sprint v1.12: mcp-infra is called by console, workspace and airflow. Each
# pair has its own INTERNAL_API_KEY_*_TO_MCP_INFRA secret. The legacy shared
# key keeps working during the migration window and is dropped in a follow-up.
_ALLOWED_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "console":   "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
    "workspace": "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
    "airflow":   "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
    # The old whitelist allowed these; we keep them via legacy key only.
    "refinement": None,
    "mcp-infra":  None,
}


def verify_api_key(x_api_key: str = Header(None), x_internal_service: str = Header(None)):
    # v1.42.1 auditor fix: distinguish "no auth presented" (401) from
    # "auth presented but invalid" (403). Matches the cartridge pattern
    # in app/api/deps.py and the wider HTTP convention.
    if not x_internal_service or not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )
    if x_internal_service not in _ALLOWED_SERVICES_TO_KEY_ENV:
        raise HTTPException(
            status_code=403,
            detail="Invalid internal service origin",
        )

    accepted: list[str] = []
    pair_key_env = _ALLOWED_SERVICES_TO_KEY_ENV.get(x_internal_service)
    if pair_key_env:
        pair_key = os.environ.get(pair_key_env)
        if pair_key:
            accepted.append(pair_key)
    if INTERNAL_API_KEY:
        accepted.append(INTERNAL_API_KEY)

    if not any(secrets.compare_digest(x_api_key, k) for k in accepted if k):
        raise HTTPException(status_code=403, detail="Forbidden")


def _safe_rag_segment(value: str, label: str) -> str:
    value = (value or "").strip()
    if not _RAG_SEGMENT_RE.fullmatch(value):
        raise HTTPException(400, f"invalid {label}")
    return value

app = FastAPI(
    title="ΩMEGA by EPIUSE MCP Infra",
    description="MCP tools for Airflow, MinIO, PostgreSQL, Superset, RAG",
    version="1.1.0",
    lifespan=_lifespan,
)

# Sprint v1.41.1 — correlation IDs.
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)


class InvokeRequest(BaseModel):
    tool: str
    args: dict = {}


# ── MCP endpoints ──────────────────────────────────────────────────────────────

@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
def get_tools():
    return {"tools": registry.list_tools()}


@app.post("/mcp/invoke", dependencies=[Depends(verify_api_key)])
async def invoke_tool(req: InvokeRequest):
    try:
        result = await registry.invoke(req.tool, req.args)
        return {"result": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        # Return structured error so the LLM can reason about it
        return {"error": str(exc), "tool": req.tool}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    Same contract as the legacy /health below — minimal payload, no
    auth — but uses the /healthz path the rest of the platform pins
    its healthchecks on."""
    return {"ok": True, "service": "mcp-infra"}


@app.get("/health")
def health():
    # Public endpoint used by Docker healthchecks and load balancers — keep
    # the response minimal so unauthenticated callers can't fingerprint how
    # many tools / integrations this instance has loaded. Internal callers
    # that need that detail use GET /mcp/tools behind x-api-key.
    return {"status": "ok"}


# ── RAG REST endpoints (used by Studio UI) ─────────────────────────────────────

import io as _io
from app.rag.store import list_sources as _rag_list_sources, delete_source as _rag_delete_source
from app.tools.rag import _do_ingest as _rag_do_ingest, _do_search as _rag_do_search


@app.get("/rag/sources", dependencies=[Depends(verify_api_key)])
async def rag_rest_list_sources(kinds: str | None = None):
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    return {"sources": await _rag_list_sources(kinds=kind_list)}


@app.delete("/rag/sources/{source_id}", dependencies=[Depends(verify_api_key)])
async def rag_rest_delete_source(source_id: int):
    ok = await _rag_delete_source(source_id)
    if not ok:
        raise HTTPException(404, "Source not found")
    return {"deleted": True, "source_id": source_id}


@app.post("/rag/search", dependencies=[Depends(verify_api_key)])
async def rag_rest_search(body: dict):
    return {"results": await _rag_do_search(
        query=body["query"],
        top_k=body.get("top_k", 5),
        source_ids=body.get("source_ids"),
        kinds=body.get("kinds"),
    )}


@app.post("/rag/ingest", dependencies=[Depends(verify_api_key)])
async def rag_rest_ingest(body: dict):
    content = body.get("content", "")
    if body.get("mime_type") == "application/pdf":
        import base64
        from pypdf import PdfReader
        pdf_bytes = base64.b64decode(body["content"])
        reader = PdfReader(_io.BytesIO(pdf_bytes))
        content = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if not content:
            raise HTTPException(400, "Could not extract text from PDF")
    return await _rag_do_ingest(
        name=body["name"],
        content=content,
        description=body.get("description", ""),
        mime_type=body.get("mime_type", "text/plain"),
        kind=body.get("kind", "document"),
    )


# ── Re-index a single raw entity or dataset into the RAG ────────────────────

@app.post("/rag/reindex", dependencies=[Depends(verify_api_key)])
async def rag_rest_reindex(body: dict):
    """
    Re-read the schema (and SQL for datasets) live, then re-ingest into the
    RAG so the assistant sees current columns. Idempotent — the source name
    gets ON CONFLICT replaced.

    Body:
      {kind: "raw" | "dataset", name: "...", cartridge: "replicon" (raw only)}
    """
    kind = (body.get("kind") or "").lower()
    raw_name = (body.get("name") or "").strip()
    if kind not in ("raw", "dataset") or not raw_name:
        raise HTTPException(400, "kind in {raw,dataset} and name are required")
    name = _safe_rag_segment(raw_name, "name")

    if kind == "raw":
        cartridge = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
        content = _build_raw_doc(cartridge, name)
        source = f"raw:{cartridge}:{name}"
        desc = f"raw bronze entity {name} ({cartridge})"
    else:
        content, desc = _build_dataset_doc(name)
        source = f"dataset:{name}"

    r = await _rag_do_ingest(
        name=source,
        content=content,
        description=desc,
        mime_type="text/plain",
        kind="schema",
    )
    semantic_result = None
    if kind == "dataset":
        cartridge_for_semantic = (
            _safe_rag_segment(body.get("cartridge"), "cartridge")
            if body.get("cartridge")
            else _cartridge_of(name)
        )
    else:
        cartridge_for_semantic = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
    if cartridge_for_semantic:
        try:
            semantic_result = await _rebuild_semantic_doc(cartridge_for_semantic)
        except Exception as exc:                              # noqa: BLE001
            semantic_result = {"rebuilt": False, "error": str(exc)}
    return {
        "reindexed": True,
        "source": source,
        **(r if isinstance(r, dict) else {}),
        "semantic": semantic_result,
    }


@app.post("/rag/rebuild-semantic", dependencies=[Depends(verify_api_key)])
async def rag_rest_rebuild_semantic(body: dict):
    cartridge = _safe_rag_segment(body.get("cartridge") or "replicon", "cartridge")
    return await _rebuild_semantic_doc(cartridge)


def _cartridge_of(dataset_name: str) -> str | None:
    import psycopg2
    from app.config import settings as s
    try:
        conn = psycopg2.connect(
            host=s.pg_host,
            port=s.pg_port,
            dbname=s.pg_db,
            user=s.pg_user,
            password=s.pg_password,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT cartridge FROM datasets WHERE name = %s", (dataset_name,))
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _duckdb_s3_settings(con, endpoint: str, region: str) -> None:
    import os

    url_style = "vhost" if "amazonaws.com" in endpoint else "path"
    secure = (os.environ.get("MINIO_SECURE", "false").lower() in {"1", "true", "yes", "on"})
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"""
        SET s3_endpoint='{endpoint}';
        SET s3_access_key_id='{os.environ.get('MINIO_ACCESS_KEY','')}';
        SET s3_secret_access_key='{os.environ.get('MINIO_SECRET_KEY','')}';
        SET s3_url_style='{url_style}';
        SET s3_use_ssl={'true' if secure else 'false'};
        SET s3_region='{region}';
    """)


async def _rebuild_semantic_doc(cartridge: str) -> dict:
    """Rebuild `_semantic_<cartridge>` from live schemas + data_catalog."""
    import os
    import duckdb
    import psycopg2
    from datetime import datetime, timezone
    from app.config import settings as s

    bucket = os.environ.get("MINIO_BUCKET", "")
    cartridge = _safe_rag_segment(cartridge, "cartridge")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    region = os.environ.get("AWS_REGION", "us-east-1")

    conn = psycopg2.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, layer FROM datasets WHERE cartridge = %s ORDER BY layer, name",
            (cartridge,),
        )
        datasets = cur.fetchall()
        cur.execute("""
            SELECT dataset, column_name, COALESCE(description,''), COALESCE(tags, '{}')
              FROM data_catalog
             WHERE cartridge = %s
        """, (cartridge,))
        desc_rows = cur.fetchall()
    conn.close()
    desc_by = {
        (r[0], r[1]): {"description": r[2], "tags": r[3] or []}
        for r in desc_rows
    }

    con = duckdb.connect()
    _duckdb_s3_settings(con, endpoint, region)
    pggold_dsn = (
        f"host={s.pg_gold_host} port={s.pg_gold_port} dbname={s.pg_gold_db} "
        f"user={s.pg_gold_user or s.pg_user} password={s.pg_gold_password or s.pg_password}"
    )
    try:
        con.execute(f"INSTALL postgres; LOAD postgres; ATTACH '{pggold_dsn}' AS pggold (TYPE postgres);")
    except Exception:
        pass

    out: list[str] = [
        f"# Modelo Semántico — Cartucho `{cartridge}`\n",
        "_Generado automáticamente desde `data_catalog` + schemas reales._\n",
        f"_Última recompilación: {datetime.now(timezone.utc).isoformat()[:19]}Z_\n\n",
    ]

    def fmt_col(dataset: str, col: str, ty: str) -> str:
        d = desc_by.get((dataset, col), {})
        text = d.get("description") or ""
        tags = d.get("tags") or []
        tail = f" [tags: {', '.join(tags)}]" if tags else ""
        return f"- `{col}` ({ty}){': ' + text if text else ''}{tail}\n"

    try:
        raw_rows = con.execute(f"""
            SELECT DISTINCT regexp_extract(file, 'raw/{cartridge}/([^/]+)/', 1) AS entity
            FROM glob('s3://{bucket}/raw/{cartridge}/*/**/*.parquet')
            WHERE regexp_extract(file, 'raw/{cartridge}/([^/]+)/', 1) != ''
            ORDER BY entity
        """).fetchall()
        raws = [r[0] for r in raw_rows]
    except Exception:
        raws = []
    if raws:
        out.append(f"## Bronze · raw — {len(raws)} entidades\n\n")
        for ent in raws:
            out.append(f"### {ent}\n")
            try:
                fields = con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('s3://{bucket}/raw/{cartridge}/{ent}/**/*.parquet',"
                    f" hive_partitioning=true, union_by_name=true) LIMIT 0"
                ).fetchall()
            except Exception as exc:                          # noqa: BLE001
                fields = []
                out.append(f"_(schema unavailable: {exc})_\n")
            for col, ty, *_ in fields:
                out.append(fmt_col(ent, col, ty))
            out.append("\n")

    by_layer: dict[str, list[str]] = {}
    for name, layer in datasets:
        by_layer.setdefault(layer or "silver", []).append(name)
    for layer in ("silver", "gold"):
        names = by_layer.get(layer, [])
        if not names:
            continue
        out.append(f"## {layer.capitalize()} — {len(names)} datasets\n\n")
        for name in names:
            out.append(f"### {name}\n")
            try:
                if layer == "gold":
                    fields = con.execute(f'DESCRIBE pggold."gold_{name}"').fetchall()
                else:
                    parquet = f"s3://{bucket}/silver/{cartridge}/{name}/data.parquet"
                    fields = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}') LIMIT 0").fetchall()
            except Exception as exc:                          # noqa: BLE001
                fields = []
                out.append(f"_(schema unavailable: {exc})_\n")
            for col, ty, *_ in fields:
                out.append(fmt_col(name, col, ty))
            out.append("\n")

    content = "".join(out)
    source_name = f"_semantic_{cartridge}"
    ingest = await _rag_do_ingest(
        name=source_name,
        content=content,
        description=f"Modelo semántico consolidado del cartucho {cartridge}",
        mime_type="text/plain",
        kind="document",
    )
    return {
        "rebuilt": True,
        "source": source_name,
        "raws": len(raws),
        "datasets": len(datasets),
        "chars": len(content),
        **(ingest if isinstance(ingest, dict) else {}),
    }


def _build_raw_doc(cartridge: str, entity: str) -> str:
    import os
    import duckdb

    bucket = os.environ.get("MINIO_BUCKET", "")
    cartridge = _safe_rag_segment(cartridge, "cartridge")
    entity = _safe_rag_segment(entity, "entity")
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    con = duckdb.connect()
    _duckdb_s3_settings(con, endpoint, region)
    path = f"s3://{bucket}/raw/{cartridge}/{entity}/**/*.parquet"
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=true, union_by_name=true) LIMIT 0"
        ).fetchall()
        fields = "\n".join(f"- `{r[0]}` : {r[1]}" for r in rows)
    except Exception as exc:                                  # noqa: BLE001
        fields = f"(schema unavailable: {exc})"
    return (
        f"# Raw entity: {entity}\nLayer: bronze (raw)\nCartridge: {cartridge}\n\n"
        f"## Storage\n`{path}`\n\n## Schema\n{fields}\n"
    )


def _build_dataset_doc(name: str) -> tuple[str, str]:
    import psycopg2
    from app.config import settings as s

    name = _safe_rag_segment(name, "dataset")
    conn = psycopg2.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT layer, cartridge, COALESCE(sql_def,''), COALESCE(description,'') "
                "FROM datasets WHERE name = %s",
                (name,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, f"dataset '{name}' not found")
    layer, cartridge, sql_def, description = row
    body = (
        f"# Dataset: {name}\nLayer: {layer}\nCartridge: {cartridge}\n\n"
        f"## Description\n{description or '(none)'}\n\n"
        f"## SQL\n```sql\n{sql_def}\n```\n"
    )
    return body, f"{layer} dataset {name}"
