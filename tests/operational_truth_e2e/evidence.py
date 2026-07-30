from __future__ import annotations

import os
from typing import Any

import psycopg
from minio import Minio
from psycopg import errors


def _minio() -> Minio:
    return Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )


def assert_real_parquet_chain(scope: dict[str, str]) -> None:
    marker = f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
    prefixes = (
        f"raw/replicon/OperationalTruthProbe/{marker}",
        f"silver/replicon/operational_truth_silver/{marker}",
        f"gold/replicon/pnl_mensual/{marker}",
    )
    client = _minio()
    for prefix in prefixes:
        objects = [
            item.object_name
            for item in client.list_objects("lakehouse", prefix=prefix, recursive=True)
            if item.object_name.endswith(".parquet")
        ]
        assert objects, prefix
        response = client.get_object("lakehouse", objects[0], offset=0, length=4)
        try:
            assert response.read() == b"PAR1"
        finally:
            response.close()
            response.release_conn()


def _count(cur: psycopg.Cursor, sql: str, scope: dict[str, str]) -> int:
    cur.execute(sql, (scope["tenant_id"], scope["workspace_id"]))
    return int(cur.fetchone()[0])


def durable_counts(scope: dict[str, str]) -> dict[str, int]:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        counts = {
            "lineage": _count(
                cur,
                "SELECT count(*) FROM silver_lineage WHERE tenant_id=%s AND workspace_id=%s",
                scope,
            ),
            "catalog": _count(
                cur,
                "SELECT count(*) FROM data_catalog WHERE tenant_id=%s AND workspace_id=%s "
                "AND dataset IN ('operational_truth_silver','pnl_mensual')",
                scope,
            ),
            "slots": _count(
                cur,
                "SELECT count(*) FROM pipeline_runs WHERE tenant_id=%s AND workspace_id=%s "
                "AND run_id LIKE 'dataset_refresh_item:%%' AND status='success'",
                scope,
            ),
            "chain": _count(
                cur,
                "SELECT count(*) FROM pipeline_runs WHERE tenant_id=%s AND workspace_id=%s "
                "AND dag_id='dataset_refresh_chain' AND mode='refresh' AND status='success'",
                scope,
            ),
            "intelligence": _count(
                cur,
                "SELECT count(*) FROM intelligence_runs WHERE tenant_id=%s AND workspace_id=%s",
                scope,
            ),
            "signals": _count(
                cur,
                "SELECT count(*) FROM intelligence_signals WHERE tenant_id=%s AND workspace_id=%s",
                scope,
            ),
            "items": _count(
                cur,
                "SELECT count(*) FROM control_room_items WHERE tenant_id=%s AND workspace_id=%s "
                "AND source_dataset='pnl_mensual'",
                scope,
            ),
        }
    with psycopg.connect(os.environ["GOLD_POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM gold_pnl_mensual WHERE tenant_id=%s AND workspace_id=%s",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        counts["gold_rows"] = int(cur.fetchone()[0])
    return counts


def item_ids(scope: dict[str, str]) -> set[str]:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT item_id FROM control_room_items WHERE tenant_id=%s AND workspace_id=%s "
            "AND source_dataset='pnl_mensual'",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        return {str(row[0]) for row in cur.fetchall()}


def assert_airflow_role_isolation(a: dict[str, str], b: dict[str, str]) -> None:
    dsn = os.environ["AIRFLOW_POSTGRES_DSN"]
    with psycopg.connect(dsn) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pipeline_runs")
            assert int(cur.fetchone()[0]) == 0
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id',%s,true), "
                "set_config('app.workspace_id',%s,true)",
                (a["tenant_id"], a["workspace_id"]),
            )
            cur.execute("SELECT DISTINCT workspace_id::text FROM pipeline_runs")
            assert {str(row[0]) for row in cur.fetchall()} == {a["workspace_id"]}
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id',%s,true), "
                "set_config('app.workspace_id',%s,true)",
                (a["tenant_id"], a["workspace_id"]),
            )
            try:
                cur.execute(
                    """
                    INSERT INTO pipeline_runs(
                        run_id,dag_id,cartridge_id,entity,status,started_at,
                        tenant_id,workspace_id
                    ) VALUES('cross-tenant-e2e','probe','replicon','Probe','running',NOW(),%s,%s)
                    """,
                    (b["tenant_id"], b["workspace_id"]),
                )
            except errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError("omega_airflow_dag crossed workspace RLS")


def control_room_diagnostic_count(payload: dict[str, Any]) -> int:
    assert payload.get("schema_version") == "control-room-diagnostics/v1"
    items = payload.get("diagnostic_items")
    assert isinstance(items, list)
    assert all(isinstance(item, dict) and item.get("title") for item in items)
    return len(items)
