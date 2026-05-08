"""
DuckDB Engine — Refinement service (transversal, multi-cartridge).

Silver layer:
  - Analítico (TimeEntry, BillingItem, etc.) → Parquet snapshot en MinIO
    s3://lakehouse/silver/{cartridge}/{name}/data.parquet  (sobreescrito en cada refresh)

Master layer (dimensiones pequeñas):
  - Parquet en MinIO (mismo path que silver) + tabla Postgres en el GOLD DB
    Tabla: master_{name}  en postgres_gold (modecissions_gold)

Gold layer:
  - Agregaciones / KPIs → tabla Postgres en el GOLD DB: gold_{name}

Postgres aliases dentro de DuckDB:
  - pgdb   → service DB (lineage, datasets, catalog, etc.)
  - pggold → analytical DB (master_* y gold_*)

Lineage:
  - Cada materialización escribe una fila en silver_lineage (en el service DB)
"""
from __future__ import annotations

import os
import io
import json
import re
import threading
from datetime import datetime, timezone

import duckdb
import psycopg2

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
SAFE_S3_BRONZE_TAIL_RE = re.compile(r"^[a-zA-Z0-9_./=*-]+$")


def _normalize_postgres_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


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
        # Analytical (gold + master) DB. Falls back to service DB if unset, so
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
            self._con.execute(f"""
                SET s3_endpoint='{self.minio_endpoint}';
                SET s3_access_key_id='{self.minio_access}';
                SET s3_secret_access_key='{self.minio_secret}';
                SET s3_url_style='path';
                SET s3_use_ssl={'true' if self.minio_secure else 'false'};
            """)
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
        dsn = self.pg_url.replace("postgresql+psycopg2://", "postgresql://")
        try:
            con.execute(f"ATTACH '{dsn}' AS pgdb (TYPE postgres);")
        except Exception:
            pass  # already attached
        return "pgdb"

    def _pg_gold_attach(self, con: duckdb.DuckDBPyConnection) -> str:
        """Attach analytical Postgres (pggold) and return alias."""
        dsn = self.pg_gold_url.replace("postgresql+psycopg2://", "postgresql://")
        try:
            con.execute(f"ATTACH '{dsn}' AS pggold (TYPE postgres);")
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

    def _bronze_path(self, source: str) -> str:
        source = self._validate_bronze_source(source)
        if source.startswith("s3://"):
            return source
        return f"s3://{self.minio_bucket}/{source}/**/*.parquet"

    def _bronze_read(self, source: str) -> str:
        path = self._bronze_path(source)
        return f"read_parquet('{path}', hive_partitioning=true, union_by_name=true)"

    def _silver_path(self, cartridge: str, name: str) -> str:
        """Silver es un snapshot único — un archivo por dataset, siempre sobreescrito."""
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        return f"s3://{self.minio_bucket}/silver/{cartridge}/{name}/data.parquet"

    def _resolve_latest_date(self, source: str) -> str | None:
        """Devuelve el load_date más reciente disponible en una fuente Bronze."""
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source)
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

    def get_source_schema(self, source: str) -> dict:
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source)
                rows = con.execute(f"DESCRIBE SELECT * FROM {expr} LIMIT 0").fetchall()
            return {"source": source, "fields": [{"name": r[0], "type": r[1]} for r in rows]}
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    def get_source_partitions(self, source: str) -> dict:
        """
        Returns partition values (load_date, batch_id) available in a bronze source.
        Also returns sql_latest — a ready-to-use SQL filtered to the most recent load_date.
        """
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source)
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
                "sql_latest": (
                    f"SELECT * FROM {expr} WHERE load_date = '{latest['load_date']}'"
                ) if latest else None,
            }
        except Exception as exc:
            return {"source": source, "error": str(exc)}

    def preview_source(self, source: str, limit: int = 5) -> dict:
        try:
            with self._duckdb_lock:
                con = self._conn()
                expr = self._bronze_read(source)
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

    _DANGEROUS_READ_RE = re.compile(r"\bread_(?:csv|text|json)\s*\(", re.IGNORECASE)
    _READ_PARQUET_RE = re.compile(r"\bread_parquet\s*\(\s*(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
    _DANGEROUS_PATH_RE = re.compile(r"(?i)(file://|['\"]/(?:etc|proc|var)/)")

    def _validate_safe_sql(self, sql: str) -> None:
        if self._DANGEROUS_READ_RE.search(sql):
            raise ValueError("SQL contains a blocked local/external read function")
        if self._DANGEROUS_PATH_RE.search(sql):
            raise ValueError("SQL contains a blocked local file path")
        for match in self._READ_PARQUET_RE.finditer(sql):
            path = match.group(2).strip()
            if not path.startswith("s3://"):
                raise ValueError("read_parquet is only allowed for s3:// sources")

    def preview_sql(self, sql: str, limit: int = 20, sources: list[str] | None = None, user_context: dict = None, params: list = None) -> dict:
        self._validate_safe_sql(sql)
        try:
            with self._duckdb_lock:
                if params is None:
                    sql, params = self.get_rls_filters(sql, user_context)

                # Using cached connection. The user requested read_only if possible, but DuckDB doesn't allow changing it on the fly.
                con = self._conn()

                effective_sql = self._inject_bucket(self._inject_latest_date(sql, sources or []))
                limited = f"SELECT * FROM ({effective_sql}) _q LIMIT {limit}"

                cursor = con.execute(limited, params)
                desc = cursor.description
                data = cursor.fetchall()

            # duckdb Python API description returns (name, type_code, display_size, internal_size, precision, scale, null_ok)
            # type_code is usually None or unhelpful in DuckDB, but we can return "UNKNOWN" or just map it as string.
            # Actually, `cursor.description` in duckdb returns types like 'VARCHAR' in the second tuple item in newer duckdb versions.
            # For robustness, we will extract it if available or fallback.
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
            return {"error": str(exc)}

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


    def get_rls_filters(self, sql: str, user_context: dict) -> tuple[str, list]:
        if user_context and user_context.get("role") == "admin":
            return sql, []

        if not user_context:
            user_context = {}

        params = []

        def replacer(match):
            table = match.group(0)
            table_name = table.split('.')[-1]
            try:
                validate_safe_identifier(table_name, "table")
                con = self._conn()
                schema_rows = con.execute(f"DESCRIBE SELECT * FROM pggold.{table_name} LIMIT 0").fetchall()
                cols = [r[0].lower() for r in schema_rows]
            except Exception:
                cols = []

            filters = []
            if 'tenant_id' in cols:
                filters.append("tenant_id = ?")
                params.append(str(user_context.get("tenant_id") or ""))
            elif 'workspace_id' in cols:
                filters.append("workspace_id = ?")
                params.append(str(user_context.get("workspace_id") or ""))
            elif 'project_id' in cols:
                filters.append("project_id = ?")
                params.append(str(user_context.get("project_id") or ""))
            elif 'user_id' in cols:
                filters.append("user_id = ?")
                params.append(str(user_context.get("id") or ""))
            elif 'revenue_manager' in cols:
                filters.append("(revenue_manager = ? OR revenue_manager = ? OR revenue_manager = 'N/D')")
                params.extend([str(user_context.get("email", "")), str(user_context.get("name") or "")])

            if not filters:
                return f"(SELECT * FROM {table} WHERE 1=0)"

            if filters:
                return f"(SELECT * FROM {table} WHERE {' OR '.join(filters)})"

            return table

        with self._duckdb_lock:
            new_sql = re.sub(r'pggold\.[a-zA-Z0-9_]+', replacer, sql, flags=re.IGNORECASE)
        return new_sql, params

    def apply_rls(self, sql: str, user_context: dict) -> str:
        # We must not use this unsafe fallback. Any caller MUST use get_rls_filters to get params, OR use preview_sql/query_dataset.
        # But if anything calls apply_rls directly and expects a string without params, we have to handle it carefully.
        # Currently only query_dataset and preview_sql call apply_rls, and I updated them to use get_rls_filters and params.
        # Let's remove this danger loop entirely and just return the string if there are no params, else raise.
        sql_with_placeholders, params = self.get_rls_filters(sql, user_context)
        if params:
             raise ValueError("apply_rls cannot safely return a parameterized string. Use get_rls_filters instead.")
        return sql_with_placeholders

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

        with self._duckdb_lock:
            rls_sql, rls_params = self.get_rls_filters(sql, user_context)
        combined_params = rls_params + filter_params
        return self.preview_sql(rls_sql, limit, params=combined_params)

    def _inject_latest_date(self, sql: str, sources: list[str]) -> str:
        """
        Sustituye el placeholder {latest_date} en el SQL por el load_date más
        reciente de la primera fuente Bronze. Si el SQL ya NO usa el placeholder,
        lo devuelve sin modificar.
        """
        if "{latest_date}" not in sql:
            return sql
        primary_source = sources[0] if sources else None
        if not primary_source:
            return sql.replace("{latest_date}", "1970-01-01")
        latest = self._resolve_latest_date(primary_source)
        return sql.replace("{latest_date}", latest or "1970-01-01")

    def _inject_bucket(self, sql: str) -> str:
        """Sustituye el placeholder {bucket} en el SQL por el bucket configurado.
        Esto desacopla los datasets del nombre concreto del bucket (que cambia
        entre local 'lakehouse' y AWS 'modecissions-lakehouse-XXXXXX')."""
        if "{bucket}" not in sql:
            return sql
        return sql.replace("{bucket}", self.minio_bucket)

    # ── Materialization ───────────────────────────────────────────────────────

    def materialize(self, ds: dict) -> dict:
        """
        Materialize a dataset to silver or gold.

        ds fields:
          name         — dataset name
          sql_def      — transformation SQL
          layer        — "silver" | "master" | "gold"
          cartridge    — source cartridge id (e.g. "replicon")
          sources      — list of bronze source paths
          column_mapping — {src_col: business_term, ...} (optional, for lineage)
          source_load_date — partition date of the bronze source (for lineage)
          source_batch_id  — batch_id of the bronze source (for lineage)
        """
        validate_safe_identifier(ds["name"], "dataset")
        validate_safe_identifier(ds.get("cartridge", "unknown"), "cartridge")
        with self._duckdb_lock:
            con = self._conn()
            name        = ds["name"]
            layer       = ds.get("layer", "silver")
            sql         = ds["sql_def"]
            cartridge   = ds.get("cartridge", "unknown")
            sources     = ds.get("sources") or []
            storage_uri = ""
            row_count   = 0

            sql = self._inject_bucket(sql)

            if layer == "gold":
                # ── Gold → tabla en postgres_gold ────────────────────────────────
                self._pg_gold_attach(con)
                table = f"gold_{name}"
                validate_safe_identifier(table, "table")
                con.execute(f"CREATE OR REPLACE TABLE pggold.{table} AS ({sql})")
                row_count = con.execute(f"SELECT COUNT(*) FROM pggold.{table}").fetchone()[0]
                storage_uri = f"postgres_gold:{table}"

            elif layer == "master":
                # ── Master → Parquet único + tabla en postgres_gold (dimensión) ──
                effective_sql = self._inject_latest_date(sql, sources)
                parquet_path  = self._silver_path(cartridge, name)
                con.execute(f"COPY ({effective_sql}) TO '{parquet_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
                row_count = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{parquet_path}')"
                ).fetchone()[0]
                storage_uri = parquet_path

                # Dimensión también en postgres_gold para joins con hechos gold
                self._pg_gold_attach(con)
                table = f"master_{name}"
                validate_safe_identifier(table, "table")
                con.execute(f"CREATE OR REPLACE TABLE pggold.{table} AS ({effective_sql})")

            else:
                # ── Silver → Parquet único, siempre sobreescrito (última extracción) ──
                effective_sql = self._inject_latest_date(sql, sources)
                parquet_path  = self._silver_path(cartridge, name)
                con.execute(f"COPY ({effective_sql}) TO '{parquet_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
                row_count = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{parquet_path}')"
                ).fetchone()[0]
                storage_uri = parquet_path

            # ── Infer schema for catalog & lineage ──────────────────────────────
            try:
                if layer in ("silver", "master"):
                    parquet_path = self._silver_path(cartridge, name)
                    schema_rows  = con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}') LIMIT 0"
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
        latest_date   = self._resolve_latest_date(source_entity) if source_entity else None
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
