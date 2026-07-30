from __future__ import annotations

from app.services import control_room_service


def test_replicon_pnl_normalizer_types_margin_with_revenue_denominator():
    source = next(
        source
        for source in control_room_service._all_sources()  # noqa: SLF001
        if source.dataset == "pnl_mensual"
    )

    item = control_room_service._normalize_replicon_pnl(  # noqa: SLF001
        source,
        {
            "mes": "2026-05-01",
            "proyecto": "P-ZERO",
            "revenue_usd": 100_000,
            "margen_bruto_pct": 0,
            "financial_status": "ready",
        },
    )

    assert item is not None
    assert item["metric_type"] == "percentage"
    assert item["observed_value"] == 0
    assert item["denominator"] == 100_000


def test_replicon_pnl_normalizer_blocks_unverified_currency_rows():
    source = next(
        source
        for source in control_room_service._all_sources()  # noqa: SLF001
        if source.dataset == "pnl_mensual"
    )
    item = control_room_service._normalize_replicon_pnl(  # noqa: SLF001
        source,
        {
            "proyecto": "legacy-fx",
            "margen_bruto_pct": -10,
            "wip_usd": 50_000,
            "financial_status": "missing_fx",
        },
    )
    assert item is None


def test_source_state_cleanup_is_not_exposed_on_read_service():
    assert not hasattr(control_room_service, "_cleanup_obsolete_source_state_items")
