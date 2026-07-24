from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path

import asyncpg
import pytest


REPO = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = os.getenv("RLS_TEST_POSTGRES_IMAGE", "pgvector/pgvector:pg15")
POSTGRES_DB = "modecissions"
POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = "test_postgres_password"
OMEGA_CONSOLE_PASSWORD = "test_omega_console_password"
OMEGA_REFINEMENT_PASSWORD = "test_omega_refinement_password"
POSTGRES_PULL_ATTEMPTS = int(os.getenv("RLS_TEST_POSTGRES_PULL_ATTEMPTS", "3"))

CRITICAL_POLICY_TABLES = (
    "datasets",
    "decisions",
    "control_room_items",
    "control_room_item_events",
    "control_room_action_executions",
    "pipeline_runs",
    "copilot_goals",
    "copilot_lessons",
    "rag_sources",
    "rag_chunks",
    "entity_watermarks",
    "token_usage",
    "user_cartridge_overrides",
    "marketplace_orders",
    "tenant_entitlements",
    "cartridge_installations",
)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "docker command failed: "
            f"docker {' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _require_docker() -> None:
    result = _docker("info", check=False)
    if result.returncode != 0:
        pytest.skip(
            f"Docker is required for live operational RLS tests: {result.stderr.strip()}"
        )


def _ensure_postgres_image() -> None:
    inspect = _docker("image", "inspect", POSTGRES_IMAGE, check=False)
    if inspect.returncode == 0:
        return

    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, max(1, POSTGRES_PULL_ATTEMPTS) + 1):
        last = _docker("pull", POSTGRES_IMAGE, check=False)
        if last.returncode == 0:
            return
        if attempt < POSTGRES_PULL_ATTEMPTS:
            time.sleep(min(10, attempt * 2))

    assert last is not None
    raise RuntimeError(
        "docker image pull failed after retries: "
        f"{POSTGRES_IMAGE}\nstdout:\n{last.stdout}\nstderr:\n{last.stderr}"
    )


def _init_pgoptions() -> str:
    passwords = {
        "app.omega_console_password": OMEGA_CONSOLE_PASSWORD,
        "app.omega_refinement_password": OMEGA_REFINEMENT_PASSWORD,
        "app.omega_vault_password": "test_omega_vault_password",
        "app.omega_workspace_password": "test_omega_workspace_password",
        "app.omega_mcp_infra_password": "test_omega_mcp_infra_password",
        "app.omega_cartridge_sap_hcm_password": "test_omega_cartridge_sap_hcm_password",
        "app.omega_cartridge_sap_s4_password": "test_omega_cartridge_sap_s4_password",
        "app.omega_cartridge_sap_sf_password": "test_omega_cartridge_sap_sf_password",
        "app.omega_airflow_dag_password": "test_omega_airflow_dag_password",
        "app.omega_airflow_meta_password": "test_omega_airflow_meta_password",
        "app.omega_superset_meta_password": "test_omega_superset_meta_password",
        "app.omega_cartridge_replicon_password": "test_omega_cartridge_replicon_password",
        "app.omega_cartridge_hubspot_password": "test_omega_cartridge_hubspot_password",
        "app.omega_cartridge_banxico_password": "test_omega_cartridge_banxico_password",
        "app.omega_cartridge_inegi_password": "test_omega_cartridge_inegi_password",
        "app.omega_cartridge_sec_edgar_password": "test_omega_cartridge_sec_edgar_password",
    }
    return " ".join(f"-c {key}={value}" for key, value in passwords.items())


def _mapped_postgres_port(container_id: str) -> int:
    mapping = _docker("port", container_id, "5432/tcp").stdout
    for line in mapping.splitlines():
        _, _, raw_port = line.rpartition(":")
        if raw_port.isdigit():
            return int(raw_port)
    raise RuntimeError(f"postgres container has no mapped 5432/tcp port:\n{mapping}")


async def _wait_for_schema(dsn: str, container_id: str) -> None:
    deadline = time.monotonic() + 180
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        state = _docker("inspect", "-f", "{{.State.Status}}", container_id, check=False)
        if state.stdout.strip() in {"exited", "dead"}:
            logs = _docker("logs", "--tail=200", container_id, check=False)
            raise RuntimeError(
                f"postgres init container exited early:\n{logs.stdout}\n{logs.stderr}"
            )
        try:
            conn = await asyncpg.connect(dsn)
            try:
                required = await conn.fetchrow(
                    """
                    SELECT
                        to_regclass('public.datasets') AS datasets,
                        to_regclass('public.control_room_items') AS control_room_items,
                        to_regclass('public.pipeline_runs') AS pipeline_runs,
                        to_regclass('public.rag_sources') AS rag_sources,
                        to_regclass('public.entity_watermarks') AS entity_watermarks
                    """
                )
                if all(required.values()):
                    return
            finally:
                await conn.close()
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(1)

    logs = _docker("logs", "--tail=200", container_id, check=False)
    raise RuntimeError(
        "postgres schema did not become ready within 180s"
        f"\nlast_error={last_error!r}\nlogs:\n{logs.stdout}\n{logs.stderr}"
    )


@pytest.fixture(scope="module")
def postgres_with_real_init_schema() -> str:
    _require_docker()
    _ensure_postgres_image()
    container_name = f"consola-console-refinement-rls-{uuid.uuid4().hex[:12]}"
    init_dir = REPO / "infra" / "init"
    result = _docker(
        "run",
        "-d",
        "--name",
        container_name,
        "-e",
        f"POSTGRES_DB={POSTGRES_DB}",
        "-e",
        f"POSTGRES_USER={POSTGRES_USER}",
        "-e",
        f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        "-e",
        f"PGOPTIONS={_init_pgoptions()}",
        "-v",
        f"{init_dir}:/docker-entrypoint-initdb.d:ro",
        "-P",
        POSTGRES_IMAGE,
    )
    container_id = result.stdout.strip()
    try:
        port = _mapped_postgres_port(container_id)
        dsn = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@127.0.0.1:{port}/{POSTGRES_DB}"
        asyncio.run(_wait_for_schema(dsn, container_id))
        yield dsn
    finally:
        _docker("rm", "-f", container_id, check=False)


async def _seed_probe(conn: asyncpg.Connection) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    tenant_a = await conn.fetchval(
        "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
        f"rls-console-a-{suffix}",
        f"rls-console-a-{suffix}",
    )
    tenant_b = await conn.fetchval(
        "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
        f"rls-console-b-{suffix}",
        f"rls-console-b-{suffix}",
    )
    workspace_a = await conn.fetchval(
        "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
        tenant_a,
        f"RLS Console A {suffix}",
    )
    workspace_b = await conn.fetchval(
        "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
        tenant_b,
        f"RLS Console B {suffix}",
    )

    await conn.execute(
        """
        INSERT INTO cartridges (id, name, category)
        VALUES ('replicon', 'Replicon PSA', 'cartridge')
        ON CONFLICT (id) DO NOTHING
        """
    )

    dataset_a = f"rls_dataset_a_{suffix}"
    dataset_b = f"rls_dataset_b_{suffix}"
    pipeline_a = f"rls_pipeline_a_{suffix}"
    pipeline_b = f"rls_pipeline_b_{suffix}"
    item_a = f"rls_item_a_{suffix}"
    item_b = f"rls_item_b_{suffix}"
    rag_a = f"rls_rag_a_{suffix}"
    rag_b = f"rls_rag_b_{suffix}"
    watermark_a = f"rls_watermark_a_{suffix}"
    watermark_b = f"rls_watermark_b_{suffix}"

    for tenant_id, workspace_id, dataset_name in (
        (tenant_a, workspace_a, dataset_a),
        (tenant_b, workspace_b, dataset_b),
    ):
        await conn.execute(
            """
            INSERT INTO datasets (
                name, description, layer, cartridge, sources,
                sql_def, column_mapping, workspace_id
            )
            VALUES ($1, 'rls probe dataset', 'gold', 'replicon', '[]'::jsonb, 'SELECT 1', '{}'::jsonb, $2)
            """,
            dataset_name,
            workspace_id,
        )

    for tenant_id, workspace_id, run_id in (
        (tenant_a, workspace_a, pipeline_a),
        (tenant_b, workspace_b, pipeline_b),
    ):
        await conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, status,
                tenant_id, workspace_id, started_at
            )
            VALUES ($1, 'replicon_extract', 'replicon', 'projects', 'success', $2, $3, NOW())
            """,
            run_id,
            tenant_id,
            workspace_id,
        )

    for tenant_id, workspace_id, item_id in (
        (tenant_a, workspace_a, item_a),
        (tenant_b, workspace_b, item_b),
    ):
        await conn.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id,
                domain, source_dataset, title, severity, status
            )
            VALUES ($1, $2, $3, 'replicon', 'Finance', 'gold_pnl_mensual', $4, 'high', 'open')
            """,
            tenant_id,
            workspace_id,
            item_id,
            f"RLS item {item_id}",
        )

    for tenant_id, workspace_id, rag_name in (
        (tenant_a, workspace_a, rag_a),
        (tenant_b, workspace_b, rag_b),
    ):
        source_id = await conn.fetchval(
            """
            INSERT INTO rag_sources (
                name, description, tenant_id, workspace_id, visibility, source_key
            )
            VALUES ($1, 'rls probe source', $2, $3, 'workspace', $1)
            RETURNING id
            """,
            rag_name,
            tenant_id,
            workspace_id,
        )
        await conn.execute(
            """
            INSERT INTO rag_chunks (
                source_id, chunk_type, chunk_index, content,
                tenant_id, workspace_id
            )
            VALUES ($1, 'child', 0, $2, $3, $4)
            """,
            source_id,
            f"content for {rag_name}",
            tenant_id,
            workspace_id,
        )

    for tenant_id, workspace_id, entity_name in (
        (tenant_a, workspace_a, watermark_a),
        (tenant_b, workspace_b, watermark_b),
    ):
        await conn.execute(
            """
            INSERT INTO entity_watermarks (
                watermark_scope, cartridge_id, entity_name,
                tenant_id, workspace_id, last_watermark_value
            )
            VALUES (
                'tenant:' || $1::uuid::text || ':workspace:' || $2::uuid::text,
                'replicon', $3, $1::uuid, $2::uuid, '2026-06-15T00:00:00Z'
            )
            """,
            tenant_id,
            workspace_id,
            entity_name,
        )

    return {
        "tenant_a": str(tenant_a),
        "tenant_b": str(tenant_b),
        "workspace_a": str(workspace_a),
        "workspace_b": str(workspace_b),
        "dataset_a": dataset_a,
        "dataset_b": dataset_b,
        "pipeline_a": pipeline_a,
        "pipeline_b": pipeline_b,
        "item_a": item_a,
        "item_b": item_b,
        "rag_a": rag_a,
        "rag_b": rag_b,
        "watermark_a": watermark_a,
        "watermark_b": watermark_b,
    }


def _role_dsn(dsn: str, role: str, password: str) -> str:
    return dsn.replace(f"{POSTGRES_USER}:{POSTGRES_PASSWORD}", f"{role}:{password}")


@pytest.fixture(scope="module")
def omega_console_live_dsn(postgres_with_real_init_schema: str) -> str:
    return _role_dsn(
        postgres_with_real_init_schema,
        "omega_console",
        OMEGA_CONSOLE_PASSWORD,
    )


async def _visible_rows(
    dsn: str,
    *,
    role: str,
    password: str,
    tenant_id: str | None,
    workspace_id: str | None,
    probe: dict[str, str],
) -> dict[str, list[str]]:
    conn = await asyncpg.connect(_role_dsn(dsn, role, password))
    try:
        async with conn.transaction():
            if tenant_id is not None and workspace_id is not None:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                    tenant_id,
                    workspace_id,
                )
            datasets = await conn.fetch(
                """
                SELECT name FROM datasets
                 WHERE name = ANY($1::text[])
                 ORDER BY name
                """,
                [probe["dataset_a"], probe["dataset_b"]],
            )
            pipeline_runs = await conn.fetch(
                """
                SELECT run_id FROM pipeline_runs
                 WHERE run_id = ANY($1::text[])
                 ORDER BY run_id
                """,
                [probe["pipeline_a"], probe["pipeline_b"]],
            )

            result = {
                "datasets": [row["name"] for row in datasets],
                "pipeline_runs": [row["run_id"] for row in pipeline_runs],
            }

            if role == "omega_console":
                control_room_items = await conn.fetch(
                    """
                    SELECT item_id FROM control_room_items
                     WHERE item_id = ANY($1::text[])
                     ORDER BY item_id
                    """,
                    [probe["item_a"], probe["item_b"]],
                )
                rag_sources = await conn.fetch(
                    """
                    SELECT name FROM rag_sources
                     WHERE name = ANY($1::text[])
                     ORDER BY name
                    """,
                    [probe["rag_a"], probe["rag_b"]],
                )
                entity_watermarks = await conn.fetch(
                    """
                    SELECT entity_name FROM entity_watermarks
                     WHERE entity_name = ANY($1::text[])
                     ORDER BY entity_name
                    """,
                    [probe["watermark_a"], probe["watermark_b"]],
                )
                result.update(
                    {
                        "control_room_items": [
                            row["item_id"] for row in control_room_items
                        ],
                        "rag_sources": [row["name"] for row in rag_sources],
                        "entity_watermarks": [
                            row["entity_name"] for row in entity_watermarks
                        ],
                    }
                )
            return result
    finally:
        await conn.close()


async def _run_isolation_probe(dsn: str) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    try:
        probe = await _seed_probe(conn)
    finally:
        await conn.close()

    return {
        "probe": probe,
        "console_no_scope": await _visible_rows(
            dsn,
            role="omega_console",
            password=OMEGA_CONSOLE_PASSWORD,
            tenant_id=None,
            workspace_id=None,
            probe=probe,
        ),
        "console_a": await _visible_rows(
            dsn,
            role="omega_console",
            password=OMEGA_CONSOLE_PASSWORD,
            tenant_id=probe["tenant_a"],
            workspace_id=probe["workspace_a"],
            probe=probe,
        ),
        "console_b": await _visible_rows(
            dsn,
            role="omega_console",
            password=OMEGA_CONSOLE_PASSWORD,
            tenant_id=probe["tenant_b"],
            workspace_id=probe["workspace_b"],
            probe=probe,
        ),
        "refinement_no_scope": await _visible_rows(
            dsn,
            role="omega_refinement",
            password=OMEGA_REFINEMENT_PASSWORD,
            tenant_id=None,
            workspace_id=None,
            probe=probe,
        ),
        "refinement_a": await _visible_rows(
            dsn,
            role="omega_refinement",
            password=OMEGA_REFINEMENT_PASSWORD,
            tenant_id=probe["tenant_a"],
            workspace_id=probe["workspace_a"],
            probe=probe,
        ),
        "refinement_b": await _visible_rows(
            dsn,
            role="omega_refinement",
            password=OMEGA_REFINEMENT_PASSWORD,
            tenant_id=probe["tenant_b"],
            workspace_id=probe["workspace_b"],
            probe=probe,
        ),
    }


async def _load_owner_policies(dsn: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            """
            SELECT
                tablename,
                policyname,
                array_to_string(roles, ',') AS roles,
                COALESCE(qual, '') AS qual,
                COALESCE(with_check, '') AS with_check
              FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename = ANY($1::text[])
             ORDER BY tablename, policyname
            """,
            list(CRITICAL_POLICY_TABLES),
        )
    finally:
        await conn.close()


def test_console_and_refinement_roles_are_nobypassrls(postgres_with_real_init_schema):
    async def _runner() -> dict[str, bool]:
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            rows = await conn.fetch(
                """
                SELECT rolname, rolbypassrls
                  FROM pg_roles
                 WHERE rolname IN ('omega_console', 'omega_refinement')
                """
            )
            return {row["rolname"]: row["rolbypassrls"] for row in rows}
        finally:
            await conn.close()

    roles = asyncio.run(_runner())
    assert roles == {"omega_console": False, "omega_refinement": False}


def test_final_owner_policies_for_critical_tables_are_scoped(
    postgres_with_real_init_schema,
):
    rows = asyncio.run(_load_owner_policies(postgres_with_real_init_schema))
    by_table: dict[str, list[asyncpg.Record]] = {}
    for row in rows:
        by_table.setdefault(row["tablename"], []).append(row)

    for table in CRITICAL_POLICY_TABLES:
        if table not in by_table:
            continue
        owner_rows = [
            row
            for row in by_table[table]
            if "omega_console" in row["roles"] or "omega_refinement" in row["roles"]
        ]
        assert owner_rows, f"{table} must have a console/refinement scoped policy"
        assert any(
            row["policyname"] == "console_refinement_scope_rls" for row in owner_rows
        )
        for row in owner_rows:
            assert row["policyname"] != f"{table}_platform_owner_rls"
            assert row["qual"].strip().lower() not in {"true", "(true)"}
            assert row["with_check"].strip().lower() not in {"true", "(true)"}


def test_console_and_refinement_cannot_cross_workspace_rows(
    postgres_with_real_init_schema,
):
    result = asyncio.run(_run_isolation_probe(postgres_with_real_init_schema))
    probe = result["probe"]

    assert result["console_no_scope"] == {
        "datasets": [],
        "pipeline_runs": [],
        "control_room_items": [],
        "rag_sources": [],
        "entity_watermarks": [],
    }
    assert result["refinement_no_scope"] == {"datasets": [], "pipeline_runs": []}

    assert result["console_a"] == {
        "datasets": [probe["dataset_a"]],
        "pipeline_runs": [probe["pipeline_a"]],
        "control_room_items": [probe["item_a"]],
        "rag_sources": [probe["rag_a"]],
        "entity_watermarks": [probe["watermark_a"]],
    }
    assert result["console_b"] == {
        "datasets": [probe["dataset_b"]],
        "pipeline_runs": [probe["pipeline_b"]],
        "control_room_items": [probe["item_b"]],
        "rag_sources": [probe["rag_b"]],
        "entity_watermarks": [probe["watermark_b"]],
    }
    assert result["refinement_a"] == {
        "datasets": [probe["dataset_a"]],
        "pipeline_runs": [probe["pipeline_a"]],
    }
    assert result["refinement_b"] == {
        "datasets": [probe["dataset_b"]],
        "pipeline_runs": [probe["pipeline_b"]],
    }
