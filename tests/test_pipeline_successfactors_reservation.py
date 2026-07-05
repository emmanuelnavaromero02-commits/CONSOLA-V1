from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

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


@pytest.mark.anyio
async def test_reserve_entity_extract_slot_returns_none_for_other_cartridge():
    async def fail_table_has_column(*_args, **_kwargs):
        raise AssertionError("non SuccessFactors reservations should not touch storage")

    result = await sf_reservation.reserve_entity_extract_slot(
        cartridge="replicon",
        entity="User",
        dag_id="replicon_extract",
        conf={},
        user=None,
        requested_dag_run_id=None,
        expected_cartridge="sap_successfactors",
        entity_dag_id="sap_successfactors_extract",
        extract_all_dag_id="sap_successfactors_extract_all",
        aggregate_entity="__extract_all__",
        active_window_seconds=300,
        max_active_entity_extracts=2,
        build_security_context=lambda user: {},
        table_has_column=fail_table_has_column,
        airflow_run_id_fragment=lambda entity: entity,
        active_extract_run_payload=None,
        get_db_pool=None,
        token="abc",
    )

    assert result is None


@pytest.mark.anyio
async def test_reserve_entity_extract_slot_requires_scope_when_columns_exist():
    async def table_has_column(table, column, **kwargs):
        assert table == "pipeline_runs"
        assert kwargs == {"refresh": True}
        return column in {"tenant_id", "workspace_id"}

    with pytest.raises(HTTPException) as exc:
        await sf_reservation.reserve_entity_extract_slot(
            cartridge="sap_successfactors",
            entity="User",
            dag_id="sap_successfactors_extract",
            conf={},
            user=None,
            requested_dag_run_id=None,
            expected_cartridge="sap_successfactors",
            entity_dag_id="sap_successfactors_extract",
            extract_all_dag_id="sap_successfactors_extract_all",
            aggregate_entity="__extract_all__",
            active_window_seconds=300,
            max_active_entity_extracts=2,
            build_security_context=lambda user: {},
            table_has_column=table_has_column,
            airflow_run_id_fragment=lambda entity: entity,
            active_extract_run_payload=None,
            get_db_pool=None,
            token="abc",
        )

    assert exc.value.status_code == 403
    assert "tenant/workspace scope" in str(exc.value.detail)


@pytest.mark.anyio
async def test_reserve_entity_extract_slot_records_legacy_schema_reservation():
    class FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def __init__(self):
            self.fetch_calls = []
            self.execute_calls = []

        def transaction(self):
            return FakeTransaction()

        async def fetch(self, query, *args):
            self.fetch_calls.append((query, args))
            return []

        async def execute(self, query, *args):
            self.execute_calls.append((query, args))

    class FakePool:
        def __init__(self, conn):
            self.conn = conn

        def acquire(self):
            return FakeAcquire(self.conn)

    conn = FakeConn()

    async def table_has_column(*_args, **_kwargs):
        return False

    async def get_db_pool():
        return FakePool(conn)

    result = await sf_reservation.reserve_entity_extract_slot(
        cartridge="sap_successfactors",
        entity="User",
        dag_id="sap_successfactors_extract",
        conf={"mode": "full"},
        user=None,
        requested_dag_run_id=None,
        expected_cartridge="sap_successfactors",
        entity_dag_id="sap_successfactors_extract",
        extract_all_dag_id="sap_successfactors_extract_all",
        aggregate_entity="__extract_all__",
        active_window_seconds=300,
        max_active_entity_extracts=2,
        build_security_context=lambda user: {},
        table_has_column=table_has_column,
        airflow_run_id_fragment=lambda entity: entity,
        active_extract_run_payload=None,
        get_db_pool=get_db_pool,
        token="abc",
    )

    assert result == {
        "dag_run_id": "console__sap_successfactors_extract__User__abc",
        "reserved": True,
    }
    fetch_query, fetch_args = conn.fetch_calls[0]
    assert "FROM pipeline_runs" in fetch_query
    assert "tenant_id=$3" not in fetch_query
    assert fetch_args == ("sap_successfactors", 300)
    insert_query, insert_args = conn.execute_calls[-1]
    assert "INSERT INTO pipeline_runs" in insert_query
    assert insert_args[:6] == (
        "console__sap_successfactors_extract__User__abc",
        "sap_successfactors_extract",
        "sap_successfactors",
        "User",
        "console__sap_successfactors_extract__User__abc",
        "full",
    )
