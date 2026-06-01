"""
replicon_extract DAG  (Pattern B — portable, no custom container)
=================================================================
Extrae UNA entidad de Replicon API → Parquet → MinIO (Bronze).

Credenciales: Console/Vault en connections/replicon/default.

El conf de cada run puede sobreescribir parámetros:
  entity          — nombre de la entidad (requerido)
  mode            — "full" | "incremental"  (default: incremental)
  from_date       — YYYY-MM-DD (solo modo histórico)
  to_date         — YYYY-MM-DD (solo modo histórico)
"""
from __future__ import annotations

import io
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.decorators import dag, task
from airflow.models import Variable

try:
    from app.core.auth_factory import auth_trace, build_auth_headers
except ModuleNotFoundError:  # pragma: no cover - Airflow mounts app code separately
    import importlib.util

    _auth_factory_candidates = (
        Path("/registry/cartridges/replicon/app/core/auth_factory.py"),
        Path(__file__).resolve().parents[1] / "app/core/auth_factory.py",
    )
    for _auth_factory_path in _auth_factory_candidates:
        if _auth_factory_path.exists():
            break
    else:
        raise
    _auth_factory_spec = importlib.util.spec_from_file_location(
        "replicon_auth_factory",
        _auth_factory_path,
    )
    if not _auth_factory_spec or not _auth_factory_spec.loader:
        raise
    _auth_factory = importlib.util.module_from_spec(_auth_factory_spec)
    _auth_factory_spec.loader.exec_module(_auth_factory)
    auth_trace = _auth_factory.auth_trace
    build_auth_headers = _auth_factory.build_auth_headers


DEFAULT_CONN_ID = "default"
LEGACY_CONN_IDS = ("analytics",)
logger = logging.getLogger(__name__)
_SAFE_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.:-]+")


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


# ── Console Vault connection helper ───────────────────────────────────────────
# Credentials stay in Console/Vault. Airflow receives only a service key and
# reveals the Replicon connection at runtime through a narrow internal bypass.

def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _internal_auth_headers() -> dict[str, str]:
    replicon_key = os.environ.get("INTERNAL_API_KEY_REPLICON_TO_CONSOLE", "")
    if replicon_key:
        return {"x-internal-service": "replicon", "x-api-key": replicon_key}

    airflow_key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", "")
    if airflow_key:
        return {"x-internal-service": "airflow", "x-api-key": airflow_key}

    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return {"x-internal-service": "airflow", "x-api-key": legacy}
    raise RuntimeError(
        "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE missing; legacy INTERNAL_API_KEY fallback is disabled in production"
    )


def _get_connection(conn_id: str = DEFAULT_CONN_ID) -> tuple[str, dict, str]:
    """Return (base_url, connection_payload, conn_id) from Console Vault."""
    import os
    import requests

    console_url = os.environ.get("CONSOLE_URL", "http://console:8000").rstrip("/")
    headers = _internal_auth_headers()

    requested = (conn_id or DEFAULT_CONN_ID).strip() or DEFAULT_CONN_ID
    candidates = tuple(dict.fromkeys((requested, DEFAULT_CONN_ID, *LEGACY_CONN_IDS)))
    errors: list[str] = []
    for candidate in candidates:
        try:
            resp = requests.get(
                f"{console_url}/api/vault/connections/replicon/{candidate}/reveal",
                headers=headers,
                timeout=20,
            )
            if resp.status_code == 404:
                errors.append(f"{candidate}: 404")
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            continue

        base_url = data.get("base_url") or data.get("url") or data.get("host") or ""
        auth_payload = dict(data)
        auth_payload.setdefault("auth_method", "bearer_token")
        if base_url:
            auth_payload["base_url"] = base_url
        try:
            build_auth_headers(
                auth_payload,
                default_method="bearer_token",
                default_api_key_header="X-API-Key",
                base_headers={"Accept": "application/json"},
            )
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if base_url:
            logger.warning("Vault connection resolved from connections/replicon/%s", candidate)
            return base_url, auth_payload, candidate
        errors.append(f"{candidate}: missing base_url")

    raise ValueError(
        "No se pudo revelar Vault connections/replicon/default"
        + (f" (intentos: {'; '.join(errors)})" if errors else "")
    )


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
    return {"connection_id": DEFAULT_CONN_ID, "watermark_field": None, "mode": "full"}


def _resolve_connection(entity: str, requested_conn_id: str | None = None) -> tuple[str, dict, str]:
    """Return (base_url, connection_payload, conn_id) via entity_config → Console/Vault."""
    cfg     = _get_entity_config(entity)
    conn_id = requested_conn_id or cfg.get("connection_id") or DEFAULT_CONN_ID
    return _get_connection(conn_id)


# ── MinIO helpers ─────────────────────────────────────────────────────────────

def _minio_client():
    from minio import Minio
    secure = Variable.get("minio_secure", default_var="false").strip().lower() in {
        "true", "1", "yes", "on",
    }
    return Minio(
        endpoint=Variable.get("minio_endpoint"),
        access_key=Variable.get("minio_access_key"),
        secret_key=Variable.get("minio_secret_key"),
        secure=secure,
    )


def _safe_scope_segment(value: object) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_SCOPE_SEGMENT.fullmatch(text):
        return ""
    return text


def _scope_prefix(tenant_id: object = None, workspace_id: object = None) -> str:
    tenant = _safe_scope_segment(tenant_id)
    workspace = _safe_scope_segment(workspace_id)
    if not (tenant and workspace):
        return ""
    return f"tenant_id={tenant}/workspace_id={workspace}/"


def _upload_parquet(
    df,
    entity: str,
    run_id: str,
    tenant_id: object = None,
    workspace_id: object = None,
) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    bucket = Variable.get("minio_bucket")
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scope = _scope_prefix(tenant_id, workspace_id)
    key = f"raw/replicon/{entity}/{scope}load_date={today}/batch_id={run_id}/{entity}.parquet"

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

    def __init__(self, base_url: str, connection: dict,
                 poll_interval: float = 2.0, poll_timeout: int = 300):
        import requests as _req
        self._s = _req.Session()
        headers, method, auth_headers = build_auth_headers(
            connection,
            default_method="bearer_token",
            default_api_key_header="X-API-Key",
            base_headers={
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
        )
        self._s.headers.update(headers)
        self._auth_method = method
        self._auth_headers = auth_headers
        self.base          = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.poll_timeout  = poll_timeout

    def _log_auth(self, status_code: int | None = None) -> None:
        suffix = f" -> Respuesta del servidor {status_code}" if status_code is not None else ""
        logger.warning("%s%s", auth_trace(self._auth_method, self._auth_headers), suffix)

    def _get(self, path: str) -> dict:
        import requests as _req
        url, delay = f"{self.base}{path}", 1.0
        for _ in range(self.RETRY):
            try:
                logger.warning("Replicon outbound GET %s", url)
                r = self._s.get(url, timeout=60)
                self._log_auth(r.status_code)
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
                logger.warning("Replicon outbound POST %s", url)
                r = self._s.post(url, json=body, timeout=60)
                self._log_auth(r.status_code)
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
        from airflow.operators.python import get_current_context

        context   = get_current_context()
        dag_run   = context.get("dag_run")
        run_conf  = dag_run.conf if dag_run and dag_run.conf else {}
        conf      = {**(params or {}), **run_conf}
        entity    = conf.get("entity", "User")
        mode      = conf.get("mode", "incremental")
        from_date = conf.get("from_date") or None
        to_date   = conf.get("to_date")   or None
        conn_id   = conf.get("connection_id") or DEFAULT_CONN_ID
        security_context = conf.get("security_context") if isinstance(conf.get("security_context"), dict) else {}
        tenant_id = conf.get("tenant_id") or security_context.get("tenant_id")
        workspace_id = conf.get("workspace_id") or security_context.get("workspace_id")

        base_url, connection, resolved_conn_id = _resolve_connection(entity, conn_id)
        logger.warning(
            "replicon_extract starting external API call entity=%s mode=%s vault_path=connections/replicon/%s",
            entity,
            mode,
            resolved_conn_id,
        )
        watermark_field = _get_entity_config(entity).get("watermark_field")

        client = _RepliconClient(base_url, connection)
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
        storage_uri = _upload_parquet(df, entity, run_id, tenant_id, workspace_id)
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

    @task
    def trigger_refresh_chain(result: dict) -> dict:
        """Propaga silver/gold aguas abajo con el meta-DAG dataset_refresh_chain."""
        import logging
        import os as _os
        import requests as _req
        from airflow.operators.python import get_current_context

        log = logging.getLogger("airflow.task")
        if result.get("record_count", 0) == 0:
            log.info("refresh_chain omitido (sin datos nuevos)")
            return {"triggered": False}

        entity = result["entity"]
        dag_run = get_current_context().get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        refresh_conf = {
            "seed_raw": f"raw/replicon/{entity}",
            "triggered_by": "replicon_extract",
        }
        for key in ("tenant_id", "workspace_id", "security_context"):
            if run_conf.get(key):
                refresh_conf[key] = run_conf[key]
        airflow_url = _os.environ.get("AIRFLOW_URL", "http://airflow:8080").rstrip("/")
        user = (
            _os.environ.get("AIRFLOW_USER")
            or _os.environ.get("AIRFLOW_ADMIN_USER")
            or "admin"
        )
        password = (
            _os.environ.get("AIRFLOW_PASSWORD")
            or _os.environ.get("AIRFLOW_ADMIN_PASSWORD")
            or "admin"
        )
        try:
            response = _req.post(
                f"{airflow_url}/api/v1/dags/dataset_refresh_chain/dagRuns",
                auth=(user, password),
                json={"conf": refresh_conf},
                timeout=15,
            )
            response.raise_for_status()
            log.info("refresh_chain disparado: %s", response.status_code)
            return {"triggered": True, "status": response.status_code}
        except Exception as exc:
            log.warning("refresh_chain trigger fallo: %s", exc)
            return {"triggered": False, "error": str(exc)}

    trigger_refresh_chain(extract())


replicon_extract()
