from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from control_room_surface_fixtures import business_item, snapshot


_DIAGNOSTIC_CORES = (
    "Error HTTP 503",
    "Error 503",
    "blocked",
    "source unavailable",
    "Access denied",
    "Permission denied",
    "faltan datos",
    "source_state: missing",
    "source_state missing",
    "dataset missing",
    "Dataset HR no materializado",
    "sin permiso para este usuario",
    "No hay datos",
)
_SAFE_BUSINESS_COPY = (
    "Datos para decisiones",
    "Operación completa",
    "Fuente operativa",
    "Contrato disponible",
    "Tasa de error: 2%",
    "Estado de resultados 2026",
    "ROI=18%",
    "Sin permiso retribuido: 12 ausencias",
)


def _classify(value: str):
    item = business_item()
    identity = resolve_business_surface_identity(item)
    assert identity is not None
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=240,
    )


@pytest.mark.parametrize("core", _DIAGNOSTIC_CORES)
@pytest.mark.parametrize("suffix_size", (1, 5, 20))
def test_diagnostic_core_cannot_be_revived_by_suffix(
    core: str,
    suffix_size: int,
) -> None:
    result = _classify(f"{core} {'context ' * suffix_size}".strip())

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("core", _DIAGNOSTIC_CORES)
def test_diagnostic_fact_and_parent_section_are_removed(core: str) -> None:
    item = business_item(title=f"{core} al consultar SuccessFactors")

    assert build_business_experience(snapshot(items=(item,))).sections == []


@pytest.mark.parametrize(
    "diagnostic",
    (
        "source_state: missing",
        "source_state=missing",
        "source_state missing",
        "source-state-missing",
        "data_status: blocked",
        "readiness-status=stub",
        "status error",
    ),
)
def test_diagnostic_state_separators_are_equivalent(diagnostic: str) -> None:
    result = _classify(diagnostic)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("business_copy", _SAFE_BUSINESS_COPY)
def test_business_phrases_remain_visible(business_copy: str) -> None:
    result = _classify(business_copy)

    assert result.allowed is True
    assert result.text == business_copy
