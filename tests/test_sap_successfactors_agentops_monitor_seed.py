from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/init/99t_sap_successfactors_talent_agentops_monitor.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_successfactors_talent_monitor_is_workspace_scoped():
    sql = _sql()
    assert "sap_successfactors_talent_monitor" in sql
    assert "tenant_id, workspace_id" in sql
    assert "FROM cartridge_installations ci" in sql
    assert "ci.cartridge_id = 'sap_successfactors'" in sql
    assert "ci.status = 'ready'" in sql
    assert "COALESCE(te.status, 'active') = 'active'" in sql
    assert "NOT EXISTS (SELECT 1 FROM active_scope)" in sql
    assert "workspace_id = p.workspace_id" in sql


def test_successfactors_talent_monitor_has_agentops_tools_and_contract():
    sql = _sql()
    for tool in (
        "mcp-infra__wisdom_bits__run",
        "mcp-infra__control_room__raise_analysis_alert",
        "mcp-infra__decision__orchestrate",
        "mcp-infra__simulation__monte_carlo_run",
        "mcp-infra__calibration__bayesian_state",
    ):
        assert tool in sql
    for token in (
        '"role": "monitor"',
        '"schedule"',
        '"monitor"',
        '"wisdom_bit_id": "WB-TALENTO"',
        '"recommendation_only": true',
        '"writeback_enabled": false',
        '"engines"',
        '"name": "monte_carlo"',
        '"enabled": true',
        '"source_type": "wisdom_bit"',
        '"source_id": "WB-TALENTO"',
        '"seed": 45120',
        '"output_metric": "delta"',
        '"decision_mode": "recommendation_only"',
        '"name": "bayesian_calibration"',
        '"name": "decision_orchestrator"',
        '"enabled": true',
        '"execute_engines": true',
        '"bayesian_calibration"',
        '"calibration_group": "sap_successfactors:talent_readiness"',
        '"model_version": "bayesian_calibration.v1"',
        '"limit": 10',
        '"source_type": "wisdom_bit"',
        '"source_id": "WB-TALENTO"',
        "Sin full_name, user_id, PERNR, salario ni payCompValue",
    ):
        assert token in sql


def test_successfactors_talent_monitor_blocks_pii_and_writeback():
    sql = _sql().lower()
    for token in ("full_name", "pernr", "salario", "paycompvalue"):
        assert token in sql
    assert "no escribas en successfactors" in sql
    assert "no hay write-back externo" in sql
    assert not re.search(r"\bdelete\s+from\s+agents\b|\btruncate\b|\bdrop\b", sql)
