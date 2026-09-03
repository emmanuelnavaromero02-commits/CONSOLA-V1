"""
DAG: replicon_ses_inbox_import

Reads emails received by Amazon SES Inbound at
    s3://${INBOX_BUCKET}/inbound/
extracts every attachment, and lands them in the lakehouse uploads area:
    s3://${LAKEHOUSE_BUCKET}/uploads/replicon/tenant_id=<tenant>/workspace_id=<workspace>/in/<filename>

Behavior:
- ZIP attachments are extracted; each file inside the archive is uploaded.
- Excel/CSV/PDF attachments go straight through.
- Processed message objects are moved to inbound-processed/ so the next run
  doesn't pick them up again (idempotent — a re-run only sees new mail).
- Files that are not interesting attachments (text/plain bodies, signatures,
  AMAZON_SES_SETUP_NOTIFICATION) are skipped.

The DAG is split into 4 tasks so the run shows as a real graph in the
Airflow UI and the Studio entity row gets per-step logs:

    list_inbox  →  extract_attachments  →  archive_processed  →  record_run

`record_run` writes a row to pipeline_runs (via mcp-infra) so the studio
"última ejecución" badge picks up status, finished_at and counts.
"""

from __future__ import annotations

import email
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

import boto3
import requests
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator

try:
    from runtime_security_context import build_pipeline_run_context
except ModuleNotFoundError:
    _runtime_dags = next(
        parent / "airflow/dags"
        for parent in Path(__file__).resolve().parents
        if (parent / "airflow/dags/runtime_security_context.py").is_file()
    )
    sys.path.insert(0, str(_runtime_dags))
    from runtime_security_context import build_pipeline_run_context


# ── Config ───────────────────────────────────────────────────────────────────

CARTRIDGE_ID = "replicon"
ENTITY = "SESInboxJobNoEntity"
MCP_INFRA_URL = "http://mcp-infra:8010"

INBOX_BUCKET = Variable.get(
    "ses_inbox_bucket", default_var="modecissions-mail-inbound-36243c"
)
INBOX_PREFIX = Variable.get("ses_inbox_prefix", default_var="inbound/")
DONE_PREFIX = Variable.get(
    "ses_inbox_processed_prefix", default_var="inbound-processed/"
)

LAKE_BUCKET = Variable.get(
    "lakehouse_bucket", default_var="modecissions-lakehouse-0baf85"
)
UPLOADS_PREFIX = Variable.get(
    "lakehouse_uploads_prefix", default_var="uploads/replicon/in/"
)

# Attachments we actually want to land on the lakehouse.
PASSTHROUGH_EXTS = {".xlsx", ".xls", ".csv", ".pdf", ".tsv", ".txt"}
# Filenames produced by SES tooling that are not real mail.
SKIP_NAMES = {"AMAZON_SES_SETUP_NOTIFICATION"}
MAX_ZIP_MEMBERS = int(os.environ.get("SES_IMPORT_MAX_ZIP_MEMBERS", "100"))
MAX_ZIP_MEMBER_BYTES = int(
    os.environ.get("SES_IMPORT_MAX_ZIP_MEMBER_BYTES", str(50 * 1024 * 1024))
)
MAX_ZIP_TOTAL_BYTES = int(
    os.environ.get("SES_IMPORT_MAX_ZIP_TOTAL_BYTES", str(250 * 1024 * 1024))
)
_SAFE_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.:-]+")


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _ses_inbox_enabled() -> bool:
    raw = os.environ.get("REPLICON_SES_INBOX_ENABLED")
    if raw is None:
        raw = Variable.get("replicon_ses_inbox_enabled", default_var="false")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _require_ses_inbox_enabled() -> None:
    if not _ses_inbox_enabled():
        raise RuntimeError("ses_inbox_disabled")


def _internal_key(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError(
        f"{env_name} missing; legacy INTERNAL_API_KEY fallback is disabled in production"
    )


default_args = {
    "owner": "replicon",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


def _pause_scheduled_dag_on_creation() -> bool:
    value = (
        os.environ.get("AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION")
        or os.environ.get("AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION")
        or ""
    )
    return value.strip().lower() in {"1", "true", "yes", "on"}


dag = DAG(
    dag_id="replicon_ses_inbox_import",
    default_args=default_args,
    description="Pull SES inbound emails from S3, extract attachments, land in uploads/replicon/in/",
    schedule_interval="*/15 * * * *" if _ses_inbox_enabled() else None,
    start_date=datetime(2026, 5, 7),
    tags=["replicon", "ses", "ingest", "inbound"],
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=_pause_scheduled_dag_on_creation(),
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _endpoint_url(value: str) -> str:
    endpoint = str(value or "").strip()
    if not endpoint or "://" in endpoint:
        return endpoint
    return f"https://{endpoint}"


def _client_with_pair(*, endpoint: str, region: str, access: str, secret: str):
    if bool(access) != bool(secret):
        raise RuntimeError("complete object-storage credential pair is required")
    kwargs = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = _endpoint_url(endpoint)
    if access and secret:
        kwargs["aws_access_key_id"] = access
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("s3", **kwargs)


def _inbox_s3():
    """Return the native AWS/SES client, never the GCS lakehouse client."""

    _require_ses_inbox_enabled()
    region = (
        os.environ.get("SES_INBOX_AWS_REGION")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    if region.strip().lower() == "auto":
        region = "us-east-1"
    endpoint = os.environ.get("SES_INBOX_S3_ENDPOINT_URL", "").strip()
    if not endpoint and not _is_production():
        endpoint = os.environ.get("MINIO_ENDPOINT") or Variable.get(
            "minio_endpoint", default_var=""
        )
    dedicated_access = os.environ.get("SES_INBOX_AWS_ACCESS_KEY_ID", "").strip()
    dedicated_secret = os.environ.get("SES_INBOX_AWS_SECRET_ACCESS_KEY", "").strip()
    if bool(dedicated_access) != bool(dedicated_secret):
        raise RuntimeError("ses_inbox_credentials_missing")
    provider = os.environ.get("LAKEHOUSE_PROVIDER", "").strip().lower()
    if provider == "gcs" and not (dedicated_access and dedicated_secret):
        # A GCP runtime has no AWS instance role. Never borrow GCS or generic
        # lakehouse credentials for the separate SES inbox.
        raise RuntimeError("ses_inbox_credentials_missing")
    access = dedicated_access
    secret = dedicated_secret
    if provider != "gcs" and not access:
        # Native AWS deployments may keep their existing static pair or use
        # the boto workload identity chain when both values are absent.
        access = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    return _client_with_pair(
        endpoint=endpoint,
        region=region,
        access=access,
        secret=secret,
    )


def _lakehouse_s3():
    """Return the active lakehouse client with provider-native credentials."""

    provider = os.environ.get("LAKEHOUSE_PROVIDER", "").strip().lower()
    endpoint = os.environ.get("LAKEHOUSE_ENDPOINT", "").strip()
    if provider == "gcs" or "storage.googleapis.com" in endpoint.lower():
        gcs_access = os.environ.get("GCS_ACCESS_KEY_ID", "").strip()
        gcs_secret = os.environ.get("GCS_SECRET_ACCESS_KEY", "").strip()
        if not gcs_access or not gcs_secret:
            raise RuntimeError("complete GCS lakehouse credentials are required")
        return _client_with_pair(
            endpoint=endpoint or "storage.googleapis.com",
            region="auto",
            access=gcs_access,
            secret=gcs_secret,
        )
    minio_endpoint = os.environ.get("MINIO_ENDPOINT") or Variable.get(
        "minio_endpoint", default_var=""
    )
    secure = (
        os.environ.get("MINIO_SECURE")
        or Variable.get("minio_secure", default_var="false")
    ).lower() == "true"
    return _client_with_pair(
        endpoint=(f"{'https' if secure else 'http'}://{minio_endpoint}" if minio_endpoint else ""),
        region=os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1",
        access=os.environ.get("MINIO_ACCESS_KEY")
        or Variable.get("minio_access_key", default_var=""),
        secret=os.environ.get("MINIO_SECRET_KEY")
        or Variable.get("minio_secret_key", default_var=""),
    )


def _lakehouse_bucket() -> str:
    return (
        os.environ.get("GCS_BUCKET")
        or os.environ.get("LAKEHOUSE_BUCKET")
        or os.environ.get("MINIO_BUCKET")
        or LAKE_BUCKET
    )


def _safe_name(name: str) -> str:
    base = os.path.basename(name).strip()
    for ch in ("..", "/", "\\"):
        base = base.replace(ch, "")
    return base or "unnamed"


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
        "replicon_allow_unscoped_ses_uploads", default_var="false"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return "", ""
    raise ValueError(
        "tenant_id and workspace_id are required for replicon_ses_inbox_import"
    )


def _scoped_uploads_prefix(tenant_id: str, workspace_id: str) -> str:
    prefix = (UPLOADS_PREFIX or "uploads/replicon/in/").strip().strip("/")
    if "tenant_id=" in prefix or "workspace_id=" in prefix:
        expected = f"tenant_id={tenant_id}/workspace_id={workspace_id}"
        if tenant_id and workspace_id and expected not in prefix:
            raise ValueError(
                "lakehouse_uploads_prefix scope does not match dag_run scope"
            )
        return f"{prefix}/"

    parts = [part for part in prefix.split("/") if part]
    if parts and parts[-1] == "in":
        parts = parts[:-1]
    if tenant_id and workspace_id:
        parts.extend([f"tenant_id={tenant_id}", f"workspace_id={workspace_id}", "in"])
    else:
        parts.append("in")
    return "/".join(parts) + "/"


def _is_passthrough(name: str) -> bool:
    return os.path.splitext(name.lower())[1] in PASSTHROUGH_EXTS


def _safe_zip_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError(f"zip has too many members ({len(infos)} > {MAX_ZIP_MEMBERS})")
    total = 0
    safe: list[zipfile.ZipInfo] = []
    for info in infos:
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"zip member too large: {info.filename}")
        total += int(info.file_size or 0)
        if total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("zip uncompressed total is too large")
        safe.append(info)
    return safe


def _pipeline_run_save(**kwargs) -> None:
    """Best-effort write to pipeline_runs through mcp-infra."""
    try:
        headers = {
            "x-api-key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA"),
            "x-internal-service": "airflow",
        }
        args = {
            "dag_id": "replicon_ses_inbox_import",
            "cartridge_id": CARTRIDGE_ID,
            "entity": ENTITY,
            **kwargs,
        }
        response = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json={
                "tool": "pipeline_run_save",
                "args": args,
                "security_context": build_pipeline_run_context(args),
            },
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
    except Exception:
        pass


# ── Tasks ────────────────────────────────────────────────────────────────────


def list_inbox(**context) -> dict:
    """List unprocessed mail objects in s3://INBOX_BUCKET/INBOX_PREFIX.
    Pushes the list of keys to XCom for the next task."""
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = context["run_id"]
    tenant_id, workspace_id = _scope_values(context)
    upload_prefix = _scoped_uploads_prefix(tenant_id, workspace_id)

    _pipeline_run_save(
        run_id=run_id,
        status="running",
        started_at=started_at,
        mode="full",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        storage_uri=f"s3://{_lakehouse_bucket()}/{upload_prefix}",
    )

    s3 = _inbox_s3()
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=INBOX_BUCKET, Prefix=INBOX_PREFIX):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            base = os.path.basename(key)
            if not base or base in SKIP_NAMES or key.endswith("/"):
                continue
            keys.append(key)

    print(f"📬 {len(keys)} new mail object(s) in s3://{INBOX_BUCKET}/{INBOX_PREFIX}")
    for k in keys[:10]:
        print(f"  · {k}")
    if len(keys) > 10:
        print(f"  · …and {len(keys) - 10} more")

    ti = context["task_instance"]
    ti.xcom_push(key="keys", value=keys)
    ti.xcom_push(key="started_at", value=started_at)
    ti.xcom_push(key="run_id", value=run_id)
    ti.xcom_push(key="tenant_id", value=tenant_id)
    ti.xcom_push(key="workspace_id", value=workspace_id)
    ti.xcom_push(key="upload_prefix", value=upload_prefix)
    return {"messages_found": len(keys)}


def extract_attachments(**context) -> dict:
    """For every queued mail object, pull the .eml from S3, parse it, and
    upload each interesting attachment under the scoped Replicon upload prefix.
    ZIPs are expanded. Records which keys succeeded so the next task can
    archive them."""
    ti = context["task_instance"]
    keys = ti.xcom_pull(task_ids="list_inbox", key="keys") or []
    upload_prefix = ti.xcom_pull(task_ids="list_inbox", key="upload_prefix")
    if not upload_prefix:
        tenant_id, workspace_id = _scope_values(context)
        upload_prefix = _scoped_uploads_prefix(tenant_id, workspace_id)

    inbox_s3 = _inbox_s3()
    lakehouse_s3 = _lakehouse_s3()
    lakehouse_bucket = _lakehouse_bucket()
    succeeded: list[str] = []
    failed: list[dict] = []
    landed_total = 0
    bytes_total = 0

    for key in keys:
        try:
            raw = inbox_s3.get_object(Bucket=INBOX_BUCKET, Key=key)["Body"].read()
            msg: Message = email.message_from_bytes(raw)
            print(
                f"\n📩 {key}\n   From: {msg.get('From', '?')}\n   Subject: {msg.get('Subject', '?')}"
            )

            for part in msg.walk():
                if part.get_content_disposition() != "attachment":
                    continue
                filename = part.get_filename()
                if not filename:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                name = _safe_name(filename)
                if name.lower().endswith(".zip"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                            for member in _safe_zip_members(zf):
                                inner_name = _safe_name(member.filename)
                                if not _is_passthrough(inner_name):
                                    print(
                                        f"   skip (zip member, unknown ext): {inner_name}"
                                    )
                                    continue
                                inner = zf.read(member)
                                target_key = f"{upload_prefix}{inner_name}"
                                lakehouse_s3.put_object(
                                    Bucket=lakehouse_bucket, Key=target_key, Body=inner
                                )
                                landed_total += 1
                                bytes_total += len(inner)
                                print(
                                    f"   ✓ {inner_name} ({len(inner)} bytes) → s3://{lakehouse_bucket}/{target_key}"
                                )
                    except zipfile.BadZipFile as exc:
                        print(f"   ✗ corrupt zip {name}: {exc}")
                    except ValueError as exc:
                        print(f"   ✗ rejected zip {name}: {exc}")
                    continue

                if not _is_passthrough(name):
                    print(f"   skip (unknown ext): {name}")
                    continue

                target_key = f"{upload_prefix}{name}"
                lakehouse_s3.put_object(
                    Bucket=lakehouse_bucket, Key=target_key, Body=payload
                )
                landed_total += 1
                bytes_total += len(payload)
                print(
                    f"   ✓ {name} ({len(payload)} bytes) → s3://{lakehouse_bucket}/{target_key}"
                )

            succeeded.append(key)
        except Exception as exc:  # noqa: BLE001
            print(f"   ✗ failed {key}: {exc}")
            failed.append({"key": key, "error": str(exc)})

    print(
        f"\nExtracted: {landed_total} attachments ({bytes_total} bytes); "
        f"{len(succeeded)} mails OK, {len(failed)} failed"
    )

    ti.xcom_push(key="succeeded", value=succeeded)
    ti.xcom_push(key="failed", value=failed)
    ti.xcom_push(key="landed_total", value=landed_total)
    ti.xcom_push(key="bytes_total", value=bytes_total)
    return {
        "landed": landed_total,
        "bytes": bytes_total,
        "succeeded": len(succeeded),
        "failed": len(failed),
    }


def archive_processed(**context) -> dict:
    """Move every successfully-processed mail object out of inbound/ to
    inbound-processed/. Failed messages stay in inbound/ for retry/inspection."""
    ti = context["task_instance"]
    succeeded = ti.xcom_pull(task_ids="extract_attachments", key="succeeded") or []

    s3 = _inbox_s3()
    moved = 0
    for key in succeeded:
        done_key = key.replace(INBOX_PREFIX, DONE_PREFIX, 1)
        try:
            s3.copy_object(
                Bucket=INBOX_BUCKET,
                Key=done_key,
                CopySource={"Bucket": INBOX_BUCKET, "Key": key},
            )
            s3.delete_object(Bucket=INBOX_BUCKET, Key=key)
            moved += 1
            print(f"   → archived {key} as {done_key}")
        except Exception as exc:  # noqa: BLE001
            print(f"   ✗ archive failed for {key}: {exc}")

    print(f"\nArchived: {moved}/{len(succeeded)}")
    ti.xcom_push(key="archived", value=moved)
    return {"archived": moved}


def record_run(**context) -> dict:
    """Final write to pipeline_runs so the studio entity row reflects the
    run's status, end time and counts."""
    ti = context["task_instance"]
    keys = ti.xcom_pull(task_ids="list_inbox", key="keys") or []
    succeeded = ti.xcom_pull(task_ids="extract_attachments", key="succeeded") or []
    failed = ti.xcom_pull(task_ids="extract_attachments", key="failed") or []
    landed = ti.xcom_pull(task_ids="extract_attachments", key="landed_total") or 0
    written = ti.xcom_pull(task_ids="extract_attachments", key="bytes_total") or 0
    archived = ti.xcom_pull(task_ids="archive_processed", key="archived") or 0
    started = ti.xcom_pull(task_ids="list_inbox", key="started_at")
    run_id = ti.xcom_pull(task_ids="list_inbox", key="run_id") or context["run_id"]
    tenant_id = ti.xcom_pull(task_ids="list_inbox", key="tenant_id")
    workspace_id = ti.xcom_pull(task_ids="list_inbox", key="workspace_id")
    upload_prefix = ti.xcom_pull(task_ids="list_inbox", key="upload_prefix")
    if not upload_prefix:
        tenant_id, workspace_id = _scope_values(context)
        upload_prefix = _scoped_uploads_prefix(tenant_id, workspace_id)

    finished = datetime.now(timezone.utc).isoformat()
    duration = None
    try:
        if started:
            duration = (
                datetime.fromisoformat(finished) - datetime.fromisoformat(started)
            ).total_seconds()
    except Exception:
        pass

    error_msg = None
    if failed:
        first = failed[0]
        error_msg = f"{first.get('key', '?')}: {first.get('error', '?')}"
        if len(failed) > 1:
            error_msg += f" (+{len(failed) - 1} more)"

    status = "success" if not failed else ("partial" if succeeded else "failed")

    _pipeline_run_save(
        run_id=run_id,
        status=status,
        started_at=started,
        finished_at=finished,
        duration_seconds=duration,
        record_count=landed,
        bytes_written=written,
        storage_uri=f"s3://{_lakehouse_bucket()}/{upload_prefix}",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        error_message=error_msg,
        extra={
            "messages_seen": len(keys),
            "messages_ok": len(succeeded),
            "messages_failed": len(failed),
            "archived": archived,
        },
    )

    print(f"\n=== Run summary ===")
    print(f"  status:        {status}")
    print(f"  messages seen: {len(keys)}")
    print(f"  messages ok:   {len(succeeded)} (archived {archived})")
    print(f"  messages fail: {len(failed)}")
    print(f"  attachments:   {landed} ({written} bytes)")
    if error_msg:
        print(f"  first error:   {error_msg}")

    return {
        "status": status,
        "landed": landed,
        "archived": archived,
        "failed": len(failed),
    }


# ── DAG wiring ───────────────────────────────────────────────────────────────

t_list = PythonOperator(
    task_id="list_inbox",
    python_callable=list_inbox,
    dag=dag,
)

t_extract = PythonOperator(
    task_id="extract_attachments",
    python_callable=extract_attachments,
    dag=dag,
)

t_archive = PythonOperator(
    task_id="archive_processed",
    python_callable=archive_processed,
    dag=dag,
)

t_record = PythonOperator(
    task_id="record_run",
    python_callable=record_run,
    trigger_rule="all_done",  # always summarize, even if upstream failed
    dag=dag,
)

t_list >> t_extract >> t_archive >> t_record
