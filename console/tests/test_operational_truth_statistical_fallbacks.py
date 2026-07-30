from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from app.services.control_room import business_omega_sections
from app.services.control_room.business_omega_sections import omega_options
from app.services.intelligence import scoring
from app.services.intelligence.scoring import decision_options


def test_productive_option_fallbacks_do_not_reintroduce_fixed_scores():
    source = "\n".join(
        Path(module.__file__).read_text(encoding="utf-8")
        for module in (business_omega_sections, scoring)
    )

    assert not re.search(r"['\"]score['\"]\s*:\s*(?:92|68|45)\b", source)


def test_omega_without_analytical_options_has_no_decorative_fallbacks():
    options, selected, intelligence = omega_options(
        {},
        {"status": "unavailable", "estimate": None},
        decision_intelligence={},
    )

    assert options == []
    assert selected is None
    assert intelligence == {}


@pytest.mark.parametrize(
    "score", [None, math.nan, math.inf, -math.inf, "unknown", True]
)
def test_omega_drops_options_without_a_finite_analytical_score(score):
    options, selected, _intelligence = omega_options(
        {
            "intelligence": {
                "options": [
                    {
                        "option_id": "incomplete",
                        "label": "Incomplete analytical option",
                        "score": score,
                    }
                ]
            }
        },
        {},
        decision_intelligence={},
    )

    assert options == []
    assert selected is None


def test_omega_does_not_zero_fill_missing_option_fields_or_invent_usd():
    options, selected, _intelligence = omega_options(
        {
            "intelligence": {
                "options": [
                    {
                        "option_id": "review",
                        "label": "Review evidence",
                        "action_kind": "owner_review",
                        "score": 12,
                    }
                ]
            }
        },
        {},
        decision_intelligence={},
    )

    assert selected is None
    assert options == [
        {
            "id": "review",
            "label": "Review evidence",
            "action": "owner_review",
            "money": None,
            "time": None,
            "score": 12,
            "risk": None,
            "auto": False,
            "recommendation": "",
            "selected": False,
        }
    ]


def test_omega_preserves_real_zero_values_without_inventing_currency():
    options, selected, _intelligence = omega_options(
        {
            "intelligence": {
                "options": [
                    {
                        "option_id": "monitor",
                        "label": "Monitor",
                        "action_kind": "monitor",
                        "impact_expected": 0,
                        "time_cost": 0,
                        "score": 0,
                        "risk": 0,
                        "selected": True,
                    }
                ]
            }
        },
        {},
        decision_intelligence={},
    )

    assert selected == "monitor"
    assert options[0]["score"] == 0
    assert options[0]["money"] == "0 esperado"
    assert "USD" not in options[0]["money"]
    assert options[0]["time"] == "0 puntos tiempo"
    assert options[0]["risk"] == "0"


@pytest.mark.parametrize("missing", ["option_id", "label", "action_kind"])
def test_omega_drops_options_without_explicit_identity_or_action(missing):
    option = {
        "option_id": "review",
        "label": "Review evidence",
        "action_kind": "owner_review",
        "score": 12,
    }
    option.pop(missing)

    options, selected, _intelligence = omega_options(
        {"intelligence": {"options": [option]}},
        {},
        decision_intelligence={},
    )

    assert options == []
    assert selected is None


def test_omega_does_not_select_first_analytical_option_implicitly():
    options, selected, _intelligence = omega_options(
        {
            "intelligence": {
                "options": [
                    {
                        "option_id": "review",
                        "label": "Review evidence",
                        "action_kind": "owner_review",
                        "score": 12,
                    }
                ]
            }
        },
        {},
        decision_intelligence={},
    )

    assert selected is None
    assert options[0]["selected"] is False


def test_omega_honors_explicit_analytical_selection():
    options, selected, _intelligence = omega_options(
        {
            "intelligence": {
                "options": [
                    {
                        "option_id": "review",
                        "label": "Review evidence",
                        "action_kind": "owner_review",
                        "score": 12,
                        "selected": True,
                    }
                ]
            }
        },
        {},
        decision_intelligence={},
    )

    assert selected == "review"
    assert options[0]["selected"] is True


def test_omega_honors_matching_persisted_selection():
    options, selected, _intelligence = omega_options(
        {
            "selected_option_id": "review",
            "intelligence": {
                "options": [
                    {
                        "option_id": "review",
                        "label": "Review evidence",
                        "action_kind": "owner_review",
                        "score": 12,
                    }
                ]
            },
        },
        {},
        decision_intelligence={},
    )

    assert selected == "review"
    assert options[0]["selected"] is True


@pytest.mark.parametrize("missing", ["deviation_value", "confidence"])
def test_scoring_requires_observed_signal_numbers(missing):
    signal = {"deviation_value": 10, "confidence": 0.8}
    signal.pop(missing)

    assert decision_options(signal, _metric()) == []


def test_scoring_requires_explicit_unit_value():
    metric = _metric()
    metric["impact"] = {}

    assert decision_options({"deviation_value": 10, "confidence": 0.8}, metric) == []


@pytest.mark.parametrize(
    "missing",
    ["impact_multiplier", "cost", "risk", "time_cost"],
)
def test_scoring_drops_templates_with_missing_numeric_inputs(missing):
    metric = _metric()
    metric["action_templates"][0].pop(missing)

    assert decision_options({"deviation_value": 10, "confidence": 0.8}, metric) == []


@pytest.mark.parametrize("missing", ["id", "label", "action_kind"])
def test_scoring_drops_templates_without_explicit_identity_or_action(missing):
    metric = _metric()
    metric["action_templates"][0].pop(missing)

    assert decision_options({"deviation_value": 10, "confidence": 0.8}, metric) == []


def test_scoring_preserves_real_zero_confidence_and_unit_value():
    metric = _metric()
    metric["impact"]["unit_value"] = 0

    options = decision_options(
        {"deviation_value": 10, "confidence": 0},
        metric,
    )

    assert len(options) == 1
    assert options[0]["impact_expected"] == 0
    assert options[0]["confidence"] == 0
    assert options[0]["cost"] == 0
    assert options[0]["risk"] == 0
    assert options[0]["time_cost"] == 0
    assert options[0]["score"] == 0


def _metric() -> dict:
    return {
        "impact": {"unit_value": 2},
        "action_templates": [
            {
                "id": "review",
                "label": "Review",
                "action_kind": "owner_review",
                "impact_multiplier": 1,
                "cost": 0,
                "risk": 0,
                "time_cost": 0,
            }
        ],
    }
