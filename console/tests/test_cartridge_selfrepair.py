"""Behavioral tests for the Level 2 self-repair / validation engine."""
from __future__ import annotations

from app.services import cartridge_autopilot as ap
from app.services import cartridge_selfrepair as sr


def test_validate_silver_sql_ok():
    sql = ("SELECT * FROM read_parquet('s3://lakehouse/raw/x/e/**/*.parquet', "
           "hive_partitioning=true) WHERE load_date = '{latest_date}'")
    assert sr.validate_silver_sql(sql).ok


def test_validate_silver_sql_missing_latest_date():
    sql = "SELECT * FROM read_parquet('s3://x/**/*.parquet')"
    vr = sr.validate_silver_sql(sql)
    assert not vr.ok
    assert any("latest" in r for r in vr.reasons)


def test_validate_gold_rejects_silver_markers():
    sql = "SELECT 1 FROM read_parquet('s3://x') WHERE load_date = '{latest_date}'"
    vr = sr.validate_gold_sql(sql)
    assert not vr.ok
    assert any("latest_date" in r for r in vr.reasons) or any("silver" in r for r in vr.reasons)


def test_validate_sql_rejects_dml_and_comments():
    assert not sr.validate_gold_sql("SELECT 1; DROP TABLE x").ok
    assert not sr.validate_gold_sql("SELECT 1 -- sneaky").ok


def test_repair_sql_strips_comments_and_semicolons():
    bad = "SELECT amount FROM silver_x; -- trailing"
    fixed = sr.repair_sql(bad, "gold", ["x"])
    assert ";" not in fixed
    assert "--" not in fixed


def test_repair_loop_converges_on_gold():
    # Gold SQL that wrongly carries the silver latest_date filter.
    bad = "SELECT SUM(amount) AS t FROM silver_deals WHERE load_date = '{latest_date}'"
    outcome = sr.run_repair_loop(
        bad,
        sr.validate_gold_sql,
        lambda sql, reasons: sr.repair_sql(sql, "gold", reasons),
        max_attempts=3,
    )
    assert outcome["ok"] is True
    assert "{latest_date}" not in outcome["artifact"]
    assert outcome["attempts"] >= 2  # needed at least one repair


def test_repair_loop_aborts_on_noop():
    # A repair fn that changes nothing must abort, not spin.
    outcome = sr.run_repair_loop(
        "not sql at all",
        lambda a: sr.ValidationResult(False, ["always invalid"]),
        lambda a, reasons: a,  # no-op repair
        max_attempts=5,
    )
    assert outcome["ok"] is False
    assert any("no-op" in t.get("reasons", [""])[0] for t in outcome["trace"] if t.get("reasons"))


def test_validate_blueprint_on_real_autopilot_output():
    bp = ap.build_blueprint(
        cartridge_id="demo", name="Demo",
        entities=[{"name": "deals", "fields": [
            {"name": "deal_id", "type": "string", "primary_key": True},
            {"name": "amount", "type": "float"},
            {"name": "close_date", "type": "date"},
        ]}],
    )
    vr = sr.validate_blueprint(bp)
    assert vr.ok, vr.reasons


def test_validate_blueprint_detects_dangling_dag():
    bp = {
        "id": "x", "name": "X",
        "entities": [{"entity": "e", "dag_id": "ghost_dag"}],
        "dags": [{"dag_id": "real_dag"}],
        "datasets": [],
    }
    vr = sr.validate_blueprint(bp)
    assert not vr.ok
    assert any("unknown dag_id" in r for r in vr.reasons)


def test_silver_repair_now_converges():
    """Audit-7 gap: a Silver SQL missing {latest_date} must be REPAIRED (additive),
    not aborted as a no-op."""
    bad = "SELECT * FROM read_parquet('s3://x/**/*.parquet') WHERE amount > 0"
    outcome = sr.run_repair_loop(
        bad, sr.validate_silver_sql,
        lambda sql, reasons: sr.repair_sql(sql, "silver", reasons),
        max_attempts=3,
    )
    assert outcome["ok"] is True
    assert "{latest_date}" in outcome["artifact"]
    assert outcome["attempts"] >= 2


def test_silver_repair_no_where_clause():
    bad = "SELECT * FROM read_parquet('s3://x/**/*.parquet')"
    fixed = sr.repair_sql(bad, "silver", ["missing latest"])
    assert "{latest_date}" in fixed
    assert sr.validate_silver_sql(fixed).ok


def test_validate_blueprint_non_dict():
    assert not sr.validate_blueprint("nope").ok
    assert not sr.validate_blueprint(None).ok


def test_validate_blueprint_missing_keys():
    vr = sr.validate_blueprint({"id": "x"})
    assert not vr.ok
    assert any("missing manifest key" in r for r in vr.reasons)


def test_validate_blueprint_duplicate_datasets():
    bp = {
        "id": "x", "name": "X",
        "entities": [{"entity": "e", "dag_id": "d"}],
        "dags": [{"dag_id": "d"}],
        "datasets": [
            {"name": "dup", "layer": "silver", "sql": "SELECT * FROM read_parquet('s3://a/**/*.parquet') WHERE load_date='{latest_date}'"},
            {"name": "dup", "layer": "silver", "sql": "SELECT * FROM read_parquet('s3://b/**/*.parquet') WHERE load_date='{latest_date}'"},
        ],
    }
    vr = sr.validate_blueprint(bp)
    assert not vr.ok
    assert any("duplicate dataset" in r for r in vr.reasons)


def test_validate_blueprint_gold_unknown_source():
    bp = {
        "id": "x", "name": "X",
        "entities": [{"entity": "e", "dag_id": "d"}],
        "dags": [{"dag_id": "d"}],
        "datasets": [{"name": "g", "layer": "gold", "sources": ["ghost"], "sql": "SELECT 1 FROM ghost"}],
    }
    vr = sr.validate_blueprint(bp)
    assert not vr.ok
    assert any("unknown source" in r for r in vr.reasons)


def test_sql_validation_masks_string_literals():
    """A literal containing a forbidden keyword must NOT be flagged."""
    assert sr.validate_gold_sql("SELECT 'drop table x' AS note FROM silver_t").ok


def test_repair_runs_on_broken_gold_blueprint():
    """Non-tautological: feed a blueprint with a deliberately broken Gold SQL
    and assert the repair report shows it was actually fixed."""
    bp = {
        "id": "x", "name": "X",
        "entities": [{"entity": "e", "dag_id": "d"}],
        "dags": [{"dag_id": "d"}],
        "datasets": [{
            "name": "gold_broken", "layer": "gold", "sources": ["silver_e"],
            "sql": "SELECT SUM(amount) AS t FROM silver_e WHERE load_date = '{latest_date}'",
        }],
    }
    repaired = sr.repair_blueprint_sql(bp)
    rep = repaired["_selfrepair_report"][0]
    assert rep["ok"] is True
    assert rep["attempts"] >= 2  # needed a real repair
    assert "{latest_date}" not in repaired["datasets"][0]["sql"]


def test_repair_blueprint_sql_end_to_end():
    bp = ap.build_blueprint(
        cartridge_id="demo", name="Demo",
        entities=[{"name": "deals", "fields": [
            {"name": "deal_id", "type": "string", "primary_key": True},
            {"name": "amount", "type": "float"},
            {"name": "close_date", "type": "date"},
        ]}],
    )
    repaired = sr.repair_blueprint_sql(bp)
    assert "_selfrepair_report" in repaired
    assert all(r["ok"] for r in repaired["_selfrepair_report"])


def test_validator_aligned_with_engine_trailing_semicolon():
    """Audit-13: a trailing ';' must be REJECTED to match refinement.llm_sql
    (which rejects any ';'), so SQL that passes self-repair is never rejected by
    the real engine at create time."""
    assert not sr.validate_gold_sql("SELECT SUM(x) FROM silver_t GROUP BY 1;").ok


def test_validator_aligned_forbidden_keywords():
    """Audit-13: keyword set must match the engine (set/load/pragma/attach...)."""
    for kw_sql in ("SELECT set FROM t", "SELECT * FROM t WHERE load_date='{latest_date}' AND x IN (load)"):
        assert not sr.validate_gold_sql(kw_sql).ok


def test_literal_semicolon_not_false_positive_and_survives_repair():
    """Audit-13: a ';' inside a string literal is valid and must not be mangled."""
    sql = "SELECT * FROM read_parquet('s3://x/**/*.parquet') WHERE note='a;b' AND load_date='{latest_date}'"
    assert sr.validate_silver_sql(sql).ok
    repaired = sr.repair_sql("SELECT 'a;b' AS x FROM silver_t", "gold", ["x"])
    assert "'a;b'" in repaired  # literal preserved


def test_silver_additive_repair_appends_at_end_not_subquery():
    """Audit-13: the latest_date filter is appended at the end, not injected into
    a subquery's WHERE (which would corrupt scope)."""
    sql = ("SELECT a, (SELECT max(b) FROM read_parquet('s3://s/**/*.parquet') WHERE c=1) "
           "FROM read_parquet('s3://t/**/*.parquet')")
    fixed = sr.repair_sql(sql, "silver", ["missing latest"])
    # the appended filter must be after the outer FROM, i.e. at the tail
    assert fixed.rstrip().endswith("load_date = '{latest_date}'")


def test_validate_blueprint_rejects_unsafe_identifiers():
    """Audit-13 #4: entity/dataset names must be SQL-safe."""
    bp = {
        "id": "x", "name": "X",
        "entities": [{"entity": "bad name; drop", "dag_id": "d"}],
        "dags": [{"dag_id": "d"}],
        "datasets": [{"name": "1$weird", "layer": "silver",
                      "sql": "SELECT * FROM read_parquet('s3://a/**/*.parquet') WHERE load_date='{latest_date}'"}],
    }
    vr = sr.validate_blueprint(bp)
    assert not vr.ok
    assert any("unsafe" in r for r in vr.reasons)
