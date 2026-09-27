from __future__ import annotations

import pytest

from tests.test_mcp_airflow_not_found import _load_airflow_tools

DAGS_PAYLOAD = {
    "dags": [
        {
            "dag_id": "entity_scheduler",
            "is_paused": False,
            "is_active": True,
            "tags": [{"name": "platform"}],
            "description": "Meta-scheduler",
            "schedule_interval": {"__type": "CronExpression", "value": "*/5 * * * *"},
            "timetable_description": "Every 5 minutes",
        },
        {
            "dag_id": "sap_sf_extract",
            "is_paused": True,
            "is_active": True,
            "tags": [],
            "description": None,
            "schedule_interval": None,
            "timetable_description": "Never, external triggers only",
        },
        {
            "dag_id": "sap_b1_refresh",
            "is_paused": True,
            "tags": [],
            "schedule_interval": None,
            "timetable_summary": "*/10 * * * *",
            "timetable_description": "Every 10 minutes",
        },
        {
            "dag_id": "legacy_without_timetable",
            "is_paused": False,
            "tags": [],
        },
    ]
}


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return DAGS_PAYLOAD


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, *args, **kwargs):
        return FakeResponse()


@pytest.mark.anyio
async def test_list_dags_reports_the_schedule_with_a_strict_manual_rule(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setattr(airflow, "_client", lambda: FakeClient())

    result = await airflow.airflow_list_dags()
    by_id = {dag["dag_id"]: dag for dag in result["dags"]}

    assert by_id["entity_scheduler"]["schedule_kind"] == "scheduled"
    assert by_id["entity_scheduler"]["schedule_interval"] == {
        "__type": "CronExpression",
        "value": "*/5 * * * *",
    }
    assert by_id["sap_sf_extract"]["schedule_kind"] == "manual"
    assert by_id["sap_sf_extract"]["timetable_description"] == (
        "Never, external triggers only"
    )
    assert by_id["sap_b1_refresh"]["schedule_kind"] == "scheduled"
    assert by_id["legacy_without_timetable"]["schedule_kind"] == "scheduled"
    assert by_id["legacy_without_timetable"]["schedule_interval"] is None
    assert set(by_id["sap_sf_extract"]) == {
        "dag_id",
        "is_paused",
        "is_active",
        "tags",
        "description",
        "schedule_kind",
        "timetable_description",
        "schedule_interval",
    }
