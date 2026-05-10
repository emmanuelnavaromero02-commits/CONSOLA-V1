"""
replicon_extract DAG
====================
Extrae UNA entidad de Replicon Analytics API → Parquet → MinIO Bronze.

Tareas:
  1. extract        — llama a la API, aplica filtros, sube Parquet a Bronze
  2. update_watermark — actualiza el watermark solo si fue incremental y exitoso
  3. save_stats     — registra el run en pipeline_runs (independiente del watermark)
  4. trigger_silver — le avisa al Refinement Engine que refresque datasets dependientes

Si extract falla → Airflow reintenta solo esa tarea.
Si update_watermark o save_stats fallan → el dato ya está en Bronze; se reintenta solo esa tarea.
Si trigger_silver falla → los datos están en Bronze y el watermark actualizado; se reintenta solo el refresh.

Credenciales en Airflow UI > Admin > Connections:
  Conn Id:   replicon_analytics  |  Conn Type: HTTP
  Host:      https://<tenant>.replicon.com/analyticsapi
  Password:  <bearer_token>
"""
from __future__ import annotations

import io
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.models import Variable

CARTRIDGE_ID   = "replicon"
MCP_INFRA_URL  = "http://mcp-infra:8010"
REFINEMENT_URL = "http://refinement:8500"
CONN_ID        = "analytics"

# Entidades con soporte incremental (campo watermark en la Analytics API)
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
    # full-only (sin watermark en la API): Department, Role, Activity,
    # ProjectTeamMember, ProjectDetail, ProjectAudit
}

# Timeout de polling por entidad (segundos). Entidades grandes necesitan más tiempo.
_POLL_TIMEOUT: dict[str, int] = {
    "TimeEntry":   900,
    "CostItem":    900,
    "ProfitItem":  900,
    "BillingItem": 600,
}
_POLL_TIMEOUT_DEFAULT = 300

DAG_ID = "replicon_extract"

default_args = {
    "owner":       "modecissions",
    "retries":     2,
    "retry_delay": timedelta(minutes=3),
}


# ── Helpers (módulo-level, no son tareas de Airflow) ─────────────────────────

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
        "entity": get("entity", "User"),
        "mode": get("mode", "incremental"),
        "from_date": get("from_date"),
        "to_date": get("to_date"),
    }

def _get_connection(conn_id: str) -> tuple[str, str]:
    """Devuelve (base_url, token) desde una Airflow Connection replicon_<conn_id>."""
    from airflow.hooks.base import BaseHook
    airflow_conn_id = f"replicon_{conn_id}"
    try:
        conn = BaseHook.get_connection(airflow_conn_id)
    except Exception:
        raise ValueError(
            f"Conexión '{airflow_conn_id}' no encontrada. "
            f"Configúrala en Airflow UI > Admin > Connections."
        )
    base_url = (conn.host or "").rstrip("/")
    token    = conn.password or ""
    if not base_url or not token:
        raise ValueError(f"Conexión '{airflow_conn_id}' incompleta — falta Host o Password.")
    return base_url, token


def _watermark_get(entity: str) -> str | None:
    import requests
    try:
        r = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={"tool": "watermark_get",
                  "args": {"cartridge_id": CARTRIDGE_ID, "entity": entity}},
            timeout=10,
        )
        return (r.json().get("result") or {}).get("last_value") if r.ok else None
    except Exception:
        return None


def _watermark_set(entity: str, field: str, value: str, run_id: str) -> None:
    import requests
    try:
        requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={"tool": "watermark_set",
                  "args": {"cartridge_id": CARTRIDGE_ID, "entity": entity,
                           "watermark_field": field, "value": value, "run_id": run_id}},
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


def _minio_client():
    from minio import Minio
    return Minio(
        endpoint=Variable.get("minio_endpoint"),
        access_key=Variable.get("minio_access_key"),
        secret_key=Variable.get("minio_secret_key"),
        secure=False,
    )


def _upload_parquet(df, entity: str) -> tuple[str, int]:
    """Sube df como Parquet a Bronze. Sobreescribe la partición de hoy. Devuelve (uri, bytes)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    bucket = Variable.get("minio_bucket")
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prefix = f"raw/replicon/{entity}/load_date={today}/"
    key    = f"{prefix}data.parquet"

    table = pa.Table.from_pandas(df)
    buf   = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    size = buf.getbuffer().nbytes

    client = _minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)
    client.put_object(bucket, key, buf, size, content_type="application/octet-stream")
    return f"s3://{bucket}/{key}", size


class _RepliconClient:
    """Cliente HTTP para la Analytics API de Replicon con retry y backoff."""

    RETRY     = 5
    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, base_url: str, token: str,
                 poll_interval: float = 3.0, poll_timeout: int = _POLL_TIMEOUT_DEFAULT):
        import requests as _req
        self._s = _req.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })
        self.base          = base_url
        self.poll_interval = poll_interval
        self.poll_timeout  = poll_timeout

    def _request(self, method: str, path: str, **kwargs) -> dict:
        import requests as _req
        url, delay = f"{self.base}{path}", 1.0
        for attempt in range(1, self.RETRY + 1):
            try:
                r = self._s.request(method, url, timeout=60, **kwargs)
            except (_req.ConnectionError, _req.Timeout) as exc:
                if attempt == self.RETRY:
                    raise RuntimeError(f"{method} {url} falló tras {self.RETRY} intentos") from exc
                time.sleep(delay); delay *= 2
                continue
            if r.status_code in self.RETRYABLE:
                time.sleep(min(delay, 60)); delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"{method} {url} agotó reintentos")

    def extract_analytics(self, entity: str) -> "pd.DataFrame":
        """Inicia extracción async, hace polling hasta completar, descarga CSV."""
        import io as _io
        import pandas as pd
        import requests as _req

        extract_id = self._request("POST", "/extracts", json={
            "target": {"type": "download", "format": "csv"},
            "tables": [{"tableId": entity}],
        })["extractId"]

        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            data = self._request("GET", f"/extracts/{extract_id}")
            if data["status"] == "completed":
                break
            if data["status"] == "failed":
                raise RuntimeError(f"Extract {extract_id} falló: {data}")
            time.sleep(self.poll_interval)
        else:
            raise TimeoutError(
                f"Extract {extract_id} timeout tras {self.poll_timeout}s. "
                f"Considera aumentar _POLL_TIMEOUT para {entity}."
            )

        frames = []
        for url in (data.get("dataUrls") or {}).values():
            r = _req.get(url, timeout=300)
            r.raise_for_status()
            frames.append(pd.read_csv(_io.StringIO(r.text), low_memory=False))

        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── DAG ───────────────────────────────────────────────────────────────────────

@dag(
    dag_id=DAG_ID,
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
                      "description": "YYYY-MM-DD — fuerza fecha inicio (opcional)"},
        "to_date":   {"type": "string", "default": "",
                      "description": "YYYY-MM-DD — fuerza fecha fin (opcional)"},
    },
)
def replicon_extract():

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def extract(params: dict = None) -> dict:
        """
        Llama a la Analytics API de Replicon, aplica filtros incrementales
        y sube el Parquet resultante a MinIO Bronze.
        Devuelve un dict de resultado para las tareas siguientes.
        """
        import logging
        import pandas as pd
        from airflow.operators.python import get_current_context

        log = logging.getLogger("airflow.task")
        ctx           = get_current_context()
        airflow_run_id = ctx.get("run_id", "")

        conf      = _runtime_conf(ctx, params)
        entity    = conf.get("entity", "User")
        mode      = conf.get("mode", "incremental")
        from_date = conf.get("from_date") or None
        to_date   = conf.get("to_date")   or None
        run_id    = str(uuid.uuid4())
        started   = datetime.now(timezone.utc)

        log.info("▶ extract | entity=%s mode=%s run_id=%s", entity, mode, run_id)

        base_url, token = _get_connection(CONN_ID)
        client = _RepliconClient(
            base_url, token,
            poll_timeout=_POLL_TIMEOUT.get(entity, _POLL_TIMEOUT_DEFAULT),
        )

        df = client.extract_analytics(entity)
        log.info("  filas brutas: %d", len(df))

        if df.empty:
            return {
                "run_id": run_id, "entity": entity, "mode": mode,
                "airflow_run_id": airflow_run_id,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "record_count": 0, "bytes_written": 0,
                "storage_uri": None, "new_watermark": None,
                "status": "success",
            }

        # Normalizar columnas: quitar espacios, lowercase
        df.columns = [
            c.strip().lower().replace(" ", "_").replace("-", "_")
            for c in df.columns
        ]

        wm_field = _WATERMARK_FIELD.get(entity)
        wm_field_norm = wm_field.lower().replace(" ", "_") if wm_field else None

        # Filtro incremental
        if mode == "incremental" and wm_field_norm and wm_field_norm in df.columns:
            last_wm = _watermark_get(entity)
            if last_wm:
                before = len(df)
                df = df[df[wm_field_norm].astype(str) > last_wm]
                log.info("  filtro incremental (%s > %s): %d → %d filas",
                         wm_field_norm, last_wm, before, len(df))

        # Filtros de fecha explícitos
        if wm_field_norm and wm_field_norm in df.columns:
            col = df[wm_field_norm].astype(str)
            if from_date:
                df = df[col >= from_date]
            if to_date:
                df = df[col <= to_date]

        # Calcular nuevo watermark antes de subir
        new_watermark = None
        if wm_field_norm and wm_field_norm in df.columns and not df.empty:
            vals = df[wm_field_norm].dropna().astype(str)
            new_watermark = vals.max() if not vals.empty else None

        storage_uri, bytes_written = _upload_parquet(df, entity)
        finished = datetime.now(timezone.utc)
        log.info("✔ extract completado | %s | %d filas | %d bytes | %.1fs",
                 entity, len(df), bytes_written, (finished - started).total_seconds())

        return {
            "run_id":         run_id,
            "entity":         entity,
            "mode":           mode,
            "airflow_run_id": airflow_run_id,
            "started_at":     started.isoformat(),
            "finished_at":    finished.isoformat(),
            "duration_seconds": (finished - started).total_seconds(),
            "record_count":   len(df),
            "bytes_written":  bytes_written,
            "storage_uri":    storage_uri,
            "new_watermark":  new_watermark,
            "wm_field":       wm_field_norm,
            "status":         "success",
        }

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def update_watermark(result: dict) -> dict:
        """
        Actualiza el watermark solo si la extracción fue incremental y produjo datos.
        Tarea separada para que un fallo no reintente toda la extracción.
        """
        import logging
        log = logging.getLogger("airflow.task")

        new_wm   = result.get("new_watermark")
        wm_field = result.get("wm_field")
        entity   = result["entity"]
        mode     = result["mode"]

        if mode == "incremental" and new_wm and wm_field:
            _watermark_set(entity, wm_field, new_wm, result["run_id"])
            log.info("  watermark actualizado: %s.%s = %s", entity, wm_field, new_wm)
        else:
            log.info("  watermark omitido (mode=%s, new_wm=%s)", mode, new_wm)

        return result

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def save_stats(result: dict) -> dict:
        """
        Registra el run en pipeline_runs vía mcp-infra.
        Independiente del watermark: si falla solo se reintenta el registro.
        """
        _pipeline_run_save(
            DAG_ID, result["entity"],
            run_id=result["run_id"],
            mode=result["mode"],
            status=result["status"],
            airflow_dag_run_id=result.get("airflow_run_id", ""),
            started_at=result["started_at"],
            finished_at=result["finished_at"],
            duration_seconds=result.get("duration_seconds"),
            record_count=result.get("record_count", 0),
            bytes_written=result.get("bytes_written", 0),
            storage_uri=result.get("storage_uri"),
            watermark_updated_to=result.get("new_watermark"),
        )
        return result

    @task(retries=3, retry_delay=timedelta(minutes=2))
    def trigger_silver(result: dict) -> dict:
        """
        Notifica al Refinement Engine que refresque los datasets Silver/Master
        cuya fuente sea este entity en Bronze.
        Si el engine no está disponible, el dato ya está en Bronze — se reintenta solo el refresh.
        """
        import logging
        import requests
        log = logging.getLogger("airflow.task")

        storage_uri = result.get("storage_uri")
        entity      = result["entity"]

        if not storage_uri or result.get("record_count", 0) == 0:
            log.info("  trigger_silver omitido (sin datos nuevos)")
            return {"refreshed": 0, "results": []}

        source = f"raw/replicon/{entity}"
        try:
            r = requests.post(
                f"{REFINEMENT_URL}/refresh-by-source",
                headers={
                    "x-api-key": os.environ.get("INTERNAL_API_KEY", ""),
                    "x-internal-service": "airflow",
                },
                json={"source": source},
                timeout=300,
            )
            r.raise_for_status()
            data = r.json()
            log.info("  silver refresh: %d datasets actualizados", data.get("refreshed", 0))
            return data
        except Exception as exc:
            log.warning("  trigger_silver falló: %s", exc)
            raise

    # ── Grafo de dependencias ─────────────────────────────────────────────────
    result  = extract()
    wm_done = update_watermark(result)
    st_done = save_stats(result)
    [wm_done, st_done] >> trigger_silver(result)


replicon_extract()
