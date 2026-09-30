from __future__ import annotations

import pytest

from app.schemas.control_room_talent_responses import (
    ControlRoomTalentNineBoxResponse,
    ControlRoomTalentRosterResponse,
)
from app.schemas.control_room_public_projection import (
    project_public_control_room_response,
)
from app.services import control_room_service
from console.tests.test_control_room_service import USER


def _deduced_row(index: int) -> dict:
    return {
        "user_id": f"deduced-{index}",
        "box_key": "estrella",
        "box_label": "Estrella",
        "box_status": "ready",
        "source_mode": "cpa_real",
        "performance_score": 100.0,
        "potential_score": 83.75,
        "potential_basis": "trayectoria_observada",
        "deduced_potential": True,
        "invalid_score_input": False,
    }


def _cpa_row(index: int) -> dict:
    return {
        "user_id": f"cpa-{index}",
        "box_key": "core",
        "box_label": "Core",
        "box_status": "ready",
        "source_mode": "cpa_real",
        "performance_score": 60.0,
        "potential_score": 60.0,
        "potential_basis": "cpa_observado",
        "deduced_potential": False,
        "invalid_score_input": False,
    }


def _fake_rows_factory(extra: dict[str, list[dict]] | None = None):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box":
            return [_deduced_row(0), _cpa_row(0)]
        if dataset == "sap_successfactors_talent_9box_operational":
            return []
        return (extra or {}).get(dataset, [])

    return fake_rows


@pytest.mark.asyncio
async def test_deduced_rows_make_the_cpa_blocker_informative(monkeypatch):
    monkeypatch.setattr(
        control_room_service, "query_dataset_rows", _fake_rows_factory()
    )

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    assert result["status"] == "ready"
    assert result["totals"]["ready"] == 2
    assert result["totals"]["deduced"] == 1
    star = next(cell for cell in result["cells"] if cell["box_id"] == "estrella")
    assert star["deduced_count"] == 1
    core = next(cell for cell in result["cells"] if cell["box_id"] == "core")
    assert core["deduced_count"] == 0
    informative = [
        blocker
        for blocker in result["blockers"]
        if blocker["id"] == "talent_9box_cpa_incomplete"
    ]
    assert len(informative) == 1
    assert informative[0]["status"] == "informative"
    assert "trayectoria y desempeño real observado" in informative[0]["title"]
    assert "sin PII expuesta" in informative[0]["title"]
    projected = project_public_control_room_response(
        ControlRoomTalentNineBoxResponse, result
    ).model_dump()
    projected_titles = [blocker["title"] for blocker in projected["blockers"]]
    assert informative[0]["title"] in projected_titles


@pytest.mark.asyncio
async def test_without_deduced_rows_the_blocker_semantics_are_unchanged(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box":
            return [_cpa_row(0)]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    assert result["totals"]["deduced"] == 0
    assert not any(
        blocker["id"] == "talent_9box_cpa_incomplete" for blocker in result["blockers"]
    )


def test_deduced_row_never_launders_invalid_or_missing_performance():
    base = {
        "box_key": "estrella",
        "box_status": "ready",
        "potential_score": 83.75,
        "potential_basis": "trayectoria_observada",
        "deduced_potential": True,
        "invalid_score_input": False,
    }
    assert (
        control_room_service._sf_talent_nine_box_scores_valid(
            {**base, "performance_score": None}
        )
        is False
    )
    assert (
        control_room_service._sf_talent_nine_box_scores_valid(
            {**base, "performance_score": -1.0}
        )
        is False
    )
    assert (
        control_room_service._sf_talent_nine_box_scores_valid(
            {**base, "performance_score": 100.0, "invalid_score_input": True}
        )
        is False
    )
    assert (
        control_room_service._sf_talent_nine_box_scores_valid(
            {**base, "performance_score": 100.0}
        )
        is True
    )


def test_masked_roster_row_exposes_only_known_potential_basis():
    masked = control_room_service._sf_talent_masked_roster_row(_deduced_row(1))
    assert masked["potential_basis"] == "trayectoria_observada"
    invalid = control_room_service._sf_talent_masked_roster_row(
        {**_deduced_row(2), "potential_basis": "inventado", "deduced_potential": False}
    )
    assert invalid["potential_basis"] is None
    blocked = control_room_service._sf_talent_masked_roster_row(
        {**_deduced_row(3), "performance_score": None}
    )
    assert blocked["potential_basis"] is None


@pytest.mark.asyncio
async def test_confianza_renders_no_fabricated_zeros_for_empty_sources(monkeypatch):
    monkeypatch.setattr(
        control_room_service, "query_dataset_rows", _fake_rows_factory()
    )

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    confianza = result["confianza"]
    assert confianza["cobertura_certificaciones"] is None
    assert confianza["exposicion_monetaria"] is None
    assert confianza["estrellas_en_riesgo"] is None
    assert confianza["vacantes_criticas_sin_sucesor"] is None


@pytest.mark.asyncio
async def test_estrellas_needs_a_non_empty_materialized_nine_box(monkeypatch):
    extra = {
        "sap_successfactors_talent_retention_risk": [
            {
                "user_id": "deduced-0",
                "risk_band": "high",
                "retention_risk_score": 88.0,
                "invalid_score_input": False,
            }
        ],
    }

    async def empty_nine_box(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        return extra.get(dataset, [])

    monkeypatch.setattr(control_room_service, "query_dataset_rows", empty_nine_box)

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    assert result["confianza"]["estrellas_en_riesgo"] is None


@pytest.mark.asyncio
async def test_confianza_fields_are_null_when_gold_heads_are_missing(monkeypatch):
    from fastapi import HTTPException

    async def missing_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_9box":
            return [_deduced_row(0)]
        if dataset == "sap_successfactors_talent_9box_operational":
            return []
        raise HTTPException(404, f"dataset unavailable: {dataset}")

    monkeypatch.setattr(control_room_service, "query_dataset_rows", missing_rows)

    result = await control_room_service.sap_successfactors_talent_9box(USER)

    confianza = result["confianza"]
    assert confianza["estrellas_en_riesgo"] is None
    assert confianza["vacantes_criticas_sin_sucesor"] is None
    assert confianza["cobertura_certificaciones"] is None
    assert confianza["exposicion_monetaria"] is None
    projected = project_public_control_room_response(
        ControlRoomTalentNineBoxResponse, result
    )
    assert projected.model_dump()["confianza"] == {
        "estrellas_en_riesgo": None,
        "vacantes_criticas_sin_sucesor": None,
        "cobertura_certificaciones": None,
        "exposicion_monetaria": None,
    }


@pytest.mark.asyncio
async def test_confianza_computes_real_tiles_from_gold_aggregates(monkeypatch):
    extra = {
        "sap_successfactors_talent_retention_risk": [
            {
                "user_id": "deduced-0",
                "risk_band": "high",
                "retention_risk_score": 88.0,
                "invalid_score_input": False,
            },
            {
                "user_id": "cpa-0",
                "risk_band": "low",
                "retention_risk_score": 10.0,
                "invalid_score_input": False,
            },
            {
                "user_id": "invalid-risk",
                "risk_band": "high",
                "retention_risk_score": None,
                "invalid_score_input": True,
            },
        ],
        "sap_successfactors_talent_learning_certification_status": [
            {
                "learning_status": "completed",
                "learning_events": 60,
                "completed_events": 60,
            },
            {
                "learning_status": "overdue",
                "learning_events": 40,
                "completed_events": 0,
            },
        ],
        "sap_successfactors_talent_attrition_exposure": [
            {
                "risk_band": "high",
                "currency": "MXN",
                "headcount": 11,
                "annualized_comp_total": 1000000.0,
                "annualized_comp_avg": 91000.0,
            },
            {
                "risk_band": "medium",
                "currency": "USD",
                "headcount": 7,
                "annualized_comp_total": 700000.0,
                "annualized_comp_avg": 100000.0,
            },
            {
                "risk_band": "low",
                "currency": "MXN",
                "headcount": 4,
                "annualized_comp_total": 300000.0,
                "annualized_comp_avg": 75000.0,
            },
        ],
    }
    monkeypatch.setattr(
        control_room_service, "query_dataset_rows", _fake_rows_factory(extra)
    )

    result = await control_room_service.sap_successfactors_talent_9box(USER)
    confianza = result["confianza"]

    estrellas = confianza["estrellas_en_riesgo"]
    assert estrellas["count"] == 1
    assert estrellas["employee_keys"] == [
        control_room_service._sf_talent_employee_key("deduced-0")
    ]
    assert "deduced-0" not in str(estrellas)

    # Waiting state until succession is published as gold with real criticality.
    assert confianza["vacantes_criticas_sin_sucesor"] is None

    certificaciones = confianza["cobertura_certificaciones"]
    assert certificaciones == {
        "coverage_pct": 60.0,
        "completed_events": 60,
        "learning_events": 100,
    }

    exposicion = confianza["exposicion_monetaria"]
    totals = {
        (item["risk_band"], item["currency"]): item for item in exposicion["totals"]
    }
    assert totals[("high", "MXN")]["headcount"] == 11
    assert totals[("high", "MXN")]["annualized_comp_total"] == 1000000.0
    assert totals[("high", "MXN")]["annualized_comp_avg"] == 91000.0
    assert totals[("medium", "USD")]["annualized_comp_total"] == 700000.0
    assert totals[("medium", "USD")]["annualized_comp_avg"] == 100000.0
    assert len(totals) == 2

    projected = project_public_control_room_response(
        ControlRoomTalentNineBoxResponse, result
    )
    payload = projected.model_dump()
    assert payload["confianza"]["exposicion_monetaria"]["totals"]
    assert payload["totals"]["deduced"] == 1
    star = next(cell for cell in payload["cells"] if cell["box_id"] == "estrella")
    assert star["deduced_count"] == 1


@pytest.mark.asyncio
async def test_roster_projection_keeps_potential_basis(monkeypatch):
    monkeypatch.setattr(
        control_room_service, "query_dataset_rows", _fake_rows_factory()
    )

    result = await control_room_service.sap_successfactors_talent_9box_box(
        USER, "estrella"
    )
    projected = project_public_control_room_response(
        ControlRoomTalentRosterResponse, result
    )
    payload = projected.model_dump()
    assert payload["roster"]
    assert payload["roster"][0]["potential_basis"] == "trayectoria_observada"


def test_exposure_totals_never_gain_precision_when_rows_are_summed():
    exposicion = control_room_service._sf_talent_confianza_exposicion(
        {
            "status": "ready",
            "rows": [
                {"risk_band": "high", "currency": "MXN", "headcount": 7,
                 "annualized_comp_total": 2300000.0},
                {"risk_band": "high", "currency": "MXN", "headcount": 5,
                 "annualized_comp_total": 470000.0},
                {"risk_band": "low", "currency": "MXN", "headcount": 4,
                 "annualized_comp_total": 90000.0},
            ],
        }
    )

    (entry,) = exposicion["totals"]
    assert entry["headcount"] == 12
    assert entry["annualized_comp_total"] == 2800000.0
    assert entry["annualized_comp_avg"] == 230000.0
    assert control_room_service._sf_talent_round_significant(0) == 0.0
    assert control_room_service._sf_talent_round_significant(469135.0) == 470000.0
    assert control_room_service._sf_talent_round_significant(125000.0) == 130000.0
