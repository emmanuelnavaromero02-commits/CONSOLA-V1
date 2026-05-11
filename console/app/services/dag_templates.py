"""
DAG Templates — starting points for common extraction patterns.
Each template is a complete, runnable DAG with clear TODO markers.
All templates follow the 4-task pattern:
  extract → [update_watermark ‖ save_stats] → trigger_silver
"""
from __future__ import annotations

# ── Shared helpers injected into every template ───────────────────────────────

_CONN_BLOCK = '''\
MCP_INFRA_URL  = "http://mcp-infra:8010"
REFINEMENT_URL = "http://refinement:8500"
import os

from app.security import get_internal_api_key
INTERNAL_API_KEY = get_internal_api_key()


def _get_connection(conn_id: str, cartridge_id: str = "{cartridge}") -> tuple[str, str]:
    """Return (base_url, token) from Airflow connection <cartridge_id>_<conn_id>."""
    from airflow.hooks.base import BaseHook
    airflow_conn_id = f"{{cartridge_id}}_{{conn_id}}"
    try:
        conn = BaseHook.get_connection(airflow_conn_id)
    except Exception:
        raise ValueError(
            f"Conexión Airflow \'{{airflow_conn_id}}\' no encontrada.\\n"
            f"Airflow UI > Admin > Connections > +:\\n"
            f"  Conn Id: {{airflow_conn_id}}  |  Conn Type: HTTP\\n"
            f"  Host: https://<host>/...  |  Password: <bearer_token>"
        )
    base_url = (conn.host or "").rstrip("/")
    token    = conn.password or ""
    if not base_url or not token:
        raise ValueError(f"Conexión \'{{airflow_conn_id}}\' incompleta — falta Host o Password.")
    return base_url, token


def _get_db_url(conn_id: str, cartridge_id: str = "{cartridge}") -> str:
    """Return SQLAlchemy URL from an Airflow database connection."""
    from airflow.hooks.base import BaseHook
    airflow_conn_id = f"{{cartridge_id}}_{{conn_id}}"
    try:
        conn = BaseHook.get_connection(airflow_conn_id)
    except Exception:
        raise ValueError(
            f"Conexión Airflow \'{{airflow_conn_id}}\' no encontrada.\\n"
            f"Airflow UI > Admin > Connections > +:\\n"
            f"  Conn Id: {{airflow_conn_id}}  |  Conn Type: Postgres\\n"
            f"  Host: <host>  |  Schema: <db>  |  Login: <user>  |  Password: <pass>"
        )
    dialect_map = {{"postgres": "postgresql+psycopg2", "postgresql": "postgresql+psycopg2",
                    "mysql": "mysql+pymysql", "mssql": "mssql+pyodbc"}}
    dialect = dialect_map.get((conn.conn_type or "").lower(), "postgresql+psycopg2")
    port    = f":{{conn.port}}" if conn.port else ""
    return f"{{dialect}}://{{conn.login}}:{{conn.password}}@{{conn.host}}{{port}}/{{conn.schema}}"


def _watermark_get(entity: str, cartridge_id: str = "{cartridge}") -> str | None:
    import requests
    try:
        r = requests.post(f"{{MCP_INFRA_URL}}/mcp/invoke",
                          headers={{"x-api-key": INTERNAL_API_KEY}},
                          json={{"tool": "watermark_get",
                                "args": {{"cartridge_id": cartridge_id, "entity": entity}}}},
                          timeout=10)
        return (r.json().get("result") or {{}}).get("last_value") if r.ok else None
    except Exception:
        return None


def _watermark_set(entity: str, field: str, value: str, run_id: str,
                   cartridge_id: str = "{cartridge}") -> None:
    import requests
    try:
        requests.post(f"{{MCP_INFRA_URL}}/mcp/invoke",
                      headers={{"x-api-key": INTERNAL_API_KEY}},
                      json={{"tool": "watermark_set",
                            "args": {{"cartridge_id": cartridge_id, "entity": entity,
                                     "watermark_field": field, "value": value,
                                     "run_id": run_id}}}},
                      timeout=10)
    except Exception:
        pass


def _pipeline_run_save(dag_id: str, entity: str, cartridge_id: str = "{cartridge}",
                       **kwargs) -> None:
    import requests
    try:
        requests.post(f"{{MCP_INFRA_URL}}/mcp/invoke",
                      headers={{"x-api-key": INTERNAL_API_KEY}},
                      json={{"tool": "pipeline_run_save",
                            "args": {{"dag_id": dag_id, "cartridge_id": cartridge_id,
                                     "entity": entity, **kwargs}}}},
                      timeout=10)
    except Exception:
        pass

'''

_MINIO_BLOCK = '''\
def _upload_parquet(df, cartridge_id: str, entity: str) -> tuple[str, int]:
    """Sube df como Parquet a Bronze. Sobreescribe la partición de hoy."""
    import io as _io
    import pyarrow as pa, pyarrow.parquet as pq
    from minio import Minio
    from airflow.models import Variable
    from datetime import datetime, timezone

    bucket = Variable.get("minio_bucket")
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prefix = f"raw/{{cartridge_id}}/{{entity}}/load_date={{today}}/"
    key    = f"{{prefix}}data.parquet"

    table  = pa.Table.from_pandas(df)
    buf    = _io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    size   = buf.getbuffer().nbytes

    client = Minio(Variable.get("minio_endpoint"),
                   access_key=Variable.get("minio_access_key"),
                   secret_key=Variable.get("minio_secret_key"), secure=False)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)
    client.put_object(bucket, key, buf, size, content_type="application/octet-stream")
    return f"s3://{{bucket}}/{{key}}", size

'''

# ── Shared trigger_silver task body (same logic for all templates) ─────────────

_TRIGGER_SILVER_TASK = '''\
    @task(retries=3, retry_delay=timedelta(minutes=2))
    def trigger_silver(result: dict) -> dict:
        """Notifica al Refinement Engine que refresque datasets Silver/Master dependientes."""
        import logging, requests
        log = logging.getLogger("airflow.task")
        if result.get("record_count", 0) == 0:
            log.info("  trigger_silver omitido (sin filas nuevas)")
            return {{"refreshed": 0}}
        source = f"raw/{{CARTRIDGE_ID}}/{{result.get(\'entity\', ENTITY)}}"
        resp   = requests.post(f"{{REFINEMENT_URL}}/refresh-by-source",
                               headers={{"x-api-key": INTERNAL_API_KEY,
                                         "x-internal-service": "airflow"}},
                               json={{"source": source}}, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        log.info("  silver refresh: %d datasets actualizados", data.get("refreshed", 0))
        return data

'''


TEMPLATES: list[dict] = [
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":          "rest_full",
        "name":        "REST API — Extracción Full",
        "description": "GET paginado con cursor/offset. Descarga todos los registros en cada ejecución.",
        "tags":        ["rest", "full"],
        "code": '''\
"""
DAG: {cartridge}_{entity}_full
Patrón: REST API — extracción completa (sin watermark)

Tareas:
  1. extract       — pagina el API, sube Parquet a Bronze
  2. save_stats    — registra pipeline_run en mcp-infra
  3. trigger_silver — refresca datasets Silver/Master dependientes

Airflow UI > Admin > Connections > + :
  Conn Id:   {cartridge}_default   Conn Type: HTTP
  Host:      https://api.ejemplo.com
  Password:  <bearer_token>
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

CARTRIDGE_ID = "{cartridge}"
ENTITY       = "{entity}"
DAG_ID       = f"{{CARTRIDGE_ID}}_{{ENTITY}}_full"
CONN_ID      = "default"   # TODO: Airflow conn_id = {cartridge}_default

''' + _CONN_BLOCK + _MINIO_BLOCK + '''\
default_args = {{"owner": "modecissions", "retries": 1, "retry_delay": timedelta(minutes=5)}}


@dag(dag_id=DAG_ID, description=f"Extrae {{ENTITY}} (full) → Bronze",
     default_args=default_args, schedule=None, catchup=False,
     tags=[CARTRIDGE_ID, "bronze", "full"],
     params={{
         "entity": {{"type": "string", "default": ENTITY}},
         "mode":   {{"type": "string", "default": "full"}},
     }})
def dag_func():

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def extract(params: dict = None) -> dict:
        """Descarga todos los registros del API y los sube a Bronze."""
        import logging, pandas as pd, requests
        log = logging.getLogger("airflow.task")
        ctx            = get_current_context()
        airflow_run_id = ctx.get("run_id", "")
        conf    = params or {{}}
        entity  = conf.get("entity", ENTITY)
        run_id  = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        log.info("▶ {{DAG_ID}} | entity={{entity}}")

        base_url, token = _get_connection(CONN_ID)
        headers = {{"Authorization": f"Bearer {{token}}", "Accept": "application/json"}}

        rows, page = [], 1
        while True:
            # TODO: ajustar URL y parámetros de paginación
            r = requests.get(f"{{base_url}}/{{entity}}", headers=headers,
                             params={{"page": page, "pageSize": 500}}, timeout=60)
            r.raise_for_status()
            data  = r.json()
            # TODO: ajustar la key según estructura de respuesta del API
            batch = data.get("items") or data.get("data") or data.get("results") or []
            if not batch:
                break
            rows.extend(batch)
            log.info("  página {{page}} → {{len(rows)}} filas acumuladas")
            page += 1

        df       = pd.json_normalize(rows) if rows else pd.DataFrame()
        finished = datetime.now(timezone.utc)
        log.info("  total filas: {{len(df)}}")

        if df.empty:
            return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                    "mode": "full", "record_count": 0, "bytes_written": 0,
                    "storage_uri": None,
                    "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                    "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

        storage_uri, bytes_written = _upload_parquet(df, CARTRIDGE_ID, entity)
        log.info("✔ {{entity}} | {{len(df)}} filas → {{storage_uri}}")
        return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                "mode": "full", "record_count": len(df), "bytes_written": bytes_written,
                "storage_uri": storage_uri,
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def save_stats(result: dict) -> dict:
        """Registra el run en pipeline_runs. Separado para reintentar solo el registro si falla."""
        _pipeline_run_save(
            DAG_ID, result["entity"],
            run_id=result["run_id"], mode=result["mode"], status=result["status"],
            airflow_dag_run_id=result.get("airflow_run_id", ""),
            started_at=result["started_at"], finished_at=result["finished_at"],
            duration_seconds=result.get("duration_seconds"),
            record_count=result.get("record_count", 0),
            bytes_written=result.get("bytes_written", 0),
            storage_uri=result.get("storage_uri"),
        )
        return result

''' + _TRIGGER_SILVER_TASK + '''\
    # ── Grafo de dependencias ─────────────────────────────────────────────────
    result  = extract()
    st_done = save_stats(result)
    st_done >> trigger_silver(result)


dag_func()
''',
    },
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":          "rest_incremental",
        "name":        "REST API — Incremental (watermark)",
        "description": "Extrae solo registros nuevos/modificados desde el último watermark.",
        "tags":        ["rest", "incremental"],
        "code": '''\
"""
DAG: {cartridge}_{entity}_incremental
Patrón: REST API — extracción incremental con watermark

Tareas:
  1. extract          — filtra por watermark, sube Parquet a Bronze
  2. update_watermark — actualiza watermark (solo si modo incremental y hay datos)
  3. save_stats       — registra pipeline_run en mcp-infra
  4. trigger_silver   — refresca datasets Silver/Master dependientes

Airflow UI > Admin > Connections > + :
  Conn Id:   {cartridge}_default   Conn Type: HTTP
  Host:      https://api.ejemplo.com
  Password:  <bearer_token>
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

CARTRIDGE_ID    = "{cartridge}"
ENTITY          = "{entity}"
DAG_ID          = f"{{CARTRIDGE_ID}}_{{ENTITY}}_incremental"
CONN_ID         = "default"        # TODO: Airflow conn_id = {cartridge}_default
WATERMARK_FIELD = "last_modified"  # TODO: campo de fecha en la respuesta del API
WATERMARK_PARAM = "modifiedSince"  # TODO: query-param que acepta el API para filtrar

''' + _CONN_BLOCK + _MINIO_BLOCK + '''\
default_args = {{"owner": "modecissions", "retries": 1, "retry_delay": timedelta(minutes=5)}}


@dag(dag_id=DAG_ID, description=f"Extrae {{ENTITY}} (incremental) → Bronze",
     default_args=default_args, schedule="@daily", catchup=False,
     tags=[CARTRIDGE_ID, "bronze", "incremental"],
     params={{
         "entity": {{"type": "string", "default": ENTITY}},
         "mode":   {{"type": "string", "default": "incremental",
                     "description": "full | incremental"}},
     }})
def dag_func():

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def extract(params: dict = None) -> dict:
        """Extrae registros nuevos desde el último watermark y los sube a Bronze."""
        import logging, pandas as pd, requests
        log = logging.getLogger("airflow.task")
        ctx            = get_current_context()
        airflow_run_id = ctx.get("run_id", "")
        conf    = params or {{}}
        entity  = conf.get("entity", ENTITY)
        mode    = conf.get("mode", "incremental")
        run_id  = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        log.info("▶ {{DAG_ID}} | entity={{entity}} mode={{mode}}")

        base_url, token = _get_connection(CONN_ID)
        headers = {{"Authorization": f"Bearer {{token}}", "Accept": "application/json"}}

        last_wm = _watermark_get(entity) if mode == "incremental" else None
        log.info("  watermark anterior: {{last_wm or '(ninguno — carga full)'}}")

        rows, page = [], 1
        while True:
            # TODO: ajustar URL y parámetros de paginación
            req_params = {{"page": page, "pageSize": 500}}
            if last_wm:
                req_params[WATERMARK_PARAM] = last_wm
            r = requests.get(f"{{base_url}}/{{entity}}", headers=headers,
                             params=req_params, timeout=60)
            r.raise_for_status()
            data  = r.json()
            batch = data.get("items") or data.get("data") or data.get("results") or []
            if not batch:
                break
            rows.extend(batch)
            log.info("  página {{page}} → {{len(rows)}} filas acumuladas")
            page += 1

        df       = pd.json_normalize(rows) if rows else pd.DataFrame()
        finished = datetime.now(timezone.utc)
        log.info("  filas nuevas: {{len(df)}}")

        new_watermark = None
        if not df.empty and WATERMARK_FIELD in df.columns:
            new_watermark = df[WATERMARK_FIELD].dropna().astype(str).max()

        if df.empty:
            return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                    "mode": mode, "record_count": 0, "bytes_written": 0,
                    "storage_uri": None, "new_watermark": None, "wm_field": WATERMARK_FIELD,
                    "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                    "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

        storage_uri, bytes_written = _upload_parquet(df, CARTRIDGE_ID, entity)
        log.info("✔ {{entity}} | {{len(df)}} filas → {{storage_uri}}")
        return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                "mode": mode, "record_count": len(df), "bytes_written": bytes_written,
                "storage_uri": storage_uri, "new_watermark": new_watermark,
                "wm_field": WATERMARK_FIELD,
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def update_watermark(result: dict) -> dict:
        """Actualiza el watermark solo si la extracción produjo datos nuevos."""
        import logging
        log = logging.getLogger("airflow.task")
        new_wm   = result.get("new_watermark")
        wm_field = result.get("wm_field")
        if result.get("mode") == "incremental" and new_wm and wm_field:
            _watermark_set(result["entity"], wm_field, new_wm, result["run_id"])
            log.info("  watermark actualizado: %s = %s", wm_field, new_wm)
        return result

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def save_stats(result: dict) -> dict:
        """Registra el run en pipeline_runs. Separado para reintentar solo el registro si falla."""
        _pipeline_run_save(
            DAG_ID, result["entity"],
            run_id=result["run_id"], mode=result["mode"], status=result["status"],
            airflow_dag_run_id=result.get("airflow_run_id", ""),
            started_at=result["started_at"], finished_at=result["finished_at"],
            duration_seconds=result.get("duration_seconds"),
            record_count=result.get("record_count", 0),
            bytes_written=result.get("bytes_written", 0),
            storage_uri=result.get("storage_uri"),
            watermark_updated_to=result.get("new_watermark"),
        )
        return result

''' + _TRIGGER_SILVER_TASK + '''\
    # ── Grafo de dependencias ─────────────────────────────────────────────────
    result  = extract()
    wm_done = update_watermark(result)
    st_done = save_stats(result)
    [wm_done, st_done] >> trigger_silver(result)


dag_func()
''',
    },
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":          "sql_extract",
        "name":        "Base de datos — SQL Query",
        "description": "Extrae de PostgreSQL/MySQL/SQL Server vía SQLAlchemy. Conexión en Airflow.",
        "tags":        ["sql", "database"],
        "code": '''\
"""
DAG: {cartridge}_{entity}_sql
Patrón: extracción directa desde BD relacional con SQLAlchemy

Tareas:
  1. extract          — ejecuta SQL, sube Parquet a Bronze
  2. update_watermark — actualiza watermark (solo si modo incremental y hay datos)
  3. save_stats       — registra pipeline_run en mcp-infra
  4. trigger_silver   — refresca datasets Silver/Master dependientes

Airflow UI > Admin > Connections > + :
  Conn Id:   {cartridge}_database   Conn Type: Postgres (o MySQL, MSSQL)
  Host:      db.ejemplo.com
  Schema:    nombre_base_datos
  Login:     usuario_lectura
  Password:  contraseña
  Port:      5432
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

CARTRIDGE_ID    = "{cartridge}"
ENTITY          = "{entity}"
DAG_ID          = f"{{CARTRIDGE_ID}}_{{ENTITY}}_sql"
CONN_ID         = "database"       # Airflow conn_id = {cartridge}_database
WATERMARK_FIELD = "updated_at"     # TODO: columna de fecha (None si no aplica)

# TODO: ajustar la query. Usa :watermark como placeholder para extracción incremental
SQL_QUERY = """
    SELECT *
    FROM schema_name.table_name        -- TODO: cambiar
    WHERE 1=1
    -- AND updated_at > :watermark     -- descomentar para incremental
    ORDER BY updated_at
"""

''' + _CONN_BLOCK + _MINIO_BLOCK + '''\
default_args = {{"owner": "modecissions", "retries": 1, "retry_delay": timedelta(minutes=5)}}


@dag(dag_id=DAG_ID, description=f"Extrae {{ENTITY}} desde BD → Bronze",
     default_args=default_args, schedule="@daily", catchup=False,
     tags=[CARTRIDGE_ID, "bronze", "sql"],
     params={{
         "entity": {{"type": "string", "default": ENTITY}},
         "mode":   {{"type": "string", "default": "incremental"}},
     }})
def dag_func():

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def extract(params: dict = None) -> dict:
        """Ejecuta el SQL contra la BD origen y sube el resultado a Bronze."""
        import logging, pandas as pd
        from sqlalchemy import create_engine, text
        log = logging.getLogger("airflow.task")
        ctx            = get_current_context()
        airflow_run_id = ctx.get("run_id", "")
        conf    = params or {{}}
        entity  = conf.get("entity", ENTITY)
        mode    = conf.get("mode", "incremental")
        run_id  = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        log.info("▶ {{DAG_ID}} | entity={{entity}} mode={{mode}}")

        db_url = _get_db_url(CONN_ID)
        engine = create_engine(db_url, pool_pre_ping=True)

        last_wm = _watermark_get(entity) if mode == "incremental" and WATERMARK_FIELD else None
        log.info("  watermark anterior: {{last_wm or '(ninguno)'}}")

        with engine.connect() as con:
            df = pd.read_sql(
                text(SQL_QUERY), con,
                params={{"watermark": last_wm}} if last_wm else {{}},
            )
        engine.dispose()
        finished = datetime.now(timezone.utc)
        log.info("  filas: {{len(df)}}")

        new_watermark = None
        if not df.empty and WATERMARK_FIELD and WATERMARK_FIELD in df.columns:
            new_watermark = str(df[WATERMARK_FIELD].dropna().max())

        if df.empty:
            return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                    "mode": mode, "record_count": 0, "bytes_written": 0,
                    "storage_uri": None, "new_watermark": None, "wm_field": WATERMARK_FIELD,
                    "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                    "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

        storage_uri, bytes_written = _upload_parquet(df, CARTRIDGE_ID, entity)
        log.info("✔ {{entity}} | {{len(df)}} filas → {{storage_uri}}")
        return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                "mode": mode, "record_count": len(df), "bytes_written": bytes_written,
                "storage_uri": storage_uri, "new_watermark": new_watermark,
                "wm_field": WATERMARK_FIELD,
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def update_watermark(result: dict) -> dict:
        """Actualiza el watermark solo si la extracción produjo datos nuevos."""
        import logging
        log = logging.getLogger("airflow.task")
        new_wm   = result.get("new_watermark")
        wm_field = result.get("wm_field")
        if result.get("mode") == "incremental" and new_wm and wm_field:
            _watermark_set(result["entity"], wm_field, new_wm, result["run_id"])
            log.info("  watermark actualizado: %s = %s", wm_field, new_wm)
        return result

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def save_stats(result: dict) -> dict:
        """Registra el run en pipeline_runs. Separado para reintentar solo el registro si falla."""
        _pipeline_run_save(
            DAG_ID, result["entity"],
            run_id=result["run_id"], mode=result["mode"], status=result["status"],
            airflow_dag_run_id=result.get("airflow_run_id", ""),
            started_at=result["started_at"], finished_at=result["finished_at"],
            duration_seconds=result.get("duration_seconds"),
            record_count=result.get("record_count", 0),
            bytes_written=result.get("bytes_written", 0),
            storage_uri=result.get("storage_uri"),
            watermark_updated_to=result.get("new_watermark"),
        )
        return result

''' + _TRIGGER_SILVER_TASK + '''\
    # ── Grafo de dependencias ─────────────────────────────────────────────────
    result  = extract()
    wm_done = update_watermark(result)
    st_done = save_stats(result)
    [wm_done, st_done] >> trigger_silver(result)


dag_func()
''',
    },
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id":          "replicon_analytics",
        "name":        "Replicon Analytics API",
        "description": "Extracción async para Replicon: POST /extracts → poll → download CSV. Patrón probado en producción.",
        "tags":        ["replicon", "analytics", "async"],
        "code": '''\
"""
DAG: replicon_{entity}
Patrón: Replicon Analytics API (async extract → poll → CSV → Parquet)

Tareas:
  1. extract          — inicia extract async, hace polling, descarga CSV, sube Parquet
  2. update_watermark — actualiza watermark (solo si incremental y hay datos nuevos)
  3. save_stats       — registra pipeline_run en mcp-infra
  4. trigger_silver   — refresca datasets Silver/Master dependientes

Airflow UI > Admin > Connections > + :
  Conn Id:   replicon_analytics   Conn Type: HTTP
  Host:      https://<tenant>.replicon.com/analyticsapi
  Password:  <bearer_token>
"""
from __future__ import annotations
import io, time, uuid
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

CARTRIDGE_ID    = "replicon"
ENTITY          = "{entity}"         # e.g. "ProjectDetail"
DAG_ID          = f"replicon_{{ENTITY.lower()}}"
CONN_ID         = "analytics"        # Airflow conn_id = replicon_analytics
WATERMARK_FIELD = "last_modified"    # None si la entidad no tiene campo de fecha
POLL_INTERVAL   = 3.0                # segundos entre polls del extract async
POLL_TIMEOUT    = 300                # segundos máximo esperando el extract

''' + _CONN_BLOCK + _MINIO_BLOCK + '''\
default_args = {{"owner": "modecissions", "retries": 1, "retry_delay": timedelta(minutes=5)}}


@dag(dag_id=DAG_ID, description=f"Extrae {{ENTITY}} de Replicon → Bronze",
     default_args=default_args, schedule=None, catchup=False,
     tags=["replicon", "bronze"],
     params={{
         "entity": {{"type": "string", "default": ENTITY,
                     "description": "Entidad Replicon (e.g. TimeEntry, Project, User)"}},
         "mode":   {{"type": "string", "default": "incremental",
                     "description": "full | incremental"}},
     }})
def dag_func():

    @task(retries=2, retry_delay=timedelta(minutes=5))
    def extract(params: dict = None) -> dict:
        """Inicia extracción async en Replicon, hace polling, descarga CSV y sube a Bronze."""
        import logging, pandas as pd, requests as _req
        log = logging.getLogger("airflow.task")
        ctx            = get_current_context()
        airflow_run_id = ctx.get("run_id", "")
        conf    = params or {{}}
        entity  = conf.get("entity", ENTITY)
        mode    = conf.get("mode", "incremental")
        run_id  = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        log.info("▶ {{DAG_ID}} | entity={{entity}} mode={{mode}}")

        base_url, token = _get_connection(CONN_ID)
        session = _req.Session()
        session.headers.update({{
            "Authorization": f"Bearer {{token}}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }})

        last_wm = _watermark_get(entity) if WATERMARK_FIELD and mode == "incremental" else None
        log.info("  watermark anterior: {{last_wm or '(ninguno — carga full)'}}")

        # Inicia extracción async
        extract_id = session.post(
            f"{{base_url}}/extracts",
            json={{"target": {{"type": "download", "format": "csv"}},
                  "tables": [{{"tableId": entity}}]}},
        ).json()["extractId"]
        log.info("  extractId: {{extract_id}}")

        # Polling hasta completar
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            data = session.get(f"{{base_url}}/extracts/{{extract_id}}").json()
            if data["status"] == "completed":
                break
            if data["status"] == "failed":
                raise RuntimeError(f"Replicon extract fallido: {{data}}")
            time.sleep(POLL_INTERVAL)
        else:
            raise TimeoutError(f"Extract {{extract_id}} timeout ({{POLL_TIMEOUT}}s)")

        # Descarga CSV → DataFrame
        frames = [
            pd.read_csv(io.StringIO(session.get(url).text), low_memory=False)
            for url in (data.get("dataUrls") or {{}}).values()
        ]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not df.empty:
            df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_")
                          for c in df.columns]
        log.info("  filas brutas: {{len(df)}}")

        # Filtro incremental
        wm_field_norm = WATERMARK_FIELD.lower().replace(" ", "_") if WATERMARK_FIELD else None
        if last_wm and wm_field_norm and wm_field_norm in df.columns:
            before = len(df)
            df = df[df[wm_field_norm].astype(str) > last_wm]
            log.info("  filtro incremental ({{wm_field_norm}} > {{last_wm}}): {{before}} → {{len(df)}} filas")

        new_watermark = None
        if not df.empty and wm_field_norm and wm_field_norm in df.columns:
            new_watermark = df[wm_field_norm].dropna().astype(str).max()

        finished = datetime.now(timezone.utc)

        if df.empty:
            return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                    "mode": mode, "record_count": 0, "bytes_written": 0,
                    "storage_uri": None, "new_watermark": None, "wm_field": wm_field_norm,
                    "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                    "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

        storage_uri, bytes_written = _upload_parquet(df, CARTRIDGE_ID, entity)
        log.info("✔ {{entity}} | {{len(df)}} filas → {{storage_uri}}")
        return {{"entity": entity, "run_id": run_id, "airflow_run_id": airflow_run_id,
                "mode": mode, "record_count": len(df), "bytes_written": bytes_written,
                "storage_uri": storage_uri, "new_watermark": new_watermark,
                "wm_field": wm_field_norm,
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "duration_seconds": (finished - started).total_seconds(), "status": "success"}}

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def update_watermark(result: dict) -> dict:
        """Actualiza el watermark solo si la extracción produjo datos nuevos."""
        import logging
        log = logging.getLogger("airflow.task")
        new_wm   = result.get("new_watermark")
        wm_field = result.get("wm_field")
        if result.get("mode") == "incremental" and new_wm and wm_field:
            _watermark_set(result["entity"], wm_field, new_wm, result["run_id"])
            log.info("  watermark actualizado: %s = %s", wm_field, new_wm)
        return result

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def save_stats(result: dict) -> dict:
        """Registra el run en pipeline_runs. Separado para reintentar solo el registro si falla."""
        _pipeline_run_save(
            DAG_ID, result["entity"],
            run_id=result["run_id"], mode=result["mode"], status=result["status"],
            airflow_dag_run_id=result.get("airflow_run_id", ""),
            started_at=result["started_at"], finished_at=result["finished_at"],
            duration_seconds=result.get("duration_seconds"),
            record_count=result.get("record_count", 0),
            bytes_written=result.get("bytes_written", 0),
            storage_uri=result.get("storage_uri"),
            watermark_updated_to=result.get("new_watermark"),
        )
        return result

''' + _TRIGGER_SILVER_TASK + '''\
    # ── Grafo de dependencias ─────────────────────────────────────────────────
    result  = extract()
    wm_done = update_watermark(result)
    st_done = save_stats(result)
    [wm_done, st_done] >> trigger_silver(result)


dag_func()
''',
    },
]


def get_all() -> list[dict]:
    return [{"id": t["id"], "name": t["name"],
             "description": t["description"], "tags": t["tags"]}
            for t in TEMPLATES]


def get_code(template_id: str, cartridge: str = "my_cartridge",
             entity: str = "MyEntity") -> str | None:
    tpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if not tpl:
        return None
    return tpl["code"].replace("{cartridge}", cartridge).replace("{entity}", entity)
