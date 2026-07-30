from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

import requests


AIRFLOW = os.environ["AIRFLOW_URL"].rstrip("/")
AIRFLOW_AUTH = (
    os.environ["AIRFLOW_ADMIN_USER"],
    os.environ["AIRFLOW_ADMIN_PASSWORD"],
)
CONSOLE = os.environ["CONSOLE_URL"].rstrip("/")
REFINEMENT = os.environ["REFINEMENT_URL"].rstrip("/")


def _airflow(method: str, path: str, **kwargs) -> requests.Response:
    return requests.request(
        method,
        f"{AIRFLOW}{path}",
        auth=AIRFLOW_AUTH,
        timeout=15,
        **kwargs,
    )


def _activate_required_dag(dag_id: str) -> bool:
    response = _airflow(
        "PATCH",
        f"/api/v1/dags/{dag_id}",
        json={"is_paused": False},
    )
    return response.status_code == 200 and response.json().get("is_paused") is False


def wait_for_airflow_contract(timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            health = _airflow("GET", "/health")
            file_dag = _airflow("GET", "/api/v1/dags/file_ingest")
            chain_dag = _airflow("GET", "/api/v1/dags/dataset_refresh_chain")
            body = health.json()
            scheduler = body.get("scheduler", {}).get("status")
            required_dags_active = False
            if file_dag.status_code == 200 and chain_dag.status_code == 200:
                required_dags_active = all(
                    _activate_required_dag(dag_id)
                    for dag_id in ("file_ingest", "dataset_refresh_chain")
                )
            if (
                health.status_code == 200
                and scheduler == "healthy"
                and file_dag.status_code == 200
                and chain_dag.status_code == 200
                and not file_dag.json().get("has_import_errors")
                and not chain_dag.json().get("has_import_errors")
                and required_dags_active
            ):
                return
            last = f"health={body!r} file={file_dag.status_code} chain={chain_dag.status_code}"
        except Exception as exc:  # noqa: BLE001 - bounded external readiness
            last = type(exc).__name__
        time.sleep(2)
    raise AssertionError(f"Airflow functional readiness timeout: {last}")


def trigger_file_ingest(scope: dict[str, str]) -> str:
    run_id = f"operational_truth_e2e__{scope['label']}__{uuid.uuid4().hex}"
    response = _airflow(
        "POST",
        "/api/v1/dags/file_ingest/dagRuns",
        json={
            "dag_run_id": run_id,
            "conf": {
                "cartridge_id": "replicon",
                "entity": "OperationalTruthProbe",
                "file_pattern": "operational-truth.csv",
                "format": "csv",
                "parser": "default",
                "tenant_id": scope["tenant_id"],
                "workspace_id": scope["workspace_id"],
            },
        },
    )
    assert response.status_code == 200, response.text[:500]
    assert response.json()["dag_run_id"] == run_id
    return run_id


def wait_dag_run(
    dag_id: str,
    run_id: str,
    timeout: int = 420,
    *,
    expected_state: str = "success",
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = "unknown"
    while time.monotonic() < deadline:
        try:
            response = _airflow("GET", f"/api/v1/dags/{dag_id}/dagRuns/{run_id}")
            assert response.status_code == 200, response.text[:500]
            body = response.json()
            last = str(body.get("state") or "unknown")
            if body.get("state") in {"success", "failed"}:
                assert body["state"] == expected_state, body
                return body
        except requests.RequestException as exc:
            last = type(exc).__name__
        time.sleep(2)
    raise AssertionError(f"{dag_id}/{run_id} did not finish: {last}")


def trigger_dataset_chain(scope: dict[str, str], *, run_id: str | None = None) -> str:
    selected = run_id or f"operational_truth_canary__{uuid.uuid4().hex}"
    response = _airflow(
        "POST",
        "/api/v1/dags/dataset_refresh_chain/dagRuns",
        json={
            "dag_run_id": selected,
            "conf": {
                "cartridge_id": "replicon",
                "seed_dataset": "pnl_mensual",
                "tenant_id": scope["tenant_id"],
                "workspace_id": scope["workspace_id"],
            },
        },
    )
    assert response.status_code == 200, response.text[:500]
    assert response.json()["dag_run_id"] == selected
    return selected


def assert_airflow_replay_rejected(run_id: str, scope: dict[str, str]) -> None:
    response = _airflow(
        "POST",
        "/api/v1/dags/file_ingest/dagRuns",
        json={
            "dag_run_id": run_id,
            "conf": {
                "cartridge_id": "replicon",
                "entity": "OperationalTruthProbe",
                "file_pattern": "operational-truth.csv",
                "tenant_id": scope["tenant_id"],
                "workspace_id": scope["workspace_id"],
            },
        },
    )
    assert response.status_code == 409


def wait_chain_for_workspace(workspace_id: str, timeout: int = 480) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = "no matching run"
    while time.monotonic() < deadline:
        try:
            response = _airflow(
                "GET",
                "/api/v1/dags/dataset_refresh_chain/dagRuns",
                params={"limit": 20, "order_by": "-execution_date"},
            )
            assert response.status_code == 200, response.text[:500]
            for run in response.json().get("dag_runs", []):
                run_id = str(run.get("dag_run_id") or "")
                detail = _airflow(
                    "GET", f"/api/v1/dags/dataset_refresh_chain/dagRuns/{run_id}"
                )
                assert detail.status_code == 200, detail.text[:500]
                body = detail.json()
                if str((body.get("conf") or {}).get("workspace_id")) != workspace_id:
                    continue
                last = str(body.get("state") or "unknown")
                if body.get("state") in {"success", "failed"}:
                    assert body["state"] == "success", body
                    return body
        except requests.RequestException as exc:
            last = type(exc).__name__
        time.sleep(2)
    raise AssertionError(
        f"refresh chain for workspace {workspace_id} did not finish: {last}"
    )


def login_and_control_room_diagnostics(email: str, password: str) -> dict[str, Any]:
    session = requests.Session()
    login_page = session.get(f"{CONSOLE}/login", timeout=15)
    assert login_page.status_code == 200
    csrf = session.cookies.get("csrf_token")
    assert csrf
    login = session.post(
        f"{CONSOLE}/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf, "Cookie": f"csrf_token={csrf}"},
        timeout=20,
    )
    assert login.status_code == 200, login.text[:500]
    token = login.json()["access_token"]
    response = session.get(
        f"{CONSOLE}/api/control-room/diagnostics",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert response.status_code == 200, response.text[:500]
    return response.json()


def _signed_context(scope: dict[str, str], signed_at: int) -> dict[str, Any]:
    cartridge = "replicon"
    marker = f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
    context: dict[str, Any] = {
        "trusted": True,
        "source": "airflow",
        "audience": "refinement",
        "purpose": "refinement.mcp.materialize",
        "tool": "materialize",
        "dataset": "pnl_mensual",
        "user_id": "airflow:dataset_refresh_chain",
        "role": "admin",
        "workspace_role": "service",
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "permissions": ["datasets.read", "datasets.write"],
        "allowed_cartridges": [cartridge],
        "allowed_prefixes": [
            f"raw/{cartridge}/",
            f"silver/{cartridge}/{marker}",
            f"gold/{cartridge}/{marker}",
            f"uploads/{cartridge}/{marker}",
            f"cartridges/{cartridge}/",
        ],
        "_signed_at": signed_at,
        "_signature_version": "hmac-sha256-v1",
    }
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    context["_signature"] = hmac.new(
        os.environ["SECURITY_CONTEXT_SIGNING_KEY"].encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    return context


def assert_refinement_rejects_bad_signatures(scope: dict[str, str]) -> None:
    body = {"tool": "materialize", "args": {"name": "pnl_mensual"}}
    headers = {
        "X-Internal-Service": "airflow",
        "X-API-Key": os.environ["INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT"],
    }
    invalid = _signed_context(scope, int(time.time()))
    invalid["_signature"] = "0" * 64
    expired = _signed_context(scope, int(time.time()) - 600)
    for context in (invalid, expired):
        response = requests.post(
            f"{REFINEMENT}/mcp/invoke",
            json={**body, "security_context": context},
            headers=headers,
            timeout=20,
        )
        assert response.status_code == 403
