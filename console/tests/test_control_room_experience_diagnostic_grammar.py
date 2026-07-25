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


_STATE_FIELDS = (
    "status",
    "state",
    "data_status",
    "data-status",
    "data status",
    "data_state",
    "data-state",
    "data state",
    "readiness_status",
    "readiness-status",
    "readiness status",
    "readiness_state",
    "readiness-state",
    "readiness state",
    "source_status",
    "source-status",
    "source status",
    "source_state",
    "source-state",
    "source state",
)
_ASSIGNMENT_SEPARATORS = (": ", "=", " ", "-", "_")
_REQUIRED_EXAMPLES = (
    "status: ready",
    "status-ready",
    "status ready",
    "data_status: degraded",
    "readiness-status ok",
    "source_state ready",
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


@pytest.mark.parametrize("field", _STATE_FIELDS)
@pytest.mark.parametrize("separator", _ASSIGNMENT_SEPARATORS)
def test_state_grammar_treats_supported_separators_equivalently(
    field: str,
    separator: str,
) -> None:
    result = _classify(f"{field}{separator}ready")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("diagnostic", _REQUIRED_EXAMPLES)
def test_required_state_examples_remove_fact_and_parent_section(
    diagnostic: str,
) -> None:
    item = business_item(title=diagnostic)

    assert build_business_experience(snapshot(items=(item,))).sections == []


@pytest.mark.parametrize("suffix_size", (1, 5, 20))
def test_sin_permiso_family_remains_diagnostic_with_suffixes(
    suffix_size: int,
) -> None:
    suffix = " ".join(f"token{index}" for index in range(suffix_size))
    value = f"sin permiso para consultar SuccessFactors {suffix}"

    result = _classify(value)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


def test_sin_permiso_family_removes_fact_and_parent_section() -> None:
    item = business_item(title="sin permiso para consultar SuccessFactors")

    assert build_business_experience(snapshot(items=(item,))).sections == []


@pytest.mark.parametrize("business_copy", _SAFE_BUSINESS_COPY)
def test_business_copy_controls_remain_visible(business_copy: str) -> None:
    result = _classify(business_copy)

    assert result.allowed is True
    assert result.text == business_copy
