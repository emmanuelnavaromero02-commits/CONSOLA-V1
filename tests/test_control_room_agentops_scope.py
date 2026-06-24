from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "console/app/services/control_room/api.py"


def _agents_ops_source() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    return text.split("async def agents_ops", 1)[1].split("async def get_item", 1)[0]


def test_control_room_agentops_filters_by_allowed_cartridges():
    section = _agents_ops_source()
    assert "allowed_cartridges = _allowed_from_user(user)" in section
    assert "allowed_param = None if allowed_cartridges is None else sorted(allowed_cartridges)" in section
    assert "a.cartridge_id = ANY($3::text[])" in section
    assert "cartridge_id = ANY($3::text[])" in section
    assert "cartridge_id = ANY($2::text[])" in section
    assert "cartridge_id = 'platform'" in section
    assert "_agentops_tool_is_operational(tool)" in section
    assert "_agentops_tool_label(tool)" in section
    assert "_agentops_monitor_engines(monitor)" in section
    assert '"configured_engines": configured_engines' in section
    assert '"monte_carlo_simulations"' in section
    assert '"bayesian_calibration_states"' in section
    assert '"bayesian_calibration_samples"' in section
    assert '"decision_orchestrations"' in section
    assert '"engines": engines_payload' in section


def test_control_room_agentops_accepts_legacy_infra_alias():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'name.startswith("infra__")' in source
    assert 'return f"mcp-infra__{name.split' in source
