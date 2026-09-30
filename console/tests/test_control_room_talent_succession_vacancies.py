from __future__ import annotations

import pytest

from app.schemas.control_room_public_projection import (
    project_public_control_room_response,
)
from app.schemas.control_room_talent_responses import ControlRoomTalentNineBoxResponse
from app.services import control_room_service
from console.tests.test_control_room_service import USER

SUCCESSION = "sap_successfactors_talent_succession_coverage"
_DATASET_DEFAULTS = {
    "positions_total": 6,
    "positions_inactive_count": 0,
    "criticality_available": True,
    "criticality_unrecognized_count": 0,
    "criticality_missing_count": 1,
    "critical_total": 4,
    "critical_without_nominee_total": 2,
    "critical_coverage_unknown_count": 0,
    "nominations_available": True,
    "nominations_total": 3,
    "nominations_matched": 3,
    "nominations_unmatched_open": 0,
}


def _position(
    position_id: str | None,
    name: str | None,
    *,
    is_critical: bool | None,
    has_active_nominee: bool | None,
    criticality: str | None = "High",
    **dataset: object,
) -> dict:
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "position_id": position_id,
        "position_name": name,
        "department": "Ventas",
        "criticality": criticality,
        "is_critical": is_critical,
        "has_active_nominee": has_active_nominee,
        "nominee_count": 1 if has_active_nominee else 0,
        **{**_DATASET_DEFAULTS, **dataset},
    }


def _feed(succession: list[dict] | Exception):
    seen: dict[str, int] = {}

    async def fake_rows(dataset: str, _user: dict | None, limit: int) -> list[dict]:
        seen[dataset] = limit
        if dataset == "sap_successfactors_talent_9box":
            return [
                {
                    "user_id": "cpa-0",
                    "box_key": "core",
                    "box_status": "ready",
                    "source_mode": "cpa_real",
                    "performance_score": 60.0,
                    "potential_score": 60.0,
                    "invalid_score_input": False,
                }
            ]
        if dataset == SUCCESSION:
            if isinstance(succession, Exception):
                raise succession
            return succession
        return []

    return fake_rows, seen


async def _confianza(monkeypatch, succession: list[dict] | Exception) -> tuple[dict, dict]:
    fake_rows, seen = _feed(succession)
    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)
    result = await control_room_service.sap_successfactors_talent_9box(USER)
    projected = project_public_control_room_response(
        ControlRoomTalentNineBoxResponse, result
    ).model_dump()
    assert seen.get(SUCCESSION) == 50
    return result["confianza"], projected["confianza"]


async def _waiting(monkeypatch, rows: list[dict] | Exception) -> str:
    confianza, projected = await _confianza(monkeypatch, rows)
    assert confianza["vacantes_criticas_sin_sucesor"] is None
    assert projected["vacantes_criticas_sin_sucesor"] is None
    assert projected["vacantes_criticas_motivo"] == confianza["vacantes_criticas_motivo"]
    return confianza["vacantes_criticas_motivo"]


def _known_rows(**dataset: object) -> list[dict]:
    return [
        _position("P1", "Gerente de Planta", is_critical=True, has_active_nominee=False, **dataset),
        _position("P2", "Jefe de Compras", is_critical=True, has_active_nominee=False, **dataset),
        _position("P3", "Director de Finanzas", is_critical=True, has_active_nominee=True, **dataset),
        _position("P4", "Director Comercial", is_critical=True, has_active_nominee=True, **dataset),
        _position("P5", "Analista", is_critical=False, has_active_nominee=False, criticality="Low", **dataset),
        _position("P6", "Auxiliar", is_critical=None, has_active_nominee=False, criticality=None, **dataset),
    ]


@pytest.mark.asyncio
async def test_real_count_comes_from_critical_positions_without_active_successor(monkeypatch):
    confianza, projected = await _confianza(monkeypatch, _known_rows())

    assert confianza["vacantes_criticas_motivo"] is None
    assert confianza["vacantes_criticas_sin_sucesor"] == {
        "count": 2,
        "roles": ["Gerente de Planta", "Jefe de Compras"],
        "posiciones_sin_criticidad": 1,
    }
    assert projected["vacantes_criticas_sin_sucesor"] == {
        "count": 2,
        "roles": ["Gerente de Planta", "Jefe de Compras"],
        "posiciones_sin_criticidad": 1,
    }
    assert projected["vacantes_criticas_motivo"] is None


@pytest.mark.asyncio
async def test_count_uses_the_dataset_total_from_a_small_read(monkeypatch):
    rows = [
        _position("P1", "Gerente de Planta", is_critical=True, has_active_nominee=False,
                  critical_total=40, critical_without_nominee_total=37, positions_total=90),
        _position("P2", "Gerente de Planta", is_critical=True, has_active_nominee=False,
                  critical_total=40, critical_without_nominee_total=37, positions_total=90),
        _position("P3", None, is_critical=True, has_active_nominee=False,
                  critical_total=40, critical_without_nominee_total=37, positions_total=90),
    ]

    confianza, _projected = await _confianza(monkeypatch, rows)

    assert confianza["vacantes_criticas_sin_sucesor"] == {
        "count": 37,
        "roles": ["Gerente de Planta"],
        "posiciones_sin_criticidad": 1,
    }


@pytest.mark.asyncio
async def test_zero_is_shown_only_when_every_critical_position_is_known_covered(monkeypatch):
    rows = [
        _position("P1", "Gerente", is_critical=True, has_active_nominee=True, criticality_missing_count=0,
                  critical_total=1, critical_without_nominee_total=0, positions_total=2),
        _position("P2", "Analista", is_critical=False, has_active_nominee=False, criticality="Low",
                  criticality_missing_count=0,
                  critical_total=1, critical_without_nominee_total=0, positions_total=2),
    ]

    confianza, _projected = await _confianza(monkeypatch, rows)

    assert confianza["vacantes_criticas_motivo"] is None
    assert confianza["vacantes_criticas_sin_sucesor"] == {
        "count": 0,
        "roles": [],
        "posiciones_sin_criticidad": 0,
    }


@pytest.mark.asyncio
async def test_unknown_nomination_status_waits_instead_of_a_false_zero(monkeypatch):
    rows = [
        _position(pid, name, is_critical=True, has_active_nominee=None,
                  critical_total=2, critical_without_nominee_total=None, critical_coverage_unknown_count=2)
        for pid, name in (("P1", "Gerente"), ("P2", "Jefe"))
    ]

    assert await _waiting(monkeypatch, rows) == "estado_nominacion_no_reconocido"


@pytest.mark.asyncio
async def test_one_unknown_status_blocks_the_whole_count(monkeypatch):
    rows = _known_rows(critical_without_nominee_total=None, critical_coverage_unknown_count=1)

    assert await _waiting(monkeypatch, rows) == "estado_nominacion_no_reconocido"


@pytest.mark.asyncio
async def test_positions_without_criticality_values_wait_with_their_cause(monkeypatch):
    rows = [
        _position("P1", "Gerente", is_critical=None, has_active_nominee=None, criticality=None,
                  criticality_available=False, critical_total=0, critical_without_nominee_total=None)
    ]

    assert await _waiting(monkeypatch, rows) == "criticidad_no_encontrada"


@pytest.mark.asyncio
async def test_partially_mapped_criticality_never_publishes_a_count(monkeypatch):
    rows = _known_rows(criticality_unrecognized_count=1, critical_without_nominee_total=None)

    assert await _waiting(monkeypatch, rows) == "criticidad_no_reconocida"


@pytest.mark.asyncio
async def test_absent_nomination_source_asks_for_succession_extraction(monkeypatch):
    rows = [
        _position("P1", "Gerente", is_critical=True, has_active_nominee=None,
                  nominations_available=False, nominations_total=0, nominations_matched=0,
                  critical_total=1, critical_without_nominee_total=None, critical_coverage_unknown_count=1)
    ]

    assert await _waiting(monkeypatch, rows) == "sucesion_no_extraida"


@pytest.mark.asyncio
async def test_no_visible_nominations_do_not_mark_every_critical_position(monkeypatch):
    rows = [
        _position("P1", "Gerente", is_critical=True, has_active_nominee=None,
                  nominations_total=0, nominations_matched=0,
                  critical_total=1, critical_without_nominee_total=None, critical_coverage_unknown_count=1)
    ]

    assert await _waiting(monkeypatch, rows) == "sin_nominaciones"


@pytest.mark.asyncio
async def test_nominations_that_match_no_position_wait(monkeypatch):
    rows = [
        _position("1001", "Gerente", is_critical=True, has_active_nominee=None,
                  nominations_total=1, nominations_matched=0, nominations_unmatched_open=1,
                  critical_total=1, critical_without_nominee_total=None, critical_coverage_unknown_count=1)
    ]

    assert await _waiting(monkeypatch, rows) == "nominaciones_sin_cruce"


@pytest.mark.asyncio
async def test_partial_match_waits_while_it_leaves_a_critical_position_ambiguous(monkeypatch):
    rows = _known_rows(
        nominations_matched=2,
        nominations_unmatched_open=1,
        critical_without_nominee_total=None,
        critical_coverage_unknown_count=2,
    )

    assert await _waiting(monkeypatch, rows) == "nominaciones_cruce_parcial"


@pytest.mark.asyncio
async def test_partial_match_counts_when_every_critical_position_is_decided(monkeypatch):
    rows = _known_rows(nominations_matched=2, nominations_unmatched_open=0)

    confianza, _projected = await _confianza(monkeypatch, rows)

    assert confianza["vacantes_criticas_motivo"] is None
    assert confianza["vacantes_criticas_sin_sucesor"]["count"] == 2


@pytest.mark.asyncio
async def test_only_inactive_positions_wait_with_their_own_cause(monkeypatch):
    sentinel = _position(
        None, None, is_critical=None, has_active_nominee=None, criticality=None,
        positions_total=0, positions_inactive_count=4, criticality_available=False,
        criticality_missing_count=0, critical_total=0, critical_without_nominee_total=None,
    )

    assert await _waiting(monkeypatch, [sentinel]) == "sin_posiciones_activas"


@pytest.mark.asyncio
async def test_missing_criticality_without_critical_positions_never_reports_zero(monkeypatch):
    rows = [
        _position("P1", "Analista", is_critical=False, has_active_nominee=False, criticality="Low",
                  positions_total=2, criticality_missing_count=1, critical_total=0,
                  critical_without_nominee_total=None),
        _position("P2", "Auxiliar", is_critical=None, has_active_nominee=None, criticality=None,
                  positions_total=2, criticality_missing_count=1, critical_total=0,
                  critical_without_nominee_total=None),
    ]

    assert await _waiting(monkeypatch, rows) == "criticidad_incompleta"


@pytest.mark.asyncio
async def test_covered_critical_position_and_one_without_criticality_waits(monkeypatch):
    common = {
        "positions_total": 2,
        "criticality_missing_count": 1,
        "critical_total": 1,
        "critical_without_nominee_total": None,
        "nominations_total": 1,
        "nominations_matched": 1,
    }
    rows = [
        _position("P1", "Gerente", is_critical=True, has_active_nominee=True, **common),
        _position("P2", "Jefe", is_critical=None, has_active_nominee=False, criticality=None, **common),
    ]

    assert await _waiting(monkeypatch, rows) == "criticidad_incompleta"


@pytest.mark.asyncio
async def test_a_zero_is_never_shown_while_positions_lack_criticality(monkeypatch):
    rows = _known_rows(critical_without_nominee_total=0, criticality_missing_count=1)

    assert await _waiting(monkeypatch, rows) == "criticidad_incompleta"


@pytest.mark.asyncio
async def test_undecided_gold_total_is_never_replaced_by_a_console_number(monkeypatch):
    rows = _known_rows(critical_without_nominee_total=None, criticality_missing_count=0)

    assert await _waiting(monkeypatch, rows) == "sucesion_no_disponible"


@pytest.mark.asyncio
async def test_positions_without_criticality_are_reported_next_to_the_count(monkeypatch):
    rows = _known_rows(criticality_missing_count=1)

    _confianza_raw, projected = await _confianza(monkeypatch, rows)

    assert projected["vacantes_criticas_sin_sucesor"]["count"] == 2
    assert projected["vacantes_criticas_sin_sucesor"]["posiciones_sin_criticidad"] == 1


@pytest.mark.asyncio
async def test_empty_projection_means_no_positions_were_extracted(monkeypatch):
    assert await _waiting(monkeypatch, []) == "posiciones_no_extraidas"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "motivo"),
    [
        (404, "sucesion_no_calculada"),
        (403, "sin_permiso"),
        (503, "sucesion_no_disponible"),
    ],
)
async def test_unreadable_projection_names_the_cause(monkeypatch, status_code, motivo):
    from fastapi import HTTPException

    confianza, projected = await _confianza(
        monkeypatch, HTTPException(status_code, f"dataset unavailable: {SUCCESSION}")
    )

    assert confianza["vacantes_criticas_sin_sucesor"] is None
    assert confianza["vacantes_criticas_motivo"] == motivo
    assert projected["vacantes_criticas_motivo"] == motivo
    assert SUCCESSION not in str(projected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        ({"critical_without_nominee_total": 2}, {"critical_without_nominee_total": 3}),
        ({"critical_without_nominee_total": -1}, {"critical_without_nominee_total": -1}),
        ({"critical_without_nominee_total": True}, {"critical_without_nominee_total": True}),
        ({"critical_without_nominee_total": "2"}, {"critical_without_nominee_total": "2"}),
        ({"nominations_available": 1}, {"nominations_available": 1}),
        ({"criticality_available": None}, {"criticality_available": None}),
        ({"critical_total": True}, {"critical_total": 1}),
        ({"critical_total": 1}, {"critical_total": 1}),
        ({"positions_total": 1}, {"positions_total": 1}),
        ({"nominations_matched": 9}, {"nominations_matched": 9}),
        ({"nominations_total": None}, {"nominations_total": None}),
        ({"positions_total": None}, {"positions_total": None}),
        ({"critical_without_nominee_total": None}, {"critical_without_nominee_total": 2}),
        ({"criticality_missing_count": 5}, {"criticality_missing_count": 5}),
        ({"nominations_unmatched_open": 3}, {"nominations_unmatched_open": 3}),
    ],
)
async def test_inconsistent_or_invalid_dataset_values_are_not_published(monkeypatch, overrides):
    rows = [
        _position("P1", "Gerente", is_critical=True, has_active_nominee=False, **overrides[0]),
        _position("P2", "Jefe", is_critical=True, has_active_nominee=False, **overrides[1]),
    ]

    assert await _waiting(monkeypatch, rows) == "sucesion_no_disponible"


@pytest.mark.asyncio
async def test_roles_are_capped_and_technical_names_are_dropped(monkeypatch):
    rows = [
        _position(f"P{index}", f"Puesto {index:02d}", is_critical=True, has_active_nominee=False,
                  positions_total=30, critical_total=26, critical_without_nominee_total=26)
        for index in range(25)
    ]
    rows.insert(
        0,
        _position(
            "PX",
            "sap_successfactors_position_latest",
            is_critical=True,
            has_active_nominee=False,
            positions_total=30,
            critical_total=26,
            critical_without_nominee_total=26,
        ),
    )

    _confianza_raw, projected = await _confianza(monkeypatch, rows)
    roles = projected["vacantes_criticas_sin_sucesor"]["roles"]

    assert projected["vacantes_criticas_sin_sucesor"]["count"] == 26
    assert len(roles) <= 20
    assert "[REDACTED]" not in roles
    assert all("sap_successfactors" not in role for role in roles)


def test_panel_rejects_unknown_waiting_causes():
    from app.schemas.control_room_talent_responses import TalentConfianzaPanel

    projected = TalentConfianzaPanel.project(
        {"vacantes_criticas_motivo": "todas_criticas"}
    ).model_dump()

    assert projected["vacantes_criticas_motivo"] is None


@pytest.mark.parametrize(
    "motivo",
    [
        "sucesion_no_calculada",
        "sucesion_no_disponible",
        "sin_permiso",
        "posiciones_no_extraidas",
        "sin_posiciones_activas",
        "criticidad_no_encontrada",
        "criticidad_no_reconocida",
        "criticidad_incompleta",
        "sucesion_no_extraida",
        "sin_nominaciones",
        "nominaciones_sin_cruce",
        "nominaciones_cruce_parcial",
        "estado_nominacion_no_reconocido",
    ],
)
def test_panel_keeps_every_known_waiting_cause(motivo):
    from app.schemas.control_room_talent_responses import TalentConfianzaPanel

    projected = TalentConfianzaPanel.project({"vacantes_criticas_motivo": motivo}).model_dump()

    assert projected["vacantes_criticas_motivo"] == motivo
