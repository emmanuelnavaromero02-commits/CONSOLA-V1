from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.schemas import control_room_legacy_responses as responses
from app.services import control_room_service
from control_room_public_http_harness import DATASET_READER, client


TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
FOREIGN_WORKSPACE = "33333333-3333-4333-8333-333333333333"
USER = {
    "id": 7,
    "role": "user",
    "tenant_id": TENANT,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "workspace_role": "workspace_admin",
    "allowed_cartridges": ["sap_successfactors"],
}
SIGNAL = {
    "signal_type": "pipeline",
    "status": "recommendation_only",
    "generated_at": "2026-07-05T03:27:32Z",
    "readiness_status": "partial",
    "source_row_count": 10,
    "materialized_at": "2026-07-05T03:27:32Z",
    "blockers": "[]",
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
}
ROWS = [
    {
        **SIGNAL,
        "signal_id": "talent_role_requirements_missing",
        "severity": "medium",
        "affected_count": 42,
        "title": "Requisitos de rol pendientes",
        "recommendation": "Validar Position y entidades de skills.",
    },
    {
        **SIGNAL,
        "signal_id": "talent_mobility_blocked",
        "severity": "high",
        "affected_count": 7,
        "title": "Movilidad interna detenida",
        "recommendation": "Revisar vacantes internas abiertas.",
    },
]
NO_SIGNALS = {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}


INSTALLED = [
    {
        "cartridge_id": "sap_successfactors",
        "installation_status": "ready",
        "connection_id": "tenant_sf",
        "auth_method": "saml_bearer_assertion",
    }
]


async def _summary(
    rows: list[dict[str, Any]],
    *,
    signal_error: BaseException | None = None,
    installed: list[dict[str, Any]] = INSTALLED,
) -> dict[str, Any]:
    async def fetcher(dataset: str, _user: Any, _limit: int) -> list[dict[str, Any]]:
        if dataset != "sap_successfactors_talent_signals":
            return []
        if signal_error is not None:
            raise signal_error
        return list(rows)

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0
    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_load_lesson_rows", new=AsyncMock(return_value=[])
        ),
        patch.object(
            control_room_service,
            "count_open_business_decisions",
            new=AsyncMock(return_value=0),
        ),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=list(installed)),
        ),
    ):
        return await control_room_service.summary(USER, fetcher=fetcher)


@pytest.mark.asyncio
async def test_summary_counts_open_talent_signals_apart_from_anomalies():
    result = await _summary(ROWS)

    assert result["total_anomalies"] == 0
    assert result["by_severity"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}
    assert result["intelligence_signals"] == {
        "total": 2,
        "critical": 0,
        "high": 1,
        "medium": 1,
        "low": 0,
    }


@pytest.mark.asyncio
async def test_summary_does_not_count_out_of_scope_rows_as_business_signals():
    result = await _summary([{**row, "workspace_id": FOREIGN_WORKSPACE} for row in ROWS])

    assert result["intelligence_signals"] == NO_SIGNALS
    assert result["total_anomalies"] == 0


@pytest.mark.asyncio
async def test_summary_does_not_count_signals_without_signed_evidence(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", raising=False)

    result = await _summary(ROWS)

    assert result["intelligence_signals"] == NO_SIGNALS


def test_open_signals_exclude_closed_statuses_and_other_kinds():
    items = [
        {"kind": "intelligence_signal", "status": status, "severity": "high"}
        for status in (
            "open",
            "in_review",
            "decision_created",
            "approved",
            "dismissed",
            "resolved",
        )
    ] + [
        {"kind": "intelligence_signal", "severity": "low"},
        {"kind": "anomaly", "status": "open", "severity": "critical"},
        {"kind": "metric", "status": "open", "severity": "critical"},
    ]

    selected = control_room_service._open_intelligence_signals(items)

    assert [(item.get("status"), item["severity"]) for item in selected] == [
        ("open", "high"),
        ("in_review", "high"),
        ("decision_created", "high"),
        (None, "low"),
    ]


def test_summary_response_projects_signal_counts_without_extra_keys():
    projected = responses.ControlRoomBusinessSummaryResponse.project(
        {
            "total_anomalies": 0,
            "intelligence_signals": {
                "total": 3,
                "high": 1,
                "medium": 2,
                "workspace_id": "private-workspace",
            },
        }
    ).model_dump()

    assert projected["total_anomalies"] == 0
    assert projected["intelligence_signals"] == {
        "total": 3,
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 0,
    }
    assert "private-workspace" not in str(projected)
    for unknown in ({}, {"intelligence_signals": None}):
        assert (
            responses.ControlRoomBusinessSummaryResponse.project(unknown).model_dump()[
                "intelligence_signals"
            ]
            is None
        )


def test_summary_route_returns_signal_counts():
    payload = {
        "total_anomalies": 0,
        "intelligence_signals": {"total": 2, "high": 1, "medium": 1},
    }
    with patch.object(control_room_service, "summary", AsyncMock(return_value=payload)):
        response = client(DATASET_READER).get("/api/control-room/summary")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_anomalies"] == 0
    assert body["intelligence_signals"] == {
        "total": 2,
        "critical": 0,
        "high": 1,
        "medium": 1,
        "low": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    (HTTPException(404, "missing"), HTTPException(503, "down"), TimeoutError()),
)
async def test_summary_reports_unknown_signals_when_the_source_did_not_answer(error):
    result = await _summary(ROWS, signal_error=error)

    assert result["intelligence_signals"] is None
    assert result["total_anomalies"] == 0


@pytest.mark.asyncio
async def test_summary_reports_zero_signals_when_the_source_answered_empty():
    result = await _summary([])

    assert result["intelligence_signals"] == NO_SIGNALS


@pytest.mark.asyncio
async def test_summary_reports_unknown_signals_without_a_signal_source():
    result = await _summary(ROWS, installed=[])

    assert result["intelligence_signals"] is None


def test_summary_route_keeps_unknown_signals_unknown():
    payload = {"total_anomalies": 0, "intelligence_signals": None}
    with patch.object(control_room_service, "summary", AsyncMock(return_value=payload)):
        response = client(DATASET_READER).get("/api/control-room/summary")

    assert response.status_code == 200, response.text
    assert response.json()["intelligence_signals"] is None
