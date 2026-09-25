from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ANOMALIES_SQL = (
    REPO
    / "cartridges"
    / "sap_successfactors"
    / "datasets"
    / "sap_successfactors_employees_anomalies.sql"
)
CORE = REPO / "console" / "app" / "services" / "control_room" / "core.py"
BUSINESS_IMPACT = (
    REPO / "console" / "app" / "services" / "control_room" / "business_impact_rules.py"
)

EXPECTED_TOKENS = {"missing_department", "missing_manager", "invalid_job_code"}


def _sql_anomaly_tokens() -> set[str]:
    sql = ANOMALIES_SQL.read_text(encoding="utf-8")
    return set(re.findall(r"'(\w+)'\s+AS anomaly_type", sql))


def _business_impact_sf_tokens() -> set[str]:
    text = BUSINESS_IMPACT.read_text(encoding="utf-8")
    block = re.search(
        r'cartridge == "sap_successfactors" and anomaly_type in \{([^}]*)\}',
        text,
    )
    assert block, "SF business-impact branch not found (refactor drift)"
    return set(re.findall(r'"(\w+)"', block.group(1)))


def test_sql_emits_exactly_the_expected_anomaly_tokens() -> None:
    assert _sql_anomaly_tokens() == EXPECTED_TOKENS


def test_business_impact_rules_recognize_every_sql_token() -> None:
    sql_tokens = _sql_anomaly_tokens()
    rule_tokens = _business_impact_sf_tokens()
    unmatched = sql_tokens - rule_tokens
    assert not unmatched, (
        "business_impact_rules SF branch does not recognize anomaly types "
        f"{sorted(unmatched)} — a $ estimate can never reach them. "
        "Reconcile the token in business_impact_rules.py."
    )


def test_core_registry_has_a_label_for_every_sql_token() -> None:
    core = CORE.read_text(encoding="utf-8")
    for token in _sql_anomaly_tokens():
        assert re.search(rf'"{token}"\s*:\s*\{{', core), (
            f"core.py anomaly registry has no label entry for '{token}'"
        )
