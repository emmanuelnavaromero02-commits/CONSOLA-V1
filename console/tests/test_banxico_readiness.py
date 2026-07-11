from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services import banxico_readiness


@pytest.mark.asyncio
async def test_banxico_readiness_summarizes_gold_rows(monkeypatch):
    async def fake_rows(dataset, user, limit=100):
        assert dataset == "banxico_market_context"
        return [
            {
                "series_id": "SF43718",
                "metric_name": "usd_mxn_fix",
                "as_of": "2026-07-10",
                "unit": "mxn_per_usd",
                "value": Decimal("18.2"),
                "confidence": Decimal("0.95"),
                "freshness_status": "ready",
                "usable": True,
                "source_authority": "Banco de Mexico",
            },
            {
                "series_id": "SP68257",
                "metric_name": "udi_value",
                "freshness_status": "stale",
                "usable": False,
            },
        ]

    monkeypatch.setattr(banxico_readiness, "query_gold_dataset_rows", fake_rows)

    result = await banxico_readiness.banxico_readiness({"workspace_id": "w"})

    assert result["status"] == "ready"
    assert result["series_count"] == 2
    assert result["usable_count"] == 1
    assert result["series"][0]["value"] == 18.2
    assert result["series"][0]["confidence"] == 0.95
    assert result["series"][0]["source_authority"] == "Banco de Mexico"


@pytest.mark.asyncio
async def test_banxico_readiness_reports_missing_gold_as_insufficient(monkeypatch):
    async def missing(*_args, **_kwargs):
        raise HTTPException(404, "not found")

    monkeypatch.setattr(banxico_readiness, "query_gold_dataset_rows", missing)

    result = await banxico_readiness.banxico_readiness({"workspace_id": "w"})

    assert result["status"] == "insufficient_data"
    assert result["reason"] == "banxico_market_context_not_materialized"
    assert result["series"] == []
