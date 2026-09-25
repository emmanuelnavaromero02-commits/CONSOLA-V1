from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API = REPO_ROOT / "console" / "app" / "services" / "control_room" / "api.py"
FETCHER = (
    REPO_ROOT / "console" / "app" / "services" / "intelligence" / "gold_fetcher.py"
)
POPULATION = (
    REPO_ROOT / "console" / "app" / "services" / "intelligence"
    / "successfactors_talent_population.py"
)


def _kpi_metrics():
    src = API.read_text(encoding="utf-8")

    def _fn(name: str) -> str:
        match = re.search(
            rf"^def {name}\(.*?(?=^@_bind_to_core|^async def |^def )",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert match, name
        return match.group(0)

    namespace: dict = {
        "Any": object,
        "_bind_to_core": lambda fn: fn,
    }
    exec(  # noqa: S102 — controlled source, test isolation
        "import math\nfrom decimal import Decimal\nfrom typing import Any\n"
        + _fn("_sf_talent_int")
        + _fn("_sf_talent_float")
        + _fn("_sf_talent_score")
        + _fn("_sf_talent_status")
        + _fn("_sf_talent_cpa_scores_valid")
        + _fn("_sf_talent_nine_box_scores_valid")
        + _fn("_sf_talent_readiness_row_valid")
        + _fn("_sf_talent_readiness_counts")
        + _fn("_sf_talent_nine_box_available_count")
        + _fn("_sf_talent_profiled_count")
        + _fn("_sf_talent_first_int")
        + _fn("_sf_talent_source_mode")
        + _fn("_sf_talent_kpi_metrics"),
        namespace,
        namespace,
    )
    return namespace["_sf_talent_kpi_metrics"]


def _capped_rows(n: int) -> list[dict]:
    return [
        {
            "invalid_score_input": False,
            "readiness_status": "ready",
            "source_mode": "cpa_real",
            "readiness_score": 80.0,
            "competency_score": 80.0,
            "performance_score": 80.0,
            "aspiration_score": 80.0,
            "box_status": "ready",
            "potential_score": 75.0,
        }
        for _ in range(n)
    ]


def _rows_bundle(readiness_rows: list[dict]) -> dict:
    return {
        "profile_rows": [],
        "role_rows": [],
        "mobility_rows": [],
        "readiness_rows": readiness_rows,
        "nine_box_rows": readiness_rows,
        "signal_rows": [],
        "operational_row": {},
        "simulation_row": {},
    }


def test_population_totals_override_the_capped_row_count():
    metrics_fn = _kpi_metrics()
    capped = _rows_bundle(_capped_rows(5000))
    population = {
        "status": "ready",
        "readiness_calculable": 6000,
        "readiness_insufficient": 10,
        "nine_box_available": 6000,
    }
    metrics = metrics_fn(
        capped, {"operational_features": {"status": "ready"}}, population
    )
    assert metrics["readiness_calculable"] == 6000, (
        "population total must come from SQL COUNT, not the capped read"
    )
    assert metrics["readiness_insufficient"] == 10
    assert metrics["nine_box_available"] == 6000


def test_population_totals_fall_back_when_sql_unavailable():
    metrics_fn = _kpi_metrics()
    capped = _rows_bundle(_capped_rows(120))
    metrics = metrics_fn(
        capped,
        {"operational_features": {"status": "ready"}},
        {"status": "unavailable"},
    )
    assert metrics["readiness_calculable"] == 120
    assert metrics["nine_box_available"] == 120


def test_nine_box_cells_rebuild_from_full_detail_in_sql():
    src = API.read_text(encoding="utf-8")
    fn = re.search(
        r"async def sap_successfactors_talent_9box\(.*?\n(?=@_bind_to_core)",
        src,
        re.DOTALL,
    )
    assert fn, "talent 9box surface not found"
    body = fn.group(0)
    assert "_sf_talent_nine_box_cell_counts(user)" in body
    assert "population_totals_source" in body
    assert '"sql_population"' in body and '"detail_capped"' in body
    assert "_sf_talent_9box_operational_rows_from_detail(raw_detail_rows)" in body, (
        "the capped detail rebuild must stay as the fallback"
    )
    assert "aggregate_rows or " not in body, (
        "the materialized aggregate must stay display-only evidence"
    )
    assert "_sf_talent_desempeno_cohort_counts(user)" in body, (
        "cohort count/bands must come from full-population SQL"
    )
    assert 'desempeno_disponible["roster_truncated"]' in body


def test_box_roster_count_is_full_population_with_truncation_flag():
    src = API.read_text(encoding="utf-8")
    fn = re.search(
        r"async def sap_successfactors_talent_9box_box\(.*?\n(?=@_bind_to_core)",
        src,
        re.DOTALL,
    )
    assert fn, "talent 9box box surface not found"
    body = fn.group(0)
    assert "_sf_talent_nine_box_box_count(user, box_id)" in body
    assert '"count": count' in body and '"count": len(rows)' not in body, (
        "the cell total must be the SQL COUNT, not len over the capped read"
    )
    assert '"roster_truncated": count > len(roster)' in body


def test_preview_cap_stays_and_is_documented_as_preview_only():
    src = FETCHER.read_text(encoding="utf-8")
    assert "safe_limit = max(1, min(int(limit or 5000), 5000))" in src, (
        "the preview cap must stay — raising it is a memory band-aid"
    )
    cap_at = src.index("safe_limit = max(1, min(int(limit or 5000), 5000))")
    assert "PREVIEW/ROSTER" in src[:cap_at], "cap must be documented as preview-only"


def test_population_engine_preserves_the_authority_model():
    src = POPULATION.read_text(encoding="utf-8")
    assert "_valid_authority" in src
    assert "benchmark_materialization_head" in src
    assert 'return "FALSE"' in src, "missing binding columns must fail closed"
    assert "resolve_benchmark_approval_authority" in src
    assert not re.search(r"\bLIMIT\s+(\$\d+|\d)", src), (
        "population aggregation must be cap-free (no SQL LIMIT clause)"
    )
