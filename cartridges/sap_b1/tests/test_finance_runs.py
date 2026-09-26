from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.b1_source import B1ConfigurationError
from app.services import finance_runs_mapping as m

HEADER = "indicador,empresa,mes,dimension,clave,valor\n"


def test_a_manual_run_parses_with_defaults_and_accepts_money_formats():
    rows = m.parse_finance_run(
        "﻿" + HEADER
        + "margen_bruto,mx_mfg,2026-08,total,ignored,\"1,250,000.50\"\n"
        + "margen_bruto,mx_mfg,2026-08,cliente,C-0001,$ 1000\n"
        + "concentracion_top20,mx_mfg,2026-08,total,,61.5%\n"
        + "margen_contribucion,grupo,2026-08,total,,-10\n"
        + "# comentario\n\n"
    )
    assert [(r.indicator, r.company, r.dimension, r.key, r.unit, r.value) for r in rows] == [
        ("margen_bruto", "mx_mfg", "total", "", "monto", Decimal("1250000.500000")),
        ("margen_bruto", "mx_mfg", "cliente", "C-0001", "monto", Decimal("1000.000000")),
        ("concentracion_top20", "mx_mfg", "total", "", "pct", Decimal("61.500000")),
        ("margen_contribucion", "grupo", "total", "", "monto", Decimal("-10.000000")),
    ]
    assert m.summary(rows)["companies"] == {"grupo": ["2026-08"], "mx_mfg": ["2026-08"]}


@pytest.mark.parametrize(
    "body",
    [
        "",
        "indicador,empresa,mes\nmargen_bruto,mx_mfg,2026-08\n",
        HEADER,
        HEADER + "ebitda,mx_mfg,2026-08,total,,1\n",
        HEADER + "margen_bruto,mx mfg,2026-08,total,,1\n",
        HEADER + "margen_bruto,mx_mfg,2026-13,total,,1\n",
        HEADER + "margen_bruto,mx_mfg,2026-08,region,norte,1\n",
        HEADER + "destructores,mx_mfg,2026-08,sku,A,1\n",
        HEADER + "margen_bruto,grupo,2026-08,cliente,C-1,1\n",
        HEADER + "margen_bruto,mx_mfg,2026-08,cliente,,1\n",
        HEADER + "margen_bruto,mx_mfg,2026-08,total,,mucho\n",
        HEADER + "margen_bruto,mx_mfg,2026-08,total,,1e20\n",
        HEADER.replace("valor", "valor,unidad") + "margen_bruto,mx_mfg,2026-08,total,,1,piezas\n",
        HEADER + "margen_bruto,mx_mfg,2026-08,total,,1\nmargen_bruto,mx_mfg,2026-08,total,,2\n",
    ],
)
def test_malformed_runs_name_the_line(body):
    with pytest.raises(B1ConfigurationError):
        m.parse_finance_run(body)


def test_a_run_over_the_size_limit_is_refused():
    with pytest.raises(B1ConfigurationError, match="5 MB"):
        m.parse_finance_run(HEADER + "x" * (m.MAX_BYTES + 1))
