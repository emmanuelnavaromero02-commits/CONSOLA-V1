from __future__ import annotations

from app.services.monitor_alert_policy import monitor_should_alert


def test_partial_or_insufficient_monitor_payload_never_creates_alert():
    contract = {
        "threshold": {
            "status_not_in": ["ready"],
            "blockers_present": True,
            "min_signal_count": 1,
        }
    }
    for status in ("partial", "insufficient_data", "blocked", "error", "empty"):
        assert not monitor_should_alert(
            contract,
            {
                "status": status,
                "signals": {"count": 0, "items": []},
                "blockers": [{"code": "missing_evidence"}],
            },
        )


def test_blocked_or_failed_engine_cannot_be_laundered_by_ready_parent():
    assert not monitor_should_alert(
        {"threshold": {"blockers_present": True}},
        {
            "status": "ready",
            "signals": {"count": 0},
            "blockers": [{"code": "monitor_engine_blocked"}],
            "evidence": {
                "engine_results": [
                    {"engine": "decision_orchestrator", "status": "blocked"}
                ]
            },
        },
    )


def test_nonstandard_status_without_real_signal_cannot_alert():
    assert not monitor_should_alert(
        {"threshold": {"status_not_in": ["ready"]}},
        {"status": "degraded", "signals": {"count": 0}, "blockers": []},
    )


def test_ready_payload_with_real_signal_can_alert():
    assert monitor_should_alert(
        {"threshold": {"min_signal_count": 1}},
        {
            "status": "ready",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "signals": {"count": 1, "items": [{"kind": "observed"}]},
            "blockers": [],
            "evidence": {"dataset": "gold"},
        },
    )
