from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from refinement.app.duckdb_engine import (
    _duckdb_type_to_pg_type,
    _register_shared_macros,
)
from refinement.app.successfactors_foundation_fallbacks import (
    FOUNDATION_GOLD_FALLBACK_SQL,
)
from refinement.app.successfactors_talent_core_fallbacks import TALENT_CORE_FALLBACK_SQL
from refinement.app.successfactors_talent_empty_fallbacks import (
    TALENT_EMPTY_FALLBACK_SQL,
)
from refinement.app.successfactors_talent_readfree_fallbacks import (
    TALENT_READFREE_EMPTY_SQL,
)
from refinement.app.successfactors_talent_runtime_fallbacks import (
    TALENT_RUNTIME_FALLBACK_SQL,
)

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "cartridges/sap_successfactors/datasets"
_REF_RE = re.compile(
    r"'s3://\{bucket\}/(raw|silver|gold)/sap_successfactors/([A-Za-z0-9_]+)/"
    r"(?:\*\*/\*\.parquet|\*\.parquet)'"
)


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _bind(sql: str, inputs: dict[str, Path]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(2)
        if name not in inputs:
            raise AssertionError(f"missing test input {match.group(1)}/{name}")
        return _quoted(inputs[name])

    bound = _REF_RE.sub(replace, sql)
    assert "s3://{bucket}" not in bound
    return bound


def _dataset(name: str, inputs: dict[str, Path]) -> str:
    return _bind((DATASETS / f"{name}.sql").read_text(encoding="utf-8"), inputs)


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    _register_shared_macros(con)
    return con


def _write_raw(root: Path, entity: str, rows: list[dict], nulls: list[str]) -> Path:
    frame = pd.DataFrame(rows)
    frame["_extracted_at"] = "2026-09-30T04:00:00Z"
    for column in nulls:
        frame[column] = None
    target = root / "raw" / entity / "load_date=2026-09-30" / "batch_id=run-1"
    target.mkdir(parents=True)
    frame.to_parquet(target / f"{entity}.parquet", index=False, engine="pyarrow")
    return root / "raw" / entity / "**" / "*.parquet"


def _table_parquet(con, tmp_path: Path, name: str, definition: str) -> Path:
    if definition.startswith(("SELECT", "--")):
        con.execute(f"CREATE OR REPLACE TABLE {name} AS {definition}")
    else:
        con.execute(f"CREATE OR REPLACE TABLE {name} ({definition})")
    path = tmp_path / f"{name}.parquet"
    con.execute(f"COPY {name} TO {_quoted(path)} (FORMAT PARQUET)")
    return path


def _types(con, sql: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1])
        for row in con.execute(f"DESCRIBE SELECT * FROM ({sql}) _q LIMIT 0").fetchall()
    }


def _pg_types(con, sql: str) -> dict[str, str]:
    return {
        name: _duckdb_type_to_pg_type(typ) for name, typ in _types(con, sql).items()
    }


def test_pandas_writes_an_all_none_column_that_duckdb_reads_as_integer(tmp_path):
    reader = _write_raw(
        tmp_path, "FOEventReason", [{"externalCode": "R1"}], ["eventReasonCategory"]
    )
    con = _connect()
    try:
        types = _types(
            con,
            f"SELECT * FROM read_parquet({_quoted(reader)}, hive_partitioning=true, "
            "union_by_name=true)",
        )
    finally:
        con.close()
    assert types["eventReasonCategory"] == "INTEGER"


@pytest.mark.parametrize(
    ("dataset", "entity", "row", "nulls", "varchar_columns"),
    [
        (
            "sap_successfactors_performancereview_latest",
            "PerformanceReview",
            {
                "formDataId": "F1",
                "formSubjectId": "U1",
                "formTemplateId": "T1",
                "isRated": "true",
                "rating": "4",
            },
            [
                "status",
                "overallRating",
                "potentialRating",
                "formStartDate",
                "formEndDate",
                "lastModifiedDateTime",
            ],
            ["status"],
        ),
        (
            "sap_successfactors_jobapplication_latest",
            "JobApplication",
            {"applicationId": "A1", "jobReqId": "J1", "candidateId": "C1"},
            ["applicationStatus", "source", "lastModifiedDateTime"],
            ["application_status"],
        ),
        (
            "sap_successfactors_foeventreason_latest",
            "FOEventReason",
            {"externalCode": "R1", "event": "5", "status": "A"},
            ["name_defaultValue", "eventReasonCategory", "lastModifiedDateTime"],
            [
                "event_reason_id",
                "event_reason_name",
                "event",
                "event_reason_category",
                "status",
            ],
        ),
    ],
)
def test_silver_status_columns_stay_varchar_when_raw_is_all_null(
    tmp_path, dataset, entity, row, nulls, varchar_columns
):
    reader = _write_raw(tmp_path, entity, [row], nulls)
    con = _connect()
    try:
        sql = _dataset(dataset, {entity: reader})
        types = _types(con, sql)
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    for column in varchar_columns:
        assert types[column] == "VARCHAR", column


def _integer_review_silver(con, tmp_path: Path) -> Path:
    return _table_parquet(
        con,
        tmp_path,
        "performancereview_latest",
        "SELECT 'F1'::VARCHAR form_data_id, 'U1'::VARCHAR user_id, "
        "'T1'::VARCHAR form_template_id, NULL::INTEGER status, "
        "4.0::DOUBLE performance_rating, TRUE is_rated, NULL::DOUBLE potential_rating, "
        "NULL::DATE cycle_start_date, NULL::DATE cycle_end_date, "
        "DATE '2026-09-30' load_date",
    )


def test_performance_cycle_casts_an_integer_null_review_status(tmp_path):
    con = _connect()
    try:
        reviews = _integer_review_silver(con, tmp_path)
        empty = {
            "sap_successfactors_formperfpotsummarysection_latest": (
                "user_id VARCHAR, form_data_id VARCHAR, performance_rating DOUBLE, "
                "potential_rating DOUBLE, load_date DATE"
            ),
            "sap_successfactors_goalplan_latest": (
                "goal_id VARCHAR, user_id VARCHAR, goal_state VARCHAR, "
                "percent_complete DOUBLE, load_date DATE"
            ),
            "sap_successfactors_simplegoal_latest": (
                "goal_id VARCHAR, user_id VARCHAR, goal_state VARCHAR, "
                "percent_complete DOUBLE, load_date DATE"
            ),
            "sap_successfactors_formobjective_latest": (
                "objective_id VARCHAR, user_id VARCHAR, objective_status VARCHAR, "
                "percent_complete DOUBLE, load_date DATE"
            ),
            "sap_successfactors_formobjectivedetails_latest": (
                "objective_detail_id VARCHAR, user_id VARCHAR, "
                "objective_status VARCHAR, percent_complete DOUBLE, load_date DATE"
            ),
            "sap_successfactors_goalachievements_latest": (
                "goal_id VARCHAR, user_id VARCHAR, achievement_status VARCHAR, "
                "achievement_percent DOUBLE, load_date DATE"
            ),
            "sap_successfactors_calibrationsessionsubject_latest": (
                "user_id VARCHAR, performance_rating DOUBLE, potential_rating DOUBLE, "
                "load_date DATE"
            ),
            "sap_successfactors_calibrationsubjectrank_latest": (
                "user_id VARCHAR, calibration_rank VARCHAR, load_date DATE"
            ),
        }
        inputs = {
            name: _table_parquet(con, tmp_path, f"t{index}", ddl)
            for index, (name, ddl) in enumerate(empty.items())
        }
        inputs["sap_successfactors_performancereview_latest"] = reviews
        sql = _dataset("sap_successfactors_performance_cycle", inputs)
        assert _types(con, sql)["review_status"] == "VARCHAR"
        assert con.execute(f"SELECT review_status FROM ({sql})").fetchall() == [(None,)]

        fallback = _bind(
            TALENT_CORE_FALLBACK_SQL["sap_successfactors_performance_cycle"],
            {"sap_successfactors_performancereview_latest": reviews},
        )
        assert _types(con, fallback)["review_status"] == "VARCHAR"
    finally:
        con.close()


def test_job_application_pipeline_and_funnel_bind_integer_null_statuses(tmp_path):
    con = _connect()
    try:
        requisitions = _table_parquet(
            con,
            tmp_path,
            "reqs",
            "SELECT 'J1'::VARCHAR job_req_id, 'Rol'::VARCHAR job_title, "
            "'open'::VARCHAR status, NULL::INTEGER department, "
            "'L1'::VARCHAR AS location, DATE '2026-09-30' load_date",
        )
        applications = _table_parquet(
            con,
            tmp_path,
            "apps",
            "SELECT 'A1'::VARCHAR application_id, 'J1'::VARCHAR job_req_id, "
            "'C1'::VARCHAR candidate_id, NULL::INTEGER application_status, "
            "NULL::INTEGER source, DATE '2026-09-30' load_date",
        )
        candidates = _table_parquet(
            con, tmp_path, "cands", "SELECT 'C1'::VARCHAR candidate_id"
        )
        pipeline_sql = _dataset(
            "sap_successfactors_job_application_pipeline",
            {
                "sap_successfactors_jobrequisition_latest": requisitions,
                "sap_successfactors_jobapplication_latest": applications,
                "sap_successfactors_candidate_latest": candidates,
            },
        )
        assert _types(con, pipeline_sql)["application_status"] == "VARCHAR"

        pipeline = _table_parquet(
            con,
            tmp_path,
            "pipeline",
            "SELECT 'J1'::VARCHAR job_req_id, 'A1'::VARCHAR application_id, "
            "'C1'::VARCHAR candidate_id, NULL::INTEGER department, "
            "NULL::INTEGER application_status, NULL::INTEGER source",
        )
        funnel_sql = _dataset(
            "sap_successfactors_recruitment_application_funnel",
            {"sap_successfactors_job_application_pipeline": pipeline},
        )
        rows = con.execute(
            f"SELECT department, application_status, source FROM ({funnel_sql})"
        ).fetchall()
        types = _pg_types(con, funnel_sql)
    finally:
        con.close()
    assert rows == [("(sin departamento)", "(sin etapa)", "(sin fuente)")]
    assert types["department"] == types["application_status"] == "TEXT"
    assert types["source"] == "TEXT"


def test_performance_goals_groups_an_integer_null_review_status(tmp_path):
    con = _connect()
    try:
        cycle = _table_parquet(
            con,
            tmp_path,
            "cycle",
            "SELECT 'U1'::VARCHAR user_id, NULL::INTEGER review_status, "
            "4.0::DOUBLE performance_rating, NULL::DOUBLE potential_rating, "
            "0::BIGINT goals_total, 0::BIGINT goals_completed, "
            "NULL::DOUBLE goals_percent_complete_avg",
        )
        sql = _dataset(
            "sap_successfactors_talent_performance_goals",
            {"sap_successfactors_performance_cycle": cycle},
        )
        rows = con.execute(f"SELECT review_status FROM ({sql})").fetchall()
        types = _pg_types(con, sql)
    finally:
        con.close()
    assert rows == [("unknown",)]
    assert types["review_status"] == "TEXT"


def _integer_event_reasons(con, tmp_path: Path) -> Path:
    return _table_parquet(
        con,
        tmp_path,
        "reasons",
        "SELECT 'R1'::VARCHAR event_reason_id, NULL::INTEGER event_reason_name, "
        "'5'::VARCHAR AS event, NULL::INTEGER event_reason_category, "
        "'A'::VARCHAR status, DATE '2026-09-30' load_date",
    )


def test_movement_events_binds_integer_null_event_reason_columns(tmp_path):
    con = _connect()
    try:
        jobs = _table_parquet(
            con,
            tmp_path,
            "jobs",
            "SELECT 'U1'::VARCHAR user_id, DATE '2026-01-01' start_date, "
            "'JOB'::VARCHAR job_code, 'P1'::VARCHAR AS position, "
            "'D1'::VARCHAR department, 'L1'::VARCHAR AS location, "
            "'M1'::VARCHAR manager_id, 'R1'::VARCHAR event_reason, "
            "DATE '2026-09-30' load_date",
        )
        sql = _dataset(
            "sap_successfactors_movement_events",
            {
                "sap_successfactors_empjob_latest": jobs,
                "sap_successfactors_foeventreason_latest": _integer_event_reasons(
                    con, tmp_path
                ),
            },
        )
        types = _types(con, sql)
        rows = con.execute(
            f"SELECT event_reason_category, movement_type, event FROM ({sql})"
        ).fetchall()
    finally:
        con.close()
    assert rows == [("unclassified", "movement", "5")]
    for column in ("event_reason", "event_reason_name", "event_reason_category"):
        assert types[column] == "VARCHAR"


def test_turnover_by_period_binds_integer_null_event_reason_columns(tmp_path):
    con = _connect()
    try:
        terminations = _table_parquet(
            con,
            tmp_path,
            "terms",
            "SELECT 'U1'::VARCHAR user_id, DATE '2026-05-10' termination_date, "
            "NULL::VARCHAR event_reason",
        )
        sql = _dataset(
            "sap_successfactors_turnover_by_period",
            {
                "sap_successfactors_empemploymenttermination_latest": terminations,
                "sap_successfactors_foeventreason_latest": _integer_event_reasons(
                    con, tmp_path
                ),
            },
        )
        rows = con.execute(
            f"SELECT event_reason, event_reason_category, terminations FROM ({sql})"
        ).fetchall()
    finally:
        con.close()
    assert rows == [("(sin motivo)", "unclassified", 1)]


def test_talent_gold_stage_types_match_the_legacy_compatibility_relations(tmp_path):
    con = _connect()
    try:
        benchmark_sql = _dataset("sap_successfactors_talent_benchmark_internal", {})
        benchmark_types = _pg_types(con, benchmark_sql)
        benchmark = _table_parquet(con, tmp_path, "benchmark", benchmark_sql)
        cpa = _table_parquet(
            con,
            tmp_path,
            "cpa",
            "tenant_id VARCHAR, workspace_id VARCHAR, user_id VARCHAR, "
            "full_name VARCHAR, company_name VARCHAR, department_name VARCHAR, "
            "location_name VARCHAR, job_code VARCHAR, direct_reports BIGINT, "
            "tenure_months DOUBLE, role_name VARCHAR, competency_score DOUBLE, "
            "performance_score DOUBLE, aspiration_score DOUBLE, fit_score DOUBLE, "
            "invalid_score_input BOOLEAN, required_skills_status VARCHAR, "
            "role_profile_status VARCHAR",
        )
        readiness_sql = _dataset(
            "sap_successfactors_talent_readiness",
            {
                "sap_successfactors_talent_cpa_scores": cpa,
                "sap_successfactors_talent_benchmark_internal": benchmark,
            },
        )
        readiness_types = _pg_types(con, readiness_sql)
        readiness = _table_parquet(con, tmp_path, "readiness", readiness_sql)
        mobility = _table_parquet(
            con,
            tmp_path,
            "mobility",
            "user_id VARCHAR, latest_event_reason VARCHAR, movement_events BIGINT, "
            "distinct_job_codes BIGINT, distinct_departments BIGINT, "
            "first_assignment_date DATE, latest_assignment_date DATE",
        )
        nine_box_sql = _dataset(
            "sap_successfactors_talent_9box",
            {
                "sap_successfactors_talent_readiness": readiness,
                "sap_successfactors_talent_mobility_history": mobility,
            },
        )
        nine_box = _table_parquet(con, tmp_path, "nine_box", nine_box_sql)
        promotion_sql = _dataset(
            "sap_successfactors_talent_promotion_alignment",
            {
                "sap_successfactors_talent_9box": nine_box,
                "sap_successfactors_talent_mobility_history": mobility,
            },
        )
        promotion_types = _pg_types(con, promotion_sql)
        promotion = _table_parquet(con, tmp_path, "promotion", promotion_sql)
        empties = {
            "sap_successfactors_talent_retention_risk": (
                "risk_band VARCHAR, status VARCHAR, invalid_score_input BOOLEAN, "
                "fit_score DOUBLE"
            ),
            "sap_successfactors_talent_calibration_sensitivity": "near_cut_count BIGINT",
            "sap_successfactors_talent_role_fit_assignments": (
                "assignment_recommendation VARCHAR, status VARCHAR, "
                "invalid_score_input BOOLEAN, fit_score DOUBLE"
            ),
            "sap_successfactors_talent_role_profile": "required_skills_status VARCHAR",
        }
        inputs = {
            name: _table_parquet(con, tmp_path, f"e{index}", ddl)
            for index, (name, ddl) in enumerate(empties.items())
        }
        candidates_sql = _dataset(
            "sap_successfactors_talent_action_candidates",
            {
                **inputs,
                "sap_successfactors_talent_readiness": readiness,
                "sap_successfactors_talent_9box": nine_box,
                "sap_successfactors_talent_mobility_history": mobility,
                "sap_successfactors_talent_promotion_alignment": promotion,
            },
        )
        candidate_types = _pg_types(con, candidates_sql)
        candidates = _table_parquet(con, tmp_path, "candidates", candidates_sql)
        signals_types = _pg_types(
            con,
            _dataset(
                "sap_successfactors_talent_signals",
                {
                    "sap_successfactors_talent_action_candidates": candidates,
                    "sap_successfactors_talent_role_profile": inputs[
                        "sap_successfactors_talent_role_profile"
                    ],
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            ),
        )
    finally:
        con.close()

    assert {
        column: benchmark_types[column]
        for column in (
            "approved_by",
            "approved_at",
            "approval_actor_source",
            "approval_evidence_ref",
            "approval_authorization_ref",
        )
    } == {
        "approved_by": "INTEGER",
        "approved_at": "INTEGER",
        "approval_actor_source": "TEXT",
        "approval_evidence_ref": "TEXT",
        "approval_authorization_ref": "TEXT",
    }
    assert readiness_types["blocker_count"] == "INTEGER"
    assert readiness_types["confidence"] == "DECIMAL(3,2)"
    for column in ("promotion_count", "aligned_count", "misaligned_count"):
        assert promotion_types[column] == "DOUBLE PRECISION"
    assert candidate_types["affected_count"] == "DOUBLE PRECISION"
    assert signals_types["affected_count"] == "BIGINT"


@pytest.mark.parametrize(
    ("sql", "column", "expected"),
    [
        (
            FOUNDATION_GOLD_FALLBACK_SQL["sap_successfactors_manager_hierarchy"],
            "depth",
            "INTEGER",
        ),
        (
            TALENT_CORE_FALLBACK_SQL["sap_successfactors_talent_employee_profile"],
            "hierarchy_depth",
            "INTEGER",
        ),
        (
            TALENT_READFREE_EMPTY_SQL["sap_successfactors_talent_employee_profile"],
            "hierarchy_depth",
            "INTEGER",
        ),
        (
            TALENT_READFREE_EMPTY_SQL["sap_successfactors_talent_employee_profile"],
            "start_date",
            "DATE",
        ),
        (
            TALENT_READFREE_EMPTY_SQL["sap_successfactors_talent_employee_profile"],
            "end_date",
            "DATE",
        ),
        (
            TALENT_EMPTY_FALLBACK_SQL["sap_successfactors_talent_promotion_alignment"],
            "misaligned_count",
            "DOUBLE PRECISION",
        ),
        (
            TALENT_EMPTY_FALLBACK_SQL["sap_successfactors_talent_action_candidates"],
            "affected_count",
            "DOUBLE PRECISION",
        ),
        (
            TALENT_EMPTY_FALLBACK_SQL["sap_successfactors_talent_signals"],
            "affected_count",
            "BIGINT",
        ),
        (
            TALENT_RUNTIME_FALLBACK_SQL["sap_successfactors_talent_readiness"],
            "blocker_count",
            "INTEGER",
        ),
        (
            TALENT_RUNTIME_FALLBACK_SQL["sap_successfactors_talent_readiness"],
            "confidence",
            "DECIMAL(3,2)",
        ),
    ],
    ids=[
        "hierarchy-depth",
        "core-profile-depth",
        "readfree-profile-depth",
        "readfree-profile-start-date",
        "readfree-profile-end-date",
        "promotion-counts",
        "candidates-affected",
        "signals-affected",
        "readiness-blockers",
        "readiness-confidence",
    ],
)
def test_fallback_stage_types_match_the_primary_dataset(sql, column, expected):
    match = re.search(rf"^\s*(\S+)\s+AS\s+{column}\s*,?\s*$", sql, re.MULTILINE)
    assert match, column
    con = duckdb.connect()
    try:
        described = con.execute(f"DESCRIBE SELECT {match.group(1)}").fetchall()
    finally:
        con.close()
    assert _duckdb_type_to_pg_type(str(described[0][1])) == expected


def _gold_sql_without_comments(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_successfactors_gold_sql_never_selects_an_untyped_null():
    offenders = []
    for path in sorted(DATASETS.glob("*.sql")):
        if "(gold)" not in path.read_text(encoding="utf-8").splitlines()[0]:
            continue
        sql = re.sub(r"\s+", " ", _gold_sql_without_comments(path))
        for match in re.finditer(r"\bNULL\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.I):
            prefix = sql[: match.start()].rstrip()
            if re.search(r"\bCAST\s*\(\s*$", prefix, re.I):
                continue
            offenders.append(f"{path.name}:{match.group(1)}")
    assert offenders == []
