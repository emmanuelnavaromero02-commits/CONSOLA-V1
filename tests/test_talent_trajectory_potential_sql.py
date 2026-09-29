from __future__ import annotations

import duckdb
import pytest

from tests.test_talent_nine_box_downstream import MACRO, _copy, _dataset_sql


@pytest.fixture()
def con():
    connection = duckdb.connect()
    try:
        connection.execute(MACRO.read_text(encoding="utf-8"))
        yield connection
    finally:
        connection.close()


def _readiness_rows(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE readiness_src AS SELECT
          'tenant-a'::VARCHAR tenant_id, 'workspace-a'::VARCHAR workspace_id,
          user_id, 'Persona'::VARCHAR full_name,
          'Empresa'::VARCHAR company_name, 'Area'::VARCHAR department_name,
          'Madrid'::VARCHAR location_name, 'JOB'::VARCHAR job_code,
          'Rol'::VARCHAR role_name, performance_score, competency_score,
          aspiration_score, 80.0::DOUBLE readiness_score,
          'cpa_real'::VARCHAR source_mode, NULL::VARCHAR benchmark_version,
          FALSE benchmark_approval_valid,
          'not_applicable'::VARCHAR benchmark_provenance_status,
          invalid_score_input, 'ready'::VARCHAR readiness_status,
          '[]'::VARCHAR blockers
        FROM (VALUES
          ('deduced-star', 100.0::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, FALSE::BOOLEAN),
          ('no-mobility', 100.0::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, FALSE::BOOLEAN),
          ('invalid-performance', -1.0::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, TRUE::BOOLEAN),
          ('null-performance', NULL::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, FALSE::BOOLEAN),
          ('cpa-complete', 80.0::DOUBLE, 80.0::DOUBLE, 80.0::DOUBLE, FALSE::BOOLEAN),
          ('invalid-cpa-values', 100.0::DOUBLE, 250.0::DOUBLE, -5.0::DOUBLE, FALSE::BOOLEAN),
          ('partial-cpa', 100.0::DOUBLE, 80.0::DOUBLE, NULL::DOUBLE, FALSE::BOOLEAN),
          ('flat-trajectory', 60.0::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, FALSE::BOOLEAN)
        ) source(user_id, performance_score, competency_score, aspiration_score, invalid_score_input)
        """
    )


def _mobility_rows(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mobility_src AS SELECT * FROM (VALUES
          ('deduced-star', 3::BIGINT, 2::BIGINT,
           CURRENT_DATE - INTERVAL 60 MONTH, CURRENT_DATE - INTERVAL 6 MONTH),
          ('deduced-star', 9::BIGINT, 9::BIGINT,
           CURRENT_DATE - INTERVAL 90 MONTH, CURRENT_DATE - INTERVAL 30 MONTH),
          ('invalid-performance', 4::BIGINT, 4::BIGINT,
           CURRENT_DATE - INTERVAL 60 MONTH, CURRENT_DATE - INTERVAL 6 MONTH),
          ('null-performance', 4::BIGINT, 4::BIGINT,
           CURRENT_DATE - INTERVAL 60 MONTH, CURRENT_DATE - INTERVAL 6 MONTH),
          ('cpa-complete', 1::BIGINT, 1::BIGINT,
           CURRENT_DATE - INTERVAL 60 MONTH, CURRENT_DATE - INTERVAL 6 MONTH),
          ('invalid-cpa-values', 4::BIGINT, 4::BIGINT,
           CURRENT_DATE - INTERVAL 60 MONTH, CURRENT_DATE - INTERVAL 6 MONTH),
          ('partial-cpa', 4::BIGINT, 4::BIGINT,
           CURRENT_DATE - INTERVAL 60 MONTH, CURRENT_DATE - INTERVAL 6 MONTH),
          ('flat-trajectory', 1::BIGINT, 1::BIGINT,
           CURRENT_DATE - INTERVAL 40 MONTH, CURRENT_DATE - INTERVAL 40 MONTH)
        ) source(user_id, distinct_job_codes, distinct_departments,
                 first_assignment_date, latest_assignment_date)
        """
    )


def _nine_box(con: duckdb.DuckDBPyConnection, tmp_path) -> None:
    _readiness_rows(con)
    _mobility_rows(con)
    readiness = tmp_path / "readiness.parquet"
    mobility = tmp_path / "mobility.parquet"
    _copy(con, "readiness_src", readiness)
    _copy(con, "mobility_src", mobility)
    con.execute(
        "CREATE TABLE nine_box AS "
        + _dataset_sql(
            "sap_successfactors_talent_9box",
            {
                "sap_successfactors_talent_readiness": readiness,
                "sap_successfactors_talent_mobility_history": mobility,
            },
        )
    )


def _row(con: duckdb.DuckDBPyConnection, user_id: str) -> tuple:
    return con.execute(
        "SELECT potential_basis, deduced_potential, potential_pending, box_status, "
        "box_key, invalid_score_input, potential_score FROM nine_box WHERE user_id = ?",
        [user_id],
    ).fetchone()


def test_trajectory_deduces_only_with_valid_performance_and_mobility(con, tmp_path):
    _nine_box(con, tmp_path)
    basis, deduced, pending, status, box_key, invalid, potential = _row(con, "deduced-star")
    assert (basis, deduced, pending, status, invalid) == (
        "trayectoria_observada", True, False, "ready", False
    )
    assert box_key == "estrella"
    # 0.35*3.75 + 0.25*5 + 0.15*2.5 + 0.25*5 = 4.1875 on 0-5 => 83.75 on 0-100
    assert potential == pytest.approx(83.75, abs=0.01)


def test_no_mobility_leaves_potential_pending_without_basis(con, tmp_path):
    _nine_box(con, tmp_path)
    basis, deduced, pending, status, box_key, invalid, potential = _row(con, "no-mobility")
    assert (basis, deduced, pending, status) == (None, False, True, "blocked")
    assert box_key == "insufficient_data"
    assert potential is None


def test_deduction_never_resurrects_invalid_or_missing_performance(con, tmp_path):
    _nine_box(con, tmp_path)
    for user_id in ("invalid-performance", "null-performance"):
        basis, deduced, pending, status, box_key, _invalid, potential = _row(con, user_id)
        assert (basis, deduced, pending, status) == (None, False, True, "blocked"), user_id
        assert box_key == "insufficient_data"
        assert potential is None
    assert _row(con, "invalid-performance")[5] is True


def test_duplicate_mobility_rows_never_double_classify_an_employee(con, tmp_path):
    _nine_box(con, tmp_path)
    count, potential = con.execute(
        "SELECT COUNT(*), MAX(potential_score) FROM nine_box WHERE user_id = 'deduced-star'"
    ).fetchone()
    assert count == 1
    # The freshest mobility row (6 months ago) wins over the stale duplicate.
    assert potential == pytest.approx(83.75, abs=0.01)


def test_declared_but_invalid_cpa_values_stay_pending_never_deduced(con, tmp_path):
    _nine_box(con, tmp_path)
    for user_id in ("invalid-cpa-values", "partial-cpa"):
        basis, deduced, pending, status, box_key, _invalid, potential = _row(con, user_id)
        assert (basis, deduced, pending, status) == (None, False, True, "blocked"), user_id
        assert box_key == "insufficient_data"
        assert potential is None


def test_cpa_basis_wins_over_trajectory_when_scores_exist(con, tmp_path):
    _nine_box(con, tmp_path)
    basis, deduced, pending, status, _box_key, _invalid, potential = _row(con, "cpa-complete")
    assert (basis, deduced, pending, status) == ("cpa_observado", False, False, "ready")
    assert potential == pytest.approx(80.0, abs=0.01)


def test_trajectory_weights_stay_bounded_and_labelled(con, tmp_path):
    _nine_box(con, tmp_path)
    basis, deduced, _pending, status, _box_key, _invalid, potential = _row(con, "flat-trajectory")
    assert (basis, deduced, status) == ("trayectoria_observada", True, "ready")
    # 0.35*0 + 0.25*1 + 0.15*(40/24) + 0.25*3 = 1.25 on 0-5 => 25.0 on 0-100
    assert potential == pytest.approx(25.0, abs=0.01)
    bounds = con.execute(
        "SELECT MIN(potential_score), MAX(potential_score) FROM nine_box "
        "WHERE potential_score IS NOT NULL"
    ).fetchone()
    assert 0 <= bounds[0] <= bounds[1] <= 100


def test_operational_counts_deduced_rows_per_cell(con, tmp_path):
    _nine_box(con, tmp_path)
    nine_box = tmp_path / "nine_box.parquet"
    _copy(con, "nine_box", nine_box)
    con.execute(
        "CREATE TABLE nine_operational AS "
        + _dataset_sql(
            "sap_successfactors_talent_9box_operational",
            {"sap_successfactors_talent_9box": nine_box},
        )
    )
    star = con.execute(
        "SELECT employee_count, ready_count, deduced_count FROM nine_operational "
        "WHERE box_key = 'estrella'"
    ).fetchone()
    assert star == (2, 2, 1)
    totals = con.execute(
        "SELECT SUM(deduced_count) FROM nine_operational"
    ).fetchone()
    assert totals[0] == 2
