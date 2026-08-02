from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from minio import Minio
from psycopg import sql

from runtime.dataset_refresh_outcome import (
    require_successful_materialization_response,
)


@contextmanager
def reject_receipt_inserts() -> Iterator[None]:
    function = "e2e_reject_receipt_insert"
    trigger = "e2e_reject_receipt_insert"
    dsn = os.environ["GOLD_POSTGRES_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL(
                "CREATE FUNCTION omega_publication.{}() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'e2e receipt insert rejected'; END $$"
            ).format(sql.Identifier(function))
        )
        conn.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON "
                "omega_publication.materialization_receipts FOR EACH ROW "
                "EXECUTE FUNCTION omega_publication.{}()"
            ).format(sql.Identifier(trigger), sql.Identifier(function))
        )
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "DROP TRIGGER {} ON " "omega_publication.materialization_receipts"
                ).format(sql.Identifier(trigger))
            )
            conn.execute(
                sql.SQL("DROP FUNCTION omega_publication.{}()").format(
                    sql.Identifier(function)
                )
            )


def prepared_runs(scope: dict[str, str]) -> list[str]:
    with psycopg.connect(os.environ["GOLD_POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT materialization_run_id::text
                 FROM omega_publication.materialization_runs
                WHERE tenant_id=%s AND workspace_id=%s AND status='prepared'
                ORDER BY created_at""",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        return [str(row[0]) for row in cur.fetchall()]


@contextmanager
def unavailable_intelligence_runs() -> Iterator[None]:
    unavailable = f"e2e_unavailable_intelligence_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(os.environ["POSTGRES_DSN"], autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER TABLE intelligence_runs RENAME TO {}").format(
                sql.Identifier(unavailable)
            )
        )
    try:
        yield
    finally:
        with psycopg.connect(os.environ["POSTGRES_DSN"], autocommit=True) as conn:
            conn.execute(
                sql.SQL("ALTER TABLE {} RENAME TO intelligence_runs").format(
                    sql.Identifier(unavailable)
                )
            )


def bump_dataset_contract(scope: dict[str, str], dataset: str) -> None:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE datasets SET sql_def=sql_def || ' ' "
            "WHERE tenant_id=%s AND workspace_id=%s AND name=%s",
            (scope["tenant_id"], scope["workspace_id"], dataset),
        )
        assert cur.rowcount == 1
        conn.commit()


def rewrite_raw_parquet(scope: dict[str, str], version: int) -> None:
    data = {
        "proyecto": [f"project-{scope['label']}"] * 3,
        "project_name": [f"Proyecto {scope['label'].upper()}"] * 3,
        "mes": ["2026-01-01", "2026-02-01", "2026-03-01"],
        "margen_bruto_usd": [100, 100, 10 + version],
        "margen_bruto_pct": [100, 100, 10 + version],
        "wip_usd": [0, 0, 0],
        "revenue_usd": [100, 100, 100],
        "revenue_manager": [f"Manager {scope['label'].upper()}"] * 3,
        "tenant_id": [scope["tenant_id"]] * 3,
        "workspace_id": [scope["workspace_id"]] * 3,
    }
    buffer = io.BytesIO()
    pq.write_table(pa.table(data), buffer)
    raw = buffer.getvalue()
    client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
    prefix = (
        "raw/replicon/OperationalTruthProbe/"
        f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
    )
    keys = [
        item.object_name
        for item in client.list_objects("lakehouse", prefix=prefix, recursive=True)
        if item.object_name.endswith(".parquet")
    ]
    assert len(keys) == 1
    client.put_object("lakehouse", keys[0], io.BytesIO(raw), len(raw))


def seed_expired_materialization_slot(
    scope: dict[str, str], *, airflow_run_id: str, dataset: str
) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (airflow_run_id, scope["tenant_id"], scope["workspace_id"], dataset)
        ).encode()
    ).hexdigest()
    slot = f"dataset_refresh_item:{digest}"
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO pipeline_runs(
                   run_id,dag_id,cartridge_id,entity,airflow_dag_run_id,mode,
                   status,started_at,tenant_id,workspace_id,extra,heartbeat_at,
                   lease_expires_at,fencing_token
               ) VALUES(%s,'dataset_refresh_chain','replicon',%s,%s,'materialize',
                        'running',NOW() - INTERVAL '1 hour',%s,%s,'{}'::jsonb,
                        NOW() - INTERVAL '1 hour',NOW() - INTERVAL '30 minutes',7)""",
            (
                slot,
                dataset,
                airflow_run_id,
                scope["tenant_id"],
                scope["workspace_id"],
            ),
        )
        conn.commit()
    return slot


def assert_reclaimed_slot(scope: dict[str, str], slot: str) -> None:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status,fencing_token,lease_expires_at FROM pipeline_runs "
            "WHERE run_id=%s AND tenant_id=%s AND workspace_id=%s",
            (slot, scope["tenant_id"], scope["workspace_id"]),
        )
        assert cur.fetchone() == ("success", 8, None)


def intelligence_count(scope: dict[str, str]) -> int:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM intelligence_runs WHERE tenant_id=%s AND workspace_id=%s",
            (scope["tenant_id"], scope["workspace_id"]),
        )
        return int(cur.fetchone()[0])


def pipeline_status(scope: dict[str, str], airflow_run_id: str) -> str:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM pipeline_runs WHERE tenant_id=%s AND workspace_id=%s "
            "AND run_id=%s",
            (
                scope["tenant_id"],
                scope["workspace_id"],
                f"dataset_refresh_chain:{airflow_run_id}",
            ),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def seed_completed_run_without_signal(
    scope: dict[str, str], airflow_run_id: str
) -> None:
    run_ref = f"gold-refresh:{scope['workspace_id']}:replicon:{airflow_run_id}"
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO intelligence_runs(
                   run_ref,tenant_id,workspace_id,source_system,run_mode,status,
                   datasets_evaluated,signals_generated,signals_skipped,completed_at
               ) VALUES(%s,%s,%s,'replicon','gold_refresh','completed',
                        '["pnl_mensual"]'::jsonb,0,0,NOW())""",
            (run_ref, scope["tenant_id"], scope["workspace_id"]),
        )
        conn.commit()


def put_unpublished_object(scope: dict[str, str]) -> str:
    key = (
        "gold/replicon/pnl_mensual/"
        f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
        f"_snapshots/_pending/{uuid.uuid4().hex}/data.parquet"
    )
    client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
    payload = b"not-a-published-object"
    client.put_object("lakehouse", key, io.BytesIO(payload), len(payload))
    return key


class _FalseOutcome(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        payload = json.dumps({"ok": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return None


def assert_real_http_ok_false_fails_closed() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FalseOutcome)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        response = requests.get(
            f"http://127.0.0.1:{server.server_port}/materialize", timeout=5
        )
        try:
            require_successful_materialization_response(
                response, expected_name="pnl_mensual"
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("HTTP 200 with ok=false was accepted")
    finally:
        server.shutdown()
        worker.join(timeout=5)
