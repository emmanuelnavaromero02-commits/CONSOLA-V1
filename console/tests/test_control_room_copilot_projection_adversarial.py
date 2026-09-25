from __future__ import annotations

import json

import pytest

from app.services import copilot_context_service


@pytest.mark.parametrize(
    "secret",
    (
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "eyJhbGciOiJIUzI1NiJ9.e30.c2lnbmF0dXJl",
    ),
)
def test_recommendation_projection_drops_recognizable_secrets(secret: str) -> None:
    projected = copilot_context_service.project_operator_recommendations(
        {
            "recommendations": [
                {
                    "fingerprint": "live:0123456789abcdef",
                    "title": secret,
                    "body": f"Señal {secret}",
                    "action_label": secret,
                    "action_href": f"/control-room/{secret}",
                }
            ]
        }
    )

    recommendation = projected["recommendations"][0]
    assert set(recommendation).isdisjoint(
        {"title", "body", "action_label", "action_href"}
    )
    assert secret not in json.dumps(projected, ensure_ascii=False)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_operational_count_projection_rejects_non_finite_values(
    value: float,
) -> None:
    projected = copilot_context_service.project_operator_snapshot(
        {
            "status": "ready",
            "sources": [
                {
                    "name": "database.operational_counts",
                    "status": "ready",
                    "data": {
                        "pipeline": {"by_status": {"failed": value}},
                        "datasets": {"total": value},
                    },
                }
            ],
        }
    )

    diagnostic = projected["sources"][0]["diagnostic"]
    assert diagnostic["pipeline"]["by_status"]["failed"] == 0
    assert diagnostic["datasets"]["total"] == 0
    recommendation = copilot_context_service.project_operator_recommendations(
        {
            "recommendations": [
                {
                    "fingerprint": "live:0123456789abcdef",
                    "priority_score": value,
                }
            ]
        }
    )["recommendations"][0]
    assert "priority_score" not in recommendation
    json.dumps(projected, allow_nan=False)


def test_projection_preserves_safe_text_and_zero_counts() -> None:
    recommendations = copilot_context_service.project_operator_recommendations(
        {
            "recommendations": [
                {
                    "fingerprint": "live:0123456789abcdef",
                    "title": "Revisar señales operativas",
                    "body": "No hay incidencias pendientes.",
                    "action_href": "/control-room",
                    "priority_score": 0,
                }
            ]
        }
    )["recommendations"]

    assert recommendations == [
        {
            "id": "live:0123456789abcdef",
            "severity": "info",
            "status": "active",
            "action_kind": "navigate",
            "title": "Revisar señales operativas",
            "body": "No hay incidencias pendientes.",
            "action_href": "/control-room",
            "priority_score": 0,
        }
    ]
