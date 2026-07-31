from __future__ import annotations

import pytest

from app.services import control_room_service
from app.services.intelligence import (
    gold_fetcher,
    successfactors_gold_headcount as headcount_query,
)
from test_control_room_successfactors_gold_headcount_query import USER


@pytest.mark.asyncio
@pytest.mark.parametrize("company_status", ["missing", "empty"])
async def test_active_headcount_does_not_fallback_to_capped_employee_rows(
    monkeypatch, company_status: str
):
    async def employee_rows(_dataset: str, _user: dict | None, _limit: int):
        return [{"is_active": True}, {"is_active": True}]

    async def summaries(_user: dict | None, *, limit: int):
        missing = {
            "rows": [],
            "total": None,
            "status": company_status,
            "error": None,
        }
        results = {
            dataset: dict(missing)
            for _key, dataset, _name_key in headcount_query._DIMENSIONS
        }
        results["sap_successfactors_employee_360"] = {
            "rows": [],
            "total": None,
            "status": "unavailable",
            "error": "exact aggregate unavailable",
        }
        return results

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", employee_rows)
    monkeypatch.setattr(
        headcount_query, "query_successfactors_headcount_summaries", summaries
    )
    payload = await control_room_service.sap_successfactors_gold_kpis(USER)
    active = next(
        widget for widget in payload["widgets"] if widget["id"] == "sf_active_headcount"
    )

    assert active["value"] is None
    assert active["status"] == "unavailable"
