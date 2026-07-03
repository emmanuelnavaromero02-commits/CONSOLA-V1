from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.pipeline import sync_state


def test_sync_state_clean_mode_and_target_raise_http_errors():
    assert sync_state.sync_clean_mode(" full ") == "full"
    assert sync_state.sync_clean_target(" talent ") == "talent"

    with pytest.raises(HTTPException) as mode_exc:
        sync_state.sync_clean_mode("bad")
    assert mode_exc.value.status_code == 400
    assert "mode must be incremental or full" in str(mode_exc.value.detail)

    with pytest.raises(HTTPException) as target_exc:
        sync_state.sync_clean_target("bad")
    assert target_exc.value.status_code == 400
    assert "target must be all, foundation or talent" in str(target_exc.value.detail)


def test_pipeline_extract_all_validation_errors_are_http_errors():
    with pytest.raises(HTTPException) as exc:
        sync_state.pipeline_extract_all_mode_target(
            {"mode": "incremental", "target": "unknown"}
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid extract target"


def test_active_extract_run_payload_keeps_legacy_job_fields():
    payload = sync_state.active_extract_run_payload(
        row={
            "run_id": "pipeline-run-1",
            "airflow_dag_run_id": "dag-run-1",
            "status": "RUNNING",
        },
        cartridge="sap_successfactors",
        entity="EmpJob",
        dag_id="sap_successfactors_extract",
        conf={"entity": "EmpJob"},
        reason="active_entity_run",
    )

    assert payload == {
        "triggered": False,
        "reused": True,
        "cartridge": "sap_successfactors",
        "entity": "EmpJob",
        "dag_id": "sap_successfactors_extract",
        "job_id": "dag-run-1",
        "run_id": "dag-run-1",
        "dag_run_id": "dag-run-1",
        "state": "running",
        "reason": "active_entity_run",
        "conf": {"entity": "EmpJob"},
    }


def test_sync_now_lock_key_uses_security_scope():
    lock_key = sync_state.sync_now_lock_key(
        cartridge="sap_successfactors",
        mode="incremental",
        target="talent",
        conn_id="femsa_sf",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
    )

    assert lock_key == (
        "sync-now:tenant-1:workspace-1:sap_successfactors:incremental:talent:femsa_sf"
    )


def test_active_sync_run_lookup_parts_include_optional_columns():
    lookup = sync_state.active_sync_run_lookup_parts(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        has_mode=True,
        has_extra=True,
        has_started_at=True,
    )

    assert lookup["args"][0] == "sap_successfactors"
    assert lookup["args"][2:] == ["incremental", "all"]
    assert "COALESCE(mode, $3)=$3" in lookup["clauses"]
    assert "COALESCE(extra->>'target', 'all')=$4" in lookup["clauses"]
    assert any("started_at >" in clause for clause in lookup["clauses"])
    assert lookup["order_sql"] == "started_at DESC NULLS LAST"


def test_active_sync_run_lookup_parts_tolerate_legacy_schema():
    lookup = sync_state.active_sync_run_lookup_parts(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        has_mode=False,
        has_extra=False,
        has_started_at=False,
    )

    joined = "\n".join(lookup["clauses"])
    assert lookup["args"][0] == "sap_successfactors"
    assert len(lookup["args"]) == 2
    assert "COALESCE(mode" not in joined
    assert "extra->>" not in joined
    assert "started_at >" not in joined
    assert lookup["order_sql"] == "run_id DESC"


def test_sync_state_public_payload_uses_project_terminal_statuses():
    payload = sync_state.sync_public_payload(
        {"run_id": "r1", "status": "partial", "cartridge_id": "sap_successfactors"},
        {"steps": [{"id": "connection", "status": "success"}]},
    )

    assert payload["active"] is False
    assert payload["progress_percent"] == 100
