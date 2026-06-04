from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import httpx

from app.services.jwt_auth import create_access_token


class SimulationFailure(RuntimeError):
    pass


@dataclass
class SimUser:
    id: int
    email: str
    workspace_id: str
    tenant_id: str
    workspace_role: str
    signal_ids: list[str] = field(default_factory=list)
    decision_ids: list[int] = field(default_factory=list)
    dataset_names: list[str] = field(default_factory=list)


@dataclass
class WorkspaceBundle:
    workspace_id: str
    admin: SimUser
    employees: list[SimUser]
    pipeline_run_id: str
    vault_key: str


@dataclass
class SimulationState:
    suffix: str
    tenant_id: str
    workspaces: list[WorkspaceBundle]
    artifact_dir: Path

    @property
    def users(self) -> list[SimUser]:
        output: list[SimUser] = []
        for bundle in self.workspaces:
            output.append(bundle.admin)
            output.extend(bundle.employees)
        return output


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SimulationFailure(f"{name} must be an integer") from exc
    if value < 0:
        raise SimulationFailure(f"{name} must be >= 0")
    return value


def _dsn_for_role(base_dsn: str, role: str, password: str | None) -> str | None:
    if not password:
        return None
    parsed = urlsplit(base_dsn)
    if not parsed.hostname:
        return None
    userinfo = f"{quote(role)}:{quote(password)}"
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme or "postgresql", f"{userinfo}@{host}", parsed.path, parsed.query, parsed.fragment))


def _token(user: SimUser) -> str:
    return create_access_token({"sub": str(user.id), "email": user.email, "role": "user"})


async def _fetch_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    user: SimUser,
    *,
    workspace_id: str | None = None,
    body: dict[str, Any] | None = None,
    expected: set[int] | None = None,
) -> tuple[int, Any]:
    headers = {
        "Authorization": f"Bearer {_token(user)}",
        "x-workspace-id": workspace_id or user.workspace_id,
    }
    response = await client.request(method, path, headers=headers, json=body)
    allowed = expected or {200}
    if response.status_code not in allowed:
        raise SimulationFailure(
            f"{method} {path} as {user.email} returned {response.status_code}: {response.text[:500]}"
        )
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, response.text


async def seed(conn: asyncpg.Connection, *, suffix: str, admins: int, min_employees: int, max_employees: int) -> SimulationState:
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"sim-prod-tenant-{suffix}",
    )
    role_rows = await conn.fetch("SELECT id, name FROM roles WHERE name = ANY($1::text[])", ["tenant_admin", "viewer"])
    role_ids = {str(row["name"]): int(row["id"]) for row in role_rows}
    missing_roles = {"tenant_admin", "viewer"} - set(role_ids)
    if missing_roles:
        raise SimulationFailure(f"missing roles: {', '.join(sorted(missing_roles))}")
    hubspot_product_id = await conn.fetchval(
        "SELECT id FROM marketplace_products WHERE cartridge_id = 'hubspot' AND status IN ('active', 'internal')"
    )
    if not hubspot_product_id:
        raise SimulationFailure("missing active marketplace product for hubspot")

    bundles: list[WorkspaceBundle] = []
    password_hash = "simulation-disabled-password-hash"
    rng = random.Random(suffix)

    async with conn.transaction():
        for index in range(1, admins + 1):
            workspace_id = await conn.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
                tenant_id,
                f"Sim Admin Workspace {suffix}-{index:03d}",
            )
            admin_email = f"sim-{suffix}-admin-{index:03d}@example.invalid"
            admin_id = await conn.fetchval(
                """
                INSERT INTO users (email, name, password_hash, role, tenant_id, is_active, must_change_password)
                VALUES ($1, $2, $3, 'user', $4, TRUE, FALSE)
                RETURNING id
                """,
                admin_email,
                f"Sim Admin {index:03d}",
                password_hash,
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO user_workspace_roles (user_id, workspace_id, role_id) VALUES ($1, $2, $3)",
                admin_id,
                workspace_id,
                role_ids["tenant_admin"],
            )
            await conn.execute(
                """
                INSERT INTO tenant_entitlements (
                    tenant_id, workspace_id, cartridge_id, product_id, status, activated_by_id
                )
                VALUES ($1, $2, 'hubspot', $3, 'active', $4)
                ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO UPDATE
                  SET status = 'active', product_id = EXCLUDED.product_id, activated_by_id = EXCLUDED.activated_by_id
                """,
                tenant_id,
                workspace_id,
                hubspot_product_id,
                admin_id,
            )
            await conn.execute(
                """
                INSERT INTO cartridge_installations (
                    id, tenant_id, workspace_id, cartridge_id, product_id, status,
                    current_step, install_fingerprint, created_by_id, ready_at
                )
                VALUES ($1, $2, $3, 'hubspot', $4, 'ready',
                        'simulation_seeded', $5, $6, NOW())
                ON CONFLICT (workspace_id, cartridge_id) DO UPDATE
                  SET status = 'ready',
                      product_id = EXCLUDED.product_id,
                      current_step = 'simulation_seeded',
                      ready_at = NOW(),
                      updated_at = NOW()
                """,
                f"sim_{suffix}_install_{index:03d}",
                tenant_id,
                workspace_id,
                hubspot_product_id,
                f"sim:{suffix}:{index:03d}:hubspot",
                admin_id,
            )
            admin = SimUser(
                id=int(admin_id),
                email=admin_email,
                workspace_id=str(workspace_id),
                tenant_id=str(tenant_id),
                workspace_role="tenant_admin",
            )
            employees: list[SimUser] = []
            employee_count = rng.randint(min_employees, max_employees) if max_employees > min_employees else min_employees
            for employee_index in range(1, employee_count + 1):
                email = f"sim-{suffix}-admin-{index:03d}-emp-{employee_index:03d}@example.invalid"
                user_id = await conn.fetchval(
                    """
                    INSERT INTO users (email, name, password_hash, role, tenant_id, is_active, must_change_password)
                    VALUES ($1, $2, $3, 'user', $4, TRUE, FALSE)
                    RETURNING id
                    """,
                    email,
                    f"Sim Employee {index:03d}-{employee_index:03d}",
                    password_hash,
                    tenant_id,
                )
                await conn.execute(
                    "INSERT INTO user_workspace_roles (user_id, workspace_id, role_id) VALUES ($1, $2, $3)",
                    user_id,
                    workspace_id,
                    role_ids["viewer"],
                )
                employee = SimUser(
                    id=int(user_id),
                    email=email,
                    workspace_id=str(workspace_id),
                    tenant_id=str(tenant_id),
                    workspace_role="viewer",
                )
                dataset = f"sim_{suffix}_w{index:03d}_u{employee_index:03d}"
                await conn.execute(
                    """
                    INSERT INTO datasets (
                        name, description, layer, cartridge, sources, sql_def,
                        column_mapping, workspace_id, created_by_id
                    )
                    VALUES ($1, 'simulation dataset', 'gold', 'hubspot', '[]'::jsonb,
                            'SELECT 1', '{}'::jsonb, $2, $3)
                    """,
                    dataset,
                    workspace_id,
                    user_id,
                )
                decision_id = await conn.fetchval(
                    """
                    INSERT INTO decisions (
                        title, description, created_by_id, assignee_id, visibility, workspace_id
                    )
                    VALUES ($1, 'simulation decision', $2, $2, 'private', $3)
                    RETURNING id
                    """,
                    f"sim decision {suffix} {index:03d}/{employee_index:03d}",
                    user_id,
                    workspace_id,
                )
                signal_id = f"sim-{suffix}-w{index:03d}-u{employee_index:03d}"
                await conn.execute(
                    """
                    INSERT INTO intelligence_signals (
                        signal_id, tenant_id, workspace_id, cartridge_id, dataset, domain,
                        entity_kind, entity_id, entity_label, metric, period_key,
                        actual_value, expected_value, deviation_value, deviation_pct,
                        severity, signal_type, confidence, summary, owner_user_id, metadata
                    )
                    VALUES ($1, $2, $3, 'hubspot', $4, 'simulation', 'employee', $5, $6,
                            'simulation_metric', '2026-06', 120, 100, 20, 0.20,
                            'medium', 'opportunity', 0.80, $7, $8, $9::jsonb)
                    """,
                    signal_id,
                    tenant_id,
                    workspace_id,
                    dataset,
                    str(user_id),
                    email,
                    f"Simulation signal for {email}",
                    user_id,
                    json.dumps({"simulation_suffix": suffix}),
                )
                pack_id = await conn.fetchval(
                    """
                    INSERT INTO evidence_packs (
                        tenant_id, workspace_id, signal_id, summary, confidence, owner_user_id, metadata
                    )
                    VALUES ($1, $2, $3, 'simulation evidence pack', 0.80, $4, $5::jsonb)
                    RETURNING id
                    """,
                    tenant_id,
                    workspace_id,
                    signal_id,
                    user_id,
                    json.dumps({"simulation_suffix": suffix, "row_count": 1}),
                )
                await conn.execute(
                    """
                    INSERT INTO evidence_items (
                        tenant_id, workspace_id, evidence_pack_id, source_type, source_ref,
                        query_text, data, supports_hypothesis, strength, owner_user_id, metadata
                    )
                    VALUES ($1, $2, $3, 'sql', $4, 'SELECT simulation row',
                            $5::jsonb, 'baseline_deviation', 0.80, $6, $7::jsonb)
                    """,
                    tenant_id,
                    workspace_id,
                    pack_id,
                    dataset,
                    json.dumps({"dataset": dataset, "actual": 120, "expected": 100}),
                    user_id,
                    json.dumps({"simulation_suffix": suffix}),
                )
                await conn.execute(
                    """
                    INSERT INTO hypotheses (
                        tenant_id, workspace_id, signal_id, hypothesis_key, title,
                        rationale, confidence, evidence_pack_id, owner_user_id, metadata
                    )
                    VALUES ($1, $2, $3, 'baseline_deviation', 'Baseline deviation',
                            'Simulation evidence supports the deviation.', 0.75, $4, $5, $6::jsonb)
                    ON CONFLICT (workspace_id, signal_id, hypothesis_key)
                    DO UPDATE SET confidence = EXCLUDED.confidence
                    """,
                    tenant_id,
                    workspace_id,
                    signal_id,
                    pack_id,
                    user_id,
                    json.dumps({"simulation_suffix": suffix}),
                )
                await conn.execute(
                    """
                    INSERT INTO decision_options (
                        tenant_id, workspace_id, signal_id, option_id, label, action_kind,
                        impact_expected, confidence, cost, risk, time_cost, score,
                        owner_user_id, metadata
                    )
                    VALUES ($1, $2, $3, 'opt-1', 'Review simulation opportunity',
                            'supervised_review', 1000, 0.80, 100, 50, 10, 640,
                            $4, $5::jsonb)
                    ON CONFLICT (workspace_id, signal_id, option_id)
                    DO UPDATE SET score = EXCLUDED.score
                    """,
                    tenant_id,
                    workspace_id,
                    signal_id,
                    user_id,
                    json.dumps({"simulation_suffix": suffix}),
                )
                employee.signal_ids.append(signal_id)
                employee.decision_ids.append(int(decision_id))
                employee.dataset_names.append(dataset)
                employees.append(employee)

            pipeline_run_id = f"sim-{suffix}-pipeline-{index:03d}"
            await conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, dag_id, cartridge_id, entity, status, started_at, finished_at,
                    record_count, tenant_id, workspace_id, extra
                )
                VALUES ($1, 'sim_dag', 'hubspot', 'simulation', 'success', NOW(), NOW(),
                        $2, $3, $4, $5::jsonb)
                """,
                pipeline_run_id,
                employee_count,
                tenant_id,
                workspace_id,
                json.dumps({"simulation_suffix": suffix}),
            )
            vault_key = f"sim_key_{suffix}_{index:03d}"
            await conn.execute(
                """
                INSERT INTO vault_entries (scope, cartridge, key, value, tenant_id, workspace_id)
                VALUES ('llm', 'anthropic', $1, $2::jsonb, $3, $4)
                """,
                vault_key,
                json.dumps({"api_key": "simulated"}),
                tenant_id,
                workspace_id,
            )
            bundles.append(
                WorkspaceBundle(
                    workspace_id=str(workspace_id),
                    admin=admin,
                    employees=employees,
                    pipeline_run_id=pipeline_run_id,
                    vault_key=vault_key,
                )
            )

    artifact_dir = Path("artifacts") / "multiuser-isolation" / suffix
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return SimulationState(suffix=suffix, tenant_id=str(tenant_id), workspaces=bundles, artifact_dir=artifact_dir)


async def run_api_checks(state: SimulationState) -> dict[str, Any]:
    if os.environ.get("OMEGA_MULTIUSER_SIM_SKIP_API") == "1":
        return {"skipped": True}
    base_url = os.environ.get("CONSOLE_URL", "http://127.0.0.1:8000").rstrip("/")
    admin_sample = state.workspaces[: _int_env("OMEGA_MULTIUSER_SIM_API_SAMPLE_ADMINS", 10)]
    all_employees = [employee for bundle in state.workspaces for employee in bundle.employees]
    employee_sample = all_employees[: _int_env("OMEGA_MULTIUSER_SIM_API_SAMPLE_EMPLOYEES", 40)]
    cross_workspace = state.workspaces[-1].workspace_id if state.workspaces else None
    summary = {"admin_checks": 0, "employee_checks": 0, "concurrent_ops": 0}

    async with httpx.AsyncClient(base_url=base_url, timeout=20.0) as client:
        health = await client.get("/healthz")
        if health.status_code >= 500:
            raise SimulationFailure(f"console health failed: {health.status_code} {health.text[:300]}")

        for bundle in admin_sample:
            _, payload = await _fetch_json(client, "GET", "/api/intelligence/signals?limit=500", bundle.admin)
            signals = payload.get("signals", []) if isinstance(payload, dict) else []
            expected = {sig for employee in bundle.employees for sig in employee.signal_ids}
            visible = {str(item.get("signal_id")) for item in signals if item.get("signal_id")}
            if not expected.issubset(visible):
                raise SimulationFailure(f"admin {bundle.admin.email} cannot see all workspace signals")
            if any(not signal_id.startswith(f"sim-{state.suffix}-") for signal_id in visible if signal_id in expected):
                raise SimulationFailure("unexpected non-simulation signal leaked into admin sample")

            _, decisions = await _fetch_json(client, "GET", "/api/decisions", bundle.admin)
            rows = decisions.get("decisions", []) if isinstance(decisions, dict) else []
            expected_decisions = {decision for employee in bundle.employees for decision in employee.decision_ids}
            visible_decisions = {int(item.get("id")) for item in rows if item.get("id") in expected_decisions}
            if expected_decisions - visible_decisions:
                raise SimulationFailure(f"admin {bundle.admin.email} cannot see all workspace decisions")

            _, datasets_payload = await _fetch_json(client, "GET", "/api/datasets", bundle.admin)
            datasets = datasets_payload.get("datasets", []) if isinstance(datasets_payload, dict) else []
            expected_datasets = {name for employee in bundle.employees for name in employee.dataset_names}
            visible_datasets = {str(item.get("name")) for item in datasets if item.get("name") in expected_datasets}
            if expected_datasets - visible_datasets:
                raise SimulationFailure(f"admin {bundle.admin.email} cannot see all workspace datasets")

            await _fetch_json(
                client,
                "POST",
                "/api/v1/intelligence/run",
                bundle.admin,
                body={"include_external": True, "horizon_days": [7, 21], "dry_run": True},
            )
            first_signal = bundle.employees[0].signal_ids[0]
            await _fetch_json(
                client,
                "POST",
                f"/api/v1/intelligence/signals/{first_signal}/options/opt-1/select",
                bundle.admin,
            )
            await _fetch_json(
                client,
                "POST",
                f"/api/v1/intelligence/signals/{first_signal}/outcome",
                bundle.admin,
                body={"option_id": "opt-1", "action_taken": "simulation review", "actual_value": 125},
            )
            if cross_workspace and cross_workspace != bundle.workspace_id:
                await _fetch_json(
                    client,
                    "GET",
                    "/api/intelligence/signals",
                    bundle.admin,
                    workspace_id=cross_workspace,
                    expected={403},
                )
            summary["admin_checks"] += 1

        for employee in employee_sample:
            _, payload = await _fetch_json(client, "GET", "/api/intelligence/signals?limit=500", employee)
            signals = payload.get("signals", []) if isinstance(payload, dict) else []
            visible = {str(item.get("signal_id")) for item in signals if item.get("signal_id")}
            if set(employee.signal_ids) != visible.intersection(set(employee.signal_ids) | visible):
                raise SimulationFailure(f"employee {employee.email} saw wrong signals: {sorted(visible)}")
            _, decisions = await _fetch_json(client, "GET", "/api/decisions", employee)
            rows = decisions.get("decisions", []) if isinstance(decisions, dict) else []
            visible_decisions = {int(item.get("id")) for item in rows if item.get("id")}
            if set(employee.decision_ids) != visible_decisions.intersection(set(employee.decision_ids) | visible_decisions):
                raise SimulationFailure(f"employee {employee.email} saw wrong decisions: {sorted(visible_decisions)}")
            _, datasets_payload = await _fetch_json(client, "GET", "/api/datasets", employee)
            datasets = datasets_payload.get("datasets", []) if isinstance(datasets_payload, dict) else []
            visible_sim_datasets = {
                str(item.get("name"))
                for item in datasets
                if str(item.get("name") or "").startswith(f"sim_{state.suffix}_")
            }
            if set(employee.dataset_names) != visible_sim_datasets:
                raise SimulationFailure(f"employee {employee.email} saw wrong datasets: {sorted(visible_sim_datasets)}")
            if cross_workspace and cross_workspace != employee.workspace_id:
                await _fetch_json(
                    client,
                    "GET",
                    "/api/intelligence/signals",
                    employee,
                    workspace_id=cross_workspace,
                    expected={403},
                )
            summary["employee_checks"] += 1

        ops = _int_env("OMEGA_MULTIUSER_SIM_CONCURRENT_OPS", 300)
        if ops:
            semaphore = asyncio.Semaphore(_int_env("OMEGA_MULTIUSER_SIM_HTTP_CONCURRENCY", 50))
            users = state.users

            async def one_op(i: int) -> None:
                user = users[i % len(users)]
                async with semaphore:
                    if i % 3 == 0:
                        await _fetch_json(client, "GET", "/api/intelligence/signals?limit=50", user)
                    elif i % 3 == 1:
                        await _fetch_json(client, "GET", "/api/decisions", user)
                    else:
                        await _fetch_json(client, "GET", "/api/me", user)

            await asyncio.gather(*(one_op(i) for i in range(ops)))
            summary["concurrent_ops"] = ops
    return summary


async def _scoped_visible_workspaces(
    dsn: str,
    table: str,
    tenant_id: str,
    workspace_id: str,
    workspace_ids: list[str],
) -> set[str] | None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            rows = await conn.fetch(
                f"SELECT DISTINCT workspace_id::text AS workspace_id FROM public.{table} WHERE workspace_id = ANY($1::uuid[])",
                workspace_ids,
            )
            return {str(row["workspace_id"]) for row in rows}
    except asyncpg.exceptions.InsufficientPrivilegeError:
        return None
    finally:
        await conn.close()


async def run_db_checks(owner_dsn: str, state: SimulationState) -> dict[str, Any]:
    workspace_ids = [bundle.workspace_id for bundle in state.workspaces]
    summary: dict[str, Any] = {"direct_scope_checks": 0, "rls_role_checks": 0, "permission_denied_checks": 0}
    conn = await asyncpg.connect(owner_dsn)
    try:
        checks = {
            "vault_entries": "tenant_id IS NULL OR workspace_id IS NULL",
            "pipeline_runs": "tenant_id IS NULL OR workspace_id IS NULL",
            "intelligence_signals": "owner_user_id IS NULL",
            "evidence_packs": "owner_user_id IS NULL",
            "evidence_items": "owner_user_id IS NULL",
            "decision_options": "owner_user_id IS NULL",
        }
        for table, predicate in checks.items():
            count = int(await conn.fetchval(
                f"""
                SELECT COUNT(*)
                  FROM public.{table}
                 WHERE workspace_id = ANY($1::uuid[])
                   AND ({predicate})
                """,
                workspace_ids,
            ) or 0)
            if count:
                raise SimulationFailure(f"{table} has {count} simulation rows with missing scope/owner")
            summary["direct_scope_checks"] += 1
    finally:
        await conn.close()

    if os.environ.get("OMEGA_MULTIUSER_SIM_SKIP_DB_RLS") == "1":
        summary["rls_skipped"] = True
        return summary

    workspace_dsn = _dsn_for_role(owner_dsn, "omega_workspace", os.environ.get("OMEGA_WORKSPACE_PASSWORD"))
    console_dsn = _dsn_for_role(owner_dsn, "omega_console", os.environ.get("OMEGA_CONSOLE_PASSWORD"))
    mcp_dsn = _dsn_for_role(owner_dsn, "omega_mcp_infra", os.environ.get("OMEGA_MCP_INFRA_PASSWORD"))
    vault_dsn = _dsn_for_role(owner_dsn, "omega_vault", os.environ.get("OMEGA_VAULT_PASSWORD"))
    required_roles = {
        "omega_workspace": workspace_dsn,
        "omega_console": console_dsn,
        "omega_mcp_infra": mcp_dsn,
        "omega_vault": vault_dsn,
    }
    missing = [role for role, dsn in required_roles.items() if not dsn]
    if missing:
        raise SimulationFailure(
            "missing DB role passwords for direct RLS checks: " + ", ".join(missing)
        )

    workspace_tables = [
        "datasets",
        "decisions",
    ]
    intelligence_tables = [
        "intelligence_signals",
        "evidence_packs",
        "evidence_items",
        "decision_options",
        "prediction_outcomes",
    ]
    mcp_tables = ["pipeline_runs", "user_workspace_roles"]
    vault_tables = ["vault_entries"]
    for bundle in state.workspaces[: min(10, len(state.workspaces))]:
        for table in workspace_tables:
            visible = await _scoped_visible_workspaces(workspace_dsn, table, state.tenant_id, bundle.workspace_id, workspace_ids)  # type: ignore[arg-type]
            if visible is None:
                summary["permission_denied_checks"] += 1
                continue
            if visible - {bundle.workspace_id}:
                raise SimulationFailure(f"{table} leaked workspaces via omega_workspace: {visible}")
            summary["rls_role_checks"] += 1
        for table in intelligence_tables:
            visible = await _scoped_visible_workspaces(console_dsn, table, state.tenant_id, bundle.workspace_id, workspace_ids)  # type: ignore[arg-type]
            if visible is None:
                summary["permission_denied_checks"] += 1
                continue
            if visible - {bundle.workspace_id}:
                raise SimulationFailure(f"{table} leaked workspaces via omega_console: {visible}")
            summary["rls_role_checks"] += 1
        for table in mcp_tables:
            visible = await _scoped_visible_workspaces(mcp_dsn, table, state.tenant_id, bundle.workspace_id, workspace_ids)  # type: ignore[arg-type]
            if visible is None:
                summary["permission_denied_checks"] += 1
                continue
            if visible - {bundle.workspace_id}:
                raise SimulationFailure(f"{table} leaked workspaces via omega_mcp_infra: {visible}")
            summary["rls_role_checks"] += 1
        for table in vault_tables:
            visible = await _scoped_visible_workspaces(vault_dsn, table, state.tenant_id, bundle.workspace_id, workspace_ids)  # type: ignore[arg-type]
            if visible is None:
                summary["permission_denied_checks"] += 1
                continue
            if visible - {bundle.workspace_id}:
                raise SimulationFailure(f"{table} leaked workspaces via omega_vault: {visible}")
            summary["rls_role_checks"] += 1
    return summary


async def cleanup(conn: asyncpg.Connection, state: SimulationState) -> None:
    if os.environ.get("OMEGA_MULTIUSER_SIM_CLEANUP", "1") not in {"1", "true", "yes"}:
        return
    workspace_ids = [bundle.workspace_id for bundle in state.workspaces]
    user_ids = [user.id for user in state.users]
    async with conn.transaction():
        for table in (
            "prediction_outcomes",
            "decision_options",
            "hypotheses",
            "evidence_items",
            "evidence_packs",
            "intelligence_signals",
            "control_room_item_events",
            "control_room_items",
            "pipeline_runs",
            "vault_entries",
            "datasets",
            "decisions",
        ):
            exists = bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))
            if exists:
                await conn.execute(f"DELETE FROM public.{table} WHERE workspace_id = ANY($1::uuid[])", workspace_ids)
        await conn.execute("DELETE FROM user_workspace_roles WHERE user_id = ANY($1::bigint[])", user_ids)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::bigint[])", user_ids)
        await conn.execute("DELETE FROM workspaces WHERE id = ANY($1::uuid[])", workspace_ids)
        await conn.execute("DELETE FROM tenants WHERE id = $1", state.tenant_id)


async def main() -> None:
    started = time.monotonic()
    suffix = os.environ.get("OMEGA_MULTIUSER_SIM_SUFFIX") or uuid.uuid4().hex[:10]
    admins = _int_env("OMEGA_MULTIUSER_SIM_ADMINS", 50)
    min_employees = _int_env("OMEGA_MULTIUSER_SIM_MIN_EMPLOYEES", 5)
    max_employees = _int_env("OMEGA_MULTIUSER_SIM_MAX_EMPLOYEES", 5)
    if admins <= 0:
        raise SimulationFailure("OMEGA_MULTIUSER_SIM_ADMINS must be > 0")
    if min_employees <= 0 or max_employees < min_employees:
        raise SimulationFailure("invalid employee bounds")
    owner_dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    conn = await asyncpg.connect(owner_dsn)
    state: SimulationState | None = None
    report: dict[str, Any] = {}
    try:
        state = await seed(conn, suffix=suffix, admins=admins, min_employees=min_employees, max_employees=max_employees)
        await conn.close()
        conn = None  # type: ignore[assignment]
        api = await run_api_checks(state)
        db = await run_db_checks(owner_dsn, state)
        report = {
            "ok": True,
            "suffix": suffix,
            "tenant_id": state.tenant_id,
            "admins": len(state.workspaces),
            "users": len(state.users),
            "employees": len(state.users) - len(state.workspaces),
            "api": api,
            "db": db,
            "duration_seconds": round(time.monotonic() - started, 3),
            "cleanup": os.environ.get("OMEGA_MULTIUSER_SIM_CLEANUP", "1"),
        }
    finally:
        if conn is None:
            conn = await asyncpg.connect(owner_dsn)
        if state is not None:
            await cleanup(conn, state)
            report_path = state.artifact_dir / "report.json"
            report_path.write_text(json.dumps(report or {"ok": False, "suffix": suffix}, indent=2, sort_keys=True), encoding="utf-8")
            print(f"[multiuser-sim] report: {report_path}")
        await conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SimulationFailure as exc:
        raise SystemExit(f"[multiuser-sim] FAIL: {exc}") from exc
