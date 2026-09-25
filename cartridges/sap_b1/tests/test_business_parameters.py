"""Business parameters: parsing, validation and the Bronze snapshot shape."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.b1_source import B1ConfigurationError
from app.services import business_parameters_mapping as m


def test_a_full_specification_parses_into_typed_rows():
    spec = """
    # comentarios y lineas vacias se ignoran
    control:mx_mfg:2026-08:revenue_net=1250000.505
    control:mx_mfg:2026-08:cogs=800000
    account:*:*:revenue=4*, 7100-01
    threshold:mx_dist_a:*:margin_min_pct=22.5;setting:*:*:reconciliation_tolerance_pct=0.5
    setting:*:*:roles=manufacturer
    """
    params = m.parse_business_parameters(spec)
    assert [(p.kind, p.company, p.period, p.key) for p in params] == [
        ("control", "mx_mfg", "2026-08", "revenue_net"),
        ("control", "mx_mfg", "2026-08", "cogs"),
        ("account", "*", "*", "revenue"),
        ("threshold", "mx_dist_a", "*", "margin_min_pct"),
        ("setting", "*", "*", "reconciliation_tolerance_pct"),
        ("setting", "*", "*", "roles"),
    ]
    assert params[0].value_num == Decimal("1250000.505000")
    assert params[2].value_text == "4*,7100-01" and params[2].value_num is None
    assert params[5].value_num is None and params[5].value_text == "manufacturer"


@pytest.mark.parametrize(
    "spec",
    [
        "control:mx_mfg:2026-08:revenue_net",
        "control:mx_mfg:2026-08=1",
        "budget:mx_mfg:2026-08:revenue_net=1",
        "control:MX:2026-08:revenue_net=1",
        "control:mx_mfg:2026-13:revenue_net=1",
        "control:*:2026-08:revenue_net=1",
        "control:mx_mfg:*:revenue_net=1",
        "control:mx_mfg:2026-08:ebitda=1",
        "control:mx_mfg:2026-08:revenue_net=mucho",
        "account:*:2026-08:revenue=4*",
        "account:*:*:gastos=6*",
        "account:*:*:revenue=4*;x",
        "account:*:*:revenue=4 1",
        "threshold:*:*:margin_min_pct=alto",
        "threshold:*:*:Margin=1",
        "control:mx_mfg:2026-08:cogs=1e20",
        "control:mx_mfg:2026-08:cogs=1;control:mx_mfg:2026-08:cogs=2",
    ],
)
def test_malformed_entries_are_configuration_errors(spec):
    with pytest.raises(B1ConfigurationError):
        m.parse_business_parameters(spec)


def test_the_snapshot_always_carries_the_loaded_row_and_the_declared_schema():
    import pyarrow as pa

    assert m.parse_business_parameters("") == []
    rows = m.parameter_records([])
    assert [(r["Kind"], r["Company"], r["Period"], r["ParamKey"], r["ValueNum"]) for r in rows] == [
        ("setting", "*", "*", "loaded", Decimal("1.000000"))
    ]
    rows = m.parameter_records(m.parse_business_parameters("setting:*:*:loaded=7;threshold:*:*:margin_min_pct=20"))
    assert [r["ParamKey"] for r in rows] == ["loaded", "margin_min_pct"] and rows[0]["ValueNum"] == Decimal("1.000000")
    schema = m.arrow_schema()
    assert schema.field("ValueNum").type == pa.decimal128(19, 6)
    assert all(schema.field(name).type == pa.string() for name in ("Kind", "Company", "Period", "ParamKey", "ValueText", "_company"))
    table = pa.Table.from_pylist([{**r, **{c: None for c in schema.names if c not in r}} for r in rows], schema=schema)
    assert table.num_rows == 2
