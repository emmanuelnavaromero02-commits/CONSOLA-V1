from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
import uuid
from unittest.mock import MagicMock

import psycopg2
import pytest

from refinement.app.duckdb_engine import DuckDBEngine, _duckdb_type_to_pg_type


REPO_ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = os.getenv("GOLD_RLS_TEST_POSTGRES_IMAGE", "postgres:15")
POSTGRES_PASSWORD = "test_gold_postgres_password"
GOLD_ROLE_PASSWORD = "test_omega_refinement_gold_password"


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
        pytest.skip(f"Docker is required for live Gold RLS isolation test: {result.stderr.strip()}")


def _mapped_postgres_port(container_id: str) -> int:
    mapping = _docker("port", container_id, "5432/tcp").stdout
    for line in mapping.splitlines():
        _, _, raw_port = line.rpartition(":")
        if raw_port.isdigit():
            return int(raw_port)
    raise RuntimeError(f"postgres_gold test container has no mapped 5432/tcp port:\n{mapping}")


def _wait_for_gold_schema(dsn: str, container_id: str) -> None:
    deadline = time.monotonic() + 120
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        state = _docker("inspect", "-f", "{{.State.Status}}", container_id, check=False)
        if state.stdout.strip() in {"exited", "dead"}:
            logs = _docker("logs", "--tail=200", container_id, check=False)
            raise RuntimeError(f"postgres_gold init container exited early:\n{logs.stdout}\n{logs.stderr}")
        try:
            conn = psycopg2.connect(dsn)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement_gold'
                        ),
                        EXISTS (
                            SELECT 1 FROM pg_proc WHERE proname = 'omega_apply_gold_rls_for_table'
                        )
                        """
                    )
                    role_ready, function_ready = cur.fetchone()
                    if role_ready and function_ready:
                        return
            finally:
                conn.close()
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    logs = _docker("logs", "--tail=200", container_id, check=False)
    raise RuntimeError(
        "postgres_gold schema did not become ready within 120s"
        f"\nlast_error={last_error!r}\nlogs:\n{logs.stdout}\n{logs.stderr}"
    )


@pytest.fixture(scope="module")
def postgres_gold_with_native_rls() -> str:
    _require_docker()
    container_name = f"consola-gold-rls-{uuid.uuid4().hex[:12]}"
    init_dir = REPO_ROOT / "infra" / "init_gold"
    result = _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        container_name,
        "-e",
        "POSTGRES_DB=modecissions_gold",
        "-e",
        "POSTGRES_USER=postgres",
        "-e",
        f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        "-e",
        f"PGOPTIONS=-c app.omega_refinement_gold_password={GOLD_ROLE_PASSWORD}",
        "-v",
        f"{init_dir}:/docker-entrypoint-initdb.d:ro",
        "-P",
        POSTGRES_IMAGE,
    )
    container_id = result.stdout.strip()
    try:
        port = _mapped_postgres_port(container_id)
        dsn = f"postgresql://postgres:{POSTGRES_PASSWORD}@127.0.0.1:{port}/modecissions_gold"
        _wait_for_gold_schema(dsn, container_id)
        yield dsn
    finally:
        _docker("rm", "-f", container_id, check=False)


def test_gold_native_rls_migration_default_denies_legacy_unscoped_tables():
    sql = (REPO_ROOT / "infra" / "init_gold" / "35_gold_native_rls.sql").read_text(encoding="utf-8")

    assert "ALTER ROLE omega_refinement_gold NOBYPASSRLS" in sql
    assert "omega_gold_workspace_matches" in sql
    assert "omega_apply_gold_rls_for_table" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.tenant_id', true)" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "USING (false) WITH CHECK (false)" in sql


def test_gold_dsn_includes_tenant_workspace_options_for_scoped_context(monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://u:p@postgres_gold:5433/modecissions_gold?sslmode=disable")
    engine = DuckDBEngine()

    dsn = engine._pg_gold_dsn(
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
        }
    )

    assert "sslmode=disable" in dsn
    assert "options=" in dsn
    assert "+app.tenant_id" not in dsn
    assert "-c%20app.tenant_id" in dsn
    assert "app.tenant_id" in dsn
    assert "11111111-1111-1111-1111-111111111111" in dsn
    assert "app.workspace_id" in dsn
    assert "22222222-2222-2222-2222-222222222222" in dsn


def test_gold_materialization_requires_tenant_workspace_scope(monkeypatch):
    engine = DuckDBEngine()
    con = MagicMock()
    engine._conn = MagicMock(return_value=con)

    with pytest.raises(ValueError, match="tenant_id and workspace_id"):
        engine.materialize(
            {
                "name": "orders",
                "cartridge": "hubspot",
                "layer": "gold",
                "sql_def": "SELECT 1 AS order_count",
                "sources": [],
            },
            user_context={},
        )

    executed_sql = "\n".join(str(call.args[0]) for call in con.execute.call_args_list if call.args)
    assert "CREATE OR REPLACE TABLE pggold" not in executed_sql


def test_scoped_gold_table_creation_applies_native_rls(monkeypatch):
    engine = DuckDBEngine()
    con = MagicMock()
    applied: list[str] = []
    monkeypatch.setattr(engine, "_gold_table_columns", lambda _con, _table: None)
    monkeypatch.setattr(
        engine,
        "_gold_query_schema",
        lambda _con, _sql: {"order_count": ("order_count", "INTEGER")},
    )
    monkeypatch.setattr(engine, "_apply_gold_rls", lambda table: applied.append(table))

    engine._ensure_scoped_gold_table(con, "gold_orders", "SELECT 1 AS order_count")

    assert applied == ["gold_orders"]
    executed_sql = "\n".join(str(call.args[0]) for call in con.execute.call_args_list if call.args)
    assert "CREATE TABLE pggold.gold_orders" in executed_sql


def test_scoped_gold_table_adds_columns_when_dataset_schema_evolves(monkeypatch):
    engine = DuckDBEngine()
    con = MagicMock()
    added: list[tuple[str, list[tuple[str, str]]]] = []
    applied: list[str] = []
    monkeypatch.setattr(
        engine,
        "_gold_table_columns",
        lambda _con, _table: {"tenant_id", "workspace_id", "user_id"},
    )
    monkeypatch.setattr(
        engine,
        "_gold_query_schema",
        lambda _con, _sql: {
            "tenant_id": ("tenant_id", "TEXT"),
            "workspace_id": ("workspace_id", "TEXT"),
            "user_id": ("user_id", "TEXT"),
            "box_key": ("box_key", "TEXT"),
        },
    )
    monkeypatch.setattr(
        engine,
        "_add_missing_gold_columns",
        lambda table, columns: added.append((table, columns)),
    )
    monkeypatch.setattr(engine, "_apply_gold_rls", lambda table: applied.append(table))

    engine._ensure_scoped_gold_table(con, "gold_sap_successfactors_talent_9box", "SELECT 1")

    assert added == [("gold_sap_successfactors_talent_9box", [("box_key", "TEXT")])]
    assert applied == ["gold_sap_successfactors_talent_9box"]
    executed_sql = "\n".join(str(call.args[0]) for call in con.execute.call_args_list if call.args)
    assert "DROP TABLE" not in executed_sql
    assert "CREATE TABLE pggold.gold_sap_successfactors_talent_9box" not in executed_sql


def test_duckdb_type_mapping_for_gold_schema_evolution():
    assert _duckdb_type_to_pg_type("VARCHAR") == "TEXT"
    assert _duckdb_type_to_pg_type("BIGINT") == "BIGINT"
    assert _duckdb_type_to_pg_type("DOUBLE") == "DOUBLE PRECISION"
    assert _duckdb_type_to_pg_type("TIMESTAMP WITH TIME ZONE") == "TIMESTAMPTZ"
    assert _duckdb_type_to_pg_type("DECIMAL(18,2)") == "DECIMAL(18,2)"


def test_gold_write_path_avoids_duckdb_postgres_copy_with_rls():
    src = (REPO_ROOT / "refinement" / "app" / "duckdb_engine.py").read_text(encoding="utf-8")

    assert "def _replace_scoped_gold_rows" in src
    assert "set_config('app.tenant_id'" in src
    assert "execute_values(" in src
    assert "INSERT INTO pggold." not in src


def test_live_gold_rls_role_is_not_allowed_to_bypass_rls(postgres_gold_with_native_rls):
    conn = psycopg2.connect(postgres_gold_with_native_rls)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'omega_refinement_gold'")
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] is False


def test_live_gold_read_reattach_isolates_workspace_a_then_b_in_same_engine(
    monkeypatch,
    postgres_gold_with_native_rls,
):
    tenant_a = "tenant-a"
    workspace_a = "workspace-a"
    tenant_b = "tenant-b"
    workspace_b = "workspace-b"

    conn = psycopg2.connect(postgres_gold_with_native_rls)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE public.gold_scope_probe (
                    marker text PRIMARY KEY,
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_scope_probe (marker, tenant_id, workspace_id)
                VALUES
                    ('row-a', %s, %s),
                    ('row-b', %s, %s)
                """,
                (tenant_a, workspace_a, tenant_b, workspace_b),
            )
            cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.gold_scope_probe TO omega_refinement_gold")
            cur.execute("SELECT public.omega_apply_gold_rls_for_table('gold_scope_probe')")
        conn.commit()
    finally:
        conn.close()

    role_dsn = postgres_gold_with_native_rls.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", role_dsn)
    engine = DuckDBEngine()
    try:
        con = engine._conn()
        engine._pg_gold_attach(con, {"tenant_id": tenant_a, "workspace_id": workspace_a})
        rows_a = con.execute("SELECT marker FROM pggold.gold_scope_probe ORDER BY marker").fetchall()

        engine._pg_gold_attach(con, {"tenant_id": tenant_b, "workspace_id": workspace_b})
        rows_b = con.execute("SELECT marker FROM pggold.gold_scope_probe ORDER BY marker").fetchall()
    finally:
        if engine._con is not None:
            engine._con.close()

    assert rows_a == [("row-a",)]
    assert rows_b == [("row-b",)]
