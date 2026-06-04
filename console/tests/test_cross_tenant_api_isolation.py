"""Cross-tenant isolation regression tests backed by a live database.

The previous version of this file proved isolation by inspecting generated
SQL strings and by using a fake asyncpg pool. That left the critical guarantee
untested: a real database session for workspace A must not be able to read
workspace B rows.

This suite starts PostgreSQL with the production `infra/init/` schema, seeds two
workspaces, and queries as the real `omega_workspace` service role with
`app.tenant_id`/`app.workspace_id` set per workspace. It must use production
policies from `infra/init/99e_operational_native_rls.sql`, not test-created RLS.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.services import marketplace_service, permissions


REPO_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = os.getenv("RLS_TEST_POSTGRES_IMAGE", "pgvector/pgvector:pg15")
POSTGRES_DB = "modecissions"
POSTGRES_SUPERUSER = "postgres"
POSTGRES_PASSWORD = "test_postgres_password"
OMEGA_WORKSPACE_ROLE = "omega_workspace"
OMEGA_WORKSPACE_PASSWORD = "test_omega_workspace_password"


USER_B_WORKSPACE_ADMIN = {
    "id": 2002,
    "email": "wa-b@example.com",
    "role": "user",
    "workspace_role": "workspace_admin",
    "tenant_id": "tenant-b",
    "workspace_id": "workspace-b",
}


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


def _init_pgoptions() -> str:
    passwords = {
        "app.omega_console_password": "test_omega_console_password",
        "app.omega_refinement_password": "test_omega_refinement_password",
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
            raise RuntimeError(f"postgres init container exited early:\n{logs.stdout}\n{logs.stderr}")
        try:
            conn = await asyncpg.connect(dsn)
            try:
                required = await conn.fetchrow(
                    """
                    SELECT
                        to_regclass('public.datasets') AS datasets,
                        to_regclass('public.tenants') AS tenants,
                        to_regclass('public.workspaces') AS workspaces
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
    container_name = f"consola-cross-tenant-rls-{uuid.uuid4().hex[:12]}"
    init_dir = REPO_ROOT / "infra" / "init"
    if not init_dir.is_dir():
        raise AssertionError(f"infra init directory is missing: {init_dir}")

    result = _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        container_name,
        "-e",
        f"POSTGRES_DB={POSTGRES_DB}",
        "-e",
        f"POSTGRES_USER={POSTGRES_SUPERUSER}",
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
        dsn = (
            f"postgresql://{POSTGRES_SUPERUSER}:{POSTGRES_PASSWORD}"
            f"@127.0.0.1:{port}/{POSTGRES_DB}"
        )
        asyncio.run(_wait_for_schema(dsn, container_id))
        yield dsn
    finally:
        _docker("rm", "-f", container_id, check=False)


async def _seed_rls_probe(conn: asyncpg.Connection) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    tenant_a = await conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"rls-tenant-a-{suffix}",
    )
    tenant_b = await conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"rls-tenant-b-{suffix}",
    )
    workspace_a = await conn.fetchval(
        "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
        tenant_a,
        f"RLS Workspace A {suffix}",
    )
    workspace_b = await conn.fetchval(
        "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
        tenant_b,
        f"RLS Workspace B {suffix}",
    )

    dataset_a = f"rls_dataset_a_{suffix}"
    dataset_b = f"rls_dataset_b_{suffix}"
    for dataset_name, workspace_id in (
        (dataset_a, workspace_a),
        (dataset_b, workspace_b),
    ):
        await conn.execute(
            """
            INSERT INTO datasets (
                name, description, layer, cartridge, sources,
                sql_def, column_mapping, workspace_id
            )
            VALUES (
                $1, 'rls probe dataset', 'gold', 'replicon',
                '[]'::jsonb, 'SELECT 1', '{}'::jsonb, $2
            )
            """,
            dataset_name,
            workspace_id,
        )

    return {
        "tenant_a": str(tenant_a),
        "tenant_b": str(tenant_b),
        "workspace_a": str(workspace_a),
        "workspace_b": str(workspace_b),
        "dataset_a": dataset_a,
        "dataset_b": dataset_b,
    }


async def _visible_probe_datasets(
    dsn: str,
    *,
    tenant_id: str,
    workspace_id: str,
    dataset_names: tuple[str, str],
) -> list[str]:
    reader_dsn = dsn.replace(
        f"{POSTGRES_SUPERUSER}:{POSTGRES_PASSWORD}",
        f"{OMEGA_WORKSPACE_ROLE}:{OMEGA_WORKSPACE_PASSWORD}",
    )
    conn = await asyncpg.connect(reader_dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            rows = await conn.fetch(
                """
                SELECT name
                  FROM datasets
                 WHERE name = ANY($1::text[])
                 ORDER BY name
                """,
                list(dataset_names),
            )
            return [row["name"] for row in rows]
    finally:
        await conn.close()


async def _cross_tenant_probe(dsn: str) -> dict[str, list[str] | dict[str, str]]:
    conn = await asyncpg.connect(dsn)
    try:
        probe = await _seed_rls_probe(conn)
        policies = await conn.fetch(
            """
            SELECT policyname
              FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename = 'datasets'
             ORDER BY policyname
            """
        )
        policy_names = {row["policyname"] for row in policies}
    finally:
        await conn.close()

    assert "datasets_tenant_workspace_rls" in policy_names

    dataset_names = (probe["dataset_a"], probe["dataset_b"])
    visible_to_a = await _visible_probe_datasets(
        dsn,
        tenant_id=probe["tenant_a"],
        workspace_id=probe["workspace_a"],
        dataset_names=dataset_names,
    )
    visible_to_b = await _visible_probe_datasets(
        dsn,
        tenant_id=probe["tenant_b"],
        workspace_id=probe["workspace_b"],
        dataset_names=dataset_names,
    )
    return {"probe": probe, "visible_to_a": visible_to_a, "visible_to_b": visible_to_b}


def test_postgres_rls_blocks_workspace_b_rows_from_workspace_a(postgres_with_real_init_schema):
    result = asyncio.run(_cross_tenant_probe(postgres_with_real_init_schema))
    probe = result["probe"]

    assert result["visible_to_a"] == [probe["dataset_a"]]
    assert result["visible_to_b"] == [probe["dataset_b"]]
    assert result["visible_to_a"].count(probe["dataset_b"]) == 0


def test_workspace_admin_cannot_use_admin_only_marketplace_calls():
    async def _runner() -> None:
        with pytest.raises(marketplace_service.MarketplaceError):
            await marketplace_service.list_admin_installations(
                USER_B_WORKSPACE_ADMIN
            )

    asyncio.run(_runner())


def test_workspace_admin_cannot_activate_arbitrary_cartridges():
    async def _runner() -> None:
        with pytest.raises(marketplace_service.MarketplaceError):
            await marketplace_service.activate_product(
                "replicon", USER_B_WORKSPACE_ADMIN
            )

    asyncio.run(_runner())


def test_workspace_admin_cannot_administer_marketplace_globally():
    assert not permissions.has_permission(
        USER_B_WORKSPACE_ADMIN, "marketplace.admin"
    )


def test_workspace_admin_does_not_get_global_role_writes():
    assert not permissions.has_permission(
        USER_B_WORKSPACE_ADMIN, "iam.roles.write"
    )
    assert not permissions.has_permission(
        USER_B_WORKSPACE_ADMIN, "iam.policies.write"
    )
