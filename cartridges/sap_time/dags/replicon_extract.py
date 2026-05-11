"""
replicon_extract DAG  (Pattern B — portable, no custom container)
=================================================================
Extrae UNA entidad de Replicon API → Parquet → MinIO (Bronze).

Credenciales en Airflow UI > Admin > Connections > +:
  replicon_analytics: Conn Type=HTTP, Host=<base_url>, Password=<bearer_token>
  replicon_services:  Conn Type=HTTP, Host=<base_url>, Password=<bearer_token>

El conf de cada run puede sobreescribir parámetros:
  entity          — nombre de la entidad (requerido)
  mode            — "full" | "incremental"  (default: incremental)
  from_date       — YYYY-MM-DD (solo modo histórico)
  to_date         — YYYY-MM-DD (solo modo histórico)
"""
from __future__ import annotations

import io
import time
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.models import Variable


# Entity → connection and watermark config read from entity_config table.
# No hardcoded mappings — all driven by PostgreSQL.


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


# ── Airflow connection helper ─────────────────────────────────────────────────
# Conexiones en Airflow UI (Admin > Connections) o env var:
#   AIRFLOW_CONN_REPLICON_ANALYTICS={"conn_type":"http","host":"https://na5.replicon.com/analyticsapi","password":"TOKEN"}
#
# Campos: Conn Id = replicon_<conn_id> | Conn Type = HTTP
#         Host = URL base completa | Password = Bearer token

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


def _get_entity_config(entity: str) -> dict:
    """Read entity config from entity_config table."""
    try:
        conn = _pg_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT connection_id, watermark_field, mode "
                "FROM entity_config WHERE cartridge_id='replicon' AND entity=%s",
                (entity,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {"connection_id": row[0], "watermark_field": row[1], "mode": row[2]}
    except Exception:
        pass
    return {"connection_id": "analytics", "watermark_field": None, "mode": "full"}


def _resolve_connection(entity: str) -> tuple[str, str]:
    """Return (base_url, token) for the entity's connection via entity_config → Airflow."""
    cfg     = _get_entity_config(entity)
    conn_id = cfg.get("connection_id") or "analytics"
    return _get_connection(conn_id)


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


# ── Replicon HTTP client ───────────────────────────────────────────────────────

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
        import io as _io, requests as _req
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
            raise TimeoutError(f"Extract {extract_id} timed out after {self.poll_timeout}s")

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


# ── DAG definition ────────────────────────────────────────────────────────────

default_args = {
    "owner":       "modecissions",
    "retries":     1,
    "retry_delay": timedelta(minutes=5),
}


@dag(
    dag_id="replicon_extract",
    description="Extrae una entidad de Replicon → MinIO Bronze Parquet",
    default_args=default_args,
    schedule=None,
    catchup=False,
    tags=["replicon", "bronze", "extract"],
    params={
        "entity":    {"type": "string", "default": "User",
                      "description": "Entidad a extraer (e.g. User, TimeEntry, Project)"},
        "mode":      {"type": "string", "default": "incremental",
                      "description": "full | incremental"},
        "from_date": {"type": "string", "default": "",
                      "description": "YYYY-MM-DD — solo modo histórico"},
        "to_date":   {"type": "string", "default": "",
                      "description": "YYYY-MM-DD — solo modo histórico"},
    },
)
def replicon_extract():

    @task
    def extract(params: dict = None) -> dict:
        import pandas as pd

        conf      = params or {}
        entity    = conf.get("entity", "User")
        mode      = conf.get("mode", "incremental")
        from_date = conf.get("from_date") or None
        to_date   = conf.get("to_date")   or None

        base_url, token = _resolve_connection(entity)
        watermark_field = _get_entity_config(entity).get("watermark_field")

        client = _RepliconClient(base_url, token)
        df = client.extract_table(entity)

        if df.empty:
            return {"entity": entity, "mode": mode, "record_count": 0,
                    "status": "success", "message": "No rows returned"}

        # ── Filtro incremental ─────────────────────────────────────────────
        if mode == "incremental" and watermark_field and watermark_field in df.columns:
            last_wm = _get_watermark(entity)
            if last_wm:
                df = df[df[watermark_field].astype(str) > last_wm]

        # ── Filtro de rango de fechas ──────────────────────────────────────
        if watermark_field and watermark_field in df.columns:
            if from_date:
                df = df[df[watermark_field].astype(str) >= from_date]
            if to_date:
                df = df[df[watermark_field].astype(str) <= to_date]

        run_id      = str(uuid.uuid4())
        storage_uri = _upload_parquet(df, entity, run_id)
        count       = len(df)

        # ── Actualizar watermark ───────────────────────────────────────────
        new_wm = None
        if mode == "incremental" and watermark_field and watermark_field in df.columns:
            vals  = df[watermark_field].dropna().astype(str)
            new_wm = vals.max() if not vals.empty else None
            if new_wm:
                _set_watermark(entity, watermark_field, new_wm, run_id)

        return {
            "run_id":               run_id,
            "entity":               entity,
            "mode":                 mode,
            "record_count":         count,
            "storage_uri":          storage_uri,
            "watermark_updated_to": new_wm,
            "status":               "success",
        }

    extract()


replicon_extract()
