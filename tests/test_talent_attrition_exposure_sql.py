from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATASET = (
    ROOT
    / "cartridges/sap_successfactors/datasets"
    / "sap_successfactors_talent_attrition_exposure.sql"
)
MATERIALIZER = ROOT / "refinement/app/successfactors_exposure_materializer.py"
EXPECTED_SOURCES = [
    "silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest",
    "silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest",
    "gold/sap_successfactors/sap_successfactors_talent_retention_risk",
]
EXPECTED_COLUMNS = [
    "risk_band",
    "currency",
    "headcount",
    "annualized_comp_total",
    "annualized_comp_avg",
    "excluded_undecryptable",
    "excluded_unknown_frequency",
    "excluded_invalid_amount",
    "excluded_non_recurring_365d",
    "privacy_rule",
    "contract_version",
    "generated_at",
]


def _text() -> str:
    return DATASET.read_text(encoding="utf-8")


def _body() -> str:
    return "\n".join(
        line for line in _text().splitlines() if not line.lstrip().startswith("--")
    )


def test_contract_sql_yields_zero_rows_with_the_published_columns():
    con = duckdb.connect()
    try:
        result = con.execute(_text())
        columns = [item[0] for item in result.description]
        rows = result.fetchall()
    finally:
        con.close()
    assert rows == []
    assert columns == EXPECTED_COLUMNS


def test_contract_sql_never_reads_or_names_encrypted_compensation():
    body = _body().lower()
    assert "read_parquet" not in body
    assert "paycomp" not in body
    assert "s3://" not in body
    assert "paycomp_value" not in _text().lower()
    assert "user_id" not in _text().lower()


def test_contract_sql_delegates_to_the_python_aggregate_materializer():
    text = _text()
    assert "managed_by_sap_successfactors_exposure_materializer" in text
    assert re.search(r"\bWHERE\s+FALSE\b", _body())
    assert "'aggregate_min5_dominance50_unitmax' AS privacy_rule" in text
    assert "'talent_attrition_exposure.v4' AS contract_version" in text
    sources = json.loads(
        re.match(r"^--\s+sources:\s+(\[.*\])\s*$", text.splitlines()[1]).group(1)
    )
    assert sources == EXPECTED_SOURCES
    materializer = MATERIALIZER.read_text(encoding="utf-8")
    for source in EXPECTED_SOURCES:
        assert f'"{source}"' in materializer
    assert "MIN_GROUP_SIZE = 5" in materializer
    assert 'DOMINANCE_SHARE = Decimal("0.5")' in materializer
    assert "SIGNIFICANT_DIGITS = 2" in materializer


def test_decryption_never_becomes_a_sql_function():
    materializer = MATERIALIZER.read_text(encoding="utf-8")
    for forbidden in ("create_function", ".register(", "CREATE MACRO", "CREATE FUNCTION"):
        assert forbidden not in materializer
