from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.services import auth
from app.services.intelligence import engine as intelligence_engine
from app.services.intelligence import history, persistence
from app.services.intelligence.gold_fetcher import clear_gold_row_cache
from app.services.intelligence.outcome_binding import persist_gold_refresh_binding
from app.services.intelligence.publication_trace import capture_publication_trace
from refinement.app.staged_publication_engine import StagedPublicationEngine
from tests.operational_truth_inputs import pnl_dataset, seed_pnl_input
from tests.test_control_room_live_postgres_operational_truth_pipeline import (
    _context,
    _seed_scope,
    _user,
)
from tests.test_operational_rls_console_refinement import (
    OMEGA_OUTCOME_BINDER_PASSWORD,
    OMEGA_REFINEMENT_PASSWORD,
    POSTGRES_PASSWORD,
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)
from tests.test_staged_publication_live import staged_publication_live_stack


async def _run(body: dict, scope: dict) -> tuple[dict, dict]:
    with capture_publication_trace() as trace:
        result = await intelligence_engine.run_intelligence(
            _user(scope), body, persist=True
        )
    return result, trace


async def _run_without_external_trace(body: dict, scope: dict) -> tuple[dict, dict]:
    result = await intelligence_engine.run_intelligence(
        _user(scope), body, persist=True
    )
    return result, {}


@pytest.mark.asyncio
async def test_gold_refresh_crashes_converge_to_one_binding(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    staged_publication_live_stack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope = await _seed_scope(admin, "binding-crash")
        pipeline_ids = [
            f"dataset_refresh_chain:binding-crash:{uuid.uuid4().hex}" for _ in range(2)
        ]
        await admin.executemany(
            """INSERT INTO pipeline_runs(
                   run_id,dag_id,cartridge_id,entity,status,tenant_id,workspace_id
               ) VALUES($1,'dataset_refresh_chain','replicon',
                        'DatasetRefreshChain','running',$2,$3)""",
            [
                (run_id, scope["tenant_id"], scope["workspace_id"])
                for run_id in pipeline_ids
            ],
        )
    finally:
        await admin.close()

    stack = staged_publication_live_stack
    seed_pnl_input(
        stack,
        scope,
        [
            ("project-a", "Proyecto A", "2026-01-01", 100.0),
            ("project-a", "Proyecto A", "2026-02-01", 100.0),
            ("project-a", "Proyecto A", "2026-03-01", 10.0),
        ],
    )
    for name, value in {
        "DATABASE_URL": postgres_with_real_init_schema.replace(
            f"postgres:{POSTGRES_PASSWORD}",
            f"omega_refinement:{OMEGA_REFINEMENT_PASSWORD}",
        ),
        "GOLD_DATABASE_URL": stack.reader_dsn,
        "GOLD_PUBLISHER_DATABASE_URL": stack.publisher_dsn,
        "MINIO_ENDPOINT": stack.minio_endpoint.removeprefix("http://"),
        "MINIO_ACCESS_KEY": "minio",
        "MINIO_SECRET_KEY": "minio-secret",
        "MINIO_BUCKET": "lakehouse",
        "MINIO_SECURE": "false",
        "OUTCOME_BINDER_DATABASE_URL": postgres_with_real_init_schema.replace(
            f"postgres:{POSTGRES_PASSWORD}",
            f"omega_outcome_binder:{OMEGA_OUTCOME_BINDER_PASSWORD}",
        ),
        "OMEGA_GOLD_ROW_CACHE_TTL_SECONDS": "0",
    }.items():
        monkeypatch.setenv(name, value)
    materializer = StagedPublicationEngine()
    try:
        materializer.materialize(pnl_dataset(), _context(scope))
    finally:
        if materializer._con is not None:
            materializer._con.close()
    clear_gold_row_cache()

    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    modules = {id(module): module for module in (auth, history.auth, persistence.auth)}
    previous = [(module, module.pool) for module in modules.values()]

    async def active_pool():
        return pool

    for module in modules.values():
        module.pool = active_pool
    try:
        bodies = [
            {
                "cartridge_id": "replicon",
                "datasets": ["pnl_mensual"],
                "run_mode": "gold_refresh",
                "run_ref": f"binding-crash:{index}:{uuid.uuid4().hex}",
                "metadata": {"pipeline_run_id": pipeline_ids[index]},
            }
            for index in range(2)
        ]

        async def unavailable_stage(*_args, **_kwargs):
            raise RuntimeError("injected authority interruption")

        original_stage = intelligence_engine.stage_gold_refresh_authority
        monkeypatch.setattr(
            intelligence_engine, "stage_gold_refresh_authority", unavailable_stage
        )
        with pytest.raises(RuntimeError, match="Gold outcome authority unavailable"):
            await _run(bodies[0], scope)
        monkeypatch.setattr(
            intelligence_engine, "stage_gold_refresh_authority", original_stage
        )
        first, first_trace = await _run_without_external_trace(bodies[0], scope)
        assert first["signals"] and first["idempotent"] is False
        direct_check = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            direct = await direct_check.fetchrow(
                """SELECT i.status,
                          (SELECT count(*) FROM operational_outcome_bindings b
                            WHERE b.intelligence_run_id=i.id) binding_count
                     FROM intelligence_runs i WHERE i.run_ref=$1""",
                bodies[0]["run_ref"],
            )
            assert dict(direct) == {"status": "completed", "binding_count": 1}
        finally:
            await direct_check.close()

        original_persist = intelligence_engine.persist_gold_refresh_pending

        async def interrupted_persist(*_args, **_kwargs):
            raise RuntimeError("injected operational transaction interruption")

        monkeypatch.setattr(
            intelligence_engine, "persist_gold_refresh_pending", interrupted_persist
        )
        with pytest.raises(RuntimeError, match="transaction interruption"):
            await _run(bodies[1], scope)
        monkeypatch.setattr(
            intelligence_engine, "persist_gold_refresh_pending", original_persist
        )
        seed_pnl_input(
            stack,
            scope,
            [
                ("project-a", "Proyecto A", "2026-01-01", 200.0),
                ("project-a", "Proyecto A", "2026-02-01", 50.0),
                ("project-a", "Proyecto A", "2026-03-01", 5.0),
            ],
        )
        advanced = StagedPublicationEngine()
        try:
            advanced.materialize(pnl_dataset(), _context(scope))
        finally:
            if advanced._con is not None:
                advanced._con.close()
        clear_gold_row_cache()
        second, second_trace = await _run(bodies[1], scope)

        digests = []
        for body, result, trace in (
            (bodies[0], first, first_trace),
            (bodies[1], second, second_trace),
        ):
            digest = await persist_gold_refresh_binding(
                _user(scope),
                expected_run_id=body["metadata"]["pipeline_run_id"],
                expected_run_ref=body["run_ref"],
                intelligence_result=result,
                publication_trace=trace,
                expected_datasets=body["datasets"],
            )
            replay = await persist_gold_refresh_binding(
                _user(scope),
                expected_run_id=body["metadata"]["pipeline_run_id"],
                expected_run_ref=body["run_ref"],
                intelligence_result=result,
                publication_trace={},
                expected_datasets=[],
            )
            assert digest == replay
            digests.append(digest)
    finally:
        for module, factory in previous:
            module.pool = factory
        await pool.close()

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        rows = await check.fetch(
            """SELECT i.id,i.status,
                      (SELECT count(*) FROM operational_outcome_binding_candidates c
                        WHERE c.intelligence_run_id=i.id) candidate_count,
                      (SELECT count(*) FROM operational_outcome_bindings b
                        WHERE b.intelligence_run_id=i.id) binding_count
                 FROM intelligence_runs i
                WHERE i.run_ref=ANY($1::text[]) ORDER BY i.run_ref""",
            [body["run_ref"] for body in bodies],
        )
        assert len(rows) == 2
        assert all(row["status"] == "completed" for row in rows)
        assert all(row["candidate_count"] == 1 for row in rows)
        assert all(row["binding_count"] == 1 for row in rows)
        assert all(len(value) == 64 for value in digests)
    finally:
        await check.close()
