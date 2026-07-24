from __future__ import annotations

import pytest

from app.services import control_room_service
from console.tests.test_control_room_service import USER


def _wt_fake_rows_factory():
    async def fake_rows(dataset: str, user: dict | None, limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_operational_features":
            return [
                {
                    "active_headcount_current": 1288,
                    "avg_tenure_months_current": 174.39,
                    "attrition_rate_current": 0.0,
                    "headcount_history_months": 36,
                }
            ]
        if dataset == "sap_successfactors_talent_headcount_by_cohort_month":
            return [
                {
                    "snapshot_month": "2026-06-01",
                    "active_headcount": 100,
                    "cohort_size": 100,
                },
                {
                    "snapshot_month": "2026-06-01",
                    "active_headcount": 50,
                    "cohort_size": 50,
                },
                {
                    "snapshot_month": "2026-07-01",
                    "active_headcount": 110,
                    "cohort_size": 110,
                },
            ]
        if dataset == "sap_successfactors_talent_tenure_by_cohort_month":
            return [
                {
                    "snapshot_month": "2026-06-01",
                    "avg_tenure_months": 120,
                    "cohort_size": 100,
                },
                {
                    "snapshot_month": "2026-06-01",
                    "avg_tenure_months": 60,
                    "cohort_size": 50,
                },
                {
                    "snapshot_month": "2026-07-01",
                    "avg_tenure_months": 121,
                    "cohort_size": 110,
                },
            ]
        if dataset == "sap_successfactors_talent_attrition_by_cohort_month":
            return [
                {"snapshot_month": "2026-06-01", "separations": 2, "cohort_size": 100},
                {"snapshot_month": "2026-06-01", "separations": 0, "cohort_size": 50},
                {"snapshot_month": "2026-07-01", "separations": 0, "cohort_size": 110},
            ]
        return []

    return fake_rows


@pytest.mark.asyncio
async def test_build_workforce_trends_is_single_source_and_aggregates_correctly(
    monkeypatch,
):
    monkeypatch.setattr(
        control_room_service, "query_dataset_rows", _wt_fake_rows_factory()
    )
    bundle = await control_room_service.build_workforce_trends(USER)

    assert bundle["status"] == "ready"
    assert bundle["kpis"] == {
        "active_headcount": 1288,
        "avg_tenure_months": 174.39,
        "attrition_rate": 0.0,
        "history_months": 36,
    }
    assert bundle["series"]["months"] == ["2026-06", "2026-07"]
    assert bundle["series"]["headcount"] == [150, 110]
    assert bundle["series"]["avg_tenure_months"] == [100.0, 121.0]
    assert bundle["series"]["attrition_rate"] == [round(2 / 150, 4), 0.0]


@pytest.mark.asyncio
async def test_build_workforce_trends_waits_when_datasets_missing(monkeypatch):
    async def empty(dataset: str, user: dict | None, limit: int) -> list[dict]:
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", empty)
    bundle = await control_room_service.build_workforce_trends(USER)
    assert bundle["status"] == "waiting_for_data"
    assert bundle["series"]["months"] == []
    assert bundle["kpis"]["active_headcount"] is None


@pytest.mark.asyncio
async def test_talent_kpis_payload_includes_workforce_trends_from_same_builder(
    monkeypatch,
):
    monkeypatch.setattr(
        control_room_service, "query_dataset_rows", _wt_fake_rows_factory()
    )
    result = await control_room_service.sap_successfactors_talent_kpis(USER)
    assert "workforce_trends" in result
    wt = result["workforce_trends"]
    assert wt["kpis"]["active_headcount"] == 1288
    assert wt["series"]["headcount"] == [150, 110]
