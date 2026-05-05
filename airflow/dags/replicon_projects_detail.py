"""
replicon_projects_detail DAG
======================
Lee un archivo Excel desde MinIO y lo carga a la capa bronze (raw/)
en formato Parquet, usando el primer renglón como nombres de columna.

Para ejecutar: edita las constantes de configuración abajo y da Deploy en Studio.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

from airflow.decorators import dag, task
from airflow.models import Variable

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN — edita estos valores en Studio y da Deploy
# ═══════════════════════════════════════════════════════════════════════════════

INPUT_FILE   = "VO_PROJECTS_DETAIL Daily.csv"  # se busca en s3://{minio_bucket}/uploads/replicon/in/
CARTRIDGE_ID = "replicon"    # dominio/fuente  →  raw/{CARTRIDGE_ID}/{ENTITY}/...
ENTITY       = "ProjectDetail"    # nombre de la entidad/tabla destino
FILE_FORMAT  = "csv"        # "csv" o "excel"
DELIMITER    = ","          # solo para CSV  (usar ";" si aplica)
ENCODING     = "utf-8"      # solo para CSV
SHEET_NAME   = 0            # solo para Excel: índice 0-based o nombre de hoja

# ═══════════════════════════════════════════════════════════════════════════════

MCP_INFRA_URL = "http://mcp-infra:8010"


def _get_minio_config() -> dict[str, str]:
    return {
        "endpoint":   Variable.get("minio_endpoint"),
        "access_key": Variable.get("minio_access_key"),
        "secret_key": Variable.get("minio_secret_key"),
        "bucket":     Variable.get("minio_bucket"),
    }


def _minio_client():
    from minio import Minio
    cfg = _get_minio_config()
    return Minio(cfg["endpoint"], access_key=cfg["access_key"],
                 secret_key=cfg["secret_key"],
                 secure=Variable.get("minio_secure", default_var="false").lower() == "true")


def _download_from_minio(s3_path: str) -> bytes:
    cfg    = _get_minio_config()
    bucket = cfg["bucket"]
    if not s3_path.startswith("s3://"):
        raise ValueError(f"Se esperaba ruta s3://, se recibió: {s3_path}")
    key    = s3_path.removeprefix(f"s3://{bucket}/")
    client = _minio_client()
    resp   = client.get_object(bucket, key)
    data   = resp.read()
    resp.close()
    resp.release_conn()
    return data


def _read_to_df(data: bytes) -> "pd.DataFrame":
    import pandas as pd
    if FILE_FORMAT == "csv":
        return pd.read_csv(io.BytesIO(data), sep=DELIMITER,
                           encoding=ENCODING, header=0)
    elif FILE_FORMAT == "excel":
        return pd.read_excel(io.BytesIO(data), sheet_name=SHEET_NAME,
                             header=0, engine="openpyxl")
    else:
        raise ValueError(f"FILE_FORMAT no soportado: {FILE_FORMAT!r}. Use 'csv' o 'excel'.")


def _upload_parquet(df, batch_id: str) -> tuple[str, int]:
    import pyarrow as pa
    import pyarrow.parquet as pq
    cfg    = _get_minio_config()
    bucket = cfg["bucket"]
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prefix = f"raw/{CARTRIDGE_ID}/{ENTITY}/load_date={today}/"
    key    = f"{prefix}data.parquet"
    client = _minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    # Elimina objetos previos del mismo día para evitar duplicados
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)
    table = pa.Table.from_pandas(df)
    buf   = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    size = buf.getbuffer().nbytes
    client.put_object(bucket, key, buf, size, content_type="application/octet-stream")
    return f"s3://{bucket}/{key}", size


def _move_to_bak(s3_path: str) -> str:
    """Copia el archivo a uploads/replicon/bak/{YYYYMMDD_HHmmss}_{filename} y elimina el original."""
    from minio.commonconfig import CopySource
    cfg    = _get_minio_config()
    bucket = cfg["bucket"]
    src_key = s3_path.removeprefix(f"s3://{bucket}/")
    filename = src_key.split("/")[-1]
    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst_key  = src_key.replace("/in/", "/bak/").rsplit("/", 1)[0] + f"/{ts}_{filename}"
    client   = _minio_client()
    client.copy_object(bucket, dst_key, CopySource(bucket, src_key))
    client.remove_object(bucket, src_key)
    return f"s3://{bucket}/{dst_key}"


def _save_run(batch_id: str, **kwargs) -> None:
    import requests
    try:
        requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={"tool": "pipeline_run_save",
                  "args": {"dag_id": "replicon_projects_detail",
                           "cartridge_id": CARTRIDGE_ID,
                           "entity": ENTITY,
                           "run_id": batch_id,
                           **kwargs}},
            timeout=10,
        )
    except Exception:
        pass


@dag(
    dag_id='replicon_projects_detail',
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["minio", "bronze", "ingest"],
    doc_md=__doc__,
)
def replicon_projects_detail():

    @task
    def ingest() -> dict:
        started_at = datetime.now(timezone.utc).isoformat()
        batch_id   = str(uuid.uuid4())
        raw_bytes  = _download_from_minio(f"s3://{Variable.get('minio_bucket')}/uploads/replicon/in/{INPUT_FILE}")
        df         = _read_to_df(raw_bytes)
        storage_uri, bytes_written = _upload_parquet(df, batch_id)
        return {
            "batch_id":      batch_id,
            "row_count":     len(df),
            "bytes_written": bytes_written,
            "storage_uri":   storage_uri,
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
        }

    @task
    def save_stats(result: dict) -> None:
        _save_run(
            batch_id=result["batch_id"],
            status="success",
            record_count=result["row_count"],
            bytes_written=result["bytes_written"],
            storage_uri=result["storage_uri"],
            started_at=result["started_at"],
            finished_at=result["finished_at"],
        )

    @task
    def move_to_bak() -> str:
        return _move_to_bak(f"s3://{Variable.get('minio_bucket')}/uploads/replicon/in/{INPUT_FILE}")

    result = ingest()
    save_stats(result) >> move_to_bak()


dag = replicon_projects_detail()