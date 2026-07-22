from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests.test_control_room_service import USER


@pytest.mark.asyncio
async def test_sap_successfactors_talent_kpis_returns_aggregates_without_pii(
    monkeypatch,
):
    calls: list[tuple[str, dict | None, int]] = []

    async def fake_rows(dataset: str, user: dict | None, limit: int) -> list[dict]:
        calls.append((dataset, user, limit))
        if dataset == "sap_successfactors_talent_employee_profile":
            return [
                {
                    "user_id": "100",
                    "full_name": "Ana Gomez",
                    "job_code": "MGR",
                    "blockers": '["KB-COMPETENCIAS blocked"]',
                },
                {
                    "user_id": "101",
                    "full_name": "Luis Perez",
                    "job_code": "REP",
                    "blockers": '["KB-DESEMPENO blocked"]',
                },
            ]
        if dataset == "sap_successfactors_talent_role_profile":
            return [
                {
                    "job_code": "MGR",
                    "role_name": "Manager",
                    "active_employee_count": 2,
                    "role_profile_status": "partial",
                    "required_skills_status": "blocked",
                    "blockers": '["Skills/competencies metadata pending"]',
                }
            ]
        if dataset == "sap_successfactors_talent_mobility_history":
            return [{"user_id": "100", "full_name": "Ana Gomez", "movement_events": 1}]
        if dataset == "sap_successfactors_talent_readiness":
            return [
                {
                    "user_id": "100",
                    "full_name": "Ana Gomez",
                    "readiness_status": "insufficient_data",
                },
                {
                    "user_id": "101",
                    "full_name": "Luis Perez",
                    "readiness_status": "insufficient_data",
                },
            ]
        if dataset == "sap_successfactors_talent_9box":
            return [
                {"user_id": "100", "full_name": "Ana Gomez", "box_status": "blocked"},
                {"user_id": "101", "full_name": "Luis Perez", "box_status": "blocked"},
            ]
        if dataset == "sap_successfactors_talent_signals":
            return [
                {
                    "signal_id": "talent_cpa_missing_inputs",
                    "signal_type": "priorizacion",
                    "severity": "medium",
                    "title": "Fit Score bloqueado",
                    "affected_count": 2,
                    "recommendation": "Habilitar C/P/A.",
                    "status": "recommendation_only",
                }
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_kpis(USER)

    assert result["profile"]["wisdom_bit"] == "WB-TALENTO"
    assert result["profile"]["decision_mode"] == "recommendation_only"
    assert result["profile"]["compensation_enabled"] is False
    assert result["profile"]["write_back_enabled"] is False
    assert result["readiness"]["profiled_employees"] == 2
    assert result["readiness"]["calculable_employees"] == 0
    assert result["readiness"]["insufficient_data_employees"] == 2
    assert result["readiness"]["nine_box_available"] == 0
    assert (
        next(w for w in result["widgets"] if w["id"] == "sf_talent_roles_profiled")[
            "value"
        ]
        == 1
    )
    assert result["signals"][0]["status"] == "recommendation_only"
    assert {dataset for dataset, _user, _limit in calls} == {
        "sap_successfactors_talent_employee_profile",
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_role_profile",
        "sap_successfactors_talent_mobility_history",
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
        "sap_successfactors_talent_signals",
        "sap_successfactors_talent_simulation_inputs",
        "sap_successfactors_talent_headcount_by_cohort_month",
        "sap_successfactors_talent_tenure_by_cohort_month",
        "sap_successfactors_talent_attrition_by_cohort_month",
    }
    payload_text = json.dumps(result, ensure_ascii=False)
    assert "Ana Gomez" not in payload_text
    assert "Luis Perez" not in payload_text


@pytest.mark.asyncio
async def test_sap_successfactors_talent_kpis_degrades_when_datasets_missing(
    monkeypatch,
):
    async def missing_rows(
        _dataset: str, _user: dict | None, _limit: int
    ) -> list[dict]:
        raise HTTPException(404, "dataset unavailable")

    monkeypatch.setattr(control_room_service, "query_dataset_rows", missing_rows)

    result = await control_room_service.sap_successfactors_talent_kpis(USER)

    assert result["connection_id"] == "femsa_sf"
    assert result["readiness"]["status"] == "partial"
    assert any(
        blocker["id"] == "talent_dataset_availability" for blocker in result["blockers"]
    )
    assert all(widget["status"] == "missing" for widget in result["widgets"])
