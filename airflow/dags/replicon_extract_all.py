"""
replicon_extract_all DAG  (Pattern B — self-contained)
=======================================================
Extrae TODAS las entidades de Replicon → Parquet → MinIO (Bronze).
Útil para la carga inicial (mode=full) o para refreshes programados.

Credenciales configuradas una sola vez en Airflow UI > Admin > Connections > +:
  Conn Id:   replicon_analytics  |  Conn Type: HTTP
  Host:      https://<tenant>.replicon.com/analyticsapi
  Password:  <bearer_token>

O via variable de entorno:
  AIRFLOW_CONN_REPLICON_ANALYTICS={"conn_type":"http","host":"https://...","password":"TOKEN"}

Parámetros opcionales (Trigger DAG w/ config):
  mode      — "full" | "incremental"  (default: incremental)
  entities  — JSON list de entidades (vacío = todas)
               ej. '["TimeEntry","Project"]'
"""
from __future__ import annotations

import io
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.models import Variable

CARTRIDGE_ID  = "replicon"
MCP_INFRA_URL = "http://mcp-infra:8010"
CONN_ID       = "analytics"   # Airflow conn_id = replicon_analytics

# Entidades con watermark → modo incremental disponible.
# Entidades sin entrada → siempre full (sin filtro de fecha).
_WATERMARK_FIELD: dict[str, str] = {
    "TimeEntry":          "last_modified",
    "Timesheet":          "last_modified",
    "ExpenseEntry":       "last_modified",
    "BillingItem":        "last_modified",
    "InvoiceItem":        "last_modified",
    "CostItem":           "last_modified",
    "ProfitItem":         "last_modified",
    "Project":            "last_modified",
    "ResourceAssignment": "last_modified",
    "User":               "last_modified",
    "Client":             "last_modified",
    "Task":               "last_modified",
}

# Solo entidades de la Analytics API.
# Las entidades de Services API (Project, User, Task, etc.)
# se extraen mediante el DAG replicon_projects_detail.
ALL_ENTITIES: list[str] = list(_WATERMARK_FIELD.keys())


# ── Airflow connection helper ─────────────────────────────────────────────────
# Airflow UI > Admin > Connections > + :
#   Conn Id:   replicon_analytics  |  Conn Type: HTTP
#   Host:      https://<tenant>.replicon.com/analyticsapi
#   Password:  <bearer_token>

def _scalar_conf_value(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, (str, int, float, bool)):
        return value
    return default


def _runtime_conf(ctx: dict, task_params: dict | None = None) -> dict:
    dag_run = ctx.get("dag_run")
    dag_conf = getattr(dag_run, "conf", None) or {}
    ctx_params = ctx.get("params") if isinstance(ctx.get("params"), dict) else {}
    task_params = task_params if isinstance(task_params, dict) else {}

    def get(name: str, default=None):
        if name in dag_conf:
            return _scalar_conf_value(dag_conf.get(name), default)
        if name in task_params:
            return _scalar_conf_value(task_params.get(name), default)
        if name in ctx_params:
            return _scalar_conf_value(ctx_params.get(name), default)
        return default

    return {
        "mode": get("mode", "incremental"),
        "entities": get("entities", ""),
    }

def _get_connection(conn_id: str) -> tuple[str, str]:
    """Return (base_url, token) from Airflow connection replicon_<conn_id>."""
    from airflow.hooks.base import BaseHook
    airflow_conn_id = f"replicon_{conn_id}"
    try:
        conn = BaseHook.get_connection(airflow_conn_id)
    except Exception:
        raise ValueError(
            f"Conexión Airflow '{airflow_conn_id}' no encontrada.\n"
            f"Configúrala en Airflow UI > Admin > Connections > +:\n"
            f"  Conn Id: {airflow_conn_id}  |  Conn Type: HTTP\n"
            f"  Host: https://<tenant>.replicon.com/analyticsapi  |  Password: <bearer_token>\n"
            f"O via env var: AIRFLOW_CONN_{airflow_conn_id.upper().replace('-','_')}="
            f'{{"conn_type":"http","host":"https://...","password":"TOKEN"}}'
        )
    base_url = conn.host or ""
    token    = conn.password or ""
    if not base_url or not token:
        raise ValueError(
            f"Conexión Airflow '{airflow_conn_id}' incompleta — falta Host o Password.\n"
            f"Airflow UI > Admin > Connections > editar '{airflow_conn_id}'"
        )
    return base_url, token


# ── mcp-infra watermark helpers ───────────────────────────────────────────────

def _watermark_get(entity: str) -> str | None:
    import requests
    try:
        r = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={"tool": "watermark_get",
                  "args": {"cartridge_id": CARTRIDGE_ID, "entity": entity}},
            timeout=10,
        )
        if r.ok:
            return r.json().get("result", {}).get("last_value")
    except Exception:
        pass
    return None


def _watermark_set(entity: str, watermark_field: str, value: str, run_id: str) -> None:
    import requests
    try:
        requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={"tool": "watermark_set",
                  "args": {
                      "cartridge_id":    CARTRIDGE_ID,
                      "entity":          entity,
                      "watermark_field": watermark_field,
                      "value":           value,
                      "run_id":          run_id,
                  }},
            timeout=10,
        )
    except Exception:
        pass


def _pipeline_run_save(dag_id: str, entity: str, **kwargs) -> None:
    import requests
    try:
        requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={"tool": "pipeline_run_save",
                  "args": {"dag_id": dag_id, "cartridge_id": CARTRIDGE_ID,
                           "entity": entity, **kwargs}},
            timeout=10,
        )
    except Exception:
        pass


# ── MinIO ─────────────────────────────────────────────────────────────────────

def _minio_client():
    from minio import Minio
    return Minio(
        endpoint=Variable.get("minio_endpoint"),
        access_key=Variable.get("minio_access_key"),
        secret_key=Variable.get("minio_secret_key"),
        secure=False,
    )


def _upload_parquet(df, entity: str, run_id: str) -> tuple[str, int]:
    """Returns (storage_uri, bytes_written)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    bucket = Variable.get("minio_bucket")
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key    = f"raw/replicon/{entity}/load_date={today}/batch_id={run_id}/{entity}.parquet"

    table = pa.Table.from_pandas(df)
    buf   = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    size = buf.getbuffer().nbytes

    client = _minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, key, buf, size, content_type="application/octet-stream")
    return f"s3://{bucket}/{key}", size


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

    def extract_analytics(self, entity: str) -> "pd.DataFrame":
        """Analytics API: async extract → poll → download CSV."""
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


# ── DAG ───────────────────────────────────────────────────────────────────────

default_args = {
    "owner":       "modecissions",
    "retries":     1,
    "retry_delay": timedelta(minutes=5),
}

DAG_ID = "replicon_extract_all"


@dag(
    dag_id=DAG_ID,
    description="Extrae TODAS las entidades de Replicon → MinIO Bronze Parquet",
    default_args=default_args,
    schedule=None,
    catchup=False,
    tags=["replicon", "bronze", "extract", "full"],
    params={
        "mode": {"type": "string", "default": "incremental",
                 "description": "full | incremental"},
        "entities": {"type": "string", "default": "",
                     "description": 'JSON list de entidades (vacío = todas). Ej: ["TimeEntry","Project"]'},
    },
)
def replicon_extract_all():

    @task
    def extract_all(params: dict = None) -> dict:
        import logging
        import pandas as pd
        from airflow.operators.python import get_current_context
        log = logging.getLogger("airflow.task")

        ctx            = get_current_context()
        airflow_run_id = ctx.get("run_id", "")

        conf = _runtime_conf(ctx, params)
        mode = conf.get("mode", "incremental")

        entities_param = conf.get("entities", "")
        if entities_param:
            try:
                entity_list = json.loads(entities_param)
            except Exception:
                entity_list = ALL_ENTITIES
        else:
            entity_list = ALL_ENTITIES

        log.info("▶ replicon_extract_all | mode=%s entities=%d airflow_run=%s",
                 mode, len(entity_list), airflow_run_id)

        # ── Conexión ──────────────────────────────────────────────────────
        base_url, token = _get_connection(CONN_ID)
        log.info("  conexión: replicon_%s → %s", CONN_ID, base_url)
        client = _RepliconClient(base_url, token)

        results = []
        errors  = []

        for entity in entity_list:
            wm_field = _WATERMARK_FIELD.get(entity)
            run_id   = str(uuid.uuid4())
            started  = datetime.now(timezone.utc)

            try:
                df = client.extract_analytics(entity)
                log.info("  [%s] filas brutas: %d", entity, len(df))

                if df.empty:
                    finished = datetime.now(timezone.utc)
                    _pipeline_run_save(DAG_ID, entity, run_id=run_id, mode=mode,
                                       airflow_dag_run_id=airflow_run_id,
                                       status="success", record_count=0,
                                       started_at=started.isoformat(),
                                       finished_at=finished.isoformat(),
                                       duration_seconds=(finished - started).total_seconds())
                    results.append({"entity": entity, "record_count": 0,
                                    "status": "success", "note": "no rows"})
                    continue

                if mode == "incremental" and wm_field and wm_field in df.columns:
                    last_wm = _watermark_get(entity)
                    if last_wm:
                        before = len(df)
                        df = df[df[wm_field].astype(str) > last_wm]
                        log.info("  [%s] filtro incremental (%s > %s): %d → %d",
                                 entity, wm_field, last_wm, before, len(df))

                storage_uri, bytes_written = _upload_parquet(df, entity, run_id)
                count = len(df)

                new_wm = None
                if mode == "incremental" and wm_field and wm_field in df.columns:
                    vals  = df[wm_field].dropna().astype(str)
                    new_wm = vals.max() if not vals.empty else None
                    if new_wm:
                        _watermark_set(entity, wm_field, new_wm, run_id)
                        log.info("  [%s] watermark → %s", entity, new_wm)

                finished = datetime.now(timezone.utc)
                duration = (finished - started).total_seconds()

                _pipeline_run_save(
                    DAG_ID, entity,
                    run_id=run_id, mode=mode, status="success",
                    airflow_dag_run_id=airflow_run_id,
                    started_at=started.isoformat(),
                    finished_at=finished.isoformat(),
                    duration_seconds=duration,
                    record_count=count,
                    bytes_written=bytes_written,
                    storage_uri=storage_uri,
                    watermark_updated_to=new_wm,
                )

                log.info("  [OK] %s: %d filas → %s (%.1fs)", entity, count, storage_uri, duration)
                results.append({
                    "entity":               entity,
                    "mode":                 mode,
                    "record_count":         count,
                    "bytes_written":        bytes_written,
                    "storage_uri":          storage_uri,
                    "watermark_updated_to": new_wm,
                    "status":               "success",
                })

            except Exception as exc:
                finished  = datetime.now(timezone.utc)
                error_msg = str(exc)
                log.error("  [FAIL] %s: %s", entity, error_msg)
                _pipeline_run_save(
                    DAG_ID, entity,
                    run_id=run_id, mode=mode, status="failed",
                    airflow_dag_run_id=airflow_run_id,
                    started_at=started.isoformat(),
                    finished_at=finished.isoformat(),
                    duration_seconds=(finished - started).total_seconds(),
                    error_message=error_msg,
                )
                errors.append({"entity": entity, "status": "failed", "error": error_msg})

        summary = {
            "total_entities": len(entity_list),
            "success":        len(results),
            "failed":         len(errors),
            "results":        results,
            "errors":         errors,
        }
        log.info("✔ replicon_extract_all | total=%d ok=%d fail=%d",
                 len(entity_list), len(results), len(errors))
        return summary

    extract_all()


replicon_extract_all()
