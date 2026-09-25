"""Refresh sap_b1 silver and gold after each delivery of the Windows push agent."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from airflow.decorators import dag, task
from airflow.models import Variable


CARTRIDGE_ID = "sap_b1"
DAG_ID = "sap_b1_refresh"
MARKER_ENTITY = "IntercompanyPartners"
SCOPES_VARIABLE = "sap_b1_push_scopes"
SILENCE_LIMIT = timedelta(hours=6)
AIRFLOW_URL = os.environ.get("AIRFLOW_URL", "http://airflow:8080")
AIRFLOW_USER = os.environ.get("AIRFLOW_USER") or os.environ.get("AIRFLOW_ADMIN_USER") or "admin"
AIRFLOW_PASSWORD = os.environ.get("AIRFLOW_PASSWORD") or os.environ.get("AIRFLOW_ADMIN_PASSWORD") or "admin"
CARTRIDGE_URL = os.environ.get("SAP_B1_URL", "http://sap-b1:8206")

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MARKER_RE = re.compile(
    rf"/load_date=(\d{{4}}-\d{{2}}-\d{{2}})/batch_id=([A-Za-z0-9_.:-]+)/{MARKER_ENTITY}\.parquet$"
)


def parse_scopes(raw: str) -> list[tuple[str, str]]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SCOPES_VARIABLE} is not valid JSON") from exc
    if not isinstance(items, list):
        raise ValueError(f"{SCOPES_VARIABLE} must be a list")
    scopes: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"tenant_id", "workspace_id"}:
            raise ValueError(f"{SCOPES_VARIABLE} entries need exactly tenant_id and workspace_id")
        scope = (str(item["tenant_id"]).strip().lower(), str(item["workspace_id"]).strip().lower())
        if not all(_UUID_RE.fullmatch(value) for value in scope):
            raise ValueError(f"{SCOPES_VARIABLE} entries must be UUIDs")
        if scope in scopes:
            raise ValueError(f"{SCOPES_VARIABLE} repeats a scope")
        scopes.append(scope)
    return scopes


def marker_prefixes(tenant_id: str, workspace_id: str, now: datetime) -> list[str]:
    base = f"raw/{CARTRIDGE_ID}/{MARKER_ENTITY}/tenant_id={tenant_id}/workspace_id={workspace_id}/"
    days = (now.date(), (now - timedelta(days=1)).date())
    return [f"{base}load_date={day.isoformat()}/" for day in days]


def latest_delivery(objects: list[dict[str, Any]]) -> dict[str, Any] | None:
    deliveries = []
    for item in objects:
        match = _MARKER_RE.search(str(item.get("Key") or ""))
        modified = item.get("LastModified")
        if match and isinstance(modified, datetime):
            deliveries.append({"batch_id": match.group(2), "load_date": match.group(1), "delivered_at": modified})
    return max(deliveries, key=lambda item: (item["delivered_at"], item["batch_id"]), default=None)


def upstream_context(tenant_id: str, workspace_id: str) -> dict[str, Any]:
    from sap_b1_security_context import sap_b1_security_context

    return sap_b1_security_context(tenant_id, workspace_id, user_id=f"airflow:{DAG_ID}")


def refresh_trigger(tenant_id: str, workspace_id: str, batch_id: str) -> dict[str, Any]:
    from dataset_refresh_admission import build_dataset_refresh_trigger

    return build_dataset_refresh_trigger(
        upstream_context=upstream_context(tenant_id, workspace_id),
        conf={
            "seed_raw": f"raw/{CARTRIDGE_ID}/*",
            "cartridge_id": CARTRIDGE_ID,
            "triggered_by": DAG_ID,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "skip_intelligence": True,
        },
        source_dag_run_id=f"sap_b1_push:{tenant_id}:{workspace_id}:{batch_id}",
        prefix="sap_b1_push",
    )


def _lakehouse() -> tuple[Any, str]:
    import boto3

    if os.environ.get("LAKEHOUSE_PROVIDER", "").strip().lower() == "gcs":
        raise RuntimeError("sap_b1 push deliveries live in S3")
    bucket = (os.environ.get("LAKEHOUSE_BUCKET") or os.environ.get("MINIO_BUCKET") or "").strip()
    if not bucket:
        raise RuntimeError("LAKEHOUSE_BUCKET is required")
    endpoint = (os.environ.get("MINIO_ENDPOINT") or "").strip()
    secure = (os.environ.get("MINIO_SECURE") or "false").strip().lower() == "true"
    access = (os.environ.get("MINIO_ACCESS_KEY") or "").strip()
    secret = (os.environ.get("MINIO_SECRET_KEY") or "").strip()
    if bool(access) != bool(secret):
        raise RuntimeError("complete object-storage credential pair is required")
    kwargs: dict[str, Any] = {
        "region_name": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint if "://" in endpoint else f"{'https' if secure else 'http'}://{endpoint}"
    if access:
        kwargs["aws_access_key_id"] = access
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("s3", **kwargs), bucket


def _list(client: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents") or [])
    return objects


def _internal_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE", "")
    if not key:
        raise RuntimeError("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE missing")
    return key


def refresh_parameters(tenant_id: str, workspace_id: str) -> None:
    import requests

    response = requests.post(
        f"{CARTRIDGE_URL}/business-parameters/refresh",
        params={"refresh_silver": "false"},
        json={"security_context": upstream_context(tenant_id, workspace_id)},
        headers={"X-Api-Key": _internal_key(), "X-Internal-Service": "airflow"},
        timeout=120,
    )
    response.raise_for_status()


def chain_run_exists(run_id: str) -> bool:
    import requests
    from urllib.parse import quote

    response = requests.get(
        f"{AIRFLOW_URL.rstrip('/')}/api/v1/dags/dataset_refresh_chain/dagRuns/{quote(run_id, safe='')}",
        auth=(AIRFLOW_USER, AIRFLOW_PASSWORD),
        timeout=30,
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def refresh_scopes(
    scopes: list[tuple[str, str]],
    *,
    now: datetime,
    list_objects: Any,
    trigger: Any,
    run_exists: Any = lambda run_id: False,
    parameters: Any = lambda tenant_id, workspace_id: None,
) -> dict[str, Any]:
    triggered, already_done, silent, failed = [], [], [], []
    for tenant_id, workspace_id in scopes:
        label = f"{tenant_id}/{workspace_id}"
        try:
            objects = [item for prefix in marker_prefixes(tenant_id, workspace_id, now) for item in list_objects(prefix)]
            delivery = latest_delivery(objects)
            if delivery is None or now - delivery["delivered_at"] > SILENCE_LIMIT:
                silent.append(label)
            if delivery is None:
                continue
            admission = refresh_trigger(tenant_id, workspace_id, delivery["batch_id"])
            if run_exists(admission["dag_run_id"]):
                already_done.append(label)
                continue
            parameters(tenant_id, workspace_id)
            status = trigger(admission["dag_run_id"], admission["conf"])
            (already_done if status == 409 else triggered).append(label)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{label}: {type(exc).__name__}")
    summary = {"triggered": triggered, "already_done": already_done, "silent": silent, "failed": failed}
    if failed:
        raise RuntimeError(f"sap_b1 refresh failed for {len(failed)} scope(s): {failed}")
    if silent:
        raise RuntimeError(f"no sap_b1 delivery in the last {SILENCE_LIMIT} for: {silent}")
    return summary


@dag(
    dag_id=DAG_ID,
    schedule="*/30 * * * *",
    start_date=datetime(2026, 9, 25, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={"owner": "omega", "retries": 0},
    tags=[CARTRIDGE_ID, "refresh"],
)
def sap_b1_refresh():
    @task
    def refresh_deliveries() -> dict[str, Any]:
        from entity_scheduler_trigger import trigger_dag_run

        scopes = parse_scopes(Variable.get(SCOPES_VARIABLE, default_var="[]"))
        if not scopes:
            return {"triggered": [], "already_done": [], "silent": [], "failed": []}
        client, bucket = _lakehouse()
        return refresh_scopes(
            scopes,
            now=datetime.now(timezone.utc),
            list_objects=lambda prefix: _list(client, bucket, prefix),
            trigger=lambda run_id, conf: trigger_dag_run(
                base_url=AIRFLOW_URL,
                dag_id="dataset_refresh_chain",
                run_id=run_id,
                conf=conf,
                username=AIRFLOW_USER,
                password=AIRFLOW_PASSWORD,
            ),
            run_exists=chain_run_exists,
            parameters=refresh_parameters,
        )

    refresh_deliveries()


dag = sap_b1_refresh()
