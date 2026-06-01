"""
DuckDB Engine — Refinement service (transversal, multi-cartridge).

Silver layer:
  - Limpieza 1:1 desde raw → Parquet snapshot inmutable en MinIO
    s3://lakehouse/silver/{cartridge}/{name}/_snapshots/{timestamp}.parquet

Gold layer:
  - Modelado de negocio, dimensiones, hechos, agregaciones y KPIs → tabla Postgres en el GOLD DB: gold_{name}

Postgres aliases dentro de DuckDB:
  - pgdb   → service DB (lineage, datasets, catalog, etc.)
  - pggold → analytical DB (gold_*)

Lineage:
  - Cada materialización escribe una fila en silver_lineage (en el service DB)
"""
from __future__ import annotations

import os
import io
import json
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

import duckdb
import psycopg2
import sqlglot
from sqlglot import exp as _sqlglot_exp

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
SAFE_S3_BRONZE_TAIL_RE = re.compile(r"^[a-zA-Z0-9_./=*-]+$")


def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _sql_quote(value: str) -> str:
    """Single-quote a string literal for direct interpolation into a SQL
    statement that DuckDB will execute. We use this for ATTACH/SET arguments
    that DuckDB does not accept as parameter placeholders. Doubling embedded
    single quotes is the standard SQL escape and is what DuckDB expects."""
    return "'" + (value or "").replace("'", "''") + "'"


def _escape_sql_literal_inner(value: str) -> str:
    """v1.43.1 (Claude B4): same single-quote-doubling escape as
    ``_sql_quote`` but WITHOUT the wrapping quotes. Use when the template
    already provides the outer ``'…'`` (e.g. ``WHERE x = '{placeholder}'``)
    and we just need to neutralise any quote characters embedded in the
    value so an attacker can't break out of the literal."""
    return (value or "").replace("'", "''")


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments while preserving quoted string literals.

    Cartridge dataset SQL commonly carries leading metadata comments. The
    safety gate should inspect the executable SQL, not reject trusted dataset
    definitions because they have a header.
    """
    text = sql or ""
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote:
            out.append(ch)
            if ch == quote:
                if nxt == quote:
                    out.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            out.append("\n")
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(text))
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def validate_safe_identifier(value: str, label: str = "identifier") -> None:
    if not SAFE_IDENTIFIER_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label} name")


class DuckDBEngine:
    def __init__(self):
        self.minio_endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        self.minio_access   = os.environ.get("MINIO_ACCESS_KEY", "minio")
        self.minio_secret   = os.environ.get("MINIO_SECRET_KEY")
        self.minio_bucket   = os.environ.get("MINIO_BUCKET", "lakehouse")
        self.minio_secure   = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        self.pg_url         = os.environ.get("DATABASE_URL", "")
        # Analytical (gold) DB. Falls back to service DB if unset, so
        # local/dev environments without postgres_gold keep working.
        self.pg_gold_url    = os.environ.get("GOLD_DATABASE_URL", "") or self.pg_url
        self._con: duckdb.DuckDBPyConnection | None = None
        self._duckdb_lock = threading.RLock()

    # ── DuckDB connection ─────────────────────────────────────────────────────

    def _conn(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect()
            self._con.execute("INSTALL httpfs; LOAD httpfs;")
            self._con.execute("INSTALL postgres; LOAD postgres;")
            # SET ... requires the literal inline, so we escape single
            # quotes ourselves. Without escaping, a MinIO secret containing
            # a quote would terminate the literal early and the rest of
            # the credential would be parsed as SQL.
            self._con.execute(
                f"SET s3_endpoint={_sql_quote(self.minio_endpoint or '')};"
                f"SET s3_access_key_id={_sql_quote(self.minio_access or '')};"
                f"SET s3_secret_access_key={_sql_quote(self.minio_secret or '')};"
                "SET s3_url_style='path';"
                f"SET s3_use_ssl={'true' if self.minio_secure else 'false'};"
            )
        return self._con

    def setup(self):
        with self._duckdb_lock:
            con = self._conn()
            # Attach both Postgres instances at startup so read paths (preview_sql,
            # query_dataset) can reference pgdb.<table> / pggold.<table> without
            # depending on a prior materialization call.
            try:
                self._pg_attach(con)
            except Exception:
                pass
            try:
                self._pg_gold_attach(con)
            except Exception:
                pass

    # ── Postgres connections ──────────────────────────────────────────────────

    def _pg_conn(self):
        return psycopg2.connect(_normalize_postgres_dsn(self.pg_url))

    def _pg_gold_conn(self):
        return psycopg2.connect(_normalize_postgres_dsn(self.pg_gold_url))

    def _pg_attach(self, con: duckdb.DuckDBPyConnection) -> str:
        """Attach service Postgres (pgdb) and return alias."""
        dsn = _normalize_postgres_dsn(self.pg_url)
        try:
            con.execute(f"ATTACH {_sql_quote(dsn)} AS pgdb (TYPE postgres);")
        except Exception:
            pass  # already attached
        return "pgdb"

    def _pg_gold_attach(self, con: duckdb.DuckDBPyConnection) -> str:
        """Attach analytical Postgres (pggold) and return alias."""
        dsn = _normalize_postgres_dsn(self.pg_gold_url)
        try:
            con.execute(f"ATTACH {_sql_quote(dsn)} AS pggold (TYPE postgres);")
        except Exception:
            pass  # already attached
        return "pggold"

    # ── Path helpers ──────────────────────────────────────────────────────────

    def _validate_bronze_source(self, source: str) -> str:
        source = (source or "").strip()
        if not source:
            raise ValueError("Invalid bronze source")

        blocked = ("'", '"', ";", "..", "file://", "\\", " ")
        if any(token in source for token in blocked):
            raise ValueError("Invalid bronze source")
        if source.startswith("/") or source.startswith(("http://", "https://")):
            raise ValueError("Invalid bronze source")

        if source.startswith("s3://"):
            expected_prefix = f"s3://{self.minio_bucket}/raw/"
            if not source.startswith(expected_prefix):
                raise ValueError("Invalid bronze source")
            relative = source[len(f"s3://{self.minio_bucket}/"):]
            parts = relative.split("/")
            if len(parts) < 3 or parts[0] != "raw":
                raise ValueError("Invalid bronze source")
            validate_safe_identifier(parts[1], "cartridge")
            validate_safe_identifier(parts[2], "entity")
            if len(parts) > 3:
                tail = "/".join(parts[3:])
                if not tail or not SAFE_S3_BRONZE_TAIL_RE.fullmatch(tail):
                    raise ValueError("Invalid bronze source")
            return source

        parts = source.split("/")
        if len(parts) != 3 or parts[0] != "raw":
            raise ValueError("Invalid bronze source")
        validate_safe_identifier(parts[1], "cartridge")
        validate_safe_identifier(parts[2], "entity")
        return source

    def _safe_scope_segment(self, value: str | None, label: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError(f"Invalid {label}")
        return value

    def _scope_values(self, user_context: dict | None) -> tuple[str, str]:
        if not user_context:
            return "", ""
        tenant = self._safe_scope_segment(user_context.get("tenant_id"), "tenant_id")
        workspace = self._safe_scope_segment(user_context.get("workspace_id"), "workspace_id")
        return tenant, workspace

    def _bronze_path(self, source: str, user_context: dict | None = None) -> str:
        source = self._validate_bronze_source(source)
        if source.startswith("s3://"):
            return source
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return (
                f"s3://{self.minio_bucket}/{source}/"
                f"tenant_id={tenant}/workspace_id={workspace}/**/*.parquet"
            )
        # Keep unscoped reads on the legacy/global layout only. A recursive glob
        # over both `load_date=...` and `tenant_id=.../workspace_id=...` layouts
        # makes DuckDB's hive partition reader fail because the partition keys
        # differ across files.
        return f"s3://{self.minio_bucket}/{source}/load_date=*/batch_id=*/*.parquet"

    def _bronze_read(self, source: str, user_context: dict | None = None) -> str:
        path = self._bronze_path(source, user_context)
        return f"read_parquet('{path}', hive_partitioning=true, union_by_name=true)"

    def _silver_path(self, cartridge: str, name: str, user_context: dict | None = None) -> str:
        """Legacy Silver path used to recognize older cartridge SQL.

        New materializations write immutable `_snapshots/*.parquet` objects and
        `_scope_storage_sql` redirects this path to the latest lineage URI.
        """
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return (
                f"s3://{self.minio_bucket}/silver/{cartridge}/{name}/"
                f"tenant_id={tenant}/workspace_id={workspace}/data.parquet"
            )
        return f"s3://{self.minio_bucket}/silver/{cartridge}/{name}/data.parquet"

    def _gold_path(self, cartridge: str, name: str, user_context: dict | None = None) -> str:
        """Legacy Gold parquet path used for backwards-compatible SQL rewrites."""
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return (
                f"s3://{self.minio_bucket}/gold/{cartridge}/{name}/"
                f"tenant_id={tenant}/workspace_id={workspace}/data.parquet"
            )
        return f"s3://{self.minio_bucket}/gold/{cartridge}/{name}/data.parquet"

    def _snapshot_prefix(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict | None = None,
    ) -> str:
        if layer not in ("silver", "gold"):
            raise ValueError("Invalid dataset layer")
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        tenant, workspace = self._scope_values(user_context)
        base = f"{layer}/{cartridge}/{name}"
        if tenant and workspace:
            base = f"{base}/tenant_id={tenant}/workspace_id={workspace}"
        return f"{base}/_snapshots/"

    def _snapshot_path(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict | None = None,
    ) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        suffix = uuid.uuid4().hex[:12]
        return f"s3://{self.minio_bucket}/{self._snapshot_prefix(layer, cartridge, name, user_context)}{stamp}-{suffix}.parquet"

    def _latest_materialized_uri(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict | None = None,
    ) -> str | None:
        if layer not in ("silver", "gold"):
            return None
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        clauses = [
            "silver_name = %s",
            "cartridge_id = %s",
            "layer = %s",
            "storage_uri IS NOT NULL",
            "storage_uri <> ''",
            "storage_uri LIKE %s",
        ]
        params: list[str] = [name, cartridge, layer, "s3://%"]
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            clauses.append("storage_uri LIKE %s")
            params.append(f"%/tenant_id={tenant}/workspace_id={workspace}/%")
        try:
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT storage_uri
                    FROM silver_lineage
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
            conn.close()
            return str(row[0]) if row and row[0] else None
        except Exception:
            return None

    def _scope_storage_sql(self, sql: str, sources: list[str], user_context: dict | None) -> str:
        """Rewrite known raw/silver/gold S3 references to the tenant/workspace
        partition when the caller is scoped. This keeps dataset SQL portable:
        definitions still reference raw/<cartridge>/<entity>, while execution
        uses only the current tenant/workspace physical path.

        Materialized silver/gold datasets are immutable snapshots. Legacy
        cartridge SQL still references .../data.parquet; when lineage has a
        newer snapshot we redirect that reference before execution.
        """
        tenant, workspace = self._scope_values(user_context)

        out = sql
        for source in sources or []:
            src = str(source or "").strip()
            if src.startswith("raw/"):
                try:
                    scoped = self._bronze_path(src, user_context)
                    legacy = f"s3://{self.minio_bucket}/{src}/**/*.parquet"
                    out = out.replace(legacy, scoped)
                    out = out.replace(legacy.replace("**/*.parquet", "*.parquet"), scoped)
                except Exception:
                    continue
            elif tenant and workspace and src.startswith(("silver/", "gold/")):
                parts = src.split("/")
                if len(parts) >= 3:
                    layer, cartridge, name = parts[0], parts[1], parts[2]
                    legacy = f"s3://{self.minio_bucket}/{layer}/{cartridge}/{name}/data.parquet"
                    latest = self._latest_materialized_uri(layer, cartridge, name, user_context)
                    scoped = latest or (
                        self._silver_path(cartridge, name, user_context)
                        if layer == "silver"
                        else self._gold_path(cartridge, name, user_context)
                    )
                    out = out.replace(legacy, scoped)
            elif src.startswith(("silver/", "gold/")):
                parts = src.split("/")
                if len(parts) >= 3:
                    layer, cartridge, name = parts[0], parts[1], parts[2]
                    legacy = f"s3://{self.minio_bucket}/{layer}/{cartridge}/{name}/data.parquet"
                    latest = self._latest_materialized_uri(layer, cartridge, name, user_context)
                    if latest:
                        out = out.replace(legacy, latest)
        return out

    def _s3_object_key(self, uri: str) -> str | None:
        prefix = f"s3://{self.minio_bucket}/"
        if not str(uri or "").startswith(prefix):
            return None
        key = str(uri)[len(prefix):].strip("/")
        return key or None

    def _delete_s3_prefix(self, uri: str) -> None:
        """Delete an existing MinIO/S3 object or prefix before DuckDB rewrites it.

        DuckDB 1.2.2 can raise a low-level UnicodeDecodeError when `COPY TO`
        overwrites an existing MinIO object. Removing the object/prefix first
        gives materialization idempotent semantics and keeps repeated refreshes
        from failing under stress.
        """
        key = self._s3_object_key(uri)
        if not key:
            return
        from minio import Minio

        client = Minio(
            self.minio_endpoint,
            access_key=self.minio_access,
            secret_key=self.minio_secret,
            secure=self.minio_secure,
        )
        self._delete_s3_key_with_client(client, key)

    def _delete_s3_key_with_client(self, client, key: str) -> None:
        for obj in client.list_objects(self.minio_bucket, prefix=key, recursive=True):
            client.remove_object(self.minio_bucket, obj.object_name)
        # In the common case `data.parquet` is a single object, not a prefix.
        # Removing a missing key is harmless on MinIO/S3-compatible backends.
        try:
            client.remove_object(self.minio_bucket, key)
        except Exception:
            pass

    def _upload_local_parquet(self, local_path: str, parquet_path: str) -> str:
        key = self._s3_object_key(parquet_path)
        if not key:
            return parquet_path
        from minio import Minio

        client = Minio(
            self.minio_endpoint,
            access_key=self.minio_access,
            secret_key=self.minio_secret,
            secure=self.minio_secure,
        )
        last_error: Exception | None = None
        for attempt in range(1, 9):
            attempt_key = key
            if attempt > 1:
                if key.endswith(".parquet"):
                    attempt_key = f"{key[:-8]}.retry{attempt}-{uuid.uuid4().hex[:8]}.parquet"
                else:
                    attempt_key = f"{key}.retry{attempt}-{uuid.uuid4().hex[:8]}"
            try:
                client.fput_object(
                    self.minio_bucket,
                    attempt_key,
                    local_path,
                    content_type="application/octet-stream",
                )
                return f"s3://{self.minio_bucket}/{attempt_key}"
            except Exception as exc:
                last_error = exc
                if attempt == 8:
                    break
                time.sleep(min(0.5 * attempt, 3.0))
        if last_error:
            raise last_error
        return parquet_path

    def _copy_to_parquet(self, con: duckdb.DuckDBPyConnection, sql: str, parquet_path: str) -> str:
        key = self._s3_object_key(parquet_path)
        if not key:
            con.execute(f"COPY ({sql}) TO '{parquet_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
            return parquet_path

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="omega-materialize-", suffix=".parquet", delete=False) as tmp:
                tmp_path = tmp.name
            con.execute(f"COPY ({sql}) TO '{tmp_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
            return self._upload_local_parquet(tmp_path, parquet_path)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass

    def _prune_snapshots(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict | None = None,
        keep: int = 5,
    ) -> None:
        prefix = self._snapshot_prefix(layer, cartridge, name, user_context)
        from minio import Minio

        client = Minio(
            self.minio_endpoint,
            access_key=self.minio_access,
            secret_key=self.minio_secret,
            secure=self.minio_secure,
        )
        try:
            objects = sorted(
                [obj.object_name for obj in client.list_objects(self.minio_bucket, prefix=prefix, recursive=True)],
                reverse=True,
            )
            for object_name in objects[keep:]:
                client.remove_object(self.minio_bucket, object_name)
        except Exception:
            pass

    def _ensure_scope_columns(
        self,
        con: duckdb.DuckDBPyConnection,
        sql: str,
        user_context: dict | None,
    ) -> str:
        tenant, workspace = self._scope_values(user_context)
        if not (tenant and workspace):
            return sql
        try:
            rows = con.execute(f"DESCRIBE SELECT * FROM ({sql}) _scope_probe LIMIT 0").fetchall()
            cols = {str(r[0]).lower() for r in rows}
        except Exception:
            cols = set()
        extras = []
        if "tenant_id" not in cols:
            extras.append(f"{_sql_quote(tenant)} AS tenant_id")
        if "workspace_id" not in cols:
            extras.append(f"{_sql_quote(workspace)} AS workspace_id")
        if not extras:
            return sql
        return f"SELECT {', '.join(extras)}, _scope_q.* FROM ({sql}) _scope_q"

    def _resolve_latest_date(self, source: str, user_context: dict | None = None) -> str | None:
        """Devuelve el load_date más reciente disponible en una fuente Bronze."""
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                row = con.execute(
                    f"SELECT MAX(load_date) FROM {expr}"
                ).fetchone()
            return str(row[0]) if row and row[0] else None
        except Exception:
            return None

    # ── Bronze discovery ──────────────────────────────────────────────────────

    def list_sources(self) -> list[str]:
        try:
            with self._duckdb_lock:
                con = self._conn()
                rows = con.execute(f"""
                    SELECT DISTINCT regexp_extract(file, 's3://[^/]+/([^/]+/[^/]+/[^/]+)', 1) AS source
                    FROM glob('s3://{self.minio_bucket}/raw/**/*.parquet')
                """).fetchall()
            return sorted({r[0] for r in rows if r[0]})
        except Exception:
            return []

    def get_source_schema(self, source: str, user_context: dict | None = None) -> dict:
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                rows = con.execute(f"DESCRIBE SELECT * FROM {expr} LIMIT 0").fetchall()
            return {"source": source, "fields": [{"name": r[0], "type": r[1]} for r in rows]}
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    def get_source_partitions(self, source: str, user_context: dict | None = None) -> dict:
        """
        Returns partition values (load_date, batch_id) available in a bronze source.
        Also returns sql_latest — a ready-to-use SQL filtered to the most recent load_date.
        """
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                rows = con.execute(
                    f"SELECT DISTINCT load_date, batch_id FROM {expr} "
                    f"ORDER BY load_date DESC, batch_id DESC LIMIT 30"
                ).fetchall()
            partitions = [{"load_date": str(r[0]), "batch_id": str(r[1])} for r in rows]
            latest = partitions[0] if partitions else None
            return {
                "source":     source,
                "partitions": partitions,
                "latest":     latest,
                # load_date comes from Parquet metadata and could in theory be
                # tampered with — escape it for safe SQL interpolation. The
                # SQL is returned to the caller (not executed here), so we
                # can't use prepared-statement placeholders.
                "sql_latest": (
                    f"SELECT * FROM {expr} WHERE load_date = {_sql_quote(str(latest['load_date']))}"
                ) if latest else None,
            }
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    def preview_source(self, source: str, limit: int = 5, user_context: dict | None = None) -> dict:
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                schema_rows = con.execute(f"DESCRIBE SELECT * FROM {expr} LIMIT 0").fetchall()
                data = con.execute(f"SELECT * FROM {expr} LIMIT {limit}").fetchall()
            cols = [r[0] for r in schema_rows]
            return {
                "source": source,
                "schema": [{"name": r[0], "type": r[1]} for r in schema_rows],
                "data": [dict(zip(cols, row)) for row in data],
            }
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    # ── SQL preview ───────────────────────────────────────────────────────────

    # Blacklist of DuckDB constructs that must NEVER appear in user-supplied
    # SQL. Each pattern is checked independently so the rejection reason can
    # name the exact pattern that matched. _validate_safe_sql is only called
    # for SQL that came from the LLM/user — internal engine calls (ATTACH at
    # startup, INSTALL/LOAD of httpfs+postgres extensions in _conn) bypass
    # this gate by going straight to con.execute().
    _DANGEROUS_PATTERNS = [
        re.compile(r"\bread_(?:csv|text|json|blob|parquet_objects)\s*\(", re.IGNORECASE),
        re.compile(r"\bATTACH\b", re.IGNORECASE),
        re.compile(r"\bDETACH\b", re.IGNORECASE),
        re.compile(r"\bINSTALL\b", re.IGNORECASE),
        re.compile(r"\bLOAD\b", re.IGNORECASE),
        re.compile(r"\bPRAGMA\b", re.IGNORECASE),
        re.compile(r"\bCOPY\s+(?:.*\s+)?FROM\b", re.IGNORECASE | re.DOTALL),
        re.compile(r"\bSET\s+(?:GLOBAL|SESSION|memory_limit|threads|extension_directory)\b", re.IGNORECASE),
        re.compile(r"\bCREATE\s+(?:TABLE|VIEW|FUNCTION|MACRO|SECRET)\b", re.IGNORECASE),
        re.compile(r"\bDROP\s+(?:TABLE|VIEW|FUNCTION|MACRO|SECRET|SCHEMA|DATABASE)\b", re.IGNORECASE),
    ]
    _READ_PARQUET_RE = re.compile(r"\bread_parquet\s*\(\s*(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
    _DANGEROUS_PATH_RE = re.compile(r"(?i)(file://|['\"]/(?:etc|proc|var)/)")

    def _validate_safe_sql(self, sql: str) -> None:
        policy_sql = _strip_sql_comments(sql)
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.search(policy_sql):
                raise ValueError(
                    f"SQL blocked by safety policy: matched {pattern.pattern}"
                )
        if self._DANGEROUS_PATH_RE.search(policy_sql):
            raise ValueError("SQL contains a blocked local file path")
        for match in self._READ_PARQUET_RE.finditer(policy_sql):
            path = match.group(2).strip()
            if not path.startswith("s3://"):
                raise ValueError("read_parquet is only allowed for s3:// sources")

    # Hard cap on preview/query result size — protects the server from a
    # runaway query (cartesian product, missing WHERE, etc.) that asks for
    # millions of rows in one shot. Single point of enforcement: any caller
    # that delegates to preview_sql (query_dataset, the /preview endpoints)
    # inherits the cap automatically and MUST NOT re-cap.
    _MAX_PREVIEW_LIMIT = 10_000
    _DEFAULT_PREVIEW_LIMIT = 20
    # Sprint v1.16: wall-clock cap on a single preview_sql execution.
    # Limits the blast radius of an LLM-generated query that misses a
    # WHERE clause, builds a cartesian product, or otherwise asks DuckDB
    # to scan a dataset that takes longer than this to read. DuckDB 1.2.x
    # has no native `statement_timeout` configuration knob, so this is
    # enforced via a threading.Timer that calls con.interrupt() on the
    # shared connection — DuckDB raises InterruptException which we
    # translate into a friendly error message below.
    _STATEMENT_TIMEOUT_SECONDS = 30

    def preview_sql(self, sql: str, limit: int = 20, sources: list[str] | None = None, user_context: dict = None, params: list = None) -> dict:
        """Execute SQL with RLS applied and caller params merged.

        RLS is ALWAYS applied regardless of whether the caller supplies params.
        get_rls_filters() injects ? placeholders inside inner subqueries; those
        placeholders appear first in the SQL, so rls_params must come before
        caller_params in the combined list (positional DuckDB binding is L→R).

        Callers that already applied RLS (e.g. query_dataset) must NOT call
        get_rls_filters separately — delegate entirely to preview_sql instead.

        `limit` is coerced into [1, _MAX_PREVIEW_LIMIT]; non-positive or None
        values fall back to _DEFAULT_PREVIEW_LIMIT.

        Execution is bounded by ``_STATEMENT_TIMEOUT_SECONDS`` — see the
        class-level comment for the mechanism.
        """
        if limit is None or limit <= 0:
            limit = self._DEFAULT_PREVIEW_LIMIT
        elif limit > self._MAX_PREVIEW_LIMIT:
            limit = self._MAX_PREVIEW_LIMIT
        self._validate_safe_sql(sql)
        caller_params = list(params or [])
        try:
            with self._duckdb_lock:
                rls_sql, rls_params = self.get_rls_filters(sql, user_context)
                combined_params = rls_params + caller_params

                con = self._conn()

                effective_sql = self._inject_bucket(rls_sql)
                effective_sql = self._scope_storage_sql(effective_sql, sources or [], user_context)
                effective_sql = self._inject_latest_date(effective_sql, sources or [], user_context)
                limited = f"SELECT * FROM ({effective_sql}) _q LIMIT {limit}"

                # Watchdog: fires con.interrupt() if the query runs past
                # the cap. The Timer is cancelled immediately after a
                # successful fetch so the connection is free for the
                # next caller. daemon=True keeps the process exitable
                # even if the timer is somehow still pending at shutdown.
                timer = threading.Timer(self._STATEMENT_TIMEOUT_SECONDS, con.interrupt)
                timer.daemon = True
                timer.start()
                try:
                    cursor = con.execute(limited, combined_params)
                    desc = cursor.description
                    data = cursor.fetchall()
                finally:
                    timer.cancel()

            schema_rows = [
                {"name": c[0], "type": c[1] if len(c) > 1 and isinstance(c[1], str) else "VARCHAR"}
                for c in desc
            ]
            cols = [c[0] for c in desc]
            return {
                "schema":    schema_rows,
                "data":      [dict(zip(cols, row)) for row in data],
                "row_count": len(data),
            }
        except Exception as exc:
            msg = str(exc)
            # DuckDB raises InterruptException ("INTERRUPT Error: Interrupted!")
            # when the watchdog calls con.interrupt(). Normalize the message
            # so the LLM/caller gets something actionable instead of a stack
            # trace fragment.
            lower = msg.lower()
            missing_parquet = (
                ("no files found" in lower and ("read_parquet" in lower or "s3://" in lower))
                or ("404" in lower and ("lakehouse/" in lower or "http://minio" in lower or "minio:" in lower))
            )
            if missing_parquet:
                return {
                    "code": "source_files_missing",
                    "error": (
                        "No hay archivos Parquet para la fuente seleccionada. "
                        "Ejecuta primero la extracción o materializa la dependencia upstream."
                    ),
                    "raw_error": msg,
                }
            if (
                "interrupt" in lower
                or "timeout" in lower
                or exc.__class__.__name__ == "InterruptException"
            ):
                return {
                    "error": (
                        f"Query exceeded timeout of "
                        f"{self._STATEMENT_TIMEOUT_SECONDS}s. Try a more "
                        f"restrictive WHERE clause or reduce the scope of "
                        f"the scan."
                    )
                }
            return {"error": msg}

    # ── Dataset query ─────────────────────────────────────────────────────────

    def get_dataset_schema(self, ds: dict) -> dict:
        try:
            validate_safe_identifier(ds["name"], "dataset")
            with self._duckdb_lock:
                con = self._conn()
                rows = con.execute(
                    f"DESCRIBE SELECT * FROM ({self._inject_bucket(ds['sql_def'])}) _q LIMIT 0"
                ).fetchall()
            return {"name": ds["name"], "fields": [{"name": r[0], "type": r[1]} for r in rows]}
        except Exception as exc:
            return {"name": ds.get("name"), "error": str(exc)}


    def _rls_filter_clause(self, cols: list, user_context: dict, params: list) -> str:
        """Return the WHERE clause body for a pggold table (no leading WHERE).

        Gold reads are workspace-strict for non-admin callers. Tables without a
        workspace_id column are treated as unsafe legacy/global data and default
        to deny. Tables with both tenant_id and workspace_id must match both
        values so one workspace cannot read a sibling workspace in the same
        tenant.
        """
        tenant = str(user_context.get("tenant_id") or "")
        workspace = str(user_context.get("workspace_id") or "")
        colset = set(cols)

        if "workspace_id" not in colset or not workspace:
            return "1=0"
        if "tenant_id" in colset:
            if not tenant:
                return "1=0"
            params.extend([tenant, workspace])
            return "tenant_id = ? AND workspace_id = ?"
        params.append(workspace)
        return "workspace_id = ?"

    def _inject_rls_ast(self, sql: str, user_context: dict) -> tuple[str, list]:
        """AST-based RLS injection using sqlglot.

        Walks every exp.Table node in the parsed tree (including CTEs,
        subqueries and UNION branches), and for any table whose schema is
        "pggold" replaces it with an inline subquery filtered on the user's
        tenancy column. Original aliases are preserved so the surrounding
        SQL (qualified column refs like `t.col`) keeps resolving.

        Default-deny: if sqlglot cannot parse the SQL we raise ValueError
        and the caller must NOT execute the query. There is no regex
        fallback (that was the v1.0 design weakness called out by audit).
        """
        try:
            tree = sqlglot.parse_one(sql, read='duckdb')
        except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as exc:
            # v1.43.1 (Claude B9): TokenError fires on lexer failures
            # (e.g. unbalanced quotes, raw garbage) before sqlglot even
            # reaches the parse step. The audit's default-deny posture
            # treats those the same as ParseError — the caller MUST NOT
            # see the query execute.
            raise ValueError(
                f"SQL failed AST parse — default-deny applied: {exc}"
            ) from exc
        if tree is None:
            raise ValueError("SQL produced empty AST — default-deny applied")

        params: list = []

        # Cache column lookups so a UNION of N pggold tables only fires N
        # DESCRIBEs, not N×2.
        cols_cache: dict[str, list] = {}

        def _columns_for(table_name: str) -> list:
            if table_name in cols_cache:
                return cols_cache[table_name]
            try:
                validate_safe_identifier(table_name, "table")
                con = self._conn()
                rows = con.execute(
                    f"DESCRIBE SELECT * FROM pggold.{table_name} LIMIT 0"
                ).fetchall()
                cols = [r[0].lower() for r in rows]
            except Exception:
                cols = []
            cols_cache[table_name] = cols
            return cols

        def transformer(node):
            if not isinstance(node, _sqlglot_exp.Table):
                return node
            if (node.db or "").lower() != "pggold":
                return node

            table_name = node.name
            cols = _columns_for(table_name)
            where_body = self._rls_filter_clause(cols, user_context, params)

            # Capture original alias (if any) so qualified refs still resolve.
            original_alias = node.alias

            inner_sql = f"SELECT * FROM pggold.{table_name} WHERE {where_body}"
            wrapper = sqlglot.parse_one(
                f"SELECT * FROM ({inner_sql}) sub", read='duckdb'
            )
            sub_node = wrapper.find(_sqlglot_exp.Subquery)
            if original_alias:
                sub_node.set(
                    "alias",
                    _sqlglot_exp.TableAlias(
                        this=_sqlglot_exp.to_identifier(original_alias)
                    ),
                )
            else:
                sub_node.set("alias", None)
            return sub_node

        new_tree = tree.transform(transformer)
        return new_tree.sql(dialect='duckdb'), params

    def get_rls_filters(self, sql: str, user_context: dict) -> tuple[str, list]:
        # Admin bypass requires a server-built context. A raw body claiming
        # role=admin is not enough; refinement constructs
        # _server_trusted_context only after verifying the internal caller and
        # its security_context source.
        if (
            user_context
            and str(user_context.get("role") or "").lower() in {"admin", "owner", "super_admin"}
            and user_context.get("_server_trusted_context")
            and not (user_context.get("workspace_id") or user_context.get("tenant_id"))
        ):
            return sql, []

        if not user_context:
            user_context = {}

        with self._duckdb_lock:
            return self._inject_rls_ast(sql, user_context)

    def query_dataset(self, ds: dict, filters: dict, limit: int = 100, user_context: dict = None) -> dict:
        validate_safe_identifier(ds.get("name", ""), "dataset")
        sql = ds.get("sql_def", "")
        self._validate_safe_sql(sql)
        filter_params = []
        if filters:
            for key in filters.keys():
                validate_safe_identifier(key, "filter")
            clauses = [f"{k} = ?" for k in filters.keys()]
            filter_params = list(filters.values())
            sql = f"SELECT * FROM ({sql}) _q WHERE {' AND '.join(clauses)}"

        # Delegate RLS to preview_sql — do NOT call get_rls_filters here.
        # preview_sql always applies RLS and combines rls_params + filter_params
        # in the correct positional order (RLS ? inside subqueries come first).
        return self.preview_sql(
            sql,
            limit,
            sources=ds.get("sources") or [],
            params=filter_params,
            user_context=user_context,
        )

    def _inject_latest_date(
        self,
        sql: str,
        sources: list[str],
        user_context: dict | None = None,
    ) -> str:
        """
        Sustituye el placeholder {latest_date} en el SQL por el load_date más
        reciente de la primera fuente Bronze. Si el SQL ya NO usa el placeholder,
        lo devuelve sin modificar.

        v1.43.1 (Claude B4): el load_date viene de MAX(load_date) sobre
        Parquet en MinIO. Un cartucho comprometido podría escribir un valor
        como ``2024-01-01' UNION SELECT secrets FROM x WHERE '1'='1`` y
        romper la consulta cuando se concatena dentro del literal de la
        plantilla (``llm_sql.py:20`` enseña al modelo a usar
        ``WHERE load_date = '{latest_date}'`` — el LLM provee las comillas
        externas). Doblamos cualquier comilla simple embebida en el valor
        para que quede atrapado dentro de su literal — mismo escape que
        ``_sql_quote`` aplica internamente pero SIN añadir las comillas
        externas (la plantilla ya las trae). El fallback ``1970-01-01`` no
        contiene comillas pero pasa por el mismo escape por simetría.
        """
        if "{latest_date}" not in sql:
            return sql
        primary_source = sources[0] if sources else None
        if not primary_source:
            return sql.replace("{latest_date}", _escape_sql_literal_inner("1970-01-01"))
        # No silent fallback: _resolve_latest_date is always scope-aware, so we
        # pass user_context straight through. A pre-scope one-argument caller or
        # test double now raises TypeError loudly instead of degrading tenant
        # isolation by resolving the latest load_date across all tenants.
        latest = self._resolve_latest_date(primary_source, user_context)
        return sql.replace(
            "{latest_date}", _escape_sql_literal_inner(latest or "1970-01-01")
        )

    def _inject_bucket(self, sql: str) -> str:
        """Sustituye el placeholder {bucket} en el SQL por el bucket configurado.
        Esto desacopla los datasets del nombre concreto del bucket (que cambia
        entre local 'lakehouse' y AWS 'modecissions-lakehouse-XXXXXX')."""
        if "{bucket}" not in sql:
            return sql
        return sql.replace("{bucket}", self.minio_bucket)

    # ── Materialization ───────────────────────────────────────────────────────

    def _gold_table_columns(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
    ) -> set[str] | None:
        try:
            rows = con.execute(
                f"DESCRIBE SELECT * FROM pggold.{table} LIMIT 0"
            ).fetchall()
            return {str(r[0]).lower() for r in rows}
        except Exception:
            return None

    def _ensure_scoped_gold_table(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
        sql: str,
    ) -> None:
        cols = self._gold_table_columns(con, table)
        if cols is None:
            con.execute(f"CREATE TABLE pggold.{table} AS SELECT * FROM ({sql}) _q WHERE 1=0")
            return
        if "tenant_id" in cols and "workspace_id" in cols:
            return
        # Legacy unscoped gold tables cannot safely coexist with SaaS-scoped
        # writes. Recreate the table as empty with scoped columns rather than
        # silently mixing tenants in pggold.gold_<dataset>.
        con.execute(f"DROP TABLE IF EXISTS pggold.{table}")
        con.execute(f"CREATE TABLE pggold.{table} AS SELECT * FROM ({sql}) _q WHERE 1=0")

    def materialize(self, ds: dict, user_context: dict | None = None) -> dict:
        """
        Materialize a dataset to silver or gold.

        ds fields:
          name         — dataset name
          sql_def      — transformation SQL
          layer        — "silver" | "gold"
          cartridge    — source cartridge id (e.g. "replicon")
          sources      — list of bronze source paths
          column_mapping — {src_col: business_term, ...} (optional, for lineage)
          source_load_date — partition date of the bronze source (for lineage)
          source_batch_id  — batch_id of the bronze source (for lineage)
        """
        validate_safe_identifier(ds["name"], "dataset")
        validate_safe_identifier(ds.get("cartridge", "unknown"), "cartridge")
        name        = ds["name"]
        layer       = ds.get("layer", "silver")
        if layer not in ("silver", "gold"):
            raise ValueError("Invalid dataset layer")
        sql         = ds["sql_def"]
        self._validate_safe_sql(sql)
        cartridge   = ds.get("cartridge", "unknown")
        sources     = ds.get("sources") or []
        with self._duckdb_lock:
            con = self._conn()
            storage_uri = ""
            row_count   = 0

            sql = self._inject_bucket(sql)
            sql = self._scope_storage_sql(sql, sources, user_context)

            if layer == "gold":
                # ── Gold → tabla en postgres_gold ────────────────────────────────
                self._pg_gold_attach(con)
                table = f"gold_{name}"
                validate_safe_identifier(table, "table")
                effective_sql = self._inject_latest_date(sql, sources, user_context)
                effective_sql = self._ensure_scope_columns(con, effective_sql, user_context)
                tenant, workspace = self._scope_values(user_context)
                if tenant and workspace:
                    self._ensure_scoped_gold_table(con, table, effective_sql)
                    con.execute(
                        f"DELETE FROM pggold.{table} WHERE tenant_id = ? AND workspace_id = ?",
                        [tenant, workspace],
                    )
                    con.execute(
                        f"INSERT INTO pggold.{table} SELECT * FROM ({effective_sql}) _q"
                    )
                    row_count = con.execute(
                        f"SELECT COUNT(*) FROM pggold.{table} WHERE tenant_id = ? AND workspace_id = ?",
                        [tenant, workspace],
                    ).fetchone()[0]
                    gold_parquet_path = self._snapshot_path("gold", cartridge, name, user_context)
                    gold_storage_uri = self._copy_to_parquet(
                        con,
                        (
                            f"SELECT * FROM pggold.{table} "
                            f"WHERE tenant_id = {_sql_quote(tenant)} "
                            f"AND workspace_id = {_sql_quote(workspace)}"
                        ),
                        gold_parquet_path,
                    )
                else:
                    con.execute(f"CREATE OR REPLACE TABLE pggold.{table} AS ({effective_sql})")
                    row_count = con.execute(f"SELECT COUNT(*) FROM pggold.{table}").fetchone()[0]
                    gold_parquet_path = self._snapshot_path("gold", cartridge, name)
                    gold_storage_uri = self._copy_to_parquet(con, f"SELECT * FROM pggold.{table}", gold_parquet_path)
                storage_uri = f"postgres_gold:{table}"
                if gold_storage_uri:
                    storage_uri = gold_storage_uri

            else:
                # ── Silver → Parquet snapshot inmutable (última extracción vía lineage) ──
                effective_sql = self._inject_latest_date(sql, sources, user_context)
                effective_sql = self._ensure_scope_columns(con, effective_sql, user_context)
                parquet_path  = self._snapshot_path("silver", cartridge, name, user_context)
                storage_uri = self._copy_to_parquet(con, effective_sql, parquet_path)
                row_count = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{storage_uri}')"
                ).fetchone()[0]

            # ── Infer schema for catalog & lineage ──────────────────────────────
            try:
                if layer == "silver":
                    schema_rows  = con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{storage_uri}') LIMIT 0"
                    ).fetchall()
                else:
                    self._pg_gold_attach(con)
                    schema_rows = con.execute(
                        f"DESCRIBE SELECT * FROM pggold.gold_{name} LIMIT 0"
                    ).fetchall()
                schema_fields = [{"name": r[0], "type": r[1]} for r in schema_rows]
            except Exception:
                schema_fields = []

        # ── Write lineage ────────────────────────────────────────────────────
        source_entity = (sources or [""])[0]
        latest_date   = self._resolve_latest_date(source_entity, user_context) if source_entity else None
        self._write_lineage(
            silver_name      = name,
            cartridge_id     = cartridge,
            source_entity    = source_entity,
            source_load_date = latest_date or ds.get("source_load_date"),
            source_batch_id  = ds.get("source_batch_id"),
            sql_def          = sql,
            column_mapping   = ds.get("column_mapping", {}),
            layer            = layer,
            row_count        = row_count,
            storage_uri      = storage_uri,
        )
        try:
            self._prune_snapshots(layer, cartridge, name, user_context)
        except Exception:
            pass

        # ── Update semantic catalog ──────────────────────────────────────────
        self._update_catalog(
            name           = name,
            layer          = layer,
            cartridge      = cartridge,
            schema_fields  = schema_fields,
            column_mapping = ds.get("column_mapping", {}),
            description    = ds.get("description", ""),
        )

        return {"name": name, "layer": layer, "row_count": row_count,
                "storage_uri": storage_uri}

    def _update_catalog(
        self,
        name: str,
        layer: str,
        cartridge: str,
        schema_fields: list[dict],
        column_mapping: dict,
        description: str = "",
    ) -> None:
        """Upsert column entries into data_catalog after a successful materialization."""
        try:
            conn = self._pg_conn()
            with conn.cursor() as cur:
                for field in schema_fields:
                    col  = field["name"]
                    desc = column_mapping.get(col, "")
                    cur.execute("""
                        INSERT INTO data_catalog
                            (dataset, layer, cartridge, column_name, data_type, description, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (dataset, column_name) DO UPDATE
                            SET data_type   = EXCLUDED.data_type,
                                layer       = EXCLUDED.layer,
                                cartridge   = EXCLUDED.cartridge,
                                description = CASE
                                    WHEN EXCLUDED.description != '' THEN EXCLUDED.description
                                    ELSE data_catalog.description
                                END,
                                updated_at  = NOW()
                    """, (name, layer, cartridge, col, field["type"], desc))
            conn.commit()
            conn.close()
        except Exception:
            pass  # catalog is best-effort

    def _write_lineage(
        self,
        silver_name: str,
        cartridge_id: str,
        source_entity: str,
        source_load_date: str | None,
        source_batch_id: str | None,
        sql_def: str,
        column_mapping: dict,
        layer: str,
        row_count: int,
        storage_uri: str,
    ) -> None:
        try:
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO silver_lineage
                        (silver_name, cartridge_id, source_entity, source_load_date,
                         source_batch_id, sql_def, column_mapping, layer,
                         row_count, storage_uri)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                """, (
                    silver_name, cartridge_id, source_entity,
                    source_load_date, source_batch_id,
                    sql_def, json.dumps(column_mapping), layer,
                    row_count, storage_uri,
                ))
            conn.commit()
            conn.close()
        except Exception:
            pass  # lineage is best-effort
