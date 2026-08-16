"""F11 — reproducible purge of stale generic-Gold signals, proven on real PG.

Seeds BOTH classes into an ephemeral PostgreSQL: the pre-#475 garbage
(structural-column metrics, scope-UUID entities) and legitimate signals
(real business KPIs, talent signals). The purge must report them in dry-run,
delete exactly the stale class on apply, leave every legitimate row and every
other workspace untouched, and be idempotent — after it, zero stale-generic.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid

import psycopg2
import pytest

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
WORKSPACE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_PASSWORD = "test_purge_password"

_SCHEMA = """
CREATE TABLE tenants (id uuid PRIMARY KEY);
CREATE TABLE workspaces (id uuid PRIMARY KEY, tenant_id uuid NOT NULL);
CREATE TABLE intelligence_signals (
    workspace_id uuid NOT NULL,
    signal_id text NOT NULL,
    metric text NOT NULL,
    entity_id text NOT NULL,
    status text NOT NULL DEFAULT 'open',
    PRIMARY KEY (workspace_id, signal_id)
);
CREATE TABLE control_room_items (
    workspace_id uuid NOT NULL,
    item_id text NOT NULL,
    item_kind text NOT NULL,
    anomaly_type text NOT NULL,
    entity_id text NOT NULL
);
CREATE TABLE metric_baselines (
    id bigserial PRIMARY KEY,
    workspace_id uuid NOT NULL,
    metric text NOT NULL,
    entity_id text NOT NULL
);
CREATE TABLE evidence_packs (id bigserial PRIMARY KEY, signal_id text NOT NULL);
CREATE TABLE evidence_items (
    id bigserial PRIMARY KEY,
    evidence_pack_id bigint NOT NULL REFERENCES evidence_packs(id)
);
CREATE TABLE hypotheses (id bigserial PRIMARY KEY, signal_id text NOT NULL);
CREATE TABLE decision_options (id bigserial PRIMARY KEY, signal_id text NOT NULL);
CREATE TABLE prediction_outcomes (id bigserial PRIMARY KEY, signal_id text NOT NULL);
CREATE TABLE decision_intelligence_snapshots (id bigserial PRIMARY KEY, signal_id text NOT NULL);
CREATE TABLE control_room_item_events (id bigserial PRIMARY KEY, item_id text NOT NULL);
CREATE TABLE action_runs (id bigserial PRIMARY KEY, item_id text NOT NULL);
CREATE TABLE control_room_action_executions (id bigserial PRIMARY KEY, item_id text NOT NULL);
"""


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stderr}")
    return result


@pytest.fixture(scope="module")
def purge_postgres() -> str:
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the live purge regression test")
    name = f"consola-purge-{uuid.uuid4().hex[:12]}"
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "-e",
        f"POSTGRES_PASSWORD={_PASSWORD}",
        "-p",
        "127.0.0.1:0:5432",
        "postgres:15",
    )
    try:
        mapping = _docker("port", name, "5432/tcp").stdout
        port = int(mapping.strip().rsplit(":", 1)[-1])
        dsn = f"postgresql://postgres:{_PASSWORD}@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 90
        while True:
            try:
                psycopg2.connect(dsn).close()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
                cur.execute(
                    "INSERT INTO tenants VALUES (%s), (%s)", (TENANT_A, TENANT_B)
                )
                cur.execute(
                    "INSERT INTO workspaces VALUES (%s, %s), (%s, %s)",
                    (WORKSPACE_A, TENANT_A, WORKSPACE_B, TENANT_B),
                )
                stale = [
                    ("s-user-1", "generic_user_id", "emp-1"),
                    ("s-user-2", "generic_user_id", "emp-2"),
                    ("s-dept", "generic_department_id", "dep-9"),
                    ("s-scope-1", "generic_headcount", WORKSPACE_A),
                    ("s-scope-2", "generic_headcount", TENANT_A),
                ]
                legit = [
                    ("l-revenue", "generic_revenue_total", "acme-north"),
                    ("l-margin", "generic_margin_pct", "acme-south"),
                    ("l-talent", "talent_readiness_drop", "emp-77"),
                ]
                for sid, metric, entity in stale + legit:
                    cur.execute(
                        "INSERT INTO intelligence_signals (workspace_id, signal_id, metric, entity_id) "
                        "VALUES (%s, %s, %s, %s)",
                        (WORKSPACE_A, sid, metric, entity),
                    )
                cur.execute(
                    "INSERT INTO intelligence_signals (workspace_id, signal_id, metric, entity_id) "
                    "VALUES (%s, 'b-stale', 'generic_user_id', 'emp-b')",
                    (WORKSPACE_B,),
                )
                cur.execute(
                    "INSERT INTO control_room_items VALUES "
                    "(%s, 'i-stale-1', 'intelligence_signal', 'generic_user_id', 'emp-1'),"
                    "(%s, 'i-stale-2', 'intelligence_signal', 'generic_headcount', %s),"
                    "(%s, 'i-legit', 'intelligence_signal', 'generic_revenue_total', 'acme-north'),"
                    "(%s, 'i-margin', 'agent_alert', 'low_margin', 'proj-1')",
                    (WORKSPACE_A, WORKSPACE_A, WORKSPACE_A, WORKSPACE_A, WORKSPACE_A),
                )
                cur.execute(
                    "INSERT INTO metric_baselines (workspace_id, metric, entity_id) VALUES "
                    "(%s, 'generic_display_order', 'x'), (%s, 'generic_revenue_total', 'acme-north')",
                    (WORKSPACE_A, WORKSPACE_A),
                )
                cur.execute("INSERT INTO evidence_packs (signal_id) VALUES ('s-user-1'), ('l-revenue')")
                cur.execute(
                    "INSERT INTO evidence_items (evidence_pack_id) "
                    "SELECT id FROM evidence_packs"
                )
                cur.execute("INSERT INTO hypotheses (signal_id) VALUES ('s-user-1'), ('l-revenue')")
                cur.execute("INSERT INTO decision_options (signal_id) VALUES ('s-dept')")
                cur.execute("INSERT INTO prediction_outcomes (signal_id) VALUES ('s-scope-1')")
            conn.commit()
        finally:
            conn.close()
        yield dsn
    finally:
        _docker("rm", "-f", "-v", name, check=False)


@pytest.mark.asyncio
async def test_purge_removes_exactly_the_stale_class(purge_postgres: str):
    import asyncpg

    from app.services.intelligence.stale_signal_purge import process_workspace

    conn = await asyncpg.connect(purge_postgres)
    try:
        dry = await process_workspace(conn, TENANT_A, WORKSPACE_A, apply=False)
        assert dry["signals"] == 5
        assert dry["control_room_items"] == 2
        assert dry["metric_baselines"] == 1
        assert dry["child_evidence_packs"] == 1
        assert dry["child_hypotheses"] == 1
        assert dry["child_decision_options"] == 1
        assert dry["child_prediction_outcomes"] == 1
        assert "DRY-RUN" in dry["action"]
        # Dry-run deleted nothing.
        assert await conn.fetchval("SELECT count(*) FROM intelligence_signals") == 9

        applied = await process_workspace(conn, TENANT_A, WORKSPACE_A, apply=True)
        assert applied["signals"] == 5
        assert applied["control_room_items"] == 2
        assert applied["metric_baselines"] == 1

        survivors = {
            r["signal_id"]
            for r in await conn.fetch(
                "SELECT signal_id FROM intelligence_signals WHERE workspace_id = $1::uuid",
                WORKSPACE_A,
            )
        }
        assert survivors == {"l-revenue", "l-margin", "l-talent"}, (
            "only legitimate signals may survive the purge"
        )
        items = {
            r["item_id"]
            for r in await conn.fetch(
                "SELECT item_id FROM control_room_items WHERE workspace_id = $1::uuid",
                WORKSPACE_A,
            )
        }
        assert items == {"i-legit", "i-margin"}
        baselines = {
            r["metric"]
            for r in await conn.fetch(
                "SELECT metric FROM metric_baselines WHERE workspace_id = $1::uuid",
                WORKSPACE_A,
            )
        }
        assert baselines == {"generic_revenue_total"}
        # Children of stale signals are gone; children of legit ones stay.
        assert await conn.fetchval(
            "SELECT count(*) FROM evidence_packs WHERE signal_id = 's-user-1'"
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM evidence_packs WHERE signal_id = 'l-revenue'"
        ) == 1
        assert await conn.fetchval("SELECT count(*) FROM hypotheses") == 1

        # Other workspaces are untouched until their own purge runs.
        assert await conn.fetchval(
            "SELECT count(*) FROM intelligence_signals WHERE workspace_id = $1::uuid",
            WORKSPACE_B,
        ) == 1

        # Idempotent: a re-run finds nothing.
        rerun = await process_workspace(conn, TENANT_A, WORKSPACE_A, apply=True)
        assert rerun["signals"] == 0
        assert rerun["control_room_items"] == 0
        assert rerun["metric_baselines"] == 0

        # The exit criterion: zero stale-generic left for the workspace.
        from app.services.intelligence.gold_control_room import (
            is_stale_generic_signal,
        )

        remaining = await conn.fetch(
            "SELECT metric, entity_id FROM intelligence_signals WHERE workspace_id = $1::uuid",
            WORKSPACE_A,
        )
        assert not [
            r
            for r in remaining
            if is_stale_generic_signal(r["metric"], r["entity_id"], (TENANT_A, WORKSPACE_A))
        ]
    finally:
        await conn.close()
