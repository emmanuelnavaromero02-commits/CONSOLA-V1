from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services import inegi_readiness


@pytest.mark.asyncio
async def test_inegi_readiness_summarizes_gold_rows(monkeypatch):
    async def fake_rows(dataset, user, limit=100):
        assert dataset == "inegi_market_context"
        return [
            {
                "series_id": "454168",
                "metric_name": "igae_index",
                "as_of": "2026-05-01",
                "unit": "index_2018_100",
                "value": Decimal("105.2"),
                "confidence": Decimal("0.94"),
                "freshness_status": "ready",
                "usable": True,
                "source_authority": "INEGI",
            },
            {
                "series_id": "736537",
                "metric_name": "unemployment_rate",
                "freshness_status": "stale",
                "usable": False,
            },
        ]

    monkeypatch.setattr(inegi_readiness, "query_gold_dataset_rows", fake_rows)

    result = await inegi_readiness.inegi_readiness({"workspace_id": "w"})

    assert result["status"] == "ready"
    assert result["series_count"] == 2
    assert result["usable_count"] == 1
    assert result["series"][0]["value"] == 105.2
    assert result["series"][0]["confidence"] == 0.94
    assert result["series"][0]["source_authority"] == "INEGI"


@pytest.mark.asyncio
async def test_inegi_readiness_reports_missing_gold_as_insufficient(monkeypatch):
    async def missing(*_args, **_kwargs):
        raise HTTPException(404, "not found")

    monkeypatch.setattr(inegi_readiness, "query_gold_dataset_rows", missing)

    result = await inegi_readiness.inegi_readiness({"workspace_id": "w"})

    assert result["status"] == "insufficient_data"
    assert result["reason"] == "inegi_market_context_not_materialized"
    assert result["series"] == []
