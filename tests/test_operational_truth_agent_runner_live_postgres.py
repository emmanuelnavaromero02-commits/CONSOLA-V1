"""The severed agent_runner loop, demonstrated against the real init schema.

`airflow/dags/agent_runner.py` reads `public.agents` with direct SQL as
`omega_airflow_dag`. That role holds `GRANT SELECT` on the table
(`infra/init/36_cartridge_and_meta_roles.sql`), the table runs `ENABLE` +
`FORCE ROW LEVEL SECURITY` (`99s_remaining_operational_rls.sql`), the role is
`NOBYPASSRLS` (`99f_native_rls_completion.sql`), and none of the three
policies on the table name it -- they are all granted to
omega_console/omega_refinement.

PostgreSQL answers that combination with zero rows and no error, which is
exactly what "no agent is scheduled" looks like. These tests prove the two
halves separately: that the rows really do vanish, and that the catalog probe
the DAG now runs can tell the difference.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (  # noqa: F401
    OMEGA_CONSOLE_PASSWORD,
    _role_dsn,
    postgres_with_real_init_schema,
)


REPO = Path(__file__).resolve().parents[1]
AGENT_RUNNER = REPO / "airflow/dags/agent_runner.py"
AIRFLOW_DAG_ROLE = "omega_airflow_dag"
AIRFLOW_DAG_PASSWORD = "test_omega_airflow_dag_password"

# The query the DAG issues, copied from find_due_agents().
DUE_AGENTS_SQL = (
    "SELECT id, cartridge_id, slug, name, extra, tenant_id, workspace_id "
    "FROM agents WHERE is_active = TRUE AND extra ? 'schedule'"
)


def _dag_constant(name: str) -> str:
    """Read a module-level string constant out of the DAG without importing it."""
    tree = ast.parse(AGENT_RUNNER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            return node.value.value
    raise AssertionError(f"{name} not found in {AGENT_RUNNER}")


async def _seed_scheduled_agent(conn: asyncpg.Connection) -> str:
    """Insert one active agent carrying a cron schedule, as the superuser."""
    suffix = uuid.uuid4().hex[:12]
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id",
        f"agent-runner-{suffix}",
    )
    workspace_id = await conn.fetchval(
        "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
        tenant_id,
        f"Agent Runner WS {suffix}",
    )
    await conn.execute(
        """
        INSERT INTO cartridges (id, name, category)
        VALUES ('platform', 'Platform', 'cartridge')
        ON CONFLICT (id) DO NOTHING
        """
    )
    return await conn.fetchval(
        """
        INSERT INTO agents (cartridge_id, slug, name, extra, tenant_id, workspace_id,
                            is_active)
        VALUES ('platform', $1, $1,
                '{"schedule": {"cron": "*/5 * * * *", "enabled": true}}'::jsonb,
                $2, $3, TRUE)
        RETURNING id::text
        """,
        f"scheduled-{suffix}",
        tenant_id,
        workspace_id,
    )


@pytest.mark.asyncio
async def test_scheduled_agents_are_invisible_to_the_dag_role(
    postgres_with_real_init_schema: str,
):
    """The severed loop itself: a scheduled agent exists, and the role the DAG
    connects as reads back nothing at all -- silently, with no error."""
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        agent_id = await _seed_scheduled_agent(admin)
        visible_to_admin = await admin.fetch(DUE_AGENTS_SQL)
    finally:
        await admin.close()

    assert any(str(row["id"]) == agent_id for row in visible_to_admin), (
        "the seeded agent must be visible to a BYPASSRLS superuser"
    )

    dag_conn = await asyncpg.connect(
        _role_dsn(postgres_with_real_init_schema, AIRFLOW_DAG_ROLE, AIRFLOW_DAG_PASSWORD)
    )
    try:
        # No exception: SELECT is granted. The rows are simply gone.
        visible_to_dag = await dag_conn.fetch(DUE_AGENTS_SQL)
    finally:
        await dag_conn.close()

    assert visible_to_dag == [], (
        "agent_runner reported success on this emptiness every five minutes"
    )


@pytest.mark.asyncio
async def test_visibility_probe_reports_the_dag_role_as_blind(
    postgres_with_real_init_schema: str,
):
    """The probe added to the DAG must run on a real server and must name the
    blindness rather than let it pass as an idle window."""
    sql = _dag_constant("_AGENTS_VISIBILITY_SQL")
    conn = await asyncpg.connect(
        _role_dsn(postgres_with_real_init_schema, AIRFLOW_DAG_ROLE, AIRFLOW_DAG_PASSWORD)
    )
    try:
        row = await conn.fetchrow(sql)
    finally:
        await conn.close()

    assert row is not None
    role, rls_enabled, rls_forced, bypasses_rls, has_policy = tuple(row)
    assert role == AIRFLOW_DAG_ROLE
    assert rls_enabled is True
    assert rls_forced is True
    assert bypasses_rls is False
    assert has_policy is False, (
        "no policy on public.agents applies to omega_airflow_dag; if this "
        "starts failing, F2b was resolved and the probe expectation moves"
    )


@pytest.mark.asyncio
async def test_visibility_probe_clears_a_role_that_has_a_policy(
    postgres_with_real_init_schema: str,
):
    """The probe must not cry wolf: omega_console owns policies on the table
    and has to come back visible."""
    sql = _dag_constant("_AGENTS_VISIBILITY_SQL")
    conn = await asyncpg.connect(
        _role_dsn(postgres_with_real_init_schema, "omega_console", OMEGA_CONSOLE_PASSWORD)
    )
    try:
        row = await conn.fetchrow(sql)
    finally:
        await conn.close()

    assert row is not None
    role, _, _, bypasses_rls, has_policy = tuple(row)
    assert role == "omega_console"
    assert bypasses_rls is False
    assert has_policy is True
