from __future__ import annotations

from app.services import sync_agentops


def test_sync_agentops_schedule_key_is_stable_and_scoped_to_sync_now():
    first = sync_agentops.sync_agentops_schedule_key("sync_now:sap_successfactors:abc")
    second = sync_agentops.sync_agentops_schedule_key("sync_now:sap_successfactors:abc")

    assert first == second
    assert first.startswith("sync-now:")


def test_no_monitor_candidates_payload_keeps_existing_contract_text():
    payload = sync_agentops.no_monitor_candidates_payload("2026-07-03T00:00:00+00:00")

    assert payload == {
        "status": "partial",
        "checked_at": "2026-07-03T00:00:00+00:00",
        "total": 0,
        "completed": 0,
        "failed": 0,
        "results": [],
        "reason": "No hay monitores activos con contrato AgentOps para este cartucho/workspace.",
    }


def test_duplicate_monitor_payload_and_counters_are_normalized():
    reservation = {
        "status": "ok",
        "agent_run_id": "run-1",
        "id": "schedule-1",
    }

    assert sync_agentops.duplicate_monitor_counters("ok") == (1, 0)
    assert sync_agentops.duplicate_monitor_counters("cancelled") == (0, 1)
    assert sync_agentops.duplicate_monitor_counters("reserved") == (0, 0)
    assert sync_agentops.duplicate_monitor_result(
        agent_id="agent-1",
        agent_slug="monitor",
        reservation=reservation,
    ) == {
        "agent_id": "agent-1",
        "agent_slug": "monitor",
        "status": "duplicate",
        "schedule_status": "ok",
        "run_id": "run-1",
        "schedule_run": reservation,
    }


def test_scheduled_monitor_results_are_safe_for_ui_payloads():
    assert sync_agentops.missing_agent_id_result("monitor") == {
        "agent_id": "",
        "agent_slug": "monitor",
        "status": "failed",
        "error": "agent id missing",
    }

    success = sync_agentops.scheduled_monitor_success_result(
        agent_id="agent-1",
        agent_slug="monitor",
        result={
            "run_id": "run-1",
            "reply": "x" * 600,
            "deterministic_monitor": True,
        },
        reservation={"id": "schedule-1"},
    )

    assert success["status"] == "success"
    assert success["run_id"] == "run-1"
    assert success["deterministic_monitor"] is True
    assert len(success["reply"]) == 500

    failure = sync_agentops.scheduled_monitor_failure_result(
        agent_id="agent-1",
        agent_slug="monitor",
        exc=RuntimeError("boom"),
    )
    assert failure == {
        "agent_id": "agent-1",
        "agent_slug": "monitor",
        "status": "failed",
        "error": "RuntimeError: boom",
    }


def test_sync_agentops_summary_matches_main_status_contract():
    assert (
        sync_agentops.sync_agentops_summary(
            checked_at="now",
            sync_run_id="sync-1",
            total=1,
            completed=1,
            failed=0,
            results=[{"status": "success"}],
        )["status"]
        == "success"
    )
    assert (
        sync_agentops.sync_agentops_summary(
            checked_at="now",
            sync_run_id="sync-1",
            total=2,
            completed=1,
            failed=1,
            results=[{"status": "success"}, {"status": "failed"}],
        )["status"]
        == "partial"
    )
    assert (
        sync_agentops.sync_agentops_summary(
            checked_at="now",
            sync_run_id="sync-1",
            total=1,
            completed=0,
            failed=0,
            results=[],
        )["status"]
        == "failed"
    )
