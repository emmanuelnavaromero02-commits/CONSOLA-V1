from __future__ import annotations

import pytest

from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from control_room_surface_fixtures import business_item


_UNICODE_DIAGNOSTICS = (
    "status꞉ready",
    "status∶ready",
    "status‐ready",
    "source_state꞉missing",
)
_STATE_ASSIGNMENTS = (
    (":", "ready"),
    ("=", "degraded"),
    (" ", "ok"),
    ("-", "partial"),
    ("_", "missing"),
    ("꞉", "blocked"),
    ("∶", "error"),
    ("‐", "future_technical_value"),
)
_SAFE_BUSINESS_COPY = (
    "Estado de resultados 2026",
    "Tasa de error: 2%",
    "ROI=18%",
    "Sin permiso retribuido: 12 ausencias",
)
_SYMBOL_ONLY_STATE_VALUES = (
    "status: !!!",
    "status: ❌",
    "state=—",
    "status: \u0301",
)
_EMPTY_STATE_ASSIGNMENTS = ("status", "status:", "state=")


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


def test_exactly_64_safe_tokens_remain_allowed() -> None:
    result = _classify(" ".join(["business"] * 64))

    assert result.allowed is True
    assert result.cause is VisibleCopyCause.ALLOWED


def test_65_tokens_fail_closed() -> None:
    result = _classify(" ".join(["business"] * 65))

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


def test_diagnostic_shifted_after_token_64_fails_closed() -> None:
    result = _classify(("a " * 64) + "status: ready")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("value", _UNICODE_DIAGNOSTICS)
def test_unicode_separator_variants_are_diagnostic(value: str) -> None:
    result = _classify(value)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize(
    ("separator", "value"),
    tuple(case for case in _STATE_ASSIGNMENTS if case[1] != "future_technical_value"),
)
@pytest.mark.parametrize("field", ("status", "state", "source_state"))
def test_state_fields_reject_runtime_diagnostic_values(
    field: str,
    separator: str,
    value: str,
) -> None:
    result = _classify(f"{field}{separator}{value}")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("field", ("status", "state"))
def test_state_fields_reject_machine_shaped_unknown_values(field: str) -> None:
    result = _classify(f"{field}‐future_technical_value")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("value", _SAFE_BUSINESS_COPY)
def test_business_controls_remain_visible(value: str) -> None:
    result = _classify(value)

    assert result.allowed is True
    assert result.text == value


@pytest.mark.parametrize("value", _SYMBOL_ONLY_STATE_VALUES)
def test_symbol_only_state_values_are_diagnostic(value: str) -> None:
    result = _classify(value)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("value", _EMPTY_STATE_ASSIGNMENTS)
def test_state_field_without_value_is_not_an_assignment(value: str) -> None:
    result = _classify(value)

    assert result.allowed is True
