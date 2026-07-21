from datetime import UTC, datetime

from app.services import control_room_service


def test_global_calibration_is_diagnostic_not_business_count():
    now = datetime(2026, 7, 17, tzinfo=UTC)
    raw = {
        "agents": [],
        "runs": [],
        "alert_rows": [],
        "origin_rows": [],
        "monte_carlo_rows": [{"source_type": "signal", "total": 2, "latest_at": now}],
        "operational_calibration_rows": [
            {"total": 3, "sample_count": 21, "latest_at": now}
        ],
        "orchestration_rows": [],
        "execution_rows": [],
    }

    payload = control_room_service._agentops_payload_from_raw(
        raw,
        tenant_id="tenant-A",
        workspace_id="workspace-A",
    )

    assert payload["summary"]["monte_carlo_simulations"] == 2
    assert payload["summary"]["bayesian_calibration_states"] == 0
    assert payload["summary"]["bayesian_calibration_samples"] == 0
    calibration = next(
        engine
        for engine in payload["engines"]
        if engine["engine"] == "bayesian_calibration"
    )
    assert calibration["evidence_count"] == 0
    assert calibration["sample_count"] == 0
    assert payload["operational_diagnostics"] == [
        {
            "diagnostic": "bayesian_calibration_global",
            "scope": "workspace",
            "state_count": 3,
            "sample_count": 21,
            "latest_at": now.isoformat(),
            "included_in_business_counters": False,
        }
    ]
