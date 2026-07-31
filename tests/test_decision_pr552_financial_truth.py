from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PNL_SQL = (
    ROOT / "cartridges/replicon/datasets/pnl_mensual.sql",
    ROOT / "cartridges/replicon/datasets/pnl_detalle_consultor.sql",
    ROOT / "infra/init/10_replicon_gold_seed.sql",
    ROOT / "infra/init/65_replicon_mejoras_seed_refresh.sql",
)
CANONICAL_PNL_SQL = PNL_SQL[:2]
ACTIVE_SEED_SQL = PNL_SQL[2:]


def test_all_eight_productive_fx_divisors_are_removed() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PNL_SQL)
    assert len(re.findall(r"/\s*20\.0\b", combined)) == 0
    for path in CANONICAL_PNL_SQL:
        lowered = path.read_text(encoding="utf-8").lower()
        assert "missing_fx" in lowered
        assert "financial_status" in lowered
        assert "fx_source" in lowered
        assert "fx_observed_at" in lowered
        assert "original_currency" in lowered
    for path in ACTIVE_SEED_SQL:
        lowered = path.read_text(encoding="utf-8").lower()
        assert lowered.count("financial_status==='ready'") == 3
        assert "sin revenue" not in lowered
        assert "number(r[k]||0)" not in lowered
        assert "math.round(+n||0)" not in lowered
        assert "+r.costo_directo||0" not in lowered
        assert "+r.revenue_aporte||0" not in lowered
        assert len(
            re.findall(r"projectcurrencyid\s*=\s*8\s*then\s+null", lowered)
        ) == 2
        assert (
            len(
                re.findall(
                    r"count\(\*\) filter \(where \"moneda\" = 'mxn'\) > 0"
                    r"\s*then\s+null",
                    lowered,
                )
            )
            == 2
        )
        assert lowered.count("'insufficient_data' as financial_status") == 2
        for field in (
            "revenue_usd",
            "facturacion_mes_usd",
            "wip_usd",
            "costo_total",
            "margen_bruto_usd",
            "margen_bruto_pct",
        ):
            assert f"null::double as {field}" in lowered
