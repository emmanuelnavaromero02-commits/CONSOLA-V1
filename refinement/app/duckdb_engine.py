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
import json
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import duckdb
import psycopg2
from psycopg2.extras import execute_values
import sqlglot
from sqlglot import exp as _sqlglot_exp

from omega_lakehouse import ObjectAlreadyExists, storage_from_env

try:
    from app.duckdb_runtime import connect_duckdb_runtime
    from app.sql_table_function_policy import validate_table_function_query
    from app.storage_scope_policy import has_exact_storage_scope
except ModuleNotFoundError:
    from refinement.app.duckdb_runtime import connect_duckdb_runtime
    from refinement.app.sql_table_function_policy import validate_table_function_query
    from refinement.app.storage_scope_policy import has_exact_storage_scope

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
SAFE_S3_BRONZE_TAIL_RE = re.compile(r"^[a-zA-Z0-9_./=*-]+$")
S3_LITERAL_RE = re.compile(
    r"(['\"])((?:s3|gs)://.*?)(?<!\\)\1", re.IGNORECASE | re.DOTALL
)
DUCKDB_MEMORY_LIMIT_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE
)


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


def _duckdb_memory_limit_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_MEMORY_LIMIT_RE.fullmatch(value):
        raise ValueError("DUCKDB_MEMORY_LIMIT must look like 512MB, 1GB or 1024MiB")
    return value


def _duckdb_threads_from_env(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("DUCKDB_THREADS must be an integer") from exc
    if parsed < 1 or parsed > 64:
        raise ValueError("DUCKDB_THREADS must be between 1 and 64")
    return parsed


def _libpq_quote(value: str) -> str:
    return "'" + (value or "").replace("\\", "\\\\").replace("'", "\\'") + "'"


def _pg_ident(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def _duckdb_type_to_pg_type(raw: str) -> str:
    """Map DuckDB DESCRIBE types to PostgreSQL column types for Gold drift.

    Gold tables are born from DuckDB queries, then persisted into Postgres.
    When a dataset SQL evolves by adding columns, we add only the missing
    nullable columns in Postgres rather than dropping all tenants' rows.
    """
    typ = (raw or "").strip().upper()
    if not typ:
        raise ValueError("Gold query returned a column with no type")
    if typ.startswith("DECIMAL("):
        return typ
    if typ in {"VARCHAR", "TEXT", "UUID"}:
        return "TEXT"
    if typ in {"BOOLEAN", "BOOL"}:
        return "BOOLEAN"
    if typ in {"TINYINT", "UTINYINT", "SMALLINT", "USMALLINT"}:
        return "SMALLINT"
    if typ in {"INTEGER", "INT", "UINTEGER"}:
        return "INTEGER"
    if typ in {"BIGINT", "HUGEINT", "UBIGINT", "UHUGEINT"}:
        return "BIGINT"
    if typ in {"REAL", "FLOAT"}:
        return "REAL"
    if typ in {"DOUBLE", "FLOAT8"}:
        return "DOUBLE PRECISION"
    if typ == "DATE":
        return "DATE"
    if typ.startswith("TIMESTAMP"):
        return "TIMESTAMPTZ" if "WITH TIME ZONE" in typ else "TIMESTAMP"
    if typ.startswith("TIME"):
        return "TIME"
    if typ in {"JSON", "JSONB"}:
        return "JSONB"
    if typ.endswith("[]") or typ.startswith(("STRUCT(", "MAP(", "LIST(", "UNION(")):
        return "JSONB"
    raise ValueError(f"Unsupported DuckDB type for Gold schema evolution: {raw}")


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
        _aws_endpoint = "amazonaws.com" in (self.minio_endpoint or "").lower()
        self.minio_access = os.environ.get("MINIO_ACCESS_KEY") or (
            "" if _aws_endpoint else "minio"
        )
        self.minio_secret = os.environ.get("MINIO_SECRET_KEY")
        self.minio_bucket = os.environ.get("MINIO_BUCKET", "")
        self.minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        storage_bucket = self.minio_bucket or None
        if os.environ.get(
            "LAKEHOUSE_PROVIDER", ""
        ).strip().lower() == "gcs" or os.environ.get("GCS_BUCKET"):
            storage_bucket = (
                os.environ.get("LAKEHOUSE_BUCKET")
                or os.environ.get("GCS_BUCKET")
                or None
            )
        self.storage = storage_from_env(bucket=storage_bucket)
        self.minio_bucket = getattr(
            getattr(self.storage, "config", None),
            "bucket",
            self.minio_bucket or "lakehouse",
        )
        self.pg_url = os.environ.get("DATABASE_URL", "")
        # Analytical (gold) DB. Falls back to service DB if unset, so
        # local/dev environments without postgres_gold keep working.
        self.pg_gold_url = os.environ.get("GOLD_DATABASE_URL", "") or self.pg_url
        self.duckdb_memory_limit = _duckdb_memory_limit_from_env(
            os.environ.get("DUCKDB_MEMORY_LIMIT")
        )
        self.duckdb_threads = _duckdb_threads_from_env(os.environ.get("DUCKDB_THREADS"))
        self._con: duckdb.DuckDBPyConnection | None = None
        self._duckdb_lock = threading.RLock()

    def _uses_aws_s3_credential_chain(self) -> bool:
        """True when running against AWS S3 with instance/profile creds.

        Local MinIO uses explicit MINIO_ACCESS_KEY / MINIO_SECRET_KEY. The AWS
        deployment intentionally uses the EC2 instance role, so configuring
        DuckDB with empty access/secret strings makes httpfs attempt anonymous
        S3 reads and every materialization fails with HTTP 403.
        """
        endpoint = (self.minio_endpoint or "").lower()
        return (
            "amazonaws.com" in endpoint
            and not (self.minio_access or "").strip()
            and not (self.minio_secret or "").strip()
        )

    def _uses_gcs_lakehouse(self) -> bool:
        provider = getattr(getattr(self.storage, "config", None), "provider", "")
        return provider == "gcs"

    def _gcs_hmac_credentials(self) -> tuple[str, str]:
        key_id = (
            os.environ.get("GCS_ACCESS_KEY_ID")
            or os.environ.get("GOOGLE_HMAC_ACCESS_KEY_ID")
            or os.environ.get("LAKEHOUSE_ACCESS_KEY")
            or ""
        ).strip()
        secret = (
            os.environ.get("GCS_SECRET_ACCESS_KEY")
            or os.environ.get("GOOGLE_HMAC_SECRET_ACCESS_KEY")
            or os.environ.get("LAKEHOUSE_SECRET_KEY")
            or ""
        ).strip()
        if not key_id or not secret:
            raise ValueError(
                "GCS lakehouse refinement reads require HMAC credentials "
                "via GCS_ACCESS_KEY_ID/GCS_SECRET_ACCESS_KEY or LAKEHOUSE_ACCESS_KEY/LAKEHOUSE_SECRET_KEY"
            )
        return key_id, secret

    def _configure_duckdb_gcs(self, con: duckdb.DuckDBPyConnection) -> None:
        key_id, secret = self._gcs_hmac_credentials()
        try:
            con.execute(
                "CREATE OR REPLACE SECRET omega_gcs ("
                "TYPE gcs, "
                f"KEY_ID {_sql_quote(key_id)}, "
                f"SECRET {_sql_quote(secret)}"
                ");"
            )
        except Exception:
            raise ValueError("GCS lakehouse DuckDB credential setup failed") from None

    def _s3_url_style(self) -> str:
        endpoint = (self.minio_endpoint or "").lower()
        return "vhost" if "amazonaws.com" in endpoint else "path"

    def _conn(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            try:
                self._con = connect_duckdb_runtime()
                if self.duckdb_memory_limit:
                    self._con.execute(
                        f"SET memory_limit={_sql_quote(self.duckdb_memory_limit)};"
                    )
                if self.duckdb_threads is not None:
                    self._con.execute(f"SET threads={self.duckdb_threads};")
                if self._uses_gcs_lakehouse():
                    self._configure_duckdb_gcs(self._con)
                else:
                    self._con.execute(
                        f"SET s3_endpoint={_sql_quote(self.minio_endpoint or '')};"
                        f"SET s3_url_style={_sql_quote(self._s3_url_style())};"
                        f"SET s3_use_ssl={'true' if self.minio_secure else 'false'};"
                    )
                if (
                    not self._uses_gcs_lakehouse()
                    and self._uses_aws_s3_credential_chain()
                ):
                    region = (
                        os.environ.get("AWS_REGION")
                        or os.environ.get("AWS_DEFAULT_REGION")
                        or "us-east-1"
                    )
                    self._con.execute(
                        "CREATE OR REPLACE SECRET omega_s3_role ("
                        "TYPE S3, PROVIDER credential_chain, "
                        f"REGION {_sql_quote(region)}"
                        ");"
                    )
                elif not self._uses_gcs_lakehouse():
                    self._con.execute(
                        f"SET s3_access_key_id={_sql_quote(self.minio_access or '')};"
                        f"SET s3_secret_access_key={_sql_quote(self.minio_secret or '')};"
                    )
            except Exception:
                if self._con is not None:
                    getattr(self._con, "close", lambda: None)()
                self._con = None
                raise
        return self._con

    def setup(self):
        with self._duckdb_lock:
            con = self._conn()
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

    def _pg_gold_dsn(self, user_context: dict | None = None) -> str:
        dsn = _normalize_postgres_dsn(self.pg_gold_url)
        if not dsn:
            return ""
        tenant, workspace = self._scope_values(user_context)
        if not (tenant and workspace):
            return dsn
        options = f"-c app.tenant_id={tenant} -c app.workspace_id={workspace}"
        if "://" in dsn:
            parts = urlsplit(dsn)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query["options"] = options
            return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))
        return f"{dsn} options={_libpq_quote(options)}"

    def _pg_attach(self, con: duckdb.DuckDBPyConnection) -> str:
        """Attach service Postgres (pgdb) and return alias."""
        dsn = _normalize_postgres_dsn(self.pg_url)
        try:
            con.execute(f"ATTACH {_sql_quote(dsn)} AS pgdb (TYPE postgres);")
        except Exception:
            pass  # already attached
        return "pgdb"

    def _pg_gold_attach(
        self,
        con: duckdb.DuckDBPyConnection,
        user_context: dict | None = None,
    ) -> str:
        """Attach analytical Postgres (pggold) and return alias."""
        dsn = self._pg_gold_dsn(user_context)
        if not dsn:
            return "pggold"
        try:
            con.execute("DETACH pggold;")
        except Exception:
            pass
        con.execute(f"ATTACH {_sql_quote(dsn)} AS pggold (TYPE postgres);")
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

        if source.startswith(("s3://", "gs://")):
            expected_prefix = f"{self._storage_scheme()}://{self.minio_bucket}/raw/"
            if not source.startswith(expected_prefix):
                raise ValueError("Invalid bronze source")
            relative = source[len(self._storage_uri("")) :]
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
        workspace = self._safe_scope_segment(
            user_context.get("workspace_id"), "workspace_id"
        )
        return tenant, workspace

    def _bronze_path(self, source: str, user_context: dict | None = None) -> str:
        source = self._validate_bronze_source(source)
        if source.startswith("s3://"):
            return source
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return self._storage_uri(
                f"{source}/tenant_id={tenant}/workspace_id={workspace}/**/*.parquet"
            )
        # Keep unscoped reads on the legacy/global layout only. A recursive glob
        # over both `load_date=...` and `tenant_id=.../workspace_id=...` layouts
        # makes DuckDB's hive partition reader fail because the partition keys
        # differ across files.
        return self._storage_uri(f"{source}/load_date=*/batch_id=*/*.parquet")

    def _bronze_read(self, source: str, user_context: dict | None = None) -> str:
        path = self._bronze_path(source, user_context)
        return f"read_parquet('{path}', hive_partitioning=true, union_by_name=true)"

    def _silver_path(
        self, cartridge: str, name: str, user_context: dict | None = None
    ) -> str:
        """Legacy Silver path used to recognize older cartridge SQL.

        New materializations write immutable `_snapshots/*.parquet` objects and
        `_scope_storage_sql` redirects this path to the latest lineage URI.
        """
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return self._storage_uri(
                f"silver/{cartridge}/{name}/tenant_id={tenant}/workspace_id={workspace}/data.parquet"
            )
        return self._storage_uri(f"silver/{cartridge}/{name}/data.parquet")

    def _gold_path(
        self, cartridge: str, name: str, user_context: dict | None = None
    ) -> str:
        """Legacy Gold parquet path used for backwards-compatible SQL rewrites."""
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return self._storage_uri(
                f"gold/{cartridge}/{name}/tenant_id={tenant}/workspace_id={workspace}/data.parquet"
            )
        return self._storage_uri(f"gold/{cartridge}/{name}/data.parquet")

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
        return self._storage_uri(
            f"{self._snapshot_prefix(layer, cartridge, name, user_context)}{stamp}-{suffix}.parquet"
        )

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
        tenant, workspace = self._scope_values(user_context)
        if not tenant or not workspace:
            return None
        try:
            try:
                from app.publication_snapshot import PublicationSnapshotResolver
            except ModuleNotFoundError:
                from refinement.app.publication_snapshot import (
                    PublicationSnapshotResolver,
                )
            snapshot = PublicationSnapshotResolver(self.storage).published_snapshot(
                {"name": name, "layer": layer, "cartridge": cartridge},
                {"tenant_id": tenant, "workspace_id": workspace},
            )
            uri = str((snapshot.head if snapshot else {}).get("object_uri") or "")
            return uri or None
        except Exception:
            return None

    def missing_materialized_dependencies(
        self,
        sources: list[str],
        user_context: dict | None = None,
    ) -> list[str]:
        missing: list[str] = []
        for source in sources or []:
            src = str(source or "").strip()
            if not src.startswith(("silver/", "gold/")):
                continue
            parts = src.split("/")
            if len(parts) < 3:
                missing.append(src)
                continue
            layer, cartridge, name = parts[0], parts[1], parts[2]
            if not self._latest_materialized_uri(layer, cartridge, name, user_context):
                missing.append(src)
        return missing

    def _scope_storage_sql(
        self, sql: str, sources: list[str], user_context: dict | None
    ) -> str:
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

        def _replace_dataset_refs(
            layer: str, cartridge: str, name: str, replacement: str
        ) -> None:
            nonlocal out
            base = self._storage_uri(f"{layer}/{cartridge}/{name}")
            for pattern in (
                f"{base}/data.parquet",
                f"{base}/*.parquet",
                f"{base}/**/*.parquet",
            ):
                out = out.replace(pattern, replacement)

        for source in sources or []:
            src = str(source or "").strip()
            if src.startswith("raw/"):
                try:
                    scoped = self._bronze_path(src, user_context)
                    legacy = self._storage_uri(f"{src}/**/*.parquet")
                    out = out.replace(legacy, scoped)
                    out = out.replace(
                        legacy.replace("**/*.parquet", "*.parquet"), scoped
                    )
                except Exception:
                    continue
            elif tenant and workspace and src.startswith(("silver/", "gold/")):
                parts = src.split("/")
                if len(parts) >= 3:
                    layer, cartridge, name = parts[0], parts[1], parts[2]
                    latest = self._latest_materialized_uri(
                        layer, cartridge, name, user_context
                    )
                    scoped = latest or (
                        self._silver_path(cartridge, name, user_context)
                        if layer == "silver"
                        else self._gold_path(cartridge, name, user_context)
                    )
                    _replace_dataset_refs(layer, cartridge, name, scoped)
            elif src.startswith(("silver/", "gold/")):
                parts = src.split("/")
                if len(parts) >= 3:
                    layer, cartridge, name = parts[0], parts[1], parts[2]
                    latest = self._latest_materialized_uri(
                        layer, cartridge, name, user_context
                    )
                    if latest:
                        _replace_dataset_refs(layer, cartridge, name, latest)
        return out

    def _validate_scoped_storage_sql(self, sql: str, user_context: dict | None) -> None:
        tenant, workspace = self._scope_values(user_context)
        if not tenant or not workspace:
            return
        bucket_prefix = self._storage_uri("")
        for match in S3_LITERAL_RE.finditer(sql or ""):
            uri = match.group(2)
            if not uri.startswith(bucket_prefix):
                raise ValueError("S3 path uses an unapproved bucket")
            key = uri[len(bucket_prefix) :]
            if key.startswith(("raw/", "silver/", "gold/", "uploads/")) and not (
                has_exact_storage_scope(key, tenant, workspace)
            ):
                raise ValueError("S3 path is outside the caller tenant/workspace scope")

    def _s3_object_key(self, uri: str) -> str | None:
        prefix = self._storage_uri("")
        if not str(uri or "").startswith(prefix):
            return None
        key = str(uri)[len(prefix) :].strip("/")
        return key or None

    def _storage_scheme(self) -> str:
        provider = getattr(getattr(self.storage, "config", None), "provider", "s3")
        return "gs" if provider == "gcs" else "s3"

    def _storage_uri(self, key: str) -> str:
        clean = str(key or "").strip("/")
        base = f"{self._storage_scheme()}://{self.minio_bucket}"
        return f"{base}/{clean}" if clean else f"{base}/"

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
        self.storage.delete_prefix(key, require_trailing_slash=False)

    def _upload_local_parquet(self, local_path: str, parquet_path: str) -> str:
        key = self._s3_object_key(parquet_path)
        if not key:
            return parquet_path
        last_error: Exception | None = None
        for attempt in range(1, 9):
            attempt_key = key
            if attempt > 1:
                if key.endswith(".parquet"):
                    attempt_key = (
                        f"{key[:-8]}.retry{attempt}-{uuid.uuid4().hex[:8]}.parquet"
                    )
                else:
                    attempt_key = f"{key}.retry{attempt}-{uuid.uuid4().hex[:8]}"
            try:
                result = self.storage.put_file(
                    attempt_key,
                    Path(local_path),
                    overwrite=False,
                )
                return result.uri
            except ObjectAlreadyExists as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                if attempt == 8:
                    break
        if last_error:
            raise last_error
        return parquet_path

    def _copy_to_parquet(
        self, con: duckdb.DuckDBPyConnection, sql: str, parquet_path: str
    ) -> str:
        key = self._s3_object_key(parquet_path)
        if not key:
            con.execute(
                f"COPY ({sql}) TO '{parquet_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
            )
            return parquet_path

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix="omega-materialize-", suffix=".parquet", delete=False
            ) as tmp:
                tmp_path = tmp.name
            con.execute(
                f"COPY ({sql}) TO '{tmp_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
            )
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
        try:
            objects = sorted(
                [obj.key for obj in self.storage.iter_list(prefix)],
                reverse=True,
            )
            for object_name in objects[keep:]:
                self.storage.delete_object(object_name)
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
            rows = con.execute(
                f"DESCRIBE SELECT * FROM ({sql}) _scope_probe LIMIT 0"
            ).fetchall()
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

    def _resolve_latest_date(
        self, source: str, user_context: dict | None = None
    ) -> str | None:
        """Devuelve el load_date más reciente disponible en una fuente Bronze."""
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                row = con.execute(f"SELECT MAX(load_date) FROM {expr}").fetchone()
            return str(row[0]) if row and row[0] else None
        except Exception:
            return None

    # ── Bronze discovery ──────────────────────────────────────────────────────

    def _bronze_source_from_object_key(
        self,
        key: str,
        user_context: dict | None = None,
    ) -> str | None:
        key = str(key or "").strip("/")
        if not key.endswith(".parquet"):
            return None
        parts = [part for part in key.split("/") if part]
        if len(parts) < 4 or parts[0] != "raw":
            return None

        cartridge = parts[1]
        entity = parts[2]
        tenant, workspace = self._scope_values(user_context)

        if entity.startswith("tenant_id="):
            if len(parts) < 6 or not parts[3].startswith("workspace_id="):
                return None
            if (
                tenant
                and workspace
                and (
                    parts[2] != f"tenant_id={tenant}"
                    or parts[3] != f"workspace_id={workspace}"
                )
            ):
                return None
            entity = parts[4]
        elif tenant and workspace:
            if (
                f"tenant_id={tenant}" not in parts
                or f"workspace_id={workspace}" not in parts
            ):
                return None

        try:
            validate_safe_identifier(cartridge, "cartridge")
            validate_safe_identifier(entity, "entity")
        except ValueError:
            return None
        return f"raw/{cartridge}/{entity}"

    def _bronze_listing_prefixes(
        self, allowed_prefixes: list[str] | None = None
    ) -> list[str]:
        prefixes: list[str] = []
        for raw_prefix in allowed_prefixes or []:
            prefix = str(raw_prefix or "").strip().lstrip("/").rstrip("/")
            if not prefix or not prefix.startswith("raw"):
                continue
            parts = [part for part in prefix.split("/") if part]
            if parts == ["raw"]:
                candidate = "raw/"
            elif len(parts) >= 2:
                candidate = f"raw/{parts[1]}/"
            else:
                continue
            if candidate not in prefixes:
                prefixes.append(candidate)
        return prefixes or ["raw/"]

    def _list_sources_from_object_store(
        self,
        user_context: dict | None = None,
        allowed_prefixes: list[str] | None = None,
    ) -> list[str]:
        prefixes = self._bronze_listing_prefixes(allowed_prefixes)
        sources: set[str] = set()
        for prefix in prefixes:
            for obj in self.storage.iter_list(prefix):
                source = self._bronze_source_from_object_key(obj.key, user_context)
                if source:
                    sources.add(source)
        return sorted(sources)

    def _list_sources_from_duckdb_glob(
        self, user_context: dict | None = None
    ) -> list[str]:
        try:
            with self._duckdb_lock:
                con = self._conn()
                rows = con.execute(f"""
                    SELECT file
                    FROM glob('s3://{self.minio_bucket}/raw/**/*.parquet')
                """).fetchall()
            return sorted(
                {
                    source
                    for row in rows
                    if (
                        source := self._bronze_source_from_object_key(
                            self._s3_object_key(str(row[0] or "")) or "",
                            user_context,
                        )
                    )
                }
            )
        except Exception:
            return []

    def list_sources(
        self,
        user_context: dict | None = None,
        allowed_prefixes: list[str] | None = None,
    ) -> list[str]:
        try:
            sources = self._list_sources_from_object_store(
                user_context, allowed_prefixes
            )
            if sources:
                return sources
        except Exception:
            pass
        return self._list_sources_from_duckdb_glob(user_context)

    def get_source_schema(self, source: str, user_context: dict | None = None) -> dict:
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                rows = con.execute(f"DESCRIBE SELECT * FROM {expr} LIMIT 0").fetchall()
            return {
                "source": source,
                "fields": [{"name": r[0], "type": r[1]} for r in rows],
            }
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    def get_source_partitions(
        self, source: str, user_context: dict | None = None
    ) -> dict:
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
                "source": source,
                "partitions": partitions,
                "latest": latest,
                # load_date comes from Parquet metadata and could in theory be
                # tampered with — escape it for safe SQL interpolation. The
                # SQL is returned to the caller (not executed here), so we
                # can't use prepared-statement placeholders.
                "sql_latest": (
                    f"SELECT * FROM {expr} WHERE load_date = {_sql_quote(str(latest['load_date']))}"
                )
                if latest
                else None,
            }
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    def preview_source(
        self, source: str, limit: int = 5, user_context: dict | None = None
    ) -> dict:
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                schema_rows = con.execute(
                    f"DESCRIBE SELECT * FROM {expr} LIMIT 0"
                ).fetchall()
        except Exception as exc:
            return {"source": source, "error": str(exc)}

        cols = [r[0] for r in schema_rows]
        try:
            safe_limit = max(0, min(int(limit), 100))
        except (TypeError, ValueError):
            safe_limit = 5
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                data = con.execute(
                    f"SELECT * FROM {expr} LIMIT {safe_limit}"
                ).fetchall()
            return {
                "source": source,
                "schema": [{"name": r[0], "type": r[1]} for r in schema_rows],
                "columns": [{"name": r[0], "type": r[1]} for r in schema_rows],
                "data": [dict(zip(cols, row)) for row in data],
            }
        except Exception as exc:
            return {
                "source": source,
                "status": "partial",
                "schema": [{"name": r[0], "type": r[1]} for r in schema_rows],
                "columns": [{"name": r[0], "type": r[1]} for r in schema_rows],
                "data": [],
                "rows": [],
                "warnings": [
                    {
                        "stage": "preview_rows",
                        "reason": "sample_read_error",
                        "detail": str(exc),
                    }
                ],
            }

    # ── SQL preview ───────────────────────────────────────────────────────────

    # Blacklist of DuckDB constructs that must NEVER appear in user-supplied
    # SQL. Each pattern is checked independently so the rejection reason can
    # name the exact pattern that matched. _validate_safe_sql is only called
    # for SQL that came from the LLM/user — internal engine calls (ATTACH at
    # startup, INSTALL/LOAD of httpfs+postgres extensions in _conn) bypass
    # this gate by going straight to con.execute().
    _DANGEROUS_PATTERNS = [
        re.compile(
            r"\bread_(?:csv|text|json|blob|parquet_objects)\s*\(", re.IGNORECASE
        ),
        re.compile(r"\bATTACH\b", re.IGNORECASE),
        re.compile(r"\bDETACH\b", re.IGNORECASE),
        re.compile(r"\bINSTALL\b", re.IGNORECASE),
        re.compile(r"\bLOAD\b", re.IGNORECASE),
        re.compile(r"\bPRAGMA\b", re.IGNORECASE),
        re.compile(r"\bCOPY\s+(?:.*\s+)?FROM\b", re.IGNORECASE | re.DOTALL),
        re.compile(
            r"\bSET\s+(?:GLOBAL|SESSION|memory_limit|threads|extension_directory)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bCREATE\s+(?:TABLE|VIEW|FUNCTION|MACRO|SECRET)\b", re.IGNORECASE),
        re.compile(
            r"\bDROP\s+(?:TABLE|VIEW|FUNCTION|MACRO|SECRET|SCHEMA|DATABASE)\b",
            re.IGNORECASE,
        ),
    ]
    _READ_PARQUET_RE = re.compile(
        r"\bread_parquet\s*\(\s*(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL
    )
    _DANGEROUS_PATH_RE = re.compile(r"(?i)(file://|['\"]/(?:etc|proc|var)/)")

    def _validate_safe_sql(self, sql: str) -> None:
        policy_sql = _strip_sql_comments(sql)
        validate_table_function_query(
            policy_sql,
            expected_bucket=getattr(self, "minio_bucket", None),
        )
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

    def _validate_effective_sql(
        self,
        sql: str,
        *,
        allow_server_resolved_path_list: bool = False,
    ) -> None:
        validate_table_function_query(
            sql,
            expected_bucket=getattr(self, "minio_bucket", None),
            allow_bucket_placeholder=False,
            allow_server_resolved_path_list=allow_server_resolved_path_list,
        )

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

    def preview_sql(
        self,
        sql: str,
        limit: int = 20,
        sources: list[str] | None = None,
        user_context: dict = None,
        params: list = None,
    ) -> dict:
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
                effective_sql = self._scope_storage_sql(
                    effective_sql, sources or [], user_context
                )
                effective_sql = self._inject_latest_date(
                    effective_sql, sources or [], user_context
                )
                self._validate_scoped_storage_sql(effective_sql, user_context)
                if re.search(
                    r'(?<![A-Za-z0-9_])"?pggold"?\s*\.', effective_sql, re.IGNORECASE
                ):
                    self._pg_gold_attach(con, user_context)
                limited = f"SELECT * FROM ({effective_sql}) _q LIMIT {limit}"
                self._validate_effective_sql(
                    effective_sql, allow_server_resolved_path_list=True
                )

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
                {
                    "name": c[0],
                    "type": c[1] if len(c) > 1 and isinstance(c[1], str) else "VARCHAR",
                }
                for c in desc
            ]
            cols = [c[0] for c in desc]
            return {
                "schema": schema_rows,
                "data": [dict(zip(cols, row)) for row in data],
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
                "no files found" in lower
                and ("read_parquet" in lower or "s3://" in lower)
            ) or (
                "404" in lower
                and (
                    "lakehouse/" in lower
                    or "http://minio" in lower
                    or "minio:" in lower
                )
            )
            s3_listing_error = (
                "http get error" in lower
                and "list-type=2" in lower
                and "http 400" in lower
                and ("read_parquet" in lower or "s3://" in lower)
            )
            if s3_listing_error:
                return {
                    "code": "s3_storage_list_failed",
                    "error": (
                        "No se pudo listar Parquet en S3 para la fuente seleccionada. "
                        "Verifica que la extracción haya escrito archivos para este workspace "
                        "y que bucket, región y permisos del lakehouse estén disponibles."
                    ),
                    "raw_error": msg,
                }
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

    def get_dataset_schema(self, ds: dict, user_context: dict | None = None) -> dict:
        try:
            validate_safe_identifier(ds["name"], "dataset")
            sql = self._managed_materialized_sql(ds, user_context)
            self._validate_safe_sql(sql)
            sources = ds.get("sources") or []
            with self._duckdb_lock:
                rls_sql, rls_params = self.get_rls_filters(sql, user_context or {})
                con = self._conn()
                effective_sql = self._inject_bucket(rls_sql)
                effective_sql = self._scope_storage_sql(
                    effective_sql, sources, user_context
                )
                effective_sql = self._inject_latest_date(
                    effective_sql, sources, user_context
                )
                self._validate_scoped_storage_sql(effective_sql, user_context)
                if re.search(
                    r'(?<![A-Za-z0-9_])"?pggold"?\s*\.', effective_sql, re.IGNORECASE
                ):
                    self._pg_gold_attach(con, user_context)
                self._validate_effective_sql(
                    effective_sql, allow_server_resolved_path_list=True
                )
                rows = con.execute(
                    f"DESCRIBE SELECT * FROM ({effective_sql}) _q LIMIT 0",
                    rls_params,
                ).fetchall()
            return {
                "name": ds["name"],
                "fields": [{"name": r[0], "type": r[1]} for r in rows],
            }
        except Exception as exc:
            return {"name": ds.get("name"), "error": str(exc)}

    def _managed_materialized_sql(
        self, ds: dict, user_context: dict | None = None
    ) -> str:
        sql = ds.get("sql_def") or ds.get("sql") or ""
        if "managed_by_" not in sql or "_materializer" not in sql:
            return sql
        layer = str(ds.get("layer") or "")
        cartridge = str(ds.get("cartridge") or "")
        name = str(ds.get("name") or "")
        if layer not in {"silver", "gold"}:
            return sql
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        uri = self._latest_materialized_uri(layer, cartridge, name, user_context)
        if not uri:
            return sql
        return f"SELECT * FROM read_parquet({_sql_quote(uri)}, hive_partitioning=true, union_by_name=true)"

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
            tree = sqlglot.parse_one(sql, read="duckdb")
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
                f"SELECT * FROM ({inner_sql}) sub", read="duckdb"
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
        return new_tree.sql(dialect="duckdb"), params

    def get_rls_filters(self, sql: str, user_context: dict) -> tuple[str, list]:
        # Admin bypass requires a server-built context. A raw body claiming
        # role=admin is not enough; refinement constructs
        # _server_trusted_context only after verifying the internal caller and
        # its security_context source.
        if (
            user_context
            and str(user_context.get("role") or "").lower()
            in {"admin", "owner", "super_admin"}
            and user_context.get("_server_trusted_context")
            and not (user_context.get("workspace_id") or user_context.get("tenant_id"))
        ):
            return sql, []

        if not user_context:
            user_context = {}

        with self._duckdb_lock:
            return self._inject_rls_ast(sql, user_context)

    def query_dataset(
        self, ds: dict, filters: dict, limit: int = 100, user_context: dict = None
    ) -> dict:
        validate_safe_identifier(ds.get("name", ""), "dataset")
        sql = self._managed_materialized_sql(ds, user_context)
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

    def _gold_query_schema(
        self,
        con: duckdb.DuckDBPyConnection,
        sql: str,
    ) -> dict[str, tuple[str, str]]:
        rows = con.execute(f"DESCRIBE SELECT * FROM ({sql}) _q LIMIT 0").fetchall()
        schema: dict[str, tuple[str, str]] = {}
        for row in rows:
            name = str(row[0])
            schema[name.lower()] = (name, _duckdb_type_to_pg_type(str(row[1])))
        if not schema:
            raise ValueError("Gold query must return columns")
        return schema

    def _add_missing_gold_columns(
        self,
        table: str,
        columns: list[tuple[str, str]],
    ) -> None:
        if not columns:
            return
        validate_safe_identifier(table, "table")
        conn = self._pg_gold_conn()
        try:
            with conn.cursor() as cur:
                for name, pg_type in columns:
                    cur.execute(
                        f"ALTER TABLE public.{_pg_ident(table)} "
                        f"ADD COLUMN IF NOT EXISTS {_pg_ident(name)} {pg_type}"
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_gold_rls(self, table: str) -> None:
        validate_safe_identifier(table, "table")
        conn = self._pg_gold_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT public.omega_apply_gold_rls_for_table(%s)", (table,)
                )
            conn.commit()
        finally:
            conn.close()

    def _ensure_scoped_gold_table(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
        sql: str,
    ) -> None:
        cols = self._gold_table_columns(con, table)
        query_schema = self._gold_query_schema(con, sql)
        if cols is None:
            con.execute(
                f"CREATE TABLE pggold.{table} AS SELECT * FROM ({sql}) _q WHERE 1=0"
            )
            self._apply_gold_rls(table)
            return
        if "tenant_id" in cols and "workspace_id" in cols:
            missing = [query_schema[key] for key in query_schema if key not in cols]
            self._add_missing_gold_columns(table, missing)
            self._apply_gold_rls(table)
            return
        # Legacy unscoped gold tables cannot safely coexist with SaaS-scoped
        # writes. Recreate the table as empty with scoped columns rather than
        # silently mixing tenants in pggold.gold_<dataset>.
        con.execute(f"DROP TABLE IF EXISTS pggold.{table}")
        con.execute(
            f"CREATE TABLE pggold.{table} AS SELECT * FROM ({sql}) _q WHERE 1=0"
        )
        self._apply_gold_rls(table)

    def _replace_scoped_gold_rows(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
        sql: str,
        tenant: str,
        workspace: str,
    ) -> int:
        """Replace scoped rows without DuckDB's postgres COPY fast path.

        DuckDB's postgres extension writes INSERT ... SELECT through COPY
        under the hood. PostgreSQL rejects COPY FROM on tables with row-level
        security enabled, so the RLS backstop requires a psycopg2 write path
        with the same app.tenant_id/workspace_id settings that policies read.
        """
        validate_safe_identifier(table, "table")
        result = con.execute(f"SELECT * FROM ({sql}) _q")
        columns = [str(desc[0]) for desc in (result.description or [])]
        if not columns:
            raise ValueError("Gold query must return columns")
        rows = result.fetchall()
        table_ident = _pg_ident(table)
        column_list = ", ".join(_pg_ident(col) for col in columns)
        conn = self._pg_gold_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                cur.execute(
                    "SELECT set_config('app.workspace_id', %s, true)", (workspace,)
                )
                cur.execute(
                    f"DELETE FROM public.{table_ident} WHERE tenant_id = %s AND workspace_id = %s",
                    (tenant, workspace),
                )
                if rows:
                    execute_values(
                        cur,
                        f"INSERT INTO public.{table_ident} ({column_list}) VALUES %s",
                        rows,
                        page_size=1000,
                    )
                cur.execute(
                    f"SELECT COUNT(*) FROM public.{table_ident} WHERE tenant_id = %s AND workspace_id = %s",
                    (tenant, workspace),
                )
                row_count = int(cur.fetchone()[0])
            conn.commit()
            return row_count
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _copy_scoped_gold_table_snapshot(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
        storage_path: str,
        tenant: str,
        workspace: str,
        user_context: dict | None,
    ) -> str:
        # Schema evolution happens through psycopg2 in a separate connection.
        # Refresh DuckDB's postgres attachment before exporting, otherwise the
        # parquet snapshot can keep an old cached column list even after the
        # pggold table was altered successfully.
        self._pg_gold_attach(con, user_context)
        return self._copy_to_parquet(
            con,
            (
                f"SELECT * FROM pggold.{table} "
                f"WHERE tenant_id = {_sql_quote(tenant)} "
                f"AND workspace_id = {_sql_quote(workspace)}"
            ),
            storage_path,
        )

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
        name = ds["name"]
        layer = ds.get("layer", "silver")
        if layer not in ("silver", "gold"):
            raise ValueError("Invalid dataset layer")
        cartridge = ds.get("cartridge", "unknown")
        if cartridge == "banxico":
            try:
                from app.banxico_materializer import (
                    BANXICO_DATASETS,
                    materialize_banxico_dataset,
                )
            except ModuleNotFoundError:  # local tests import refinement.app.*
                from refinement.app.banxico_materializer import (
                    BANXICO_DATASETS,
                    materialize_banxico_dataset,
                )

            if name in BANXICO_DATASETS:
                return materialize_banxico_dataset(self, ds, user_context)
        if cartridge == "inegi":
            try:
                from app.inegi_materializer import (
                    INEGI_DATASETS,
                    materialize_inegi_dataset,
                )
            except ModuleNotFoundError:  # local tests import refinement.app.*
                from refinement.app.inegi_materializer import (
                    INEGI_DATASETS,
                    materialize_inegi_dataset,
                )

            if name in INEGI_DATASETS:
                return materialize_inegi_dataset(self, ds, user_context)
        if cartridge == "sec_edgar":
            try:
                from app.sec_edgar_materializer import (
                    SEC_DATASETS,
                    materialize_sec_dataset,
                )
            except ModuleNotFoundError:  # local tests import refinement.app.*
                from refinement.app.sec_edgar_materializer import (
                    SEC_DATASETS,
                    materialize_sec_dataset,
                )

            if name in SEC_DATASETS:
                return materialize_sec_dataset(self, ds, user_context)
        sql = ds["sql_def"]
        self._validate_safe_sql(sql)
        sources = ds.get("sources") or []
        with self._duckdb_lock:
            con = self._conn()
            storage_uri = ""
            row_count = 0

            sql = self._inject_bucket(sql)
            sql = self._scope_storage_sql(sql, sources, user_context)
            self._validate_scoped_storage_sql(sql, user_context)

            if layer == "gold":
                # ── Gold → tabla en postgres_gold ────────────────────────────────
                self._pg_gold_attach(con, user_context)
                table = f"gold_{name}"
                validate_safe_identifier(table, "table")
                effective_sql = self._inject_latest_date(sql, sources, user_context)
                self._validate_scoped_storage_sql(effective_sql, user_context)
                self._validate_effective_sql(
                    effective_sql, allow_server_resolved_path_list=True
                )
                effective_sql = self._ensure_scope_columns(
                    con, effective_sql, user_context
                )
                self._validate_effective_sql(
                    effective_sql, allow_server_resolved_path_list=True
                )
                tenant, workspace = self._scope_values(user_context)
                if not (tenant and workspace):
                    raise ValueError(
                        "Gold materialization requires tenant_id and workspace_id"
                    )
                self._ensure_scoped_gold_table(con, table, effective_sql)
                row_count = self._replace_scoped_gold_rows(
                    con,
                    table,
                    effective_sql,
                    tenant,
                    workspace,
                )
                self._apply_gold_rls(table)
                gold_parquet_path = self._snapshot_path(
                    "gold", cartridge, name, user_context
                )
                gold_storage_uri = self._copy_scoped_gold_table_snapshot(
                    con,
                    table,
                    gold_parquet_path,
                    tenant,
                    workspace,
                    user_context,
                )
                storage_uri = f"postgres_gold:{table}"
                if gold_storage_uri:
                    storage_uri = gold_storage_uri

            else:
                # ── Silver → Parquet snapshot inmutable (última extracción vía lineage) ──
                effective_sql = self._inject_latest_date(sql, sources, user_context)
                self._validate_scoped_storage_sql(effective_sql, user_context)
                self._validate_effective_sql(
                    effective_sql, allow_server_resolved_path_list=True
                )
                effective_sql = self._ensure_scope_columns(
                    con, effective_sql, user_context
                )
                self._validate_effective_sql(
                    effective_sql, allow_server_resolved_path_list=True
                )
                parquet_path = self._snapshot_path(
                    "silver", cartridge, name, user_context
                )
                storage_uri = self._copy_to_parquet(con, effective_sql, parquet_path)
                row_count = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{storage_uri}')"
                ).fetchone()[0]

            # ── Infer schema for catalog & lineage ──────────────────────────────
            try:
                if layer == "silver":
                    schema_rows = con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{storage_uri}') LIMIT 0"
                    ).fetchall()
                else:
                    self._pg_gold_attach(con, user_context)
                    schema_rows = con.execute(
                        f"DESCRIBE SELECT * FROM pggold.gold_{name} LIMIT 0"
                    ).fetchall()
                schema_fields = [{"name": r[0], "type": r[1]} for r in schema_rows]
            except Exception:
                schema_fields = []

        # ── Write lineage ────────────────────────────────────────────────────
        source_entity = (sources or [""])[0]
        latest_date = (
            self._resolve_latest_date(source_entity, user_context)
            if source_entity
            else None
        )
        self._write_lineage(
            silver_name=name,
            cartridge_id=cartridge,
            source_entity=source_entity,
            source_load_date=latest_date or ds.get("source_load_date"),
            source_batch_id=ds.get("source_batch_id"),
            sql_def=sql,
            column_mapping=ds.get("column_mapping", {}),
            layer=layer,
            row_count=row_count,
            storage_uri=storage_uri,
            user_context=user_context,
        )
        try:
            self._prune_snapshots(layer, cartridge, name, user_context)
        except Exception:
            pass

        # ── Update semantic catalog ──────────────────────────────────────────
        self._update_catalog(
            name=name,
            layer=layer,
            cartridge=cartridge,
            schema_fields=schema_fields,
            column_mapping=ds.get("column_mapping", {}),
            description=ds.get("description", ""),
            user_context=user_context,
        )

        return {
            "name": name,
            "layer": layer,
            "row_count": row_count,
            "storage_uri": storage_uri,
        }

    def _update_catalog(
        self,
        name: str,
        layer: str,
        cartridge: str,
        schema_fields: list[dict],
        column_mapping: dict,
        description: str = "",
        user_context: dict | None = None,
    ) -> None:
        """Upsert column entries into data_catalog after a successful materialization."""
        try:
            tenant_id, workspace_id = self._scope_values(user_context)
            if not workspace_id:
                return
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                    (tenant_id or "", workspace_id),
                )
                for field in schema_fields:
                    col = field["name"]
                    desc = column_mapping.get(col, "")
                    cur.execute(
                        """
                        INSERT INTO data_catalog
                            (dataset, layer, cartridge, column_name, data_type, description,
                             tenant_id, workspace_id, scope_status, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::uuid, %s::uuid, 'scoped', NOW())
                        ON CONFLICT (workspace_id, dataset, column_name) WHERE workspace_id IS NOT NULL
                        DO UPDATE
                            SET data_type   = EXCLUDED.data_type,
                                layer       = EXCLUDED.layer,
                                cartridge   = EXCLUDED.cartridge,
                                description = CASE
                                    WHEN EXCLUDED.description != '' THEN EXCLUDED.description
                                    ELSE data_catalog.description
                                END,
                                tenant_id   = EXCLUDED.tenant_id,
                                scope_status = 'scoped',
                                updated_at  = NOW()
                    """,
                        (
                            name,
                            layer,
                            cartridge,
                            col,
                            field["type"],
                            desc,
                            tenant_id or None,
                            workspace_id,
                        ),
                    )
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
        user_context: dict | None = None,
    ) -> None:
        tenant_id, workspace_id = self._scope_values(user_context)
        if not tenant_id or not workspace_id:
            raise RuntimeError("lineage scope is unavailable")
        try:
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id',%s,true),"
                    "set_config('app.workspace_id',%s,true)",
                    (tenant_id, workspace_id),
                )
                cur.execute(
                    """
                    INSERT INTO silver_lineage
                        (silver_name, cartridge_id, source_entity, source_load_date,
                         source_batch_id, sql_def, column_mapping, layer,
                         row_count, storage_uri, tenant_id, workspace_id, scope_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,'scoped')
                """,
                    (
                        silver_name,
                        cartridge_id,
                        source_entity,
                        source_load_date,
                        source_batch_id,
                        sql_def,
                        json.dumps(column_mapping),
                        layer,
                        row_count,
                        storage_uri,
                        tenant_id,
                        workspace_id,
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as exc:
            raise RuntimeError("lineage persistence failed") from exc
