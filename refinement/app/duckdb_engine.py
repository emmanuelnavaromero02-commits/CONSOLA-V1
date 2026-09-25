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
import pyarrow.parquet as pq
import sqlglot
from sqlglot import exp as _sqlglot_exp

from omega_lakehouse import ObjectAlreadyExists, storage_from_env
from omega_lakehouse.checksums import sha256_file

try:
    import app.partitioned_parquet as partitioned_parquet
    from app.duckdb_runtime import connect_duckdb_runtime
    from app.sql_table_function_policy import validate_table_function_query
    from app.storage_scope_policy import has_exact_storage_scope
except ModuleNotFoundError:
    import refinement.app.partitioned_parquet as partitioned_parquet
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
DUCKDB_TEMP_DIRECTORY_RE = re.compile(r"^/[^\0'\"\n\r]*$")
DUCKDB_SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
UNBOUNDED_CGROUP_BYTES = 1 << 60
DUCKDB_CONTAINER_MEMORY_FRACTION = 0.7
DUCKDB_FALLBACK_MEMORY_LIMIT = "1GB"
DUCKDB_PRODUCTION_TEMP_DIRECTORY = "/var/lib/omega/duckdb-spill"
PARTITION_MANIFEST_READER_RE = re.compile(
    r"(\bread_parquet\s*\(\s*)'((?:s3|gs)://[^'\s]+/"
    + partitioned_parquet.PARTITION_SET_DIRECTORY
    + r"/[0-9a-f]{64}\.parquet)'",
    re.IGNORECASE,
)


def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _sql_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


_SHARED_MACRO_FILES = ("sql/talent_score_scale.sql",)


def _register_shared_macros(con: "duckdb.DuckDBPyConnection") -> None:
    root = Path(__file__).resolve().parent
    for relative in _SHARED_MACRO_FILES:
        path = root / relative
        if not path.is_file():
            raise RuntimeError("required DuckDB macro is unavailable")
        con.execute(path.read_text(encoding="utf-8"))


def _escape_sql_literal_inner(value: str) -> str:
    return (value or "").replace("'", "''")


def _duckdb_memory_limit_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_MEMORY_LIMIT_RE.fullmatch(value):
        raise ValueError("DUCKDB_MEMORY_LIMIT must look like 512MB, 1GB or 1024MiB")
    return value


def _duckdb_size_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", (value or "").strip())
    unit = match.group(2).lower() if match else ""
    if unit not in DUCKDB_SIZE_UNITS:
        raise ValueError("DuckDB sizes must look like 512MB, 1GB or 1024MiB")
    return int(float(match.group(1)) * DUCKDB_SIZE_UNITS[unit])


def _container_memory_limit_bytes(
    paths: tuple[str, ...] = CGROUP_MEMORY_LIMIT_FILES,
) -> int | None:
    for path in paths:
        try:
            raw = Path(path).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if raw.isdigit() and 0 < int(raw) < UNBOUNDED_CGROUP_BYTES:
            return int(raw)
        return None
    return None


def _effective_duckdb_memory_limit(
    configured: str, container_bytes: int | None
) -> str:
    candidates: list[tuple[int, str]] = []
    if configured:
        candidates.append((_duckdb_size_bytes(configured), configured))
    if container_bytes:
        mib = max(1, int(container_bytes * DUCKDB_CONTAINER_MEMORY_FRACTION) // 1024**2)
        candidates.append((mib * 1024**2, f"{mib}MiB"))
    if not candidates:
        return DUCKDB_FALLBACK_MEMORY_LIMIT
    return min(candidates, key=lambda item: item[0])[1]


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _duckdb_temp_directory_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_TEMP_DIRECTORY_RE.fullmatch(value):
        raise ValueError("DUCKDB_TEMP_DIRECTORY must be an absolute path")
    return value


def _duckdb_max_temp_directory_size_from_env(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not DUCKDB_MEMORY_LIMIT_RE.fullmatch(value):
        raise ValueError(
            "DUCKDB_MAX_TEMP_DIRECTORY_SIZE must look like 512MB, 1GB or 1024MiB"
        )
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
        self.pg_gold_url = os.environ.get("GOLD_DATABASE_URL", "") or self.pg_url
        self.duckdb_memory_limit = _effective_duckdb_memory_limit(
            _duckdb_memory_limit_from_env(os.environ.get("DUCKDB_MEMORY_LIMIT")),
            _container_memory_limit_bytes(),
        )
        self.duckdb_threads = _duckdb_threads_from_env(os.environ.get("DUCKDB_THREADS"))
        self.duckdb_temp_directory = _duckdb_temp_directory_from_env(
            os.environ.get("DUCKDB_TEMP_DIRECTORY")
        ) or (DUCKDB_PRODUCTION_TEMP_DIRECTORY if _is_production_env() else "")
        self.duckdb_max_temp_directory_size = _duckdb_max_temp_directory_size_from_env(
            os.environ.get("DUCKDB_MAX_TEMP_DIRECTORY_SIZE")
        )
        self._con: duckdb.DuckDBPyConnection | None = None
        self._duckdb_lock = threading.RLock()

    def _uses_aws_s3_credential_chain(self) -> bool:
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
        key_id = (os.environ.get("GCS_ACCESS_KEY_ID") or "").strip()
        secret = (os.environ.get("GCS_SECRET_ACCESS_KEY") or "").strip()
        if not key_id or not secret:
            raise ValueError(
                "GCS lakehouse refinement reads require HMAC credentials "
                "via GCS_ACCESS_KEY_ID/GCS_SECRET_ACCESS_KEY"
            )
        return key_id, secret

    def _configure_duckdb_gcs(self, con: duckdb.DuckDBPyConnection) -> None:
        key_id, secret = self._gcs_hmac_credentials()
        try:
            con.execute("SET s3_region='auto';")
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
                self._con.execute("SET preserve_insertion_order=false;")
                if self.duckdb_threads is not None:
                    self._con.execute(f"SET threads={self.duckdb_threads};")
                if self.duckdb_temp_directory:
                    try:
                        os.makedirs(self.duckdb_temp_directory, exist_ok=True)
                    except OSError as exc:
                        raise RuntimeError(
                            "DUCKDB_TEMP_DIRECTORY is not creatable: "
                            f"{self.duckdb_temp_directory}"
                        ) from exc
                    self._con.execute(
                        f"SET temp_directory={_sql_quote(self.duckdb_temp_directory)};"
                    )
                if self.duckdb_max_temp_directory_size:
                    self._con.execute(
                        "SET max_temp_directory_size="
                        f"{_sql_quote(self.duckdb_max_temp_directory_size)};"
                    )
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
                    self._con.execute("LOAD aws;")
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
                _register_shared_macros(self._con)
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
        dsn = _normalize_postgres_dsn(self.pg_url)
        try:
            con.execute(f"ATTACH {_sql_quote(dsn)} AS pgdb (TYPE postgres);")
        except Exception:
            pass
        return "pgdb"

    def _pg_gold_attach(
        self,
        con: duckdb.DuckDBPyConnection,
        user_context: dict | None = None,
    ) -> str:
        dsn = self._pg_gold_dsn(user_context)
        if not dsn:
            return "pggold"
        try:
            con.execute("DETACH pggold;")
        except Exception:
            pass
        con.execute(f"ATTACH {_sql_quote(dsn)} AS pggold (TYPE postgres);")
        return "pggold"


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
            source = self._canonical_storage_uri(source)
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
        if source.startswith(("s3://", "gs://")):
            return source
        tenant, workspace = self._scope_values(user_context)
        if tenant and workspace:
            return self._storage_uri(
                f"{source}/tenant_id={tenant}/workspace_id={workspace}/**/*.parquet"
            )
        return self._storage_uri(f"{source}/load_date=*/batch_id=*/*.parquet")

    def _bronze_read(self, source: str, user_context: dict | None = None) -> str:
        path = self._bronze_path(source, user_context)
        return f"read_parquet('{path}', hive_partitioning=true, union_by_name=true)"

    def _silver_path(
        self, cartridge: str, name: str, user_context: dict | None = None
    ) -> str:
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
        tenant, workspace = self._scope_values(user_context)

        out = sql

        def _replace_dataset_refs(
            layer: str, cartridge: str, name: str, replacement: str
        ) -> None:
            nonlocal out
            base = f"{layer}/{cartridge}/{name}"
            for key in (
                f"{base}/data.parquet",
                f"{base}/*.parquet",
                f"{base}/**/*.parquet",
            ):
                for pattern in self._storage_uri_variants(key):
                    out = out.replace(pattern, replacement)

        for source in sources or []:
            src = str(source or "").strip()
            if src.startswith("raw/"):
                try:
                    scoped = self._bronze_path(src, user_context)
                    for suffix in ("**/*.parquet", "*.parquet"):
                        for legacy in self._storage_uri_variants(f"{src}/{suffix}"):
                            out = out.replace(legacy, scoped)
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

    def _expand_partition_manifests(self, sql: str, user_context: dict | None) -> str:
        marker = f"/{partitioned_parquet.PARTITION_SET_DIRECTORY}/"
        if marker not in (sql or ""):
            return sql
        tenant, workspace = self._scope_values(user_context)
        expanded: dict[str, str] = {}

        def replace(match: re.Match) -> str:
            uri = match.group(2)
            key = self._s3_object_key(uri)
            if not key or (
                tenant and workspace and not has_exact_storage_scope(key, tenant, workspace)
            ):
                return match.group(0)
            if uri not in expanded:
                expanded[uri] = self._parquet_reader_argument(uri)
            return match.group(1) + expanded[uri]

        return PARTITION_MANIFEST_READER_RE.sub(replace, sql)

    def _partition_part_uris(self, uri: str) -> list[str] | None:
        key = self._s3_object_key(uri)
        if not key or not partitioned_parquet.is_manifest_key(key):
            return None
        manifest = partitioned_parquet.read_manifest(self.storage.get_bytes(key), key)
        base = uri[: len(uri) - len(key)]
        return [base + str(part["key"]) for part in manifest["parts"]]

    def _parquet_reader_argument(self, uri: str) -> str:
        parts = self._partition_part_uris(uri)
        if not parts:
            return _sql_quote(uri)
        return "[" + ", ".join(_sql_quote(part) for part in parts) + "]"

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
        value = str(uri or "")
        for prefix in self._storage_uri_variants(""):
            if value.startswith(prefix):
                key = value[len(prefix) :].strip("/")
                return key or None
        return None

    def _storage_scheme(self) -> str:
        provider = getattr(getattr(self.storage, "config", None), "provider", "s3")
        return "gs" if provider == "gcs" else "s3"

    def _storage_uri(self, key: str) -> str:
        clean = str(key or "").strip("/")
        base = f"{self._storage_scheme()}://{self.minio_bucket}"
        return f"{base}/{clean}" if clean else f"{base}/"

    def _storage_uri_variants(self, key: str) -> tuple[str, ...]:
        clean = str(key or "").strip("/")
        suffix = f"/{clean}" if clean else "/"
        active = self._storage_scheme()
        schemes = (active, "gs" if active == "s3" else "s3")
        return tuple(f"{scheme}://{self.minio_bucket}{suffix}" for scheme in schemes)

    def _canonical_storage_uri(self, uri: str) -> str:
        value = str(uri or "")
        for prefix in self._storage_uri_variants(""):
            if value.startswith(prefix):
                return f"{self._storage_uri('')}{value[len(prefix) :]}"
        return value

    def _delete_s3_prefix(self, uri: str) -> None:
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

    def _partition_set_key(self, parquet_path: str) -> str:
        key = self._s3_object_key(parquet_path)
        if not key or not key.endswith(".parquet"):
            raise ValueError("partitioned materialization requires managed storage")
        return f"{key[: -len('.parquet')]}/{partitioned_parquet.PARTITION_SET_DIRECTORY}"

    def _put_partition_object(
        self, local_path: Path, key: str, digest: str
    ) -> tuple[str, str]:
        try:
            result = self.storage.put_file(
                key, Path(local_path), overwrite=False, checksum_sha256=digest
            )
        except ObjectAlreadyExists:
            stat = self.storage.stat(key)
            if stat.checksum_sha256 and stat.checksum_sha256 != digest:
                raise RuntimeError("partition object checksum mismatch") from None
            return self.storage.uri_for(key), str(stat.version or "")
        return result.uri, str(result.version or "")

    def _require_stable_partition_types(
        self, con: duckdb.DuckDBPyConnection, column: str, paths: list[Path]
    ) -> None:
        listing = "[" + ", ".join(_sql_quote(str(path)) for path in paths) + "]"
        typed = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({listing}, hive_partitioning=false)"
        ).fetchall()
        hive = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({listing}, hive_partitioning=true)"
        ).fetchall()
        if [row[:2] for row in typed] != [row[:2] for row in hive]:
            raise ValueError(
                f"partition_by column {column} changes type when read as a hive "
                "partition; partition by a VARCHAR such as 'YYYY-MM'"
            )

    def _copy_partitioned_parquet(
        self,
        con: duckdb.DuckDBPyConnection,
        sql: str,
        parquet_path: str,
        partition_by: str,
    ) -> str:
        set_key = self._partition_set_key(parquet_path)
        columns = [
            str(row[0])
            for row in con.execute(
                f"DESCRIBE SELECT * FROM ({sql}) _partition_probe LIMIT 0"
            ).fetchall()
        ]
        matches = [name for name in columns if name.lower() == partition_by.lower()]
        if len(matches) != 1:
            raise ValueError(
                f"partition_by column {partition_by} is not produced by the dataset"
            )
        column = matches[0]
        validate_safe_identifier(column, "partition column")
        with tempfile.TemporaryDirectory(prefix="omega-partitioned-") as tmp:
            root = Path(tmp) / "parts"
            con.execute(
                f"COPY ({sql}) TO {_sql_quote(str(root))} (FORMAT PARQUET, "
                f'PARTITION_BY ("{column}"), WRITE_PARTITION_COLUMNS true)'
            )
            files = partitioned_parquet.local_partition_files(root, column)
            if files:
                self._require_stable_partition_types(
                    con, column, [path for _value, path in files]
                )
                schema = pq.read_schema(files[0][1])
            else:
                empty = Path(tmp) / "empty.parquet"
                con.execute(
                    f"COPY (SELECT * FROM ({sql}) _partition_schema LIMIT 0) "
                    f"TO {_sql_quote(str(empty))} (FORMAT PARQUET)"
                )
                schema = pq.read_schema(empty)
            expected = partitioned_parquet.schema_fields(schema)
            parts: list[dict] = []
            for index, (value, path) in enumerate(files):
                metadata = pq.ParquetFile(path)
                if partitioned_parquet.schema_fields(metadata.schema_arrow) != expected:
                    raise RuntimeError("partition files disagree on the dataset schema")
                digest = sha256_file(path)
                key = partitioned_parquet.part_key(set_key, column, value, index, digest)
                _uri, version = self._put_partition_object(path, key, digest)
                parts.append(
                    {
                        "key": key,
                        "value": value,
                        "checksum": digest,
                        "rows": int(metadata.metadata.num_rows),
                        "version": version,
                    }
                )
            manifest = Path(tmp) / "manifest.parquet"
            partitioned_parquet.write_manifest(manifest, schema, column, parts)
            digest = sha256_file(manifest)
            uri, _version = self._put_partition_object(
                manifest, f"{set_key}/{digest}.parquet", digest
            )
            return uri

    def _copy_to_parquet(
        self,
        con: duckdb.DuckDBPyConnection,
        sql: str,
        parquet_path: str,
        partition_by: str | None = None,
    ) -> str:
        if partition_by:
            return self._copy_partitioned_parquet(con, sql, parquet_path, partition_by)
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
            snapshots: dict[str, list[str]] = {}
            for obj in self.storage.iter_list(prefix):
                entry = str(obj.key).removeprefix(prefix).split("/", 1)[0]
                snapshots.setdefault(entry, []).append(obj.key)
            for entry in sorted(snapshots, reverse=True)[keep:]:
                for object_name in sorted(snapshots[entry]):
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
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source, user_context)
                row = con.execute(f"SELECT MAX(load_date) FROM {expr}").fetchone()
            return str(row[0]) if row and row[0] else None
        except Exception:
            return None


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
                    FROM glob('{self._storage_uri("raw/**/*.parquet")}')
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

    def _validate_safe_sql(
        self,
        sql: str,
        *,
        allow_server_resolved_publication_relation: bool = False,
    ) -> None:
        policy_sql = _strip_sql_comments(sql)
        validate_table_function_query(
            policy_sql,
            expected_bucket=getattr(self, "minio_bucket", None),
            allow_server_resolved_publication_relation=(
                allow_server_resolved_publication_relation
            ),
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
            if not path.startswith(("s3://", "gs://")):
                raise ValueError(
                    "read_parquet is only allowed for s3:// or gs:// sources"
                )

    def _validate_effective_sql(
        self,
        sql: str,
        *,
        allow_server_resolved_path_list: bool = False,
        allow_server_resolved_publication_relation: bool = False,
    ) -> None:
        validate_table_function_query(
            sql,
            expected_bucket=getattr(self, "minio_bucket", None),
            allow_bucket_placeholder=False,
            allow_server_resolved_path_list=allow_server_resolved_path_list,
            allow_server_resolved_publication_relation=(
                allow_server_resolved_publication_relation
            ),
        )

    _MAX_PREVIEW_LIMIT = 10_000
    _DEFAULT_PREVIEW_LIMIT = 20
    _STATEMENT_TIMEOUT_SECONDS = 30

    def preview_sql(
        self,
        sql: str,
        limit: int = 20,
        sources: list[str] | None = None,
        user_context: dict = None,
        params: list = None,
        *,
        allow_server_resolved_publication_relation: bool = False,
    ) -> dict:
        if limit is None or limit <= 0:
            limit = self._DEFAULT_PREVIEW_LIMIT
        elif limit > self._MAX_PREVIEW_LIMIT:
            limit = self._MAX_PREVIEW_LIMIT
        if allow_server_resolved_publication_relation:
            self._validate_safe_sql(
                sql,
                allow_server_resolved_publication_relation=True,
            )
        else:
            self._validate_safe_sql(sql)
        caller_params = list(params or [])
        try:
            with self._duckdb_lock:
                rls_sql, rls_params = self.get_rls_filters(sql, user_context)
                combined_params = rls_params + caller_params

                con = self._conn()

                effective_sql = self._inject_bucket(rls_sql)
                effective_sql = self._expand_partition_manifests(
                    self._scope_storage_sql(effective_sql, sources or [], user_context),
                    user_context,
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
                    effective_sql,
                    allow_server_resolved_path_list=True,
                    allow_server_resolved_publication_relation=True,
                )

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


    def get_dataset_schema(self, ds: dict, user_context: dict | None = None) -> dict:
        try:
            validate_safe_identifier(ds["name"], "dataset")
            sql = self._managed_materialized_sql(ds, user_context)
            publication_sql = self._is_server_resolved_publication_sql(
                ds, user_context, sql
            )
            if publication_sql:
                self._validate_safe_sql(
                    sql,
                    allow_server_resolved_publication_relation=True,
                )
            else:
                self._validate_safe_sql(sql)
            sources = ds.get("sources") or []
            with self._duckdb_lock:
                rls_sql, rls_params = self.get_rls_filters(sql, user_context or {})
                con = self._conn()
                effective_sql = self._inject_bucket(rls_sql)
                effective_sql = self._expand_partition_manifests(
                    self._scope_storage_sql(effective_sql, sources, user_context),
                    user_context,
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
                    effective_sql,
                    allow_server_resolved_path_list=True,
                    allow_server_resolved_publication_relation=True,
                )
                rows = con.execute(
                    f"DESCRIBE SELECT * FROM ({effective_sql}) _q LIMIT 0",
                    rls_params,
                ).fetchall()
            fields = [{"name": r[0], "type": r[1]} for r in rows]
            try:
                stats = self._catalog_profile_stats(ds["name"], user_context)
                for field in fields:
                    extra = stats.get(field["name"])
                    if extra:
                        field.update(extra)
            except Exception:
                pass
            return {
                "name": ds["name"],
                "fields": fields,
            }
        except Exception as exc:
            return {"name": ds.get("name"), "error": str(exc)}

    def _catalog_profile_stats(
        self, dataset: str, user_context: dict | None = None
    ) -> dict[str, dict]:
        tenant_id, workspace_id = self._scope_values(user_context)
        if not workspace_id:
            return {}
        conn = self._pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true),"
                    " set_config('app.workspace_id', %s, true)",
                    (tenant_id or "", workspace_id),
                )
                cur.execute(
                    """
                    SELECT column_name, null_rate, distinct_count,
                           min_value, max_value
                      FROM data_catalog
                     WHERE dataset = %s AND workspace_id = %s::uuid
                       AND profiled_at IS NOT NULL
                    """,
                    (dataset, workspace_id),
                )
                stats: dict[str, dict] = {}
                for name, null_rate, distinct_count, min_value, max_value in (
                    cur.fetchall()
                ):
                    stats[str(name)] = {
                        "null_rate": (
                            float(null_rate) if null_rate is not None else None
                        ),
                        "distinct_count": (
                            int(distinct_count)
                            if distinct_count is not None
                            else None
                        ),
                        "min_value": min_value,
                        "max_value": max_value,
                    }
                return stats
        finally:
            conn.close()

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
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as exc:
            raise ValueError(
                f"SQL failed AST parse — default-deny applied: {exc}"
            ) from exc
        if tree is None:
            raise ValueError("SQL produced empty AST — default-deny applied")

        params: list = []

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
        publication_sql = self._is_server_resolved_publication_sql(
            ds, user_context, sql
        )
        if publication_sql:
            self._validate_safe_sql(
                sql,
                allow_server_resolved_publication_relation=True,
            )
        else:
            self._validate_safe_sql(sql)
        filter_params = []
        if filters:
            for key in filters.keys():
                validate_safe_identifier(key, "filter")
            clauses = [f"{k} = ?" for k in filters.keys()]
            filter_params = list(filters.values())
            sql = f"SELECT * FROM ({sql}) _q WHERE {' AND '.join(clauses)}"

        return self.preview_sql(
            sql,
            limit,
            sources=ds.get("sources") or [],
            params=filter_params,
            user_context=user_context,
            allow_server_resolved_publication_relation=publication_sql,
        )

    def _is_server_resolved_publication_sql(
        self,
        ds: dict,
        user_context: dict | None,
        sql: str,
    ) -> bool:
        head_loader = getattr(self, "_published_dataset_head", None)
        sql_resolver = getattr(self, "_published_sql", None)
        if not callable(head_loader) or not callable(sql_resolver):
            return False
        try:
            head = head_loader(ds, user_context)
            return sql == sql_resolver(ds, head)
        except Exception:
            return False

    def _inject_latest_date(
        self,
        sql: str,
        sources: list[str],
        user_context: dict | None = None,
    ) -> str:
        if "{latest_date}" not in sql:
            return sql
        primary_source = sources[0] if sources else None
        if not primary_source:
            return sql.replace("{latest_date}", _escape_sql_literal_inner("1970-01-01"))
        latest = self._resolve_latest_date(primary_source, user_context)
        return sql.replace(
            "{latest_date}", _escape_sql_literal_inner(latest or "1970-01-01")
        )

    def _inject_bucket(self, sql: str) -> str:
        if "{bucket}" not in sql:
            return sql
        return sql.replace("{bucket}", self.minio_bucket)


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
        partition_by: str | None = None,
    ) -> str:
        self._pg_gold_attach(con, user_context)
        options = {"partition_by": partition_by} if partition_by else {}
        return self._copy_to_parquet(
            con,
            (
                f"SELECT * FROM pggold.{table} "
                f"WHERE tenant_id = {_sql_quote(tenant)} "
                f"AND workspace_id = {_sql_quote(workspace)}"
            ),
            storage_path,
            **options,
        )

    def materialize(self, ds: dict, user_context: dict | None = None) -> dict:
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
            except ModuleNotFoundError:
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
            except ModuleNotFoundError:
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
            except ModuleNotFoundError:
                from refinement.app.sec_edgar_materializer import (
                    SEC_DATASETS,
                    materialize_sec_dataset,
                )

            if name in SEC_DATASETS:
                return materialize_sec_dataset(self, ds, user_context)
        sql = ds["sql_def"]
        self._validate_safe_sql(sql)
        sources = ds.get("sources") or []
        partition_by = partitioned_parquet.partition_column_from_sql(sql)
        partition_options = {"partition_by": partition_by} if partition_by else {}
        with self._duckdb_lock:
            con = self._conn()
            storage_uri = ""
            row_count = 0

            sql = self._inject_bucket(sql)
            sql = self._expand_partition_manifests(
                self._scope_storage_sql(sql, sources, user_context), user_context
            )
            self._validate_scoped_storage_sql(sql, user_context)

            if layer == "gold":
                self._pg_gold_attach(con, user_context)
                table = f"gold_{name}"
                validate_safe_identifier(table, "table")
                effective_sql = self._inject_latest_date(sql, sources, user_context)
                self._validate_scoped_storage_sql(effective_sql, user_context)
                self._validate_effective_sql(
                    effective_sql,
                    allow_server_resolved_path_list=True,
                    allow_server_resolved_publication_relation=True,
                )
                effective_sql = self._ensure_scope_columns(
                    con, effective_sql, user_context
                )
                self._validate_effective_sql(
                    effective_sql,
                    allow_server_resolved_path_list=True,
                    allow_server_resolved_publication_relation=True,
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
                    **partition_options,
                )
                storage_uri = f"postgres_gold:{table}"
                if gold_storage_uri:
                    storage_uri = gold_storage_uri

            else:
                effective_sql = self._inject_latest_date(sql, sources, user_context)
                self._validate_scoped_storage_sql(effective_sql, user_context)
                self._validate_effective_sql(
                    effective_sql,
                    allow_server_resolved_path_list=True,
                    allow_server_resolved_publication_relation=True,
                )
                effective_sql = self._ensure_scope_columns(
                    con, effective_sql, user_context
                )
                self._validate_effective_sql(
                    effective_sql,
                    allow_server_resolved_path_list=True,
                    allow_server_resolved_publication_relation=True,
                )
                parquet_path = self._snapshot_path(
                    "silver", cartridge, name, user_context
                )
                storage_uri = self._copy_to_parquet(
                    con, effective_sql, parquet_path, **partition_options
                )
                silver_relation = (
                    f"read_parquet({self._parquet_reader_argument(storage_uri)})"
                    if partition_by
                    else f"read_parquet('{storage_uri}')"
                )
                row_count = con.execute(
                    f"SELECT COUNT(*) FROM {silver_relation}"
                ).fetchone()[0]

            try:
                if layer == "silver":
                    schema_rows = con.execute(
                        f"DESCRIBE SELECT * FROM {silver_relation} LIMIT 0"
                    ).fetchall()
                else:
                    self._pg_gold_attach(con, user_context)
                    schema_rows = con.execute(
                        f"DESCRIBE SELECT * FROM pggold.gold_{name} LIMIT 0"
                    ).fetchall()
                schema_fields = [{"name": r[0], "type": r[1]} for r in schema_rows]
            except Exception:
                schema_fields = []

            if schema_fields:
                try:
                    relation_expr = (
                        silver_relation if layer == "silver" else f"pggold.gold_{name}"
                    )
                    column_stats = self._profile_columns(con, relation_expr)
                    for field in schema_fields:
                        stat = column_stats.get(field["name"])
                        if stat:
                            field["null_rate"] = stat["null_rate"]
                            field["distinct_count"] = stat["distinct_count"]
                            field["min_value"] = stat["min"]
                            field["max_value"] = stat["max"]
                except Exception:
                    pass

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

    def _profile_columns(self, con, relation_expr: str) -> dict[str, dict]:
        try:
            rows = con.execute(f"SUMMARIZE SELECT * FROM {relation_expr}").fetchall()
            cols = [d[0] for d in con.description]
        except Exception:
            return {}
        idx = {name: i for i, name in enumerate(cols)}

        def _cell(row, key):
            i = idx.get(key)
            return row[i] if i is not None and i < len(row) else None

        def _num(value, cast):
            if value is None:
                return None
            try:
                return cast(value)
            except (TypeError, ValueError):
                return None

        def _text(value):
            return None if value is None else str(value)[:512]

        stats: dict[str, dict] = {}
        for row in rows:
            col = _cell(row, "column_name")
            if col is None:
                continue
            null_pct = _num(_cell(row, "null_percentage"), float)
            stats[str(col)] = {
                "null_rate": None if null_pct is None else round(null_pct / 100.0, 6),
                "distinct_count": _num(_cell(row, "approx_unique"), int),
                "min": _text(_cell(row, "min")),
                "max": _text(_cell(row, "max")),
            }
        return stats

    def validate_containment(
        self,
        from_dataset: str,
        from_column: str,
        to_dataset: str,
        to_column: str,
        user_context: dict | None = None,
    ) -> dict:
        for label, ident in (
            ("from_dataset", from_dataset),
            ("to_dataset", to_dataset),
            ("from_column", from_column),
            ("to_column", to_column),
        ):
            validate_safe_identifier(str(ident or ""), label)
        from_tbl = f"pggold.gold_{from_dataset}"
        to_tbl = f"pggold.gold_{to_dataset}"
        query = (
            f'WITH child AS ('
            f'  SELECT DISTINCT "{from_column}" AS v FROM {from_tbl} '
            f'  WHERE "{from_column}" IS NOT NULL'
            f'), parent AS ('
            f'  SELECT DISTINCT "{to_column}" AS k FROM {to_tbl} '
            f'  WHERE "{to_column}" IS NOT NULL'
            f') SELECT '
            f'  (SELECT count(*) FROM child), '
            f'  (SELECT count(*) FROM child c LEFT JOIN parent p ON c.v = p.k WHERE p.k IS NULL)'
        )
        with self._duckdb_lock:
            con = self._conn()
            self._pg_gold_attach(con, user_context)
            row = con.execute(query).fetchone()
        child_distinct = int(row[0]) if row and row[0] is not None else 0
        orphan_values = int(row[1]) if row and row[1] is not None else 0
        contained = child_distinct > 0 and orphan_values == 0
        coverage = (
            None
            if child_distinct == 0
            else round((child_distinct - orphan_values) / child_distinct, 4)
        )
        return {
            "from_dataset": from_dataset,
            "from_column": from_column,
            "to_dataset": to_dataset,
            "to_column": to_column,
            "child_distinct": child_distinct,
            "orphan_values": orphan_values,
            "coverage": coverage,
            "contained": bool(contained),
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
                    null_rate = field.get("null_rate")
                    distinct_count = field.get("distinct_count")
                    min_value = field.get("min_value")
                    max_value = field.get("max_value")
                    has_stats = any(
                        k in field
                        for k in ("null_rate", "distinct_count", "min_value", "max_value")
                    )
                    cur.execute(
                        """
                        INSERT INTO data_catalog
                            (dataset, layer, cartridge, column_name, data_type, description,
                             null_rate, distinct_count, min_value, max_value, profiled_at,
                             tenant_id, workspace_id, scope_status, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, CASE WHEN %s THEN NOW() ELSE NULL END,
                                %s::uuid, %s::uuid, 'scoped', NOW())
                        ON CONFLICT (workspace_id, dataset, column_name) WHERE workspace_id IS NOT NULL
                        DO UPDATE
                            SET data_type   = EXCLUDED.data_type,
                                layer       = EXCLUDED.layer,
                                cartridge   = EXCLUDED.cartridge,
                                description = CASE
                                    WHEN EXCLUDED.description != '' THEN EXCLUDED.description
                                    ELSE data_catalog.description
                                END,
                                -- keep prior stats when this run did not profile
                                null_rate      = CASE WHEN EXCLUDED.profiled_at IS NOT NULL THEN EXCLUDED.null_rate      ELSE data_catalog.null_rate      END,
                                distinct_count = CASE WHEN EXCLUDED.profiled_at IS NOT NULL THEN EXCLUDED.distinct_count ELSE data_catalog.distinct_count END,
                                min_value      = CASE WHEN EXCLUDED.profiled_at IS NOT NULL THEN EXCLUDED.min_value      ELSE data_catalog.min_value      END,
                                max_value      = CASE WHEN EXCLUDED.profiled_at IS NOT NULL THEN EXCLUDED.max_value      ELSE data_catalog.max_value      END,
                                profiled_at    = CASE WHEN EXCLUDED.profiled_at IS NOT NULL THEN EXCLUDED.profiled_at    ELSE data_catalog.profiled_at    END,
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
                            null_rate,
                            distinct_count,
                            min_value,
                            max_value,
                            has_stats,
                            tenant_id or None,
                            workspace_id,
                        ),
                    )
            conn.commit()
            conn.close()
        except Exception:
            pass

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
