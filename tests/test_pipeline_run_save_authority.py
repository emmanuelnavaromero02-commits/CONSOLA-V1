from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
TENANT_SCOPED_CALLERS = (
    "airflow/dags/agent_runner_record.py",
    "airflow/dags/dataset_refresh_chain.py",
    "airflow/dags/entity_scheduler_record.py",
    "airflow/dags/file_ingest.py",
    "airflow/dags/replicon_ses_inbox_import.py",
    "cartridges/replicon/dags/replicon_ses_inbox_import.py",
    "cartridges/sap_successfactors/dags/sap_successfactors_extract.py",
    "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py",
    "console/app/services/dag_code_generator.py",
    "console/app/services/dag_templates.py",
)
SERVICE_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture
def mcp_main(monkeypatch):
    _purge_app()
    sys.path[:] = [
        path
        for path in sys.path
        if not any(marker in path for marker in SERVICE_MARKERS)
    ]
    monkeypatch.syspath_prepend(str(ROOT / "mcp-infra"))
    for name, value in {
        "APP_ENV": "test",
        "INTERNAL_API_KEY": "transport-key-that-is-long-enough-123456",
        "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA": "airflow-pair-key-123456",
        "SECURITY_CONTEXT_SIGNING_KEY": "signing-key-distinct-and-long-enough-123456",
        "PG_PASSWORD": "postgres-password",
        "AIRFLOW_USER": "airflow",
        "AIRFLOW_PASSWORD": "airflow-password",
        "SUPERSET_USER": "superset",
        "SUPERSET_PASSWORD": "superset-password",
    }.items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module("app.main")
    yield module
    _purge_app()


def _args() -> dict[str, object]:
    return {
        "run_id": "scheduled__scope-b",
        "dag_id": "dataset_refresh_chain",
        "cartridge_id": "replicon",
        "entity": "employees",
        "status": "success",
        "tenant_id": "33333333-3333-3333-3333-333333333333",
        "workspace_id": "44444444-4444-4444-4444-444444444444",
    }


def test_airflow_pair_key_alone_cannot_choose_pipeline_scope(mcp_main) -> None:
    request = mcp_main.InvokeRequest(tool="pipeline_run_save", args=_args())
    with pytest.raises(HTTPException) as caught:
        mcp_main._enforce_data_scope(request, "airflow")
    assert caught.value.status_code == 403


def test_pipeline_telemetry_request_rejects_unsigned_extra_fields(mcp_main) -> None:
    with pytest.raises(ValidationError):
        mcp_main.InvokeRequest(
            tool="pipeline_run_save",
            args=_args(),
            security_context=None,
            unsigned_extra="not-bound",
        )


def test_every_productive_pipeline_telemetry_caller_uses_the_v2_signer() -> None:
    for relative in TENANT_SCOPED_CALLERS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "pipeline_run_save" in source, relative
        assert "build_pipeline_run_context" in source, relative
