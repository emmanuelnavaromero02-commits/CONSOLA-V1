from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pytest

from refinement.app.duckdb_engine import _register_shared_macros


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "cartridges/sap_successfactors/datasets"
GOLD = DATASETS / "sap_successfactors_talent_succession_coverage.sql"
SILVER = DATASETS / "sap_successfactors_position_latest.sql"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
POSITION_COLUMNS = (
    "tenant_id VARCHAR, workspace_id VARCHAR, position_id VARCHAR, position_name VARCHAR, "
    "department VARCHAR, location VARCHAR, cost_center VARCHAR, criticality VARCHAR, "
    "is_vacant BOOLEAN, effective_status VARCHAR"
)
LEGACY_POSITION_COLUMNS = (
    "tenant_id VARCHAR, workspace_id VARCHAR, position_id VARCHAR, position_name VARCHAR, "
    "department VARCHAR, location VARCHAR, cost_center VARCHAR"
)
NOMINATION_COLUMNS = (
    "tenant_id VARCHAR, workspace_id VARCHAR, nomination_id VARCHAR, user_id VARCHAR, "
    "target_position VARCHAR, readiness VARCHAR, nomination_status VARCHAR"
)
DATASET_LEVEL = (
    "positions_total",
    "positions_inactive_count",
    "criticality_available",
    "criticality_unrecognized_count",
    "criticality_missing_count",
    "critical_total",
    "critical_without_nominee_total",
    "critical_coverage_unknown_count",
    "nominations_available",
    "nominations_total",
    "nominations_matched",
    "nominations_unmatched_open",
)


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@pytest.fixture()
def con():
    connection = duckdb.connect()
    _register_shared_macros(connection)
    try:
        yield connection
    finally:
        connection.close()


def _parquet(con, tmp_path: Path, name: str, columns: str, rows: list[tuple]) -> Path:
    con.execute(f"CREATE OR REPLACE TABLE {name}_src ({columns})")
    if rows:
        placeholders = ", ".join("?" for _ in rows[0])
        con.executemany(f"INSERT INTO {name}_src VALUES ({placeholders})", rows)
    path = tmp_path / f"{name}.parquet"
    con.execute(f"COPY {name}_src TO {_quoted(path)} (FORMAT PARQUET)")
    return path


def _gold_sql(positions: Path, nominations: Path) -> str:
    sql = GOLD.read_text(encoding="utf-8")
    for source, path in (
        ("sap_successfactors_position_latest", positions),
        ("sap_successfactors_successionnomination_latest", nominations),
    ):
        sql = sql.replace(
            f"'s3://{{bucket}}/silver/sap_successfactors/{source}/**/*.parquet'",
            _quoted(path),
        )
    assert "s3://{bucket}" not in sql
    return sql


def _position(
    position_id: str,
    name: str,
    criticality: str | None,
    vacant: bool | None = None,
    status: str | None = None,
) -> tuple:
    return (TENANT, WORKSPACE, position_id, name, "Ventas", "CDMX", "CC1", criticality, vacant, status)


def _nomination(
    nomination_id: str | None,
    position_id: str | None,
    status: str | None,
    readiness: str | None = None,
    user: str | None = None,
) -> tuple:
    return (
        TENANT,
        WORKSPACE,
        nomination_id,
        user or f"hash-{nomination_id}",
        position_id,
        readiness,
        status,
    )


def _rows(con, sql: str) -> dict[str, dict]:
    cursor = con.execute(sql)
    columns = [item[0] for item in cursor.description]
    return {row[columns.index("position_id")]: dict(zip(columns, row)) for row in cursor.fetchall()}


def _run(con, tmp_path: Path, positions: list[tuple], nominations: list[tuple]) -> dict[str, dict]:
    return _rows(
        con,
        _gold_sql(
            _parquet(con, tmp_path, "positions", POSITION_COLUMNS, positions),
            _parquet(con, tmp_path, "nominations", NOMINATION_COLUMNS, nominations),
        ),
    )


def _dataset(rows: dict[str, dict]) -> dict:
    values = {key: {row[key] for row in rows.values()} for key in DATASET_LEVEL}
    assert all(len(found) == 1 for found in values.values()), values
    return {key: next(iter(found)) for key, found in values.items()}


def _scenario(con, tmp_path: Path) -> dict[str, dict]:
    return _run(
        con,
        tmp_path,
        [
            _position("P-COVERED", "Director de Finanzas", "High", False),
            _position("P-OPEN", "Gerente de Planta", "Crítica", True),
            _position("P-REJECTED", "Jefe de Compras", "MUY_ALTA"),
            _position("P-LOW", "Analista", "Low"),
            _position("P-NULL", "Auxiliar", None),
        ],
        [
            _nomination("N1", "P-COVERED", "Active", "Ready in 1-2 Years", user="u1"),
            _nomination("N2", "P-COVERED", "approved", "Ready Now", user="u2"),
            _nomination("N3", "P-COVERED", "Rejected", "Ready Now", user="u3"),
            _nomination("N4", "P-REJECTED", "Rejected", "Ready Now"),
        ],
    )


def test_critical_position_counts_only_when_no_active_successor_is_known(con, tmp_path):
    rows = _scenario(con, tmp_path)

    assert rows["P-COVERED"]["is_critical"] is True
    assert rows["P-COVERED"]["has_active_nominee"] is True
    assert rows["P-OPEN"]["is_critical"] is True
    assert rows["P-OPEN"]["has_active_nominee"] is False
    assert rows["P-OPEN"]["nominee_count"] == 0
    assert rows["P-OPEN"]["is_vacant"] is True
    assert rows["P-REJECTED"]["has_active_nominee"] is False
    assert _dataset(rows) == {
        "positions_total": 5,
        "positions_inactive_count": 0,
        "criticality_available": True,
        "criticality_unrecognized_count": 0,
        "criticality_missing_count": 1,
        "critical_total": 3,
        "critical_without_nominee_total": 2,
        "critical_coverage_unknown_count": 0,
        "nominations_available": True,
        "nominations_total": 4,
        "nominations_matched": 4,
        "nominations_unmatched_open": 0,
    }


def test_nominee_count_is_distinct_active_nominees_only(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High"), _position("P2", "Jefe", "High")],
        [
            _nomination("N1", "P1", "Active", user="same"),
            _nomination("N2", "P1", "Approved", user="same"),
            _nomination("N3", "P1", "Rejected", user="other"),
            _nomination("N4", "P1", "Withdrawn", user="third"),
            _nomination(None, "P2", "Active", user="u9"),
        ],
    )

    assert rows["P1"]["nominee_count"] == 1
    assert rows["P2"]["nominee_count"] == 1
    assert rows["P2"]["has_active_nominee"] is True


@pytest.mark.parametrize("status", ["1", "0", "Pending", "In Progress", "Draft", None])
def test_unrecognized_nomination_status_never_becomes_a_false_zero(con, tmp_path, status):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High"), _position("P2", "Jefe", "Critical")],
        [_nomination("N1", "P1", status, "Ready Now"), _nomination("N2", "P2", "Active")],
    )

    assert rows["P1"]["has_active_nominee"] is None
    assert rows["P2"]["has_active_nominee"] is True
    dataset = _dataset(rows)
    assert dataset["critical_coverage_unknown_count"] == 1
    assert dataset["critical_without_nominee_total"] is None


def test_numeric_statuses_on_every_critical_position_leave_coverage_unknown(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High"), _position("P2", "Jefe", "Critical")],
        [_nomination("N1", "P1", "1", "Ready Now"), _nomination("N2", "P2", "0", "1-2 years")],
    )

    dataset = _dataset(rows)
    assert dataset["critical_total"] == 2
    assert dataset["critical_coverage_unknown_count"] == 2
    assert dataset["critical_without_nominee_total"] is None
    assert {row["has_active_nominee"] for row in rows.values()} == {None}


def test_partially_recognized_criticality_is_counted_as_unrecognized(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [
            _position("P1", "Gerente", "Strategic"),
            _position("P2", "Jefe", "3"),
            _position("P3", "Analista", "Normal"),
            _position("P4", "Auxiliar", None),
        ],
        [_nomination("N1", "P3", "Active")],
    )

    assert rows["P1"]["is_critical"] is None
    assert rows["P2"]["is_critical"] is None
    assert rows["P3"]["is_critical"] is False
    assert rows["P4"]["is_critical"] is None
    dataset = _dataset(rows)
    assert dataset["criticality_available"] is True
    assert dataset["criticality_unrecognized_count"] == 2
    assert dataset["criticality_missing_count"] == 1
    assert dataset["critical_total"] == 0
    assert dataset["critical_without_nominee_total"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CRITICAL", True),
        ("Crítica", True),
        ("Mission-Critical", True),
        ("mission_critical", True),
        ("Muy Alta", True),
        (" high ", True),
        ("Key", True),
        ("Key Position", True),
        ("Puesto clave", True),
        ("Not Critical", False),
        ("notCritical", False),
        ("Non-Critical", False),
        ("No crítica", False),
        ("Medium", False),
        ("media", False),
        ("Low", False),
        ("Normal", False),
        ("1", None),
        ("2", None),
        ("true", None),
        ("Sí", None),
        ("Alta prioridad", None),
        ("N/A", None),
    ],
)
def test_criticality_mapping_recognizes_explicit_labels_only(con, tmp_path, value, expected):
    rows = _run(con, tmp_path, [_position("P1", "Gerente", value)], [])

    assert rows["P1"]["is_critical"] is expected
    assert _dataset(rows)["criticality_unrecognized_count"] == (1 if expected is None else 0)


def test_tenant_without_criticality_values_reports_it_and_counts_nothing(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", None), _position("P2", "Analista", None)],
        [],
    )

    assert set(rows) == {"P1", "P2"}
    dataset = _dataset(rows)
    assert dataset["criticality_available"] is False
    assert dataset["criticality_unrecognized_count"] == 0
    assert dataset["criticality_missing_count"] == 2
    assert dataset["critical_without_nominee_total"] is None
    assert dataset["nominations_total"] == 0


def test_no_visible_nominations_leave_coverage_unknown(con, tmp_path):
    rows = _run(con, tmp_path, [_position("P1", "Gerente", "High")], [])

    assert rows["P1"]["has_active_nominee"] is None
    dataset = _dataset(rows)
    assert dataset["nominations_available"] is True
    assert dataset["nominations_total"] == 0
    assert dataset["critical_without_nominee_total"] is None
    assert dataset["critical_coverage_unknown_count"] == 1


def test_nominations_that_never_match_a_position_are_reported(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("1001", "Gerente", "High")],
        [_nomination("N1", "POS_1001", "Active", "Ready Now")],
    )

    assert rows["1001"]["has_active_nominee"] is None
    dataset = _dataset(rows)
    assert dataset["nominations_total"] == 1
    assert dataset["nominations_matched"] == 0
    assert dataset["nominations_unmatched_open"] == 1
    assert dataset["critical_without_nominee_total"] is None


def test_open_unmatched_nominations_make_uncovered_positions_ambiguous(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [
            _position("P1", "Gerente", "High"),
            _position("P2", "Jefe", "High"),
            _position("P3", "Analista", "Low"),
        ],
        [
            _nomination("N1", "P1", "Active"),
            _nomination("N2", "LEGACY-7", "Active"),
        ],
    )

    assert rows["P1"]["has_active_nominee"] is True
    assert rows["P2"]["has_active_nominee"] is None
    dataset = _dataset(rows)
    assert dataset["nominations_matched"] == 1
    assert dataset["nominations_unmatched_open"] == 1
    assert dataset["critical_without_nominee_total"] is None
    assert dataset["critical_coverage_unknown_count"] == 1


def test_closed_unmatched_nominations_do_not_block_the_count(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High"), _position("P2", "Jefe", "High")],
        [
            _nomination("N1", "P1", "Active"),
            _nomination("N2", "LEGACY-7", "Rejected"),
        ],
    )

    assert rows["P2"]["has_active_nominee"] is False
    dataset = _dataset(rows)
    assert dataset["nominations_unmatched_open"] == 0
    assert dataset["critical_without_nominee_total"] == 1


def test_nominations_to_inactive_positions_still_match(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [
            _position("P1", "Gerente", "High", status="I"),
            _position("P2", "Jefe", "High", status="A"),
            _position("P3", "Director", "Critical", status=None),
            _position("P4", "Tesorero", "High", status="inactive"),
        ],
        [_nomination("N1", "P1", "Active"), _nomination("N2", "P2", "Active")],
    )

    assert set(rows) == {"P2", "P3"}
    assert rows["P3"]["has_active_nominee"] is False
    dataset = _dataset(rows)
    assert dataset["positions_total"] == 2
    assert dataset["positions_inactive_count"] == 2
    assert dataset["critical_total"] == 2
    assert dataset["critical_without_nominee_total"] == 1
    assert dataset["nominations_matched"] == 2
    assert dataset["nominations_unmatched_open"] == 0


def test_missing_criticality_without_any_critical_position_leaves_the_total_open(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "Low"), _position("P2", "Jefe", None)],
        [_nomination("N1", "P1", "Active")],
    )

    dataset = _dataset(rows)
    assert dataset["critical_total"] == 0
    assert dataset["criticality_missing_count"] == 1
    assert dataset["critical_without_nominee_total"] is None


def test_missing_criticality_is_reported_next_to_a_decided_count(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [
            _position("P1", "Gerente", "High"),
            _position("P2", "Jefe", "High"),
            _position("P3", "Auxiliar", None),
            _position("P4", "Analista", "  "),
        ],
        [_nomination("N1", "P1", "Active")],
    )

    dataset = _dataset(rows)
    assert dataset["critical_total"] == 2
    assert dataset["criticality_missing_count"] == 2
    assert dataset["critical_without_nominee_total"] == 1


def test_zero_is_never_published_while_an_active_position_lacks_criticality(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High"), _position("P2", "Jefe", None)],
        [_nomination("N1", "P1", "Active")],
    )

    assert rows["P1"]["has_active_nominee"] is True
    dataset = _dataset(rows)
    assert dataset["critical_total"] == 1
    assert dataset["criticality_missing_count"] == 1
    assert dataset["critical_coverage_unknown_count"] == 0
    assert dataset["critical_without_nominee_total"] is None


def test_only_inactive_positions_publish_one_scope_row_without_a_total(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [
            _position("P1", "Gerente", "High", status="I"),
            _position("P2", "Jefe", "High", status="INACTIVE"),
        ],
        [_nomination("N1", "P1", "Active")],
    )

    assert list(rows) == [None]
    sentinel = rows[None]
    assert sentinel["tenant_id"] == TENANT
    assert sentinel["position_name"] is None
    assert sentinel["is_critical"] is None
    dataset = _dataset(rows)
    assert dataset["positions_total"] == 0
    assert dataset["positions_inactive_count"] == 2
    assert dataset["critical_without_nominee_total"] is None
    assert dataset["nominations_matched"] == 1


def test_readiness_best_never_reads_not_ready_now_as_ready(con, tmp_path):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High"), _position("P2", "Jefe", "High")],
        [
            _nomination("N1", "P1", "Active", "Not ready now", user="u1"),
            _nomination("N2", "P1", "Active", "3-5 years", user="u2"),
            _nomination("N3", "P1", "Active", "12-24 months", user="u3"),
            _nomination("N4", "P2", "Active", "Not ready now", user="u4"),
        ],
    )

    assert rows["P1"]["readiness_best"] == "12-24 months"
    assert rows["P2"]["readiness_best"] is None
    assert rows["P2"]["has_active_nominee"] is True


@pytest.mark.parametrize(
    ("labels", "best"),
    [
        (["Ready in 1-2 Years", "Ready Now"], "Ready Now"),
        (["3-5 years", "Ready in 1 to 2 years"], "Ready in 1 to 2 years"),
        (["2-3 years", "Emergency"], "2-3 years"),
        (["Listo ahora", "1 a 2 años"], "Listo ahora"),
        (["now not", "not ready"], None),
    ],
)
def test_readiness_labels_match_whole_normalized_values(con, tmp_path, labels, best):
    rows = _run(
        con,
        tmp_path,
        [_position("P1", "Gerente", "High")],
        [
            _nomination(f"N{index}", "P1", "Active", label, user=f"u{index}")
            for index, label in enumerate(labels)
        ],
    )

    assert rows["P1"]["readiness_best"] == best


def test_readiness_best_ignores_inactive_nominations(con, tmp_path):
    rows = _scenario(con, tmp_path)

    assert rows["P-COVERED"]["readiness_best"] == "Ready Now"
    assert rows["P-REJECTED"]["readiness_best"] is None
    assert rows["P-OPEN"]["readiness_best"] is None


def test_output_carries_scope_and_no_person_keys(con, tmp_path):
    positions = _parquet(con, tmp_path, "positions", POSITION_COLUMNS, [_position("P1", "Gerente", "High")])
    nominations = _parquet(
        con, tmp_path, "nominations", NOMINATION_COLUMNS, [_nomination("N1", "P1", "Active")]
    )
    cursor = con.execute(_gold_sql(positions, nominations))
    columns = [item[0] for item in cursor.description]
    row = dict(zip(columns, cursor.fetchone()))

    assert row["tenant_id"] == TENANT
    assert row["workspace_id"] == WORKSPACE
    assert "user_id" not in columns
    assert "nomination_id" not in columns
    assert "hash-N1" not in repr(row)


def test_gold_tolerates_a_position_silver_without_the_optional_columns(con, tmp_path):
    positions = _parquet(
        con,
        tmp_path,
        "legacy_positions",
        LEGACY_POSITION_COLUMNS,
        [(TENANT, WORKSPACE, "P1", "Gerente", "Ventas", "CDMX", "CC1")],
    )
    nominations = _parquet(con, tmp_path, "nominations", NOMINATION_COLUMNS, [])
    rows = _rows(con, _gold_sql(positions, nominations))

    assert rows["P1"]["criticality"] is None
    assert rows["P1"]["is_vacant"] is None
    dataset = _dataset(rows)
    assert dataset["positions_total"] == 1
    assert dataset["criticality_available"] is False
    assert dataset["critical_without_nominee_total"] is None


def test_critical_rows_without_successor_sort_first(con, tmp_path):
    rows = list(_scenario(con, tmp_path).values())
    flagged = [row["is_critical"] is True and row["has_active_nominee"] is False for row in rows]

    assert flagged[:2] == [True, True]
    assert not any(flagged[2:])


def _silver_sql(root: Path) -> str:
    sql = SILVER.read_text(encoding="utf-8")
    sql = sql.replace(
        "'s3://{bucket}/raw/sap_successfactors/Position/**/*.parquet'",
        _quoted(root / "**" / "*.parquet"),
    )
    assert "s3://{bucket}" not in sql
    return sql


def _bronze(con, root: Path, load_date: str, name: str, select: str) -> None:
    folder = root / f"load_date={load_date}"
    folder.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY ({select}) TO {_quoted(folder / (name + '.parquet'))} (FORMAT PARQUET)")


def _silver_rows(con, root: Path) -> dict[str, dict]:
    cursor = con.execute(_silver_sql(root))
    columns = [item[0] for item in cursor.description]
    return {row[0]: dict(zip(columns, row)) for row in cursor.fetchall()}


def test_silver_exposes_null_optional_columns_when_bronze_never_selected_them(con, tmp_path):
    root = tmp_path / "Position"
    _bronze(
        con,
        root,
        "2026-09-01",
        "legacy",
        "SELECT 'P1' AS code, 'Gerente' AS externalName_defaultValue, 'D1' AS department, "
        "'L1' AS location, 'C1' AS costCenter, '2026-09-01T00:00:00Z' AS lastModifiedDateTime, "
        "'2026-09-01T01:00:00Z' AS _extracted_at, 'b1' AS batch_id",
    )
    cursor = con.execute(_silver_sql(root))
    columns = [item[0] for item in cursor.description]
    row = dict(zip(columns, cursor.fetchone()))

    assert columns[:5] == ["position_id", "position_name", "department", "location", "cost_center"]
    assert row["position_id"] == "P1"
    assert row["criticality"] is None
    assert row["is_vacant"] is None
    assert row["effective_status"] is None


def test_silver_takes_optional_fields_from_the_latest_row(con, tmp_path):
    root = tmp_path / "Position"
    _bronze(
        con,
        root,
        "2026-09-01",
        "legacy",
        "SELECT * FROM (VALUES ('P1', 'Gerente', '2026-09-01T00:00:00Z'), ('P2', 'Analista', '2026-09-01T00:00:00Z')) "
        "t(code, externalName_defaultValue, lastModifiedDateTime), "
        "(SELECT 'D1' AS department, 'L1' AS location, 'C1' AS costCenter, "
        "'2026-09-01T01:00:00Z' AS _extracted_at, 'b1' AS batch_id)",
    )
    _bronze(
        con,
        root,
        "2026-09-29",
        "with_criticality",
        "SELECT * FROM (VALUES ('P1', 'Gerente', '2026-09-28T00:00:00Z', ' High ', TRUE, 'A'), "
        "('P2', 'Analista', '2026-09-28T00:00:00Z', '', FALSE, ' I ')) "
        "t(code, externalName_defaultValue, lastModifiedDateTime, criticality, vacant, effectiveStatus), "
        "(SELECT 'D1' AS department, 'L1' AS location, 'C1' AS costCenter, "
        "'2026-09-29T01:00:00Z' AS _extracted_at, 'b2' AS batch_id)",
    )
    rows = _silver_rows(con, root)

    assert rows["P1"]["criticality"] == "High"
    assert rows["P1"]["is_vacant"] is True
    assert rows["P1"]["effective_status"] == "A"
    assert rows["P2"]["criticality"] is None
    assert rows["P2"]["is_vacant"] is False
    assert rows["P2"]["effective_status"] == "I"


def test_silver_prefers_position_criticality_over_legacy_criticality(con, tmp_path):
    root = tmp_path / "Position"
    _bronze(
        con,
        root,
        "2026-09-29",
        "both_fields",
        "SELECT * FROM (VALUES "
        "('P1', 'Gerente', 'Critical', 'Low'), "
        "('P2', 'Jefe', NULL, 'High'), "
        "('P3', 'Analista', ' ', 'Medium'), "
        "('P4', 'Auxiliar', '2', NULL)) "
        "t(code, externalName_defaultValue, positionCriticality, criticality), "
        "(SELECT 'D1' AS department, 'L1' AS location, 'C1' AS costCenter, "
        "'2026-09-28T00:00:00Z' AS lastModifiedDateTime, "
        "'2026-09-29T01:00:00Z' AS _extracted_at, 'b2' AS batch_id)",
    )
    rows = _silver_rows(con, root)

    assert rows["P1"]["criticality"] == "Critical"
    assert rows["P2"]["criticality"] == "High"
    assert rows["P3"]["criticality"] == "Medium"
    assert rows["P4"]["criticality"] == "2"


def test_silver_reads_position_criticality_alone(con, tmp_path):
    root = tmp_path / "Position"
    _bronze(
        con,
        root,
        "2026-09-29",
        "modern_only",
        "SELECT 'P1' AS code, 'Gerente' AS externalName_defaultValue, 'Not Critical' AS positionCriticality, "
        "'D1' AS department, 'L1' AS location, 'C1' AS costCenter, "
        "'2026-09-28T00:00:00Z' AS lastModifiedDateTime, "
        "'2026-09-29T01:00:00Z' AS _extracted_at, 'b2' AS batch_id",
    )

    assert _silver_rows(con, root)["P1"]["criticality"] == "Not Critical"


def test_silver_output_feeds_the_gold_projection(con, tmp_path):
    root = tmp_path / "Position"
    _bronze(
        con,
        root,
        "2026-09-29",
        "with_criticality",
        "SELECT * FROM (VALUES ('P1', 'Gerente de Planta', 'Alta', 'A'), ('P2', 'Analista', 'Baja', 'A'), "
        "('P3', 'Jefe cerrado', 'Alta', 'I')) "
        "t(code, externalName_defaultValue, positionCriticality, effectiveStatus), "
        "(SELECT 'D1' AS department, 'L1' AS location, 'C1' AS costCenter, "
        "'2026-09-28T00:00:00Z' AS lastModifiedDateTime, 'true' AS vacant, "
        "'2026-09-29T01:00:00Z' AS _extracted_at, 'b2' AS batch_id)",
    )
    silver = tmp_path / "silver.parquet"
    con.execute(
        f"COPY (SELECT '{TENANT}' AS tenant_id, '{WORKSPACE}' AS workspace_id, s.* "
        f"FROM ({_silver_sql(root)}) s) TO {_quoted(silver)} (FORMAT PARQUET)"
    )
    nominations = _parquet(
        con, tmp_path, "nominations", NOMINATION_COLUMNS, [_nomination("N1", "P2", "Active")]
    )
    rows = _rows(con, _gold_sql(silver, nominations))

    assert set(rows) == {"P1", "P2"}
    assert rows["P1"]["is_critical"] is True
    assert rows["P1"]["is_vacant"] is True
    assert rows["P1"]["has_active_nominee"] is False
    assert rows["P2"]["is_critical"] is False
    assert _dataset(rows)["critical_without_nominee_total"] == 1


@pytest.mark.parametrize("path", [GOLD, SILVER], ids=lambda path: path.stem)
def test_packaged_sql_passes_refinement_policy_and_scoping(monkeypatch, path):
    from refinement.app.duckdb_engine import DuckDBEngine

    engine = DuckDBEngine()
    scope = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
    prefix = f"tenant_id={TENANT}/workspace_id={WORKSPACE}"
    monkeypatch.setattr(
        engine,
        "_latest_materialized_uri",
        lambda layer, cartridge, name, user_context=None: engine._storage_uri(
            f"{layer}/{cartridge}/{name}/{prefix}/_snapshots/20260929T000000000000Z-a.parquet"
        ),
    )
    sql = path.read_text(encoding="utf-8")
    sources = json.loads(re.search(r"^-- sources:\s*(\[.*\])\s*$", sql, re.M).group(1))

    engine._validate_safe_sql(sql)
    scoped = engine._scope_storage_sql(engine._inject_bucket(sql), sources, scope)
    engine._validate_scoped_storage_sql(scoped, scope)
    engine._validate_effective_sql(
        scoped,
        allow_server_resolved_path_list=True,
        allow_server_resolved_publication_relation=True,
    )
    assert f"workspace_id={WORKSPACE}" in scoped


def test_gold_header_declares_the_explicit_criticality_mapping():
    lines = GOLD.read_text(encoding="utf-8").splitlines()

    assert lines[3].startswith("-- is_critical mapping")
    assert "numeric codes included" in lines[3]
    assert "=> NULL" in lines[3]
