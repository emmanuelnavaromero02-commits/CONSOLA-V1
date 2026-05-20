from __future__ import annotations

import email
import imaplib
import io
import os
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from minio import Minio
from minio.commonconfig import CopySource

default_args = {
    "owner": "replicon",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

dag = DAG(
    dag_id="replicon_outlook_audit_report_import",
    default_args=default_args,
    description="Descarga CSV desde Outlook -> MinIO auditoria -> Parquet Bronze",
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    tags=["replicon", "outlook", "audit", "ingest", "bronze"],
    catchup=False,
)

OUTLOOK_SUBJECT = "VO_PRD_KPI_PROJECT_PROGRESS_AUDIT from Replicon"
ZIP_FILENAME = "VO_PRD_KPI_PROJECT_AUDIT Daily.zip"
CSV_FILENAME = "VO_PRD_KPI_PROJECT_AUDIT Daily.csv"
MINIO_UPLOAD_PATH = "uploads/replicon/in"
MINIO_BAK_PATH = "uploads/replicon/bak"
CARTRIDGE_ID = "replicon"
ENTITY = "ProjectAudit"


def _required_variable(name: str) -> str:
    value = Variable.get(name, default_var="").strip()
    if not value:
        raise ValueError(f"Airflow Variable '{name}' is required")
    return value


def _minio_settings() -> dict:
    return {
        "endpoint": _required_variable("minio_endpoint"),
        "access_key": _required_variable("minio_access_key"),
        "secret_key": _required_variable("minio_secret_key"),
        "bucket": _required_variable("minio_bucket"),
        "secure": Variable.get("minio_secure", default_var="false").strip().lower()
        in {"true", "1", "yes", "on"},
    }


def _minio_client() -> Minio:
    settings = _minio_settings()
    return Minio(
        settings["endpoint"],
        access_key=settings["access_key"],
        secret_key=settings["secret_key"],
        secure=settings["secure"],
    )


def _ensure_bucket() -> str:
    settings = _minio_settings()
    client = _minio_client()
    bucket = settings["bucket"]
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return bucket


def fetch_outlook_attachment(**context):
    receiver_email = _required_variable("outlook_receiver_email")
    sender_email = _required_variable("outlook_sender_email")
    outlook_password = _required_variable("outlook_app_password")

    mail = imaplib.IMAP4_SSL("imap-mail.outlook.com", 993)
    mail.login(receiver_email, outlook_password)
    mail.select("INBOX")

    status, messages = mail.search(None, f'(UNSEEN FROM "{sender_email}")')
    if status != "OK" or not messages or not messages[0]:
        raise ValueError(f"No unread emails from {sender_email}")

    latest_email_id = messages[0].split()[-1]
    status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
    if status != "OK":
        raise ValueError("Could not fetch latest Outlook message")
    msg = email.message_from_bytes(msg_data[0][1])

    if OUTLOOK_SUBJECT not in (msg["Subject"] or ""):
        raise ValueError(f"Subject mismatch. Expected: {OUTLOOK_SUBJECT}")

    zip_content = None
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            filename = part.get_filename()
            if filename and ZIP_FILENAME in filename:
                zip_content = part.get_payload(decode=True)
                break
    if not zip_content:
        raise ValueError(f"Attachment not found: {ZIP_FILENAME}")

    csv_content = None
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        for filename in zf.namelist():
            if CSV_FILENAME in filename:
                csv_content = zf.read(filename)
                break
    if not csv_content:
        raise ValueError(f"CSV not found in ZIP: {CSV_FILENAME}")

    tmp_csv_path = f"/tmp/{CSV_FILENAME}"
    with open(tmp_csv_path, "wb") as f:
        f.write(csv_content)

    mail.store(latest_email_id, "+FLAGS", "\\Seen")
    mail.close()
    mail.logout()

    ti = context["task_instance"]
    ti.xcom_push(key="csv_path", value=tmp_csv_path)
    ti.xcom_push(key="csv_filename", value=CSV_FILENAME)
    ti.xcom_push(key="csv_bytes", value=len(csv_content))
    return {"status": "success", "csv_file": CSV_FILENAME, "csv_bytes": len(csv_content)}


def upload_csv_to_minio(**context):
    csv_path = context["task_instance"].xcom_pull(
        task_ids="fetch_outlook_attachment",
        key="csv_path",
    )
    csv_filename = context["task_instance"].xcom_pull(
        task_ids="fetch_outlook_attachment",
        key="csv_filename",
    )
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    bucket = _ensure_bucket()
    client = _minio_client()
    minio_csv_path = f"{MINIO_UPLOAD_PATH}/{csv_filename}"
    file_size = os.path.getsize(csv_path)
    with open(csv_path, "rb") as f:
        client.put_object(bucket, minio_csv_path, f, file_size)

    uri = f"s3://{bucket}/{minio_csv_path}"
    context["task_instance"].xcom_push(key="minio_csv_uri", value=uri)
    return {"status": "success", "minio_csv_uri": uri}


def csv_to_parquet(**context):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    ti = context["task_instance"]
    csv_path = ti.xcom_pull(task_ids="fetch_outlook_attachment", key="csv_path")

    batch_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    df = pd.read_csv(csv_path, sep=",", encoding="utf-8", header=0)
    row_count = len(df)

    bucket = _ensure_bucket()
    client = _minio_client()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parquet_prefix = f"raw/{CARTRIDGE_ID}/{ENTITY}/load_date={today}/"
    parquet_key = f"{parquet_prefix}data.parquet"

    for obj in client.list_objects(bucket, prefix=parquet_prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)

    table = pa.Table.from_pandas(df)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    bytes_written = buf.getbuffer().nbytes
    client.put_object(bucket, parquet_key, buf, bytes_written, content_type="application/octet-stream")

    parquet_uri = f"s3://{bucket}/{parquet_key}"
    ti.xcom_push(key="batch_id", value=batch_id)
    ti.xcom_push(key="parquet_uri", value=parquet_uri)
    ti.xcom_push(key="row_count", value=row_count)
    ti.xcom_push(key="bytes_written", value=bytes_written)
    ti.xcom_push(key="started_at", value=started_at)
    ti.xcom_push(key="finished_at", value=datetime.now(timezone.utc).isoformat())
    return {
        "status": "success",
        "batch_id": batch_id,
        "row_count": row_count,
        "parquet_uri": parquet_uri,
        "bytes_written": bytes_written,
    }


def move_csv_to_backup(**context):
    ti = context["task_instance"]
    minio_csv_uri = ti.xcom_pull(task_ids="upload_csv_to_minio", key="minio_csv_uri")
    csv_filename = ti.xcom_pull(task_ids="fetch_outlook_attachment", key="csv_filename")

    bucket = _ensure_bucket()
    client = _minio_client()
    src_key = minio_csv_uri.removeprefix(f"s3://{bucket}/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst_key = f"{MINIO_BAK_PATH}/{ts}_{csv_filename}"

    client.copy_object(bucket, dst_key, CopySource(bucket, src_key))
    client.remove_object(bucket, src_key)

    backup_uri = f"s3://{bucket}/{dst_key}"
    ti.xcom_push(key="backup_uri", value=backup_uri)
    return {"status": "success", "backup_uri": backup_uri}


task_fetch = PythonOperator(
    task_id="fetch_outlook_attachment",
    python_callable=fetch_outlook_attachment,
    dag=dag,
)

task_upload_csv = PythonOperator(
    task_id="upload_csv_to_minio",
    python_callable=upload_csv_to_minio,
    dag=dag,
)

task_convert = PythonOperator(
    task_id="csv_to_parquet",
    python_callable=csv_to_parquet,
    dag=dag,
)

task_backup = PythonOperator(
    task_id="move_csv_to_backup",
    python_callable=move_csv_to_backup,
    dag=dag,
)

task_fetch >> task_upload_csv >> task_convert >> task_backup
