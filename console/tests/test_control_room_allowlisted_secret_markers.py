from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services import control_room_service
from control_room_public_http_harness import OPERATOR, assert_safe, client


def test_allowlisted_text_redacts_bare_secret_markers_at_every_depth():
    payload = {
        "id": "business-1",
        "kind": "anomaly",
        "title": "password-sentinel",
        "description": "tenant_id꞉ZXQLEAK-tenant",
        "root_cause": "workspace_id∶ZXQLEAK-workspace",
        "impact": "simulation_id=ZXQLEAK-simulation",
        "recommendation": "Revisar acceso del cliente",
        "omega": {
            "decision": {"label": "orchestration_id=ZXQLEAK-orchestration"},
            "execution": {"label": "external_action_id=ZXQLEAK-action"},
            "options": [
                {"id": "review", "label": "private-key-marker"},
                {"id": "monitor", "label": "work\u200bspace_id=ZXQLEAK-zero-width"},
            ],
        },
    }
    with patch.object(
        control_room_service, "get_item", AsyncMock(return_value=payload)
    ):
        response = client(OPERATOR).get("/api/control-room/items/business-1")

    assert_safe(response)
    body = response.json()
    assert body["recommendation"] == "Revisar acceso del cliente"
    assert {
        body["title"],
        body["description"],
        body["root_cause"],
        body["impact"],
    } == {"[REDACTED]"}
    assert body["omega"]["decision"]["label"] == "[REDACTED]"
    assert body["omega"]["execution"]["label"] == "[REDACTED]"
    assert {option["label"] for option in body["omega"]["options"]} == {"[REDACTED]"}
    assert "ZXQLEAK" not in response.text
