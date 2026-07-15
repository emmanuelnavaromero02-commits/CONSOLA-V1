from __future__ import annotations

import json

import pytest

from scripts.market_decision_aws_smoke import (
    SUMMARY_PREFIX,
    _parse_summary,
    _remote_script,
    _validate_summary,
)


def _passing_summary() -> dict:
    return {
        "status": "partial",
        "persisted_status": "partial",
        "source_mode": "benchmark_internal",
        "market_provider": "banxico",
        "market_metric": "usd_mxn_fix",
        "market_freshness": "ready",
        "market_evidence_count": 1,
        "simulation_id": "mc-123",
        "orchestration_id": "orch-123",
        "action_recommended": False,
        "external_action_id": None,
        "automatic_action": False,
        "external_writeback": False,
        "creates_calibration_observation": False,
        "bayes_state_count_before": 0,
        "bayes_state_count_after": 0,
        "bayes_unchanged": True,
    }


def test_remote_script_keeps_scope_backend_owned():
    script = _remote_script(tenant_id="tenant-a", workspace_id="workspace-a")

    assert "OMEGA_SMOKE_TENANT_ID=tenant-a" in script
    assert "OMEGA_SMOKE_WORKSPACE_ID=workspace-a" in script
    assert '"allowed_cartridges": ["sap_successfactors", "banxico"]' in script
    assert "validation.run_validation(user)" in script
    assert "validation._bayes_states(user)" in script


def test_parse_and_validate_summary():
    expected = _passing_summary()
    stdout = "noise\n" + SUMMARY_PREFIX + json.dumps(expected) + "\n"

    parsed = _parse_summary(stdout)
    _validate_summary(parsed)

    assert parsed == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("market_freshness", "stale"),
        ("market_evidence_count", 0),
        ("action_recommended", True),
        ("external_action_id", "action-1"),
        ("bayes_unchanged", False),
    ],
)
def test_validate_summary_fails_closed(field, value):
    summary = _passing_summary()
    summary[field] = value

    with pytest.raises(RuntimeError):
        _validate_summary(summary)


def test_parse_summary_requires_structured_marker():
    with pytest.raises(RuntimeError, match="structured summary"):
        _parse_summary("no smoke result")
