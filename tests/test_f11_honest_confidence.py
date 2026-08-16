"""F11 — absent confidence stays absent: never a fabricated 0.7/0.6.

The declared per-rule constants (0.78, 0.74, 0.25 ...) are real, reviewed
numbers and stay. What must never happen again is `or 0.7` / `or 0.6`
papering over data that nobody computed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "console"))

from app.services.control_room.business_impact_projection import (  # noqa: E402
    build_impact_payload,
)
from app.services.control_room.business_impact_rules import (  # noqa: E402
    calculate_item_impact,
)

STATE = REPO_ROOT / "console" / "app" / "services" / "control_room" / "state.py"
RULES = (
    REPO_ROOT / "console" / "app" / "services" / "control_room"
    / "business_impact_rules.py"
)
MIGRATION = (
    REPO_ROOT / "infra" / "init"
    / "99zzzzb_control_room_lessons_honest_confidence.sql"
)

_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _payload(**kwargs):
    kwargs.setdefault("currency", "USD")
    return build_impact_payload(severity_weights=_WEIGHTS, **kwargs)


def _number(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def test_absent_confidence_is_null_not_a_default():
    item = {"id": "i-1", "severity": "high", "impact_estimate": 1200.0}
    impact = calculate_item_impact(item, number=_number, payload=_payload)
    assert impact["status"] == "ok"
    assert impact["confidence"] is None, (
        "no computed confidence -> null, never a fabricated 0.6"
    )


def test_real_confidence_still_flows_through():
    item = {
        "id": "i-2",
        "severity": "high",
        "impact_estimate": 1200.0,
        "confidence": 0.9,
    }
    impact = calculate_item_impact(item, number=_number, payload=_payload)
    assert impact["confidence"] == 0.9


def test_null_confidence_contributes_nothing_to_priority():
    base = {"id": "i-3", "severity": "medium"}
    with_conf = _payload(
        item=base, estimate=50_000.0, status="ok", confidence=0.9,
        drivers=[], formula="f", explanation="e",
    )
    without = _payload(
        item=base, estimate=50_000.0, status="ok", confidence=None,
        drivers=[], formula="f", explanation="e",
    )
    assert without["confidence"] is None
    assert without["priority_score"] == with_conf["priority_score"] - int(0.9 * 20)


def test_fabricated_defaults_are_gone_from_the_writers():
    state = STATE.read_text(encoding="utf-8")
    assert 'get("confidence") or 0.7' not in state, (
        "lessons writer must persist NULL when no confidence exists"
    )
    rules = RULES.read_text(encoding="utf-8")
    assert re.search(r'confidence=number\(item\.get\("confidence"\)\)\s*or', rules) is None, (
        "stored-impact confidence must never fall back to an invented 0.6"
    )
    # The declared, reviewed per-rule constants stay untouched.
    assert "confidence=0.25" in rules


def test_lessons_confidence_migration_drops_the_fabricated_default():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER COLUMN confidence DROP NOT NULL" in sql
    assert "ALTER COLUMN confidence DROP DEFAULT" in sql
    assert "99zzzzb_control_room_lessons_honest_confidence.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert MIGRATION.name > "99zzzza_sap_successfactors_cycle_config.sql", (
        "must sort after A2's migration on fresh installs"
    )
