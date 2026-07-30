from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg import sql


_FAULTABLE_TABLES = {"data_catalog", "intelligence_runs", "silver_lineage"}


@contextmanager
def temporarily_unavailable_table(table: str) -> Iterator[None]:
    assert table in _FAULTABLE_TABLES
    unavailable = f"e2e_unavailable_{table}_{uuid.uuid4().hex[:8]}"
    dsn = os.environ["POSTGRES_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                sql.Identifier(table), sql.Identifier(unavailable)
            )
        )
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                    sql.Identifier(unavailable), sql.Identifier(table)
                )
            )


def lineage_count(scope: dict[str, str]) -> int:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM silver_lineage "
            "WHERE tenant_id=%s AND workspace_id=%s",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        return int(cur.fetchone()[0])


def assert_chain_status(
    scope: dict[str, str], airflow_run_id: str, status: str
) -> None:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM pipeline_runs "
            "WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s",
            (
                scope["tenant_id"],
                scope["workspace_id"],
                f"dataset_refresh_chain:{airflow_run_id}",
            ),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == status


def seed_completed_run_without_signal(
    scope: dict[str, str], airflow_run_id: str
) -> None:
    run_ref = f"gold-refresh:{scope['workspace_id']}:replicon:{airflow_run_id}"
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO intelligence_runs(
                run_ref,tenant_id,workspace_id,source_system,run_mode,status,
                datasets_evaluated,signals_generated,signals_skipped,
                completed_at
            ) VALUES(%s,%s,%s,'replicon','gold_refresh','completed',
                     '["pnl_mensual"]'::jsonb,0,0,NOW())
            """,
            (run_ref, scope["tenant_id"], scope["workspace_id"]),
        )
        conn.commit()
