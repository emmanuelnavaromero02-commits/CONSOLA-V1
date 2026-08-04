"""Benchmark fallback must independently reject invalid CPA score inputs."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from tests.test_talent_nine_box_downstream import MACRO, _copy, _dataset_sql


@pytest.mark.parametrize(
    "invalid_expression",
    [
        "-1.0::DOUBLE",
        "'NaN'::DOUBLE",
        "'Infinity'::DOUBLE",
        "1000.0::DOUBLE",
    ],
)
def test_approved_benchmark_does_not_clamp_invalid_cpa_scores(
    tmp_path: Path, invalid_expression: str
) -> None:
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        con.execute(
            f"""
            CREATE TABLE cpa AS
            SELECT
              'tenant-a'::VARCHAR tenant_id,
              'workspace-a'::VARCHAR workspace_id,
              'employee-' || i::VARCHAR user_id,
              'Persona'::VARCHAR full_name,
              'Empresa'::VARCHAR company_name,
              'Area'::VARCHAR department_name,
              'Madrid'::VARCHAR location_name,
              'JOB'::VARCHAR job_code,
              0::BIGINT direct_reports,
              24.0::DOUBLE tenure_months,
              'Rol'::VARCHAR role_name,
              CASE WHEN i=0 THEN {invalid_expression} ELSE 80.0 END
                AS competency_score,
              80.0::DOUBLE performance_score,
              80.0::DOUBLE aspiration_score,
              NULL::DOUBLE fit_score,
              FALSE::BOOLEAN invalid_score_input,
              'ready'::VARCHAR required_skills_status,
              'ready'::VARCHAR role_profile_status
            FROM range(50) rows(i)
            """
        )
        cpa_path = tmp_path / "cpa.parquet"
        _copy(con, "cpa", cpa_path)
        con.execute(
            """
            CREATE TABLE benchmark AS SELECT
              TRUE enabled, TRUE approved, '42'::VARCHAR approved_by,
              TIMESTAMP '2026-08-04 00:00:00' approved_at,
              'server'::VARCHAR approval_actor_source,
              TRUE approval_recorded_by_server,
              'evidence:1'::VARCHAR approval_evidence_ref,
              'authorization:1'::VARCHAR approval_authorization_ref,
              TRUE approval_authorization_verified,
              'benchmark.v1'::VARCHAR benchmark_version,
              'contract.v1'::VARCHAR contract_version,
              70.0::DOUBLE readiness_high_threshold,
              50.0::DOUBLE readiness_medium_threshold,
              0.4::DOUBLE role_coverage_weight,
              0.3::DOUBLE tenure_weight,
              0.3::DOUBLE competency_weight,
              CURRENT_TIMESTAMP materialized_at
            """
        )
        benchmark_path = tmp_path / "benchmark.parquet"
        _copy(con, "benchmark", benchmark_path)

        con.execute(
            "CREATE TABLE readiness AS "
            + _dataset_sql(
                "sap_successfactors_talent_readiness",
                {
                    "sap_successfactors_talent_cpa_scores": cpa_path,
                    "sap_successfactors_talent_benchmark_internal": benchmark_path,
                },
            )
        )
        row = con.execute(
            """
            SELECT invalid_score_input, competency_score, benchmark_raw_score,
                   readiness_score, benchmark_approval_valid,
                   source_mode, readiness_status
              FROM readiness WHERE user_id='employee-0'
            """
        ).fetchone()
        assert row == (
            True,
            None,
            None,
            None,
            False,
            "insufficient_data",
            "insufficient_data",
        )
        valid_row = con.execute(
            """
            SELECT invalid_score_input, benchmark_raw_score, readiness_score,
                   benchmark_approval_valid, source_mode, readiness_status
              FROM readiness WHERE user_id='employee-1'
            """
        ).fetchone()
        assert valid_row == (
            False,
            None,
            None,
            False,
            "insufficient_data",
            "insufficient_data",
        )
    finally:
        con.close()
