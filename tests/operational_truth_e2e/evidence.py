from __future__ import annotations

import io
import os
from typing import Any

import psycopg
import pyarrow.parquet as pq
from minio import Minio
from psycopg import errors, sql


def _minio() -> Minio:
    return Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )


def assert_real_parquet_chain(scope: dict[str, str]) -> None:
    marker = f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
    prefixes = [f"raw/replicon/OperationalTruthProbe/{marker}"]
    with psycopg.connect(os.environ["GOLD_POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT trim(leading '/' from split_part(r.object_uri,'lakehouse',2))
                 FROM omega_publication.dataset_publication_heads h
                 JOIN omega_publication.materialization_runs r
                   ON r.materialization_run_id=h.materialization_run_id
                WHERE h.tenant_id=%s AND h.workspace_id=%s
                ORDER BY h.layer,h.dataset""",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        prefixes.extend(str(row[0]) for row in cur.fetchall())
    client = _minio()
    for prefix in prefixes:
        objects = [
            item.object_name
            for item in client.list_objects("lakehouse", prefix=prefix, recursive=True)
            if item.object_name.endswith(".parquet")
        ]
        assert objects, prefix
        response = client.get_object("lakehouse", objects[0])
        try:
            raw = response.read()
        finally:
            response.close()
            response.release_conn()
        table = pq.read_table(io.BytesIO(raw))
        assert table.num_rows == 3
        assert "tenant_id" in table.column_names
        assert table.column("tenant_id").to_pylist() == [scope["tenant_id"]] * 3


def _count(cur: psycopg.Cursor, sql: str, scope: dict[str, str]) -> int:
    cur.execute(sql, (scope["tenant_id"], scope["workspace_id"]))
    return int(cur.fetchone()[0])


def durable_counts(scope: dict[str, str]) -> dict[str, int]:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        counts = {
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
        for name, table in (
            ("publication_runs", "materialization_runs"),
            ("heads", "dataset_publication_heads"),
            ("receipts", "materialization_receipts"),
            ("evidence", "materialization_evidence"),
        ):
            counts[name] = _count(
                cur,
                f"SELECT count(*) FROM omega_publication.{table} "
                "WHERE tenant_id=%s AND workspace_id=%s",
                scope,
            )
        cur.execute(
            """SELECT relation_name FROM omega_publication.dataset_gold_relations
                 WHERE tenant_id=%s AND workspace_id=%s AND dataset='pnl_mensual'""",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        relation = str(cur.fetchone()[0])
        cur.execute(
            sql.SQL(
                "SELECT count(*) FROM public.{} WHERE tenant_id=%s AND workspace_id=%s"
            ).format(sql.Identifier(relation)),
            (scope["tenant_id"], scope["workspace_id"]),
        )
        counts["gold_rows"] = int(cur.fetchone()[0])
    return counts


def publication_snapshot(scope: dict[str, str]) -> dict[str, tuple[str, int]]:
    with psycopg.connect(os.environ["GOLD_POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT h.dataset,h.materialization_run_id::text,h.generation
                 FROM omega_publication.dataset_publication_heads h
                WHERE h.tenant_id=%s AND h.workspace_id=%s ORDER BY h.dataset""",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        return {str(row[0]): (str(row[1]), int(row[2])) for row in cur.fetchall()}


def assert_publication_reader_isolation(
    allowed: dict[str, str], foreign: dict[str, str]
) -> None:
    with psycopg.connect(os.environ["GOLD_READER_DSN"]) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM omega_publication.dataset_publication_heads"
            )
            assert int(cur.fetchone()[0]) == 0
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id',%s,true),set_config('app.workspace_id',%s,true)",
                (allowed["tenant_id"], allowed["workspace_id"]),
            )
            cur.execute(
                "SELECT DISTINCT tenant_id::text,workspace_id::text "
                "FROM omega_publication.dataset_publication_heads"
            )
            assert cur.fetchall() == [(allowed["tenant_id"], allowed["workspace_id"])]
            cur.execute(
                "SELECT count(*) FROM omega_publication.dataset_publication_heads "
                "WHERE tenant_id=%s AND workspace_id=%s",
                (foreign["tenant_id"], foreign["workspace_id"]),
            )
            assert int(cur.fetchone()[0]) == 0


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
