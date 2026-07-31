"""Verdad Operacional: a signal is never given a price the engine refused.

`decision_intelligence._expected_impact()` resolves a non-monetary metric's
unit_value to 0.0, and the resulting `MoneyEstimate` is emitted with
value=None and currency=None -- an explicit refusal to put money on the
signal. `publish_control_room_item()` used to override that refusal with
abs(deviation_value) under a default "USD" label.

That mattered twice over. The deviation is expressed in the metric's own unit,
which is routinely headcount, days or percentage points; and a non-null
`impact_estimate` takes the `stored > 0` branch of
`control_room.business_impact_rules.calculate_item_impact()`, which runs ahead
of every real cost-basis rule and ahead of its own honest "unavailable"
fallback ("falta cost basis para convertirla a dinero sin inventar cifras").
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Callable

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

MODULE = REPO / "console/app/services/intelligence/persistence.py"


@pytest.fixture(scope="module")
def declared_money() -> Callable[[dict[str, Any]], tuple[float | None, str]]:
    """Compile `_declared_money` out of persistence.py.

    The module itself pulls in the database pool and the FastAPI app, none of
    which this invariant depends on, so the function is lifted out and handed
    the real `num` parser it closes over.
    """
    from app.services.intelligence.utils import num

    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    target = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_declared_money"),
        None,
    )
    assert target is not None, "_declared_money not found in persistence.py"
    namespace: dict[str, Any] = {"num": num, "Any": Any}
    exec(compile(ast.Module(body=[target], type_ignores=[]), str(MODULE), "exec"), namespace)  # noqa: S102
    return namespace["_declared_money"]


def test_engine_refusal_is_preserved(declared_money):
    """The regression. A non-monetary metric yields value=None/currency=None;
    persistence used to replace that with abs(deviation_value) and "USD"."""
    assert declared_money({"value": None, "currency": None, "basis": "No monetary impact configured for headcount."}) == (None, "USD")


def test_a_declared_estimate_is_persisted(declared_money):
    estimate = {"value": 12500.0, "currency": "USD", "basis": "abs(deviation_value) * impact.unit_value (2.5)"}
    assert declared_money(estimate) == (12500.0, "USD")


def test_an_amount_without_a_currency_is_not_money(declared_money):
    """A bare number would inherit the "USD" default that both this column and
    business_impact_rules apply on read, which is the same fabrication by a
    different route."""
    assert declared_money({"value": 4200.0, "currency": None, "basis": "x"}) == (None, "USD")
    assert declared_money({"value": 4200.0, "currency": "   ", "basis": "x"}) == (None, "USD")


def test_currency_is_normalised(declared_money):
    assert declared_money({"value": 900.0, "currency": " mxn "}) == (900.0, "MXN")


def test_zero_is_declared_but_carries_no_weight_downstream(declared_money):
    """A declared zero is data, not absence: it is persisted as-is, and
    calculate_item_impact's `stored > 0` guard lets it fall through to the
    real cost-basis rules rather than reporting a zero-dollar impact."""
    assert declared_money({"value": 0.0, "currency": "USD"}) == (0.0, "USD")


def test_an_absent_estimate_declares_nothing(declared_money):
    assert declared_money({}) == (None, "USD")


def test_deviation_value_is_never_a_monetary_fallback():
    """Source-level guard on the specific defect: no impact assignment in
    publish_control_room_item may read back the raw deviation."""
    source = MODULE.read_text(encoding="utf-8")
    assert "abs(float(signal[\"deviation_value\"]))" not in source
    assert "impact_estimate, impact_currency = _declared_money(expected_impact)" in source
