from __future__ import annotations

import email
import imaplib
import io
import os
import re
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
MAX_ZIP_MEMBERS = int(os.environ.get("OUTLOOK_IMPORT_MAX_ZIP_MEMBERS", "25"))
MAX_ZIP_MEMBER_BYTES = int(
    os.environ.get("OUTLOOK_IMPORT_MAX_ZIP_MEMBER_BYTES", str(50 * 1024 * 1024))
)
MAX_ZIP_TOTAL_BYTES = int(
    os.environ.get("OUTLOOK_IMPORT_MAX_ZIP_TOTAL_BYTES", str(100 * 1024 * 1024))
)
_SAFE_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.:-]+")


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _required_variable(name: str) -> str:
    value = Variable.get(name, default_var="").strip()
    if not value:
        raise ValueError(f"Airflow Variable '{name}' is required")
    return value


def _safe_scope_segment(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _SAFE_SCOPE_SEGMENT.fullmatch(text):
        raise ValueError(f"{label} inválido para partición SaaS")
    return text


def _dag_conf(context: dict) -> dict:
    dag_run = context.get("dag_run")
    conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
    return conf if isinstance(conf, dict) else {}


def _scope_values(context: dict) -> tuple[str, str]:
    conf = _dag_conf(context)
    security_context = (
        conf.get("security_context")
        if isinstance(conf.get("security_context"), dict)
        else {}
    )
    tenant = (
        conf.get("tenant_id")
        or security_context.get("tenant_id")
        or Variable.get("replicon_tenant_id", default_var="")
    )
    workspace = (
        conf.get("workspace_id")
        or security_context.get("workspace_id")
        or Variable.get("replicon_workspace_id", default_var="")
    )
    tenant_id = _safe_scope_segment(tenant, "tenant_id")
    workspace_id = _safe_scope_segment(workspace, "workspace_id")
    if tenant_id and workspace_id:
        return tenant_id, workspace_id
    if not _is_production() and Variable.get(
        "replicon_allow_unscoped_outlook_uploads", default_var="false"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return "", ""
    raise ValueError(
        "tenant_id and workspace_id are required for replicon_outlook_audit_report_import"
    )


def _scoped_path(base: str, tenant_id: str, workspace_id: str, suffix: str = "") -> str:
    path = base.strip().strip("/")
    if "tenant_id=" in path or "workspace_id=" in path:
        expected = f"tenant_id={tenant_id}/workspace_id={workspace_id}"
        if tenant_id and workspace_id and expected not in path:
            raise ValueError(f"{base} scope does not match dag_run scope")
    elif tenant_id and workspace_id:
        parts = [part for part in path.split("/") if part]
        tail = parts.pop() if parts and parts[-1] in {"in", "bak"} else ""
        parts.extend([f"tenant_id={tenant_id}", f"workspace_id={workspace_id}"])
        if tail:
            parts.append(tail)
        path = "/".join(parts)
    if suffix:
        path = f"{path}/{suffix.strip('/')}"
    return path


def _minio_settings() -> dict:
    provider = os.environ.get("LAKEHOUSE_PROVIDER", "").strip().lower()
    endpoint = os.environ.get("LAKEHOUSE_ENDPOINT", "").strip()
    if provider == "gcs" or "storage.googleapis.com" in endpoint.lower():
        settings = {
            "provider": "gcs",
            "endpoint": endpoint or "storage.googleapis.com",
            "access_key": os.environ.get("GCS_ACCESS_KEY_ID", "").strip(),
            "secret_key": os.environ.get("GCS_SECRET_ACCESS_KEY", "").strip(),
            "bucket": (
                os.environ.get("GCS_BUCKET")
                or os.environ.get("LAKEHOUSE_BUCKET")
                or ""
            ).strip(),
            "secure": True,
            "region": "auto",
        }
        if not all(settings[key] for key in ("access_key", "secret_key", "bucket")):
            raise ValueError("complete GCS lakehouse credentials are required")
        return settings
    if provider == "s3" or "amazonaws.com" in endpoint.lower():
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
        if bool(access_key) != bool(secret_key):
            raise ValueError("complete AWS lakehouse credential pair is required")
        bucket = (
            os.environ.get("S3_BUCKET_NAME")
            or os.environ.get("LAKEHOUSE_BUCKET")
            or ""
        ).strip()
        if not bucket:
            raise ValueError("AWS lakehouse bucket is required")
        return {
            "provider": "s3",
            "endpoint": (
                os.environ.get("S3_ENDPOINT_URL")
                or endpoint
                or "s3.amazonaws.com"
            ).removeprefix("https://").removeprefix("http://").rstrip("/"),
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket": bucket,
            "secure": True,
            "region": os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1",
        }
    return {
        "provider": "minio",
        "endpoint": _required_variable("minio_endpoint"),
        "access_key": _required_variable("minio_access_key"),
        "secret_key": _required_variable("minio_secret_key"),
        "bucket": _required_variable("minio_bucket"),
        "secure": Variable.get("minio_secure", default_var="false").strip().lower()
        in {"true", "1", "yes", "on"},
        "region": None,
    }


def _minio_client() -> Minio:
    settings = _minio_settings()
    if settings["provider"] == "s3" and not settings["access_key"]:
        from minio.credentials.providers import IamAwsProvider

        return Minio(
            settings["endpoint"],
            credentials=IamAwsProvider(region=settings["region"]),
            secure=True,
            region=settings["region"],
        )
    return Minio(
        settings["endpoint"],
        access_key=settings["access_key"],
        secret_key=settings["secret_key"],
        secure=settings["secure"],
        region=settings["region"],
    )


def _safe_zip_member(zf: zipfile.ZipFile, expected_name: str) -> zipfile.ZipInfo:
    infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError(f"ZIP has too many members ({len(infos)} > {MAX_ZIP_MEMBERS})")
    total = sum(int(info.file_size or 0) for info in infos)
    if total > MAX_ZIP_TOTAL_BYTES:
        raise ValueError("ZIP uncompressed total is too large")
    for info in infos:
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"ZIP member too large: {info.filename}")
        if expected_name in os.path.basename(info.filename):
            return info
    raise ValueError(f"CSV not found in ZIP: {expected_name}")


def _ensure_bucket() -> str:
    settings = _minio_settings()
    client = _minio_client()
    bucket = settings["bucket"]
    if settings["provider"] == "minio" and not client.bucket_exists(bucket):
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
        csv_content = zf.read(_safe_zip_member(zf, CSV_FILENAME))
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
    return {
        "status": "success",
        "csv_file": CSV_FILENAME,
        "csv_bytes": len(csv_content),
    }


def upload_csv_to_minio(**context):
    tenant_id, workspace_id = _scope_values(context)
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
    minio_csv_path = _scoped_path(
        MINIO_UPLOAD_PATH, tenant_id, workspace_id, csv_filename
    )
    file_size = os.path.getsize(csv_path)
    with open(csv_path, "rb") as f:
        client.put_object(bucket, minio_csv_path, f, file_size)

    uri = f"s3://{bucket}/{minio_csv_path}"
    context["task_instance"].xcom_push(key="minio_csv_uri", value=uri)
    context["task_instance"].xcom_push(key="tenant_id", value=tenant_id)
    context["task_instance"].xcom_push(key="workspace_id", value=workspace_id)
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
    tenant_id, workspace_id = _scope_values(context)
    if tenant_id:
        df["tenant_id"] = tenant_id
    if workspace_id:
        df["workspace_id"] = workspace_id
    row_count = len(df)

    bucket = _ensure_bucket()
    client = _minio_client()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parquet_prefix = (
        f"raw/{CARTRIDGE_ID}/{ENTITY}/"
        f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
        f"load_date={today}/"
    )
    parquet_key = f"{parquet_prefix}data.parquet"

    for obj in client.list_objects(bucket, prefix=parquet_prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)

    table = pa.Table.from_pandas(df)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    bytes_written = buf.getbuffer().nbytes
    client.put_object(
        bucket, parquet_key, buf, bytes_written, content_type="application/octet-stream"
    )

    parquet_uri = f"s3://{bucket}/{parquet_key}"
    ti.xcom_push(key="batch_id", value=batch_id)
    ti.xcom_push(key="parquet_uri", value=parquet_uri)
    ti.xcom_push(key="row_count", value=row_count)
    ti.xcom_push(key="bytes_written", value=bytes_written)
    ti.xcom_push(key="started_at", value=started_at)
    ti.xcom_push(key="finished_at", value=datetime.now(timezone.utc).isoformat())
    ti.xcom_push(key="tenant_id", value=tenant_id)
    ti.xcom_push(key="workspace_id", value=workspace_id)
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
    tenant_id = ti.xcom_pull(task_ids="upload_csv_to_minio", key="tenant_id")
    workspace_id = ti.xcom_pull(task_ids="upload_csv_to_minio", key="workspace_id")
    if not (tenant_id and workspace_id):
        tenant_id, workspace_id = _scope_values(context)

    bucket = _ensure_bucket()
    client = _minio_client()
    src_key = minio_csv_uri.removeprefix(f"s3://{bucket}/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst_key = _scoped_path(
        MINIO_BAK_PATH, tenant_id, workspace_id, f"{ts}_{csv_filename}"
    )

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
