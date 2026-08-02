from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.scheduled_monitor_execution import (
    ScheduledMonitorLeaseLost,
    execute_reserved_scheduled_monitor,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lease_loss_retires_authority_before_cancellation_rebel() -> None:
    cancelled = asyncio.Event()
    heartbeat_calls = 0
    authority_active = True
    effects: list[str] = []

    async def monitor(*_args, lease_guard, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            try:
                await lease_guard()
            except Exception:
                return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}
            effects.append("effect-after-lease-loss")
            return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}

    async def heartbeat(**_kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if not authority_active or heartbeat_calls > 1:
            raise RuntimeError("fence lost")

    async def finish(**_kwargs):
        nonlocal authority_active
        authority_active = False

    task = asyncio.create_task(
        execute_reserved_scheduled_monitor(
            agent=SimpleNamespace(tenant_id="tenant-a", workspace_id="workspace-a"),
            message="probe",
            reservation={"id": 1, "fencing_token": 1},
            scheduled_fire_at=None,
            heartbeat_interval_seconds=0.01,
            execution_timeout_seconds=0.5,
            run_scheduled_monitor=monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=finish,
        )
    )
    await asyncio.wait_for(cancelled.wait(), timeout=0.3)
    with pytest.raises(ScheduledMonitorLeaseLost):
        await asyncio.wait_for(task, timeout=0.3)
    assert effects == []


@pytest.mark.asyncio
async def test_timeout_retires_fence_before_cancelling_rebel() -> None:
    authority_active = True
    order: list[str] = []
    effects: list[str] = []

    async def heartbeat(**_kwargs):
        if not authority_active:
            raise RuntimeError("fence retired")

    async def finish(**_kwargs):
        nonlocal authority_active
        authority_active = False
        order.append("retired")

    async def monitor(*_args, lease_guard, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            order.append("cancelled")
            try:
                await lease_guard()
            except Exception:
                return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}
            effects.append("effect-after-timeout")
            return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}

    with pytest.raises(Exception, match="exceeded its execution window"):
        await asyncio.wait_for(
            execute_reserved_scheduled_monitor(
                agent=SimpleNamespace(tenant_id="tenant-a", workspace_id="workspace-a"),
                message="probe",
                reservation={"id": 1, "fencing_token": 1},
                scheduled_fire_at=None,
                lease_seconds=30,
                heartbeat_interval_seconds=0.01,
                execution_timeout_seconds=0.03,
                run_scheduled_monitor=monitor,
                heartbeat_scheduled_run=heartbeat,
                finish_scheduled_run=finish,
            ),
            timeout=0.3,
        )

    assert order[:2] == ["retired", "cancelled"]
    assert effects == []


@pytest.mark.asyncio
async def test_external_cancel_retries_retirement_before_cancelling_rebel() -> None:
    authority_active = True
    finish_attempts = 0
    monitor_started = asyncio.Event()
    effects: list[str] = []

    async def heartbeat(**_kwargs):
        if not authority_active:
            raise RuntimeError("fence retired")

    async def finish(**_kwargs):
        nonlocal authority_active, finish_attempts
        finish_attempts += 1
        if finish_attempts == 1:
            raise OSError("transient close failure")
        authority_active = False

    async def monitor(*_args, lease_guard, **_kwargs):
        monitor_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await lease_guard()
            except Exception:
                return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}
            effects.append("effect-after-external-cancel")
            return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}

    task = asyncio.create_task(
        execute_reserved_scheduled_monitor(
            agent=SimpleNamespace(tenant_id="tenant-a", workspace_id="workspace-a"),
            message="probe",
            reservation={"id": 1, "fencing_token": 1},
            scheduled_fire_at=None,
            lease_seconds=30,
            heartbeat_interval_seconds=0.01,
            execution_timeout_seconds=1,
            run_scheduled_monitor=monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=finish,
        )
    )
    await asyncio.wait_for(monitor_started.wait(), timeout=0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.3)
    assert finish_attempts == 2
    assert effects == []


def test_every_scheduled_effect_receives_server_owned_fence_capability() -> None:
    runtime = (
        Path(__file__).resolve().parents[1] / "console/app/services/agent_runtime.py"
    ).read_text(encoding="utf-8")
    section = runtime.split("async def run_scheduled_monitor", 1)[1]
    assert "schedule_run_id" in section
    assert "fencing_token" in section
    assert "effect_authority" in section
    mcp = (
        Path(__file__).resolve().parents[1] / "mcp-infra/app/tools/control_room.py"
    ).read_text(encoding="utf-8")
    assert mcp.count('"effect_authority": effect_authority') >= 2
    assert (
        '_call_console_under_fence(\n        scope,\n        effect_authority,\n        "/internal/intelligence/monte-carlo/run"'
        not in mcp
    )
    router = (
        Path(__file__).resolve().parents[1] / "console/app/routers/intelligence.py"
    ).read_text(encoding="utf-8")
    assert router.count("async with _scheduled_effect_guard(") == 2
    assert '"simulation__monte_carlo_run"' in router
    assert '"decision__orchestrate"' in router
    assert 'user["_scheduled_effect_connection"] = conn' in router
    assert 'user.pop("_scheduled_effect_connection", None)' in router
    assert "pg_advisory_xact_lock_shared" in _read(
        "infra/init/99zzq_scheduled_effect_fencing.sql"
    )


def test_f1_verifier_is_a_distinct_runtime_and_database_authority() -> None:
    roles = _read("infra/init_gold/39_staged_publication_roles.sql")
    functions = _read("infra/init_gold/41_staged_publication_functions.sql")
    dockerfile = _read("refinement/Dockerfile")
    assert "omega_gold_verifier" in roles
    assert "TO omega_gold_verifier" in functions
    assert "record_attestation(uuid) TO omega_gold_verifier" in functions
    assert "record_attestation(uuid) TO omega_gold_publisher" not in functions
    assert (
        "publish_materialization"
        not in functions.split("TO omega_gold_verifier", 1)[0].rsplit("GRANT", 1)[-1]
    )
    assert "refinement-verifier" in dockerfile
    assert "refinement-app" in dockerfile
    worker = _read("refinement/app/publication_verifier_worker.py")
    assert "load_verification_candidate(uuid)" in worker
    assert "record_attestation(uuid)" in worker


def test_f1_aws_wiring_provisions_separate_verifier_and_binder_authority() -> None:
    compose = _read("infra/terraform/deploy/docker-compose.aws.yml")
    migrations = _read("infra/terraform/deploy/apply_db_migrations.sh")
    entrypoint = _read("scripts/aws-entrypoint.sh")
    secrets = _read("infra/terraform/infra/secretsmanager.tf")
    env_example = _read("infra/terraform/deploy/.env.example")
    for name in ("OMEGA_GOLD_VERIFIER_PASSWORD", "OMEGA_OUTCOME_BINDER_PASSWORD"):
        assert name in compose
        assert name in migrations
        assert name in entrypoint
        assert name in secrets
        assert name in env_example
    assert "GOLD_VERIFIER_DATABASE_URL_HOST_FILE" in compose
    assert "OUTCOME_BINDER_DATABASE_URL" in compose
    assert 'gold_verifier_tmp="$(mktemp' in entrypoint


def test_f2_public_lineage_uses_the_exact_gold_publication_snapshot() -> None:
    source = _read("refinement/app/publication_public.py")
    assert "PublicationSnapshot" in source
    assert "published_snapshot" in source
    assert "silver_lineage" not in source
    assert "resolver.published_snapshot" in source


def test_f3_schedule_close_serializes_with_every_durable_effect() -> None:
    finish = _read("console/app/services/agent_scheduler.py")
    assert "agent_schedule_effect:" in finish
    assert "pg_advisory_xact_lock" in finish
    migration = _read("infra/init/99zzq_scheduled_effect_fencing.sql")
    assert "assert_scheduled_effect_authority" in migration
    assert "pg_advisory_xact_lock_shared" in migration
    scope = _read("console/app/services/db_scope.py")
    assert "_assert_scheduled_effect_authority" in scope
    assert "_scheduled_effect_connection" in scope
    actions = _read("console/app/services/external_actions.py")
    propose = actions.split("async def propose", 1)[1].split(
        "async def list_actions", 1
    )[0]
    assert "connection=conn" in propose
    assert propose.index("connection=conn") < propose.index("return response")


def test_f4_all_invalid_prepared_states_enter_controlled_recovery() -> None:
    engine = _read("refinement/app/staged_publication_engine.py")
    source = _read("refinement/app/publication_recovery.py")
    assert "_recover_prepared" in engine
    for reason in (
        "attestation_expired",
        "attestation_consumed",
        "gold_stage_missing",
        "object_unavailable",
        "object_corrupt",
        "evidence_mismatch",
    ):
        assert reason in source
