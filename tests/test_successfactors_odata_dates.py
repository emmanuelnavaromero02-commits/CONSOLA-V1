from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from refinement.app.duckdb_engine import _register_shared_macros

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "cartridges/sap_successfactors/datasets"
_RAW_RE = re.compile(
    r"'s3://\{bucket\}/raw/sap_successfactors/([A-Za-z0-9_]+)/\*\*/\*\.parquet'"
)
_SILVER_RE = re.compile(
    r"'s3://\{bucket\}/silver/sap_successfactors/([A-Za-z0-9_]+)/\*\*/\*\.parquet'"
)
_UNBOUND_COLUMN_RE = re.compile(
    r'(?:Referenced column|Column|does not have a column named) "([^"]+)"'
)
MIDNIGHT_2024_10_01 = 1727740800000

SILVER_OUTPUT_TYPES = {
    "calibrationsession_latest": (
        "session_id:VARCHAR session_name:VARCHAR session_status:VARCHAR "
        "start_date:DATE end_date:DATE load_date:DATE"
    ),
    "calibrationsessionsubject_latest": (
        "subject_id:VARCHAR session_id:VARCHAR user_id:VARCHAR "
        "performance_rating:DOUBLE potential_rating:DOUBLE load_date:DATE"
    ),
    "calibrationsubjectrank_latest": (
        "rank_id:VARCHAR session_id:VARCHAR subject_id:VARCHAR user_id:VARCHAR "
        "calibration_rank:VARCHAR load_date:DATE"
    ),
    "candidate_latest": (
        "candidate_id:VARCHAR first_name:VARCHAR last_name:VARCHAR "
        "status:VARCHAR load_date:DATE"
    ),
    "careerinterest_latest": (
        "aspiration_record_id:VARCHAR user_id:VARCHAR target_role:VARCHAR "
        "interest_level:VARCHAR mobility_preference:VARCHAR load_date:DATE"
    ),
    "careerworksheet_latest": (
        "aspiration_record_id:VARCHAR user_id:VARCHAR target_role:VARCHAR "
        "readiness:VARCHAR load_date:DATE"
    ),
    "catalogsfeed_latest": (
        "item_id:VARCHAR title:VARCHAR status:VARCHAR duration:DOUBLE "
        "credit_hours:DOUBLE load_date:DATE"
    ),
    "competencyentity_latest": (
        "competency_id:VARCHAR competency_name:VARCHAR description:VARCHAR "
        "status:VARCHAR load_date:DATE"
    ),
    "curricula_latest": (
        "curriculum_id:VARCHAR title:VARCHAR status:VARCHAR "
        "expiration_date:DATE load_date:DATE"
    ),
    "devgoal_latest": (
        "aspiration_record_id:VARCHAR user_id:VARCHAR target_role:VARCHAR "
        "readiness:VARCHAR start_date:DATE due_date:DATE load_date:DATE"
    ),
    "devgoalcompetency_latest": (
        "goal_competency_id:VARCHAR aspiration_record_id:VARCHAR "
        "user_id:VARCHAR skill_id:VARCHAR skill_name:VARCHAR load_date:DATE"
    ),
    "empcompensation_latest": (
        "user_id:VARCHAR start_date:DATE pay_group:VARCHAR "
        "frequency_code:VARCHAR load_date:DATE"
    ),
    "empemployment_latest": (
        "person_id_external:VARCHAR user_id:VARCHAR start_date:DATE "
        "end_date:DATE employee_class:VARCHAR original_start_date:DATE "
        "load_date:DATE"
    ),
    "empemploymenttermination_latest": (
        "user_id:VARCHAR termination_date:DATE event_reason:VARCHAR " "load_date:DATE"
    ),
    "empjob_history_latest": (
        "user_id:VARCHAR start_date:DATE end_date:DATE "
        "relationship_type:VARCHAR related_user_id:VARCHAR load_date:DATE"
    ),
    "empjob_latest": (
        "user_id:VARCHAR start_date:DATE end_date:DATE job_code:VARCHAR "
        "position:VARCHAR department:VARCHAR division:VARCHAR location:VARCHAR "
        "business_unit:VARCHAR company:VARCHAR cost_center:VARCHAR "
        "manager_id:VARCHAR event_reason:VARCHAR load_date:DATE"
    ),
    "employeetime_latest": (
        "user_id:VARCHAR start_date:DATE end_date:DATE time_type:VARCHAR "
        "approval_status:VARCHAR load_date:DATE"
    ),
    "emppaycompnonrecurring_latest": (
        "user_id:VARCHAR pay_component:VARCHAR paycomp_value:VARCHAR "
        "currency:VARCHAR pay_date:DATE load_date:DATE"
    ),
    "emppaycomprecurring_latest": (
        "user_id:VARCHAR pay_component:VARCHAR paycomp_value:VARCHAR "
        "frequency:VARCHAR currency:VARCHAR start_date:DATE end_date:DATE "
        "load_date:DATE"
    ),
    "fobusinessunit_latest": "business_unit_id:VARCHAR business_unit_name:VARCHAR load_date:DATE",
    "focompany_latest": "company_id:VARCHAR company_name:VARCHAR country:VARCHAR load_date:DATE",
    "focostcenter_latest": (
        "cost_center_id:VARCHAR cost_center_name:VARCHAR status:VARCHAR "
        "valid_from:DATE valid_to:DATE load_date:DATE"
    ),
    "fodepartment_latest": (
        "department_id:VARCHAR department_name:VARCHAR cost_center:VARCHAR "
        "load_date:DATE"
    ),
    "fodivision_latest": "division_id:VARCHAR division_name:VARCHAR load_date:DATE",
    "foeventreason_latest": (
        "event_reason_id:VARCHAR event_reason_name:VARCHAR event:VARCHAR "
        "event_reason_category:VARCHAR status:VARCHAR load_date:DATE"
    ),
    "fojobcode_latest": "job_code:VARCHAR job_name:VARCHAR load_date:DATE",
    "folocation_latest": (
        "location_id:VARCHAR location_name:VARCHAR status:VARCHAR "
        "valid_from:DATE valid_to:DATE load_date:DATE"
    ),
    "fopaygrade_latest": (
        "pay_grade_id:VARCHAR pay_grade_name:VARCHAR status:VARCHAR "
        "start_date:DATE end_date:DATE load_date:DATE"
    ),
    "formcompetency_latest": (
        "skill_record_id:VARCHAR user_id:VARCHAR skill_id:VARCHAR "
        "skill_name:VARCHAR proficiency_score:DOUBLE form_data_id:VARCHAR "
        "load_date:DATE"
    ),
    "formobjective_latest": (
        "objective_id:VARCHAR form_data_id:VARCHAR user_id:VARCHAR "
        "objective_name:VARCHAR objective_status:VARCHAR "
        "percent_complete:DOUBLE load_date:DATE"
    ),
    "formobjectivedetails_latest": (
        "objective_detail_id:VARCHAR objective_id:VARCHAR form_data_id:VARCHAR "
        "user_id:VARCHAR objective_status:VARCHAR percent_complete:DOUBLE "
        "load_date:DATE"
    ),
    "formperfpotsummarysection_latest": (
        "form_data_id:VARCHAR user_id:VARCHAR performance_rating:DOUBLE "
        "potential_rating:DOUBLE load_date:DATE"
    ),
    "goalachievements_latest": (
        "achievement_id:VARCHAR goal_id:VARCHAR user_id:VARCHAR "
        "achievement_status:VARCHAR achievement_percent:DOUBLE load_date:DATE"
    ),
    "goalplan_latest": (
        "goal_id:VARCHAR user_id:VARCHAR goal_name:VARCHAR goal_state:VARCHAR "
        "percent_complete:DOUBLE start_date:DATE due_date:DATE load_date:DATE"
    ),
    "jobapplication_latest": (
        "application_id:VARCHAR job_req_id:VARCHAR candidate_id:VARCHAR "
        "application_status:VARCHAR source:VARCHAR load_date:DATE"
    ),
    "jobrequisition_latest": (
        "job_req_id:VARCHAR job_title:VARCHAR status:VARCHAR department:VARCHAR "
        "location:VARCHAR load_date:DATE"
    ),
    "learningassignment_latest": (
        "assignment_id:VARCHAR user_id:VARCHAR learning_item_id:VARCHAR "
        "status:VARCHAR due_date:DATE completion_date:DATE load_date:DATE"
    ),
    "learningevents_latest": (
        "history_id:VARCHAR user_id:VARCHAR item_id:VARCHAR "
        "completion_date:DATE status:VARCHAR load_date:DATE"
    ),
    "learninghistory_latest": (
        "history_id:VARCHAR user_id:VARCHAR learning_item_id:VARCHAR "
        "completion_date:DATE credit_hours:DOUBLE status:VARCHAR load_date:DATE"
    ),
    "learningitem_latest": (
        "learning_item_id:VARCHAR title:VARCHAR status:VARCHAR "
        "credit_hours:DOUBLE duration_hours:DOUBLE expiration_date:DATE "
        "load_date:DATE"
    ),
    "paymentinformationdetailv3_latest": (
        "payment_detail_id:VARCHAR worker_id:VARCHAR effective_start_date:DATE "
        "system_effective_start_date:DATE system_effective_end_date:DATE "
        "payment_method:VARCHAR bank_country:VARCHAR bank:VARCHAR "
        "business_identifier_code:VARCHAR routing_number:VARCHAR "
        "account_number:VARCHAR account_owner:VARCHAR iban:VARCHAR "
        "currency:VARCHAR amount:DOUBLE percent:DOUBLE pay_type:VARCHAR "
        "custom_pay_type:VARCHAR pay_sequence:VARCHAR purpose:VARCHAR "
        "load_date:DATE"
    ),
    "peraddressdeflt_latest": (
        "person_id_external:VARCHAR address_type:VARCHAR valid_from:DATE "
        "valid_to:DATE address_line_1:VARCHAR city:VARCHAR state:VARCHAR "
        "zip_code:VARCHAR load_date:DATE"
    ),
    "peremail_latest": (
        "person_id_external:VARCHAR email_type:VARCHAR email_address:VARCHAR "
        "is_primary:BOOLEAN load_date:DATE"
    ),
    "performancereview_latest": (
        "form_data_id:VARCHAR user_id:VARCHAR form_template_id:VARCHAR "
        "status:VARCHAR performance_rating:DOUBLE is_rated:BOOLEAN "
        "potential_rating:DOUBLE cycle_start_date:DATE cycle_end_date:DATE "
        "load_date:DATE"
    ),
    "pernationalid_latest": (
        "person_id_external:VARCHAR country:VARCHAR card_type:VARCHAR "
        "is_primary:BOOLEAN load_date:DATE"
    ),
    "perperson_latest": (
        "person_id_external:VARCHAR person_id:VARCHAR date_of_birth:VARCHAR "
        "country_of_birth:VARCHAR load_date:DATE"
    ),
    "perpersonal_latest": (
        "person_id_external:VARCHAR first_name:VARCHAR last_name:VARCHAR "
        "gender:VARCHAR marital_status:VARCHAR valid_from:DATE load_date:DATE"
    ),
    "perphone_latest": (
        "person_id_external:VARCHAR phone_type:VARCHAR phone_number:VARCHAR "
        "is_primary:BOOLEAN load_date:DATE"
    ),
    "position_latest": (
        "position_id:VARCHAR position_name:VARCHAR department:VARCHAR "
        "location:VARCHAR cost_center:VARCHAR criticality:VARCHAR "
        "is_vacant:BOOLEAN effective_status:VARCHAR load_date:DATE"
    ),
    "simplegoal_latest": (
        "goal_id:VARCHAR user_id:VARCHAR goal_name:VARCHAR goal_state:VARCHAR "
        "percent_complete:DOUBLE start_date:DATE due_date:DATE load_date:DATE"
    ),
    "skillentity_latest": (
        "skill_id:VARCHAR skill_name:VARCHAR description:VARCHAR status:VARCHAR "
        "load_date:DATE"
    ),
    "skillprofile_latest": (
        "skill_record_id:VARCHAR user_id:VARCHAR skill_id:VARCHAR "
        "skill_name:VARCHAR proficiency_score:DOUBLE load_date:DATE"
    ),
    "successionnomination_latest": (
        "nomination_id:VARCHAR user_id:VARCHAR target_position:VARCHAR "
        "readiness:VARCHAR nomination_status:VARCHAR load_date:DATE"
    ),
    "sysoverallcompetency_latest": (
        "skill_record_id:VARCHAR user_id:VARCHAR skill_id:VARCHAR "
        "skill_name:VARCHAR proficiency_score:DOUBLE load_date:DATE"
    ),
    "talentpool_latest": "pool_id:VARCHAR pool_name:VARCHAR status:VARCHAR load_date:DATE",
    "talentpoolnav_latest": (
        "aspiration_record_id:VARCHAR pool_id:VARCHAR user_id:VARCHAR "
        "readiness:VARCHAR status:VARCHAR load_date:DATE"
    ),
    "timeaccount_latest": "user_id:VARCHAR account_type:VARCHAR load_date:DATE",
    "user_latest": (
        "user_id:VARCHAR username:VARCHAR email:VARCHAR status:VARCHAR "
        "first_name:VARCHAR last_name:VARCHAR department:VARCHAR "
        "division:VARCHAR location:VARCHAR manager:VARCHAR hire_date:DATE "
        "load_date:DATE"
    ),
    "usercourses_latest": (
        "assignment_id:VARCHAR user_id:VARCHAR item_id:VARCHAR status:VARCHAR "
        "due_date:DATE completion_date:DATE load_date:DATE"
    ),
    "userprograms_latest": (
        "assignment_id:VARCHAR user_id:VARCHAR item_id:VARCHAR status:VARCHAR "
        "due_date:DATE completion_date:DATE load_date:DATE"
    ),
    "userskill_latest": (
        "skill_record_id:VARCHAR user_id:VARCHAR skill_id:VARCHAR "
        "skill_name:VARCHAR proficiency_score:DOUBLE load_date:DATE"
    ),
    "workercompetencyassessment_latest": (
        "skill_record_id:VARCHAR user_id:VARCHAR skill_id:VARCHAR "
        "skill_name:VARCHAR proficiency_score:DOUBLE load_date:DATE"
    ),
    "workschedule_latest": (
        "work_schedule_id:VARCHAR user_id:VARCHAR country:VARCHAR "
        "start_date:DATE end_date:DATE average_working_days_per_week:VARCHAR "
        "average_hours_per_week:VARCHAR load_date:DATE"
    ),
}


def _connect(timezone: str | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if timezone:
        con.execute(f"SET TimeZone='{timezone}'")
    _register_shared_macros(con)
    return con


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _odata(ms: int, suffix: str = "") -> str:
    return f"/Date({ms}{suffix})/"


def _ms(day: date) -> int:
    return (day - date(1970, 1, 1)).days * 86_400_000


def _write_raw(root: Path, name: str, entity: str, values: dict[str, list]) -> Path:
    sql = (DATASETS / f"{name}.sql").read_text(encoding="utf-8")
    required, _ = _bind_with_discovered_columns(sql, entity, root / "_schema")
    frame = pd.DataFrame(values)
    for column in required:
        if column not in frame.columns:
            frame[column] = None
    frame["_extracted_at"] = "2026-09-30T04:00:00Z"
    target = root / "raw" / entity / "load_date=2026-09-30" / "batch_id=b1"
    target.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target / f"{entity}.parquet", index=False, engine="pyarrow")
    return root / "raw" / entity / "**" / "*.parquet"


def _silver_sql(name: str, raw_glob: Path) -> str:
    sql = (DATASETS / f"{name}.sql").read_text(encoding="utf-8")
    bound = _RAW_RE.sub(_quoted(raw_glob), sql)
    assert "s3://{bucket}" not in bound
    return bound


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (f"'{_odata(MIDNIGHT_2024_10_01)}'", date(2024, 10, 1)),
        (f"'{_odata(MIDNIGHT_2024_10_01, '+0000')}'", date(2024, 10, 1)),
        (f"'{_odata(MIDNIGHT_2024_10_01 + 86_399_000, '-0600')}'", date(2024, 10, 1)),
        (f"'{_odata(-2208988800000)}'", date(1900, 1, 1)),
        (f"'{_odata(253402214400000)}'", date(9999, 12, 31)),
        ("'2024-09-30'", date(2024, 9, 30)),
        ("'2024-09-30T23:30:00Z'", date(2024, 9, 30)),
        ("' 2024-09-30 '", date(2024, 9, 30)),
        ("DATE '2024-01-02'", date(2024, 1, 2)),
        (f"'{MIDNIGHT_2024_10_01}'", date(2024, 10, 1)),
        (f"' {MIDNIGHT_2024_10_01} '", date(2024, 10, 1)),
        (f"CAST({MIDNIGHT_2024_10_01} AS BIGINT)", date(2024, 10, 1)),
        ("'-2208988800000'", date(1900, 1, 1)),
        ("NULL", None),
        ("NULL::INTEGER", None),
        ("''", None),
    ],
)
def test_odata_date_macros_parse_supported_literals(literal, expected):
    con = _connect()
    try:
        row = con.execute(
            f"SELECT sf_odata_date({literal}), sf_odata_date_strict({literal}, 'x'), "
            f"typeof(sf_odata_timestamp({literal}))"
        ).fetchone()
    finally:
        con.close()
    assert row == (expected, expected, "TIMESTAMP")


def test_tolerant_macros_return_null_for_unrecognized_values():
    con = _connect()
    try:
        row = con.execute(
            "SELECT sf_odata_timestamp('31/12/2024'), sf_odata_date('/Date(abc)/'), "
            "sf_odata_date(12345)"
        ).fetchone()
    finally:
        con.close()
    assert row == (None, None, None)


@pytest.mark.parametrize(
    "value",
    [
        "'31/12/2024'",
        "'/Date(abc)/'",
        "'secret-value-4411'",
        "12345",
        "'20240131'",
        "'1727740800000000'",
    ],
)
def test_strict_macro_fails_without_revealing_the_value(value):
    con = _connect()
    try:
        with pytest.raises(duckdb.InvalidInputException) as raised:
            con.execute(f"SELECT sf_odata_date_strict({value}, 'hire_date')").fetchall()
    finally:
        con.close()
    message = str(raised.value)
    assert "fecha con formato no reconocido en hire_date" in message
    assert value.strip("'") not in message


def test_strict_macro_keeps_an_all_null_integer_column_null():
    con = _connect()
    try:
        con.execute("CREATE TABLE t AS SELECT NULL::INTEGER AS c FROM range(3)")
        rows = con.execute(
            "SELECT sf_odata_date_strict(c, 'c'), typeof(sf_odata_date_strict(c, 'c')) FROM t"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(None, "DATE")] * 3


@pytest.mark.parametrize("timezone", ["America/Mexico_City", "Asia/Tokyo", "UTC"])
def test_odata_dates_do_not_depend_on_the_session_time_zone(timezone):
    con = _connect(timezone)
    try:
        row = con.execute(
            "SELECT sf_odata_date(?), sf_odata_date(?), sf_odata_timestamp(?)",
            [
                _odata(MIDNIGHT_2024_10_01),
                _odata(MIDNIGHT_2024_10_01 + 86_399_000, "+0000"),
                "2024-10-01T02:00:00+02:00",
            ],
        ).fetchone()
    finally:
        con.close()
    assert row[0] == date(2024, 10, 1)
    assert row[1] == date(2024, 10, 1)
    assert str(row[2]) == "2024-10-01 00:00:00"


def test_user_latest_parses_odata_hire_dates(tmp_path):
    raw = _write_raw(
        tmp_path,
        "sap_successfactors_user_latest",
        "User",
        {
            "userId": ["U1", "U2"],
            "hireDate": [_odata(MIDNIGHT_2024_10_01), None],
            "lastModifiedDateTime": [_odata(MIDNIGHT_2024_10_01, "+0000"), None],
        },
    )
    con = _connect("America/Mexico_City")
    try:
        sql = _silver_sql("sap_successfactors_user_latest", raw)
        rows = con.execute(
            f"SELECT user_id, hire_date FROM ({sql}) ORDER BY 1"
        ).fetchall()
    finally:
        con.close()
    assert rows == [("U1", date(2024, 10, 1)), ("U2", None)]


def test_empjob_latest_parses_dates_and_dedups_by_last_modified(tmp_path):
    start = _odata(_ms(date(2025, 1, 1)))
    raw = _write_raw(
        tmp_path,
        "sap_successfactors_empjob_latest",
        "EmpJob",
        {
            "userId": ["U1", "U1"],
            "startDate": [start, start],
            "endDate": [_odata(253402214400000)] * 2,
            "jobCode": ["LAST_EDITED", "FIRST_EDITED"],
            "lastModifiedDateTime": [
                _odata(_ms(date(2025, 2, 1)), "+0000"),
                _odata(_ms(date(2025, 1, 1)), "+0000"),
            ],
        },
    )
    con = _connect()
    try:
        sql = _silver_sql("sap_successfactors_empjob_latest", raw)
        rows = con.execute(
            f"SELECT user_id, start_date, end_date, job_code FROM ({sql})"
        ).fetchall()
    finally:
        con.close()
    assert rows == [("U1", date(2025, 1, 1), date(9999, 12, 31), "LAST_EDITED")]


def test_empjob_latest_fails_on_an_unrecognized_start_date(tmp_path):
    raw = _write_raw(
        tmp_path,
        "sap_successfactors_empjob_latest",
        "EmpJob",
        {"userId": ["U1"], "startDate": ["01-31-2025"], "endDate": [None]},
    )
    con = _connect()
    try:
        with pytest.raises(duckdb.InvalidInputException) as raised:
            con.execute(_silver_sql("sap_successfactors_empjob_latest", raw)).fetchall()
    finally:
        con.close()
    assert "start_date" in str(raised.value)
    assert "01-31-2025" not in str(raised.value)


def test_empemployment_latest_reads_iso_and_odata_dates(tmp_path):
    raw = _write_raw(
        tmp_path,
        "sap_successfactors_empemployment_latest",
        "EmpEmployment",
        {
            "userId": ["U1", "U2"],
            "personIdExternal": ["P1", "P2"],
            "startDate": ["2024-09-30", _odata(MIDNIGHT_2024_10_01)],
            "endDate": [None, None],
            "originalStartDate": [None, _odata(-2208988800000)],
        },
    )
    con = _connect("America/Mexico_City")
    try:
        sql = _silver_sql("sap_successfactors_empemployment_latest", raw)
        rows = con.execute(
            f"SELECT user_id, start_date, end_date, original_start_date FROM ({sql}) "
            "ORDER BY 1"
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        ("U1", date(2024, 9, 30), None, None),
        ("U2", date(2024, 10, 1), None, date(1900, 1, 1)),
    ]


def test_all_null_integer_date_columns_stay_null(tmp_path):
    raw = _write_raw(
        tmp_path,
        "sap_successfactors_simplegoal_latest",
        "SimpleGoal",
        {"id": ["G1"], "userId": ["U1"]},
    )
    con = _connect()
    try:
        sql = _silver_sql("sap_successfactors_simplegoal_latest", raw)
        types = {
            str(row[0]): str(row[1])
            for row in con.execute(
                f"DESCRIBE SELECT * FROM ({sql}) q LIMIT 0"
            ).fetchall()
        }
        rows = con.execute(f"SELECT start_date, due_date FROM ({sql})").fetchall()
    finally:
        con.close()
    assert types["start_date"] == types["due_date"] == "DATE"
    assert rows == [(None, None)]


def _empty_silver(con, tmp_path: Path, name: str, columns: str) -> Path:
    con.execute(f"CREATE OR REPLACE TABLE {name} ({columns})")
    path = tmp_path / f"{name}.parquet"
    con.execute(f"COPY {name} TO {_quoted(path)} (FORMAT PARQUET)")
    return path


def test_employee_360_prefers_the_assignment_active_today(tmp_path):
    today = date.today()
    con = _connect()
    try:
        con.execute(
            "CREATE TABLE employment AS SELECT * FROM (VALUES "
            "('U1', 'P1', DATE '2019-01-01', NULL::DATE), "
            "('U2', 'P2', DATE '2019-01-01', NULL::DATE), "
            "('U3', 'P3', DATE '2019-01-01', NULL::DATE)"
            ") v(user_id, person_id_external, start_date, end_date)"
        )
        employment = tmp_path / "employment.parquet"
        con.execute(f"COPY employment TO {_quoted(employment)} (FORMAT PARQUET)")
        con.execute(
            "CREATE TABLE jobs (user_id VARCHAR, start_date DATE, end_date DATE, "
            "job_code VARCHAR, department VARCHAR, division VARCHAR, location VARCHAR, "
            "company VARCHAR, cost_center VARCHAR, manager_id VARCHAR)"
        )
        rows = [
            ("U1", today - timedelta(days=800), today - timedelta(days=401), "PAST"),
            ("U1", today - timedelta(days=400), None, "CURRENT"),
            ("U1", today + timedelta(days=30), None, "FUTURE"),
            ("U2", today - timedelta(days=900), today - timedelta(days=500), "OLDEST"),
            (
                "U2",
                today - timedelta(days=499),
                today - timedelta(days=10),
                "LATEST_PAST",
            ),
            ("U3", today + timedelta(days=90), None, "LATER"),
            ("U3", today + timedelta(days=15), None, "NEXT"),
        ]
        con.executemany(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, 'D', 'V', 'L', 'C', 'CC', NULL)", rows
        )
        jobs = tmp_path / "jobs.parquet"
        con.execute(f"COPY jobs TO {_quoted(jobs)} (FORMAT PARQUET)")
        inputs = {
            "sap_successfactors_empemployment_latest": employment,
            "sap_successfactors_empjob_latest": jobs,
            "sap_successfactors_perpersonal_latest": _empty_silver(
                con,
                tmp_path,
                "pers",
                "person_id_external VARCHAR, first_name VARCHAR, last_name VARCHAR, "
                "gender VARCHAR, marital_status VARCHAR, valid_from DATE",
            ),
            "sap_successfactors_focompany_latest": _empty_silver(
                con, tmp_path, "co", "company_id VARCHAR, company_name VARCHAR"
            ),
            "sap_successfactors_fodepartment_latest": _empty_silver(
                con, tmp_path, "dept", "department_id VARCHAR, department_name VARCHAR"
            ),
            "sap_successfactors_fodivision_latest": _empty_silver(
                con, tmp_path, "divi", "division_id VARCHAR, division_name VARCHAR"
            ),
            "sap_successfactors_folocation_latest": _empty_silver(
                con, tmp_path, "loc", "location_id VARCHAR, location_name VARCHAR"
            ),
        }
        sql = (DATASETS / "sap_successfactors_employee_360.sql").read_text(
            encoding="utf-8"
        )
        sql = _SILVER_RE.sub(lambda match: _quoted(inputs[match.group(1)]), sql)
        selected = con.execute(
            f"SELECT user_id, job_code FROM ({sql}) ORDER BY user_id"
        ).fetchall()
    finally:
        con.close()
    assert selected == [("U1", "CURRENT"), ("U2", "LATEST_PAST"), ("U3", "NEXT")]


def _bound_dataset(name: str, inputs: dict[str, Path]) -> str:
    sql = (DATASETS / f"{name}.sql").read_text(encoding="utf-8")
    bound = re.sub(
        r"'s3://\{bucket\}/(?:silver|gold)/sap_successfactors/([A-Za-z0-9_]+)/\*\*/\*\.parquet'",
        lambda match: _quoted(inputs[match.group(1)]),
        sql,
    )
    assert "s3://{bucket}" not in bound
    return bound


def test_employee_360_prefers_the_personal_record_effective_today(tmp_path):
    today = date.today()
    con = _connect()
    try:
        con.execute(
            "CREATE TABLE employment AS SELECT * FROM (VALUES "
            "('U1', 'P1', DATE '2019-01-01', NULL::DATE), "
            "('U2', 'P2', DATE '2019-01-01', NULL::DATE), "
            "('U3', 'P3', DATE '2019-01-01', NULL::DATE)"
            ") v(user_id, person_id_external, start_date, end_date)"
        )
        employment = tmp_path / "employment.parquet"
        con.execute(f"COPY employment TO {_quoted(employment)} (FORMAT PARQUET)")
        con.execute(
            "CREATE TABLE pers (person_id_external VARCHAR, first_name VARCHAR, "
            "last_name VARCHAR, gender VARCHAR, marital_status VARCHAR, valid_from DATE)"
        )
        con.executemany(
            "INSERT INTO pers VALUES (?, ?, 'Name', 'F', ?, ?)",
            [
                ("P1", "Current", "S", today - timedelta(days=400)),
                ("P1", "Future", "M", today + timedelta(days=30)),
                ("P2", "Later", "M", today + timedelta(days=90)),
                ("P2", "Next", "S", today + timedelta(days=15)),
                ("P3", "Oldest", "S", today - timedelta(days=800)),
                ("P3", "Latest", "M", today - timedelta(days=100)),
            ],
        )
        personal = tmp_path / "personal.parquet"
        con.execute(f"COPY pers TO {_quoted(personal)} (FORMAT PARQUET)")
        inputs = {
            "sap_successfactors_empemployment_latest": employment,
            "sap_successfactors_empjob_latest": _empty_silver(
                con,
                tmp_path,
                "jobs",
                "user_id VARCHAR, start_date DATE, end_date DATE, job_code VARCHAR, "
                "department VARCHAR, division VARCHAR, location VARCHAR, "
                "company VARCHAR, cost_center VARCHAR, manager_id VARCHAR",
            ),
            "sap_successfactors_perpersonal_latest": personal,
            "sap_successfactors_focompany_latest": _empty_silver(
                con, tmp_path, "co", "company_id VARCHAR, company_name VARCHAR"
            ),
            "sap_successfactors_fodepartment_latest": _empty_silver(
                con, tmp_path, "dept", "department_id VARCHAR, department_name VARCHAR"
            ),
            "sap_successfactors_fodivision_latest": _empty_silver(
                con, tmp_path, "divi", "division_id VARCHAR, division_name VARCHAR"
            ),
            "sap_successfactors_folocation_latest": _empty_silver(
                con, tmp_path, "loc", "location_id VARCHAR, location_name VARCHAR"
            ),
        }
        sql = _bound_dataset("sap_successfactors_employee_360", inputs)
        selected = con.execute(
            f"SELECT user_id, full_name, marital_status FROM ({sql}) ORDER BY user_id"
        ).fetchall()
    finally:
        con.close()
    assert selected == [
        ("U1", "Current Name", "S"),
        ("U2", "Next Name", "S"),
        ("U3", "Latest Name", "M"),
    ]


def test_mobility_history_ignores_assignments_that_start_in_the_future(tmp_path):
    today = date.today()
    con = _connect()
    try:
        con.execute(
            "CREATE TABLE jobs (user_id VARCHAR, start_date DATE, end_date DATE, "
            "event_reason VARCHAR, job_code VARCHAR, department VARCHAR, "
            "location VARCHAR, manager_id VARCHAR)"
        )
        con.executemany(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, 'D', 'L', 'M')",
            [
                ("U1", today - timedelta(days=400), today + timedelta(days=29), "HIRE", "CUR"),
                ("U1", today + timedelta(days=30), None, "PROMO_SCHEDULED", "FUT"),
                ("U2", today + timedelta(days=15), None, "NEW_HIRE", "NEW"),
            ],
        )
        jobs = tmp_path / "jobs.parquet"
        con.execute(f"COPY jobs TO {_quoted(jobs)} (FORMAT PARQUET)")
        inputs = {
            "sap_successfactors_empjob_latest": jobs,
            "sap_successfactors_employee_360": _empty_silver(
                con,
                tmp_path,
                "emp",
                "user_id VARCHAR, full_name VARCHAR, company_name VARCHAR, "
                "department_name VARCHAR, location_name VARCHAR, job_code VARCHAR, "
                "is_active BOOLEAN",
            ),
        }
        sql = _bound_dataset("sap_successfactors_talent_mobility_history", inputs)
        selected = con.execute(
            "SELECT user_id, latest_assignment_date, latest_event_reason, "
            "DATE_DIFF('month', latest_assignment_date, CURRENT_DATE) "
            f"FROM ({sql}) ORDER BY user_id"
        ).fetchall()
    finally:
        con.close()
    assert selected == [
        ("U1", today - timedelta(days=400), "HIRE", selected[0][3]),
        ("U2", None, None, None),
    ]
    assert selected[0][3] >= 0


def _bind_with_discovered_columns(
    sql: str, entity: str, root: Path
) -> tuple[list[str], list[tuple[str, str]]]:
    columns = ["_extracted_at"]
    for _ in range(80):
        target = root / entity / "load_date=2026-09-30" / "batch_id=b1"
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({name: pa.array([], pa.string()) for name in columns}),
            target / "part.parquet",
        )
        bound = _RAW_RE.sub(_quoted(root / entity / "**" / "*.parquet"), sql)
        con = _connect()
        try:
            rows = con.execute(f"DESCRIBE SELECT * FROM ({bound}) q LIMIT 0").fetchall()
            return columns, [(str(row[0]), str(row[1])) for row in rows]
        except duckdb.BinderException as exc:
            match = _UNBOUND_COLUMN_RE.search(str(exc))
            assert match and match.group(1) not in columns, str(exc)
            columns.append(match.group(1))
        finally:
            con.close()
    raise AssertionError(f"could not bind {entity}")


def _discovered_output_types(sql: str, entity: str, root: Path) -> str:
    _, described = _bind_with_discovered_columns(sql, entity, root)
    return " ".join(f"{name}:{typ}" for name, typ in described)


def _raw_silvers() -> list[tuple[str, str]]:
    found = []
    for path in sorted(DATASETS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        entities = set(_RAW_RE.findall(sql))
        if "(silver)" in sql.splitlines()[0] and len(entities) == 1:
            found.append((path.stem, entities.pop()))
    return found


def test_snapshot_covers_every_single_entity_silver():
    assert {
        name.removeprefix("sap_successfactors_") for name, _ in _raw_silvers()
    } == set(SILVER_OUTPUT_TYPES)


@pytest.mark.parametrize(("name", "entity"), _raw_silvers(), ids=lambda value: value)
def test_silver_output_types_are_unchanged(tmp_path, name, entity):
    sql = (DATASETS / f"{name}.sql").read_text(encoding="utf-8")
    observed = _discovered_output_types(sql, entity, tmp_path)
    assert observed == SILVER_OUTPUT_TYPES[name.removeprefix("sap_successfactors_")]


def test_every_latest_silver_dedups_with_the_tolerant_odata_timestamp():
    for path in sorted(DATASETS.glob("*_latest.sql")):
        sql = path.read_text(encoding="utf-8")
        assert (
            "sf_odata_timestamp(lastModifiedDateTime) DESC NULLS LAST" in sql
        ), path.name
        assert "TRY_CAST(lastModifiedDateTime" not in sql, path.name
        assert "to_timestamp(" not in sql, path.name
