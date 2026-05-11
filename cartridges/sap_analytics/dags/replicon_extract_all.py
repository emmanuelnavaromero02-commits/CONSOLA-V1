"""
replicon_extract_all DAG  (Pattern B — portable, no custom container)
=====================================================================
Extrae TODAS las entidades del cartucho Replicon en secuencia.
Útil para la carga inicial (mode=full) o para refreshes programados.

Credenciales en Airflow UI > Admin > Connections > +:
  replicon_analytics: Conn Type=HTTP, Host=<base_url>, Password=<bearer_token>
  replicon_services:  Conn Type=HTTP, Host=<base_url>, Password=<bearer_token>

Parámetros opcionales via conf:
  mode      — "full" | "incremental"  (default: incremental)
  entities  — lista JSON de entidades a extraer  (default: todas)
"""
from __future__ import annotations

import io
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.models import Variable

# Entity list is read from entity_config table at runtime — no hardcoded list.


# ── Postgres helpers ──────────────────────────────────────────────────────────

def _pg_conn():
    import psycopg2
    return psycopg2.connect(Variable.get("postgres_conn"))


def _get_watermark(entity: str) -> str | None:
    try:
        conn = _pg_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_watermark_value FROM entity_watermarks "
                "WHERE cartridge_id='replicon' AND entity_name=%s", (entity,)
            )
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _set_watermark(entity: str, field: str, value: str, run_id: str) -> None:
    try:
        conn = _pg_conn()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO entity_watermarks
                       (cartridge_id, entity_name, watermark_field,
                        last_watermark_value, last_run_id, updated_at)
                   VALUES ('replicon', %s, %s, %s, %s, NOW())
                   ON CONFLICT (cartridge_id, entity_name) DO UPDATE
                   SET watermark_field=%s, last_watermark_value=%s,
                       last_run_id=%s, updated_at=NOW()""",
                (entity, field, value, run_id, field, value, run_id),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── DB entity loader ──────────────────────────────────────────────────────────

def _load_entities(override_names: set | None = None) -> list[dict]:
    """
    Load entity definitions from entity_config table.
    Returns list of dicts with entity, mode, watermark, connection_id.
    Optionally filtered to override_names.
    """
    conn = _pg_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT entity, mode, watermark_field, connection_id "
            "FROM entity_config "
            "WHERE cartridge_id='replicon' AND enabled=TRUE "
            "ORDER BY entity",
        )
        rows = cur.fetchall()
    conn.close()
    result = [
        {"entity": r[0], "mode": r[1], "watermark": r[2], "connection": r[3] or "analytics"}
        for r in rows
    ]
    if override_names:
        result = [e for e in result if e["entity"] in override_names]
    return result


# ── Airflow connection helper ─────────────────────────────────────────────────
# Airflow UI > Admin > Connections > + :
#   Conn Id: replicon_analytics  |  Conn Type: HTTP
#   Host: https://na5.replicon.com/analyticsapi  |  Password: <bearer_token>

def _get_connection(conn_id: str) -> tuple[str, str]:
    """Return (base_url, token) from Airflow connection replicon_<conn_id>."""
    from airflow.hooks.base import BaseHook
    airflow_conn_id = f"replicon_{conn_id}"
    try:
        conn = BaseHook.get_connection(airflow_conn_id)
    except Exception:
        raise ValueError(
            f"Conexión Airflow '{airflow_conn_id}' no encontrada.\n"
            f"Airflow UI > Admin > Connections > +:\n"
            f"  Conn Id: {airflow_conn_id}  |  Conn Type: HTTP\n"
            f"  Host: https://na5.replicon.com/analyticsapi  |  Password: <bearer_token>"
        )
    base_url = conn.host or ""
    token    = conn.password or ""
    if not base_url or not token:
        raise ValueError(
            f"Conexión Airflow '{airflow_conn_id}' incompleta — falta Host o Password."
        )
    return base_url, token


# ── MinIO helpers ─────────────────────────────────────────────────────────────

def _minio_client():
    from minio import Minio
    return Minio(
        endpoint=Variable.get("minio_endpoint"),
        access_key=Variable.get("minio_access_key"),
        secret_key=Variable.get("minio_secret_key"),
        secure=False,
    )


def _upload_parquet(df, entity: str, run_id: str) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    bucket = Variable.get("minio_bucket")
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key    = f"raw/replicon/{entity}/load_date={today}/batch_id={run_id}/{entity}.parquet"

    table = pa.Table.from_pandas(df)
    buf   = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)

    client = _minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, key, buf, buf.getbuffer().nbytes,
                      content_type="application/octet-stream")
    return f"s3://{bucket}/{key}"


# ── Replicon HTTP client ──────────────────────────────────────────────────────

class _RepliconClient:
    RETRY     = 5
    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, base_url: str, token: str,
                 poll_interval: float = 2.0, poll_timeout: int = 300):
        import requests as _req
        self._s = _req.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })
        self.base          = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.poll_timeout  = poll_timeout

    def _get(self, path: str) -> dict:
        import requests as _req
        url, delay = f"{self.base}{path}", 1.0
        for _ in range(self.RETRY):
            try:
                r = self._s.get(url, timeout=60)
            except (_req.ConnectionError, _req.Timeout):
                time.sleep(delay); delay *= 2; continue
            if r.status_code in self.RETRYABLE:
                time.sleep(delay); delay = min(delay * 2, 60); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET {url} failed after {self.RETRY} attempts")

    def _post(self, path: str, body: dict) -> dict:
        import requests as _req
        url, delay = f"{self.base}{path}", 1.0
        for _ in range(self.RETRY):
            try:
                r = self._s.post(url, json=body, timeout=60)
            except (_req.ConnectionError, _req.Timeout):
                time.sleep(delay); delay *= 2; continue
            if r.status_code in self.RETRYABLE:
                time.sleep(delay); delay = min(delay * 2, 60); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"POST {url} failed after {self.RETRY} attempts")

    def extract_table(self, entity: str) -> "pd.DataFrame":
        import io as _io
        import requests as _req
        import pandas as pd

        extract_id = self._post("/extracts", {
            "target": {"type": "download", "format": "csv"},
            "tables": [{"tableId": entity}],
        })["extractId"]

        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            data = self._get(f"/extracts/{extract_id}")
            if data["status"] == "completed":
                break
            if data["status"] == "failed":
                raise RuntimeError(f"Extract {extract_id} failed: {data}")
            time.sleep(self.poll_interval)
        else:
            raise TimeoutError(f"Extract {extract_id} timed out")

        frames = []
        for url in (data.get("dataUrls") or {}).values():
            r = _req.get(url, timeout=120)
            r.raise_for_status()
            frames.append(pd.read_csv(_io.StringIO(r.text), low_memory=False))

        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)
        df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_")
                      for c in df.columns]
        return df


# ── Single-entity extraction helper ─────────────────────────────────────────

def _extract_one(client: _RepliconClient, entity: str,
                 mode: str, watermark_field: str | None) -> dict:
    import pandas as pd

    df = client.extract_table(entity)

    if df.empty:
        return {"entity": entity, "record_count": 0, "status": "success",
                "message": "No rows returned"}

    if mode == "incremental" and watermark_field and watermark_field in df.columns:
        last_wm = _get_watermark(entity)
        if last_wm:
            df = df[df[watermark_field].astype(str) > last_wm]

    run_id      = str(uuid.uuid4())
    storage_uri = _upload_parquet(df, entity, run_id)
    count       = len(df)

    new_wm = None
    if mode == "incremental" and watermark_field and watermark_field in df.columns:
        vals  = df[watermark_field].dropna().astype(str)
        new_wm = vals.max() if not vals.empty else None
        if new_wm:
            _set_watermark(entity, watermark_field, new_wm, run_id)

    return {
        "entity": entity, "mode": mode, "record_count": count,
        "storage_uri": storage_uri, "watermark_updated_to": new_wm,
        "status": "success",
    }


# ── DAG definition ────────────────────────────────────────────────────────────

default_args = {
    "owner":       "modecissions",
    "retries":     1,
    "retry_delay": timedelta(minutes=5),
}


@dag(
    dag_id="replicon_extract_all",
    description="Extrae TODAS las entidades de Replicon → MinIO Bronze Parquet",
    default_args=default_args,
    schedule=None,
    catchup=False,
    tags=["replicon", "bronze", "extract", "full"],
    params={
        "mode": {"type": "string", "default": "incremental",
                 "description": "full | incremental (aplica a entidades incrementales)"},
        "entities": {"type": "string", "default": "",
                     "description": "JSON list de entidades a extraer. Vacío = todas."},
    },
)
def replicon_extract_all():

    @task
    def extract_all(params: dict = None) -> dict:
        conf = params or {}
        mode = conf.get("mode", "incremental")

        # Load entity list from entity_config (single source of truth)
        entities_override = conf.get("entities", "")
        override_names = None
        if entities_override:
            try:
                override_names = set(json.loads(entities_override))
            except Exception:
                pass

        entity_list = _load_entities(override_names)
        if not entity_list:
            raise ValueError(
                "No hay entidades registradas para el cartucho 'replicon'. "
                "Ejecuta el seed SQL: infra/init/02_replicon_seed.sql"
            )

        # Validate all needed connections are configured in Vault
        needed_conns = {e["connection"] for e in entity_list}
        missing = []
        conn_cache: dict[str, _RepliconClient] = {}

        for conn_id in needed_conns:
            try:
                base_url, token = _get_connection(conn_id)
                conn_cache[conn_id] = _RepliconClient(base_url, token)
            except Exception as exc:
                missing.append(f"{conn_id}: {exc}")

        if missing:
            raise ValueError(
                "Conexiones sin configurar en Vault:\n" + "\n".join(missing)
            )

        results = []
        errors  = []

        for spec in entity_list:
            entity          = spec["entity"]
            entity_mode     = mode if spec.get("mode") == "incremental" else "full"
            watermark_field = spec.get("watermark")
            conn_id         = spec.get("connection") or "analytics"
            client          = conn_cache.get(conn_id) or conn_cache.get("analytics")
            try:
                r = _extract_one(client, entity, entity_mode, watermark_field)
                results.append(r)
                print(f"[OK] {entity}: {r['record_count']:,} rows → {r['storage_uri']}")
            except Exception as exc:
                err = {"entity": entity, "status": "failed", "error": str(exc)}
                errors.append(err)
                print(f"[FAIL] {entity}: {exc}")

        return {
            "total_entities": len(entity_list),
            "success":        len(results),
            "failed":         len(errors),
            "results":        results,
            "errors":         errors,
        }

    extract_all()


replicon_extract_all()
