from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WISDOM_BITS = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "wisdom_bits.yaml"


def _wb_talento() -> dict:
    data = yaml.safe_load(WISDOM_BITS.read_text(encoding="utf-8")) or {}
    matches = [item for item in data.get("wisdom_bits", []) if item.get("id") == "WB-TALENTO"]
    assert len(matches) == 1
    return matches[0]


def test_wb_talento_privacy_and_defaults():
    wb = _wb_talento()

    assert wb["cartridge"] == "sap_successfactors"
    assert wb["decision_mode"] == "recommendation_only"
    assert wb["write_back_enabled"] is False
    assert wb["compensation_enabled"] is False
    assert wb["default_profile"] == {"industry": "retail", "company_profile": "femsa"}
    assert wb["pii_exposure"] == "masked"


def test_wb_talento_fit_score_contract():
    wb = _wb_talento()
    components = wb["fit_score"]["components"]

    assert components["competency"]["weight"] == 0.45
    assert components["performance"]["weight"] == 0.30
    assert components["aspiration"]["weight"] == 0.25
    assert round(sum(item["weight"] for item in components.values()), 2) == 1.0
    assert wb["fit_score"]["insufficient_data_status"] == "insufficient_data"
    assert wb["readiness"]["ready_min"] == 80
    assert wb["readiness"]["near_min"] == 60
    assert wb["action_policy"]["mode"] == "recommendation_only"
    assert wb["privacy"]["control_room_roster"] == "masked"


def test_wb_talento_signals_and_metadata_blockers():
    wb = _wb_talento()

    assert set(wb["signals"]) == {
        "riesgo_salida",
        "asignacion",
        "pipeline",
        "sesgo",
        "sensibilidad",
        "priorizacion",
    }
    blockers = {item["entity"] for item in wb["metadata_blockers"]}
    assert {"JobApplication", "PerformanceReview", "GoalPlan", "CompetencySkill"} <= blockers
    assert "sap_successfactors_talent_9box_operational" in set(wb["operational_outputs"])
