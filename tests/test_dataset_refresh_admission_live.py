from __future__ import annotations

import importlib.util
from pathlib import Path

import asyncpg
import pytest

from airflow.dags.dataset_refresh_admission import (
    build_dataset_refresh_admission,
    build_dataset_refresh_trigger,
    validate_dataset_refresh_admission,
)
from airflow.dags.runtime_security_context import sign_runtime_context
from tests.test_control_room_live_postgres_operational_truth_pipeline import _seed_scope
from tests.test_operational_rls_console_refinement import (
    OMEGA_AIRFLOW_DAG_PASSWORD,
    POSTGRES_PASSWORD,
    postgres_with_real_init_schema,
)


ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "dataset_refresh_admission_test_key_at_least_32_chars"


def _role_dsn(admin_dsn: str) -> str:
    return admin_dsn.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_airflow_dag:{OMEGA_AIRFLOW_DAG_PASSWORD}",
    )


def _upstream(scope: dict[str, str], *, permissions=None, now=None) -> dict:
    return sign_runtime_context(
        {
            "trusted": True,
            "source": "airflow",
            "tenant_id": scope["tenant_id"],
            "workspace_id": scope["workspace_id"],
            "permissions": permissions or ["pipelines.run"],
            "allowed_cartridges": ["replicon"],
        },
        now=now,
    )


def _mcp_builder():
    path = ROOT / "mcp-infra/app/dataset_refresh_admission.py"
    spec = importlib.util.spec_from_file_location("mcp_dataset_refresh_admission", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_admission_is_scope_bound_and_same_run_retry_is_recoverable(
    postgres_with_real_init_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope = await _seed_scope(admin, "dataset-admission")
    finally:
        await admin.close()
    conf = {
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "cartridge_id": "replicon",
        "seed_dataset": "pnl_mensual",
        "allow_partial": False,
        "skip_intelligence": False,
        "project_id": "server-project",
    }
    run_id = "manual__dataset_admission"
    admission = build_dataset_refresh_admission(
        upstream_context=_upstream(scope), conf=conf, dag_run_id=run_id
    )
    signed_at = int(admission["_signed_at"])
    assert validate_dataset_refresh_admission(
        admission,
        conf={**conf, "security_context": admission},
        run_id=run_id,
        postgres_dsn=_role_dsn(postgres_with_real_init_schema),
        now=signed_at + 1,
    ) == (scope["tenant_id"], scope["workspace_id"])
    assert validate_dataset_refresh_admission(
        admission,
        conf={**conf, "security_context": admission},
        run_id=run_id,
        postgres_dsn=_role_dsn(postgres_with_real_init_schema),
        now=signed_at + 2,
    ) == (scope["tenant_id"], scope["workspace_id"])
    assert validate_dataset_refresh_admission(
        admission,
        conf={**conf, "security_context": admission},
        run_id=run_id,
        postgres_dsn=_role_dsn(postgres_with_real_init_schema),
        now=signed_at + 301,
    ) == (scope["tenant_id"], scope["workspace_id"])
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_dataset_refresh_admission(
            admission,
            conf={**conf, "security_context": admission},
            run_id="manual__different_run",
            postgres_dsn=_role_dsn(postgres_with_real_init_schema),
            now=signed_at + 2,
        )


def test_admission_rejects_unsigned_decision_fields_and_unrelated_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    scope = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
    }
    conf = {
        **scope,
        "cartridge_id": "replicon",
        "seed_dataset": "pnl_mensual",
        "allow_partial": False,
    }
    with pytest.raises(ValueError, match="upstream authority"):
        build_dataset_refresh_admission(
            upstream_context=_upstream(scope, permissions=["datasets.read"]),
            conf=conf,
            dag_run_id="manual__unauthorized",
        )
    mcp = _mcp_builder()
    with pytest.raises(ValueError, match="scope is incomplete"):
        mcp.build_dataset_refresh_admission(
            _upstream(scope, permissions=["datasets.read"]),
            conf,
            "manual__unauthorized",
        )
    admission = build_dataset_refresh_admission(
        upstream_context=_upstream(scope),
        conf=conf,
        dag_run_id="manual__tamper",
    )
    signed_at = int(admission["_signed_at"])
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_dataset_refresh_admission(
            admission,
            conf={**conf, "allow_partial": True, "security_context": admission},
            run_id="manual__tamper",
            postgres_dsn="postgresql://unused",
            now=signed_at + 1,
        )


def test_bridge_exchanges_upstream_authority_before_long_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    scope = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
    }
    monkeypatch.setattr(
        "airflow.dags.dataset_refresh_admission.time.time", lambda: 1000
    )
    monkeypatch.setattr("airflow.dags.runtime_security_context.time.time", lambda: 1000)
    trigger = build_dataset_refresh_trigger(
        upstream_context=_upstream(scope, now=1000),
        conf={
            **scope,
            "cartridge_id": "replicon",
            "seed_raw": "raw/replicon/User",
        },
        source_dag_run_id="scheduled__source_run",
        prefix="replicon",
    )
    admission = trigger["conf"]["security_context"]
    assert admission["_expires_at"] - admission["_signed_at"] == 86_400
    assert (
        trigger["dag_run_id"]
        == build_dataset_refresh_trigger(
            upstream_context=_upstream(scope, now=1000),
            conf={
                **scope,
                "cartridge_id": "replicon",
                "seed_raw": "raw/replicon/User",
            },
            source_dag_run_id="scheduled__source_run",
            prefix="replicon",
        )["dag_run_id"]
    )
