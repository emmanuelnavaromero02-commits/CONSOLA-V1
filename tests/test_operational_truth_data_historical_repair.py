from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / "infra/init/99zo_sap_successfactors_talent_operational_truth_repair.sql"
TABLES = (
    "gold_sap_successfactors_talent_benchmark_internal",
    "gold_sap_successfactors_talent_readiness",
    "gold_sap_successfactors_talent_9box",
)
SCHEMAS = ("public", "pggold")


async def _create_stale_materialization(conn, schema: str) -> None:
    await conn.execute(
        f"""CREATE TABLE {schema}.gold_sap_successfactors_talent_benchmark_internal (
               approved BOOLEAN, approved_by TEXT, approved_at TIMESTAMPTZ,
               approval_source TEXT, benchmark_version TEXT, blockers TEXT,
               approval_actor_source TEXT, approval_recorded_by_server BOOLEAN,
               approval_evidence_ref TEXT, approval_authorization_ref TEXT,
               approval_authorization_verified BOOLEAN, approval_status TEXT
           );
           INSERT INTO {schema}.gold_sap_successfactors_talent_benchmark_internal
           VALUES (TRUE, 'bot:legacy-approver', NOW(), 'operational_activation',
                   'talent_benchmark_internal.v1.approved', '[]', 'server', TRUE,
                   'evidence:1', 'authorization:1', TRUE, 'approved_durable');

           CREATE TABLE {schema}.gold_sap_successfactors_talent_readiness (
               source_mode TEXT, readiness_status TEXT, readiness_label TEXT,
               benchmark_raw_score DOUBLE PRECISION,
               benchmark_score DOUBLE PRECISION,
               readiness_score DOUBLE PRECISION, confidence DOUBLE PRECISION
           );
           INSERT INTO {schema}.gold_sap_successfactors_talent_readiness
           VALUES ('benchmark_internal', 'ready', 'Listo', 70, 80, 80, 0.6);

           CREATE TABLE {schema}.gold_sap_successfactors_talent_9box (
               source_mode TEXT, box_status TEXT,
               benchmark_performance_proxy DOUBLE PRECISION,
               benchmark_potential_proxy DOUBLE PRECISION,
               performance_proxy_score DOUBLE PRECISION,
               potential_proxy_score DOUBLE PRECISION,
               performance_band TEXT, potential_band TEXT
           );
           INSERT INTO {schema}.gold_sap_successfactors_talent_9box
           VALUES ('benchmark_internal', 'ready', 70, 70, 70, 70, 'high', 'high');"""
    )


async def _assert_blocked(conn, schema: str) -> None:
    benchmark = await conn.fetchrow(
        f"""SELECT approved, approved_by, approval_recorded_by_server,
                  approval_authorization_verified, approval_status
             FROM {schema}.gold_sap_successfactors_talent_benchmark_internal"""
    )
    readiness = await conn.fetchrow(
        f"""SELECT source_mode, readiness_status, benchmark_raw_score,
                  readiness_score, confidence, benchmark_approval_valid,
                  benchmark_provenance_status
             FROM {schema}.gold_sap_successfactors_talent_readiness"""
    )
    nine_box = await conn.fetchrow(
        f"""SELECT source_mode, box_status, performance_proxy_score,
                  performance_band, benchmark_approval_valid,
                  benchmark_provenance_status
             FROM {schema}.gold_sap_successfactors_talent_9box"""
    )
    assert dict(benchmark) == {
        "approved": False,
        "approved_by": None,
        "approval_recorded_by_server": False,
        "approval_authorization_verified": False,
        "approval_status": "unreviewed",
    }
    assert dict(readiness) == {
        "source_mode": "insufficient_data",
        "readiness_status": "insufficient_data",
        "benchmark_raw_score": None,
        "readiness_score": None,
        "confidence": None,
        "benchmark_approval_valid": False,
        "benchmark_provenance_status": "stale_unapproved_benchmark",
    }
    assert dict(nine_box) == {
        "source_mode": "insufficient_data",
        "box_status": "blocked",
        "performance_proxy_score": None,
        "performance_band": None,
        "benchmark_approval_valid": False,
        "benchmark_provenance_status": "stale_unapproved_benchmark",
    }


@pytest.mark.asyncio
async def test_historical_unverified_benchmark_projection_is_blocked(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS pggold")
        for schema in SCHEMAS:
            for table in TABLES:
                assert (
                    await conn.fetchval("SELECT to_regclass($1)", f"{schema}.{table}")
                    is None
                )
            await _create_stale_materialization(conn, schema)

        await conn.execute(REPAIR.read_text(encoding="utf-8"))

        for schema in SCHEMAS:
            await _assert_blocked(conn, schema)
    finally:
        await transaction.rollback()
        await conn.close()
