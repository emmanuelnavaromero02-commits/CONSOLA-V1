from __future__ import annotations

from pathlib import Path

from tests.console_route_source import console_route_source


ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = ROOT / "console/app/main.py"


def test_agent_crud_and_manual_invoke_use_workspace_agent_permissions():
    src = console_route_source()
    agents_section = (ROOT / "console/app/routers/v1/agents.py").read_text(
        encoding="utf-8"
    ).replace("@router.", "@app.")
    assert 'require_permission("agents.read")' in agents_section
    assert 'require_permission("agents.write")' in agents_section
    assert 'require_permission("agents.execute")' in agents_section
    assert "await _agents.get_agent(agent_id, user_context=user)" in src
    assert '@app.post("/api/agents/{agent_id}/invoke", dependencies=[Depends(require_csrf), Depends(require_permission("agents.execute"))])' in src
    assert '@app.post("/api/agents/{agent_id}/invoke/stream", dependencies=[Depends(require_csrf), Depends(require_permission("agents.execute"))])' in src


def test_scheduled_agent_invoke_requires_enabled_cron_and_constant_time_token():
    src = console_route_source()
    section = src.split('async def api_agents_invoke_scheduled', 1)[1].split('@app.post("/api/agents/{agent_id}/invoke/stream"', 1)[0]
    assert "secrets.compare_digest" in section
    assert "agent schedule is not enabled" in section
    assert "agent schedule cron is required" in section
    assert "agent schedule is not due" in section
    assert "_agent_schedule_due(schedule)" in section
    assert 'extra.get("schedule")' in section
