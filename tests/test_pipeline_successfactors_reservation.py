from __future__ import annotations

import json

from app.domains.pipeline import successfactors_reservation as sf_reservation


def test_reservation_helpers_build_run_and_scope_payloads():
    assert sf_reservation.reservation_applies(
        cartridge="sap_successfactors",
        dag_id="sap_successfactors_extract",
        expected_cartridge="sap_successfactors",
        entity_dag_id="sap_successfactors_extract",
    )
    assert not sf_reservation.reservation_applies(
        cartridge="replicon",
        dag_id="replicon_extract",
        expected_cartridge="sap_successfactors",
        entity_dag_id="sap_successfactors_extract",
    )

    assert (
        sf_reservation.reservation_run_id(
            dag_id="sap_successfactors_extract",
            requested_dag_run_id=None,
            entity_fragment="User",
            token="abc",
        )
        == "console__sap_successfactors_extract__User__abc"
    )
    assert (
        sf_reservation.reservation_run_id(
            dag_id="sap_successfactors_extract",
            requested_dag_run_id="manual__1",
            entity_fragment="User",
            token="abc",
        )
        == "manual__1"
    )

    extra = json.loads(sf_reservation.reservation_extra({"entity": "User"}))
    assert extra["raw_conf"] == {"entity": "User"}
    assert extra["reserved"] is True
    assert extra["reason"] == "successfactors_entity_extract_backpressure"

    scope_sql, args = sf_reservation.active_scope_args(
        cartridge="sap_successfactors",
        active_window_seconds=300,
        scope_columns_present=True,
        tenant_id="tenant-1",
        workspace_id="workspace-1",
    )
    assert scope_sql == "AND tenant_id=$3::uuid AND workspace_id=$4::uuid"
    assert args == ["sap_successfactors", 300, "tenant-1", "workspace-1"]


def test_reservation_helpers_detect_conflicts_and_active_runs():
    rows = [
        {
            "run_id": "extract-all-1",
            "dag_id": "sap_successfactors_extract_all",
            "entity": "__extract_all__",
            "airflow_dag_run_id": "airflow-all-1",
        },
        {
            "run_id": "entity-user-1",
            "dag_id": "sap_successfactors_extract",
            "entity": "User",
            "airflow_dag_run_id": "airflow-user-1",
        },
        {
            "run_id": "entity-job-1",
            "dag_id": "sap_successfactors_extract",
            "entity": "EmpJob",
        },
    ]

    conflict = sf_reservation.extract_all_conflict(
        rows,
        extract_all_dag_id="sap_successfactors_extract_all",
        aggregate_entity="__extract_all__",
    )
    assert conflict == {
        "reason": "extract_all_already_running",
        "message": (
            "SAP SuccessFactors extract_all is already running; "
            "wait for it to finish before triggering individual entities."
        ),
        "job_id": "airflow-all-1",
    }
    assert (
        sf_reservation.active_entity_run(
            rows,
            entity="User",
            entity_dag_id="sap_successfactors_extract",
        )
        == rows[1]
    )
    assert (
        sf_reservation.active_entity_run_count(
            rows,
            entity_dag_id="sap_successfactors_extract",
        )
        == 2
    )
    assert sf_reservation.active_entity_limit_payload(active=2, limit=2) == {
        "reason": "too_many_active_entity_extracts",
        "message": (
            "SAP SuccessFactors extraction backpressure: "
            "2 active entity runs; use Extract All/sync or wait."
        ),
        "active": 2,
        "limit": 2,
    }
