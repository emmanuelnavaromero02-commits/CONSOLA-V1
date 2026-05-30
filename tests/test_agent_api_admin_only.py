from __future__ import annotations

from pathlib import Path

from tests.console_route_source import console_route_source


ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = ROOT / "console/app/main.py"


def test_agent_crud_and_manual_invoke_are_admin_only_until_acl_exists():
    src = console_route_source()
    agents_section = (ROOT / "console/app/routers/v1/agents.py").read_text(
        encoding="utf-8"
    ).replace("@router.", "@app.")
    assert "workspace-admin surfaces" not in agents_section
    assert "require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)" not in agents_section
    assert '@app.get("/api/agents", dependencies=[Depends(require_admin)])' in agents_section
    assert '@app.get("/api/agents/{agent_id}", dependencies=[Depends(require_admin)])' in agents_section
    assert '@app.post("/api/agents/{agent_id}/invoke", dependencies=[Depends(require_csrf), Depends(require_admin)])' in src
    assert '@app.post("/api/agents/{agent_id}/invoke/stream", dependencies=[Depends(require_csrf), Depends(require_admin)])' in src


def test_scheduled_agent_invoke_requires_enabled_cron_and_constant_time_token():
    src = console_route_source()
    section = src.split('async def api_agents_invoke_scheduled', 1)[1].split('@app.post("/api/agents/{agent_id}/invoke/stream"', 1)[0]
    assert "secrets.compare_digest" in section
    assert "agent schedule is not enabled" in section
    assert "agent schedule cron is required" in section
    assert "agent schedule is not due" in section
    assert "_agent_schedule_due(schedule)" in section
    assert 'extra.get("schedule")' in section
