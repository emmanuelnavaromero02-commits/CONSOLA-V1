from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/control-room-postgres-rls.yml"


def test_operational_truth_e2e_is_required_by_canonical_gate() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    e2e = jobs["operational-truth-e2e"]
    gate = jobs["control-room-gate"]

    assert e2e["needs"] == "changes"
    assert e2e["if"] == "needs.changes.outputs.control_room == 'true'"
    assert e2e["timeout-minutes"] == 35
    assert any(
        step.get("run") == "scripts/run_operational_truth_e2e.sh"
        for step in e2e["steps"]
    )
    assert "operational-truth-e2e" in gate["needs"]
    gate_env = gate["steps"][0]["env"]
    assert gate_env["OPERATIONAL_TRUTH_E2E_RESULT"] == (
        "${{ needs['operational-truth-e2e'].result }}"
    )


def test_e2e_runner_does_not_install_duckdb_extensions_at_runtime() -> None:
    runtime_files = [
        ROOT / "infra/e2e/compose.apps.yml",
        ROOT / "infra/e2e/compose.test.yml",
        ROOT / "scripts/run_operational_truth_e2e.sh",
    ]
    for path in runtime_files:
        assert "INSTALL httpfs" not in path.read_text(encoding="utf-8")
        assert "INSTALL postgres" not in path.read_text(encoding="utf-8")
