from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_control_room_persists_intelligence_behind_business_experience():
    service = read("console/app/services/control_room/api.py")
    state = read("console/app/services/control_room/state.py")
    omega_projection = read(
        "console/app/services/control_room/business_omega_projection.py"
    )
    router = read("console/app/routers/control_room.py")
    ui = read("console-next/src/app/(shell)/control-room/page.tsx")

    assert "async def _persisted_business_items" in service
    assert "_persisted_intelligence_items" in service
    assert 'kinds=("intelligence_signal",)' in service
    assert "Legacy agent_alert rows" in service
    runtime_projection = read(
        "console/app/services/control_room/business_runtime_projection.py"
    )
    assert '== "agent_alert"' in runtime_projection
    assert "quarantined from summaries and AgentOps projections" in runtime_projection
    assert "build_omega_projection(" in state
    assert '"decision_intelligence": decision_intelligence' in omega_projection
    assert '"omega":' in omega_projection
    assert "ControlRoomExperiencePage" in ui
    assert '"intelligence_signal"' not in ui
    assert "IntelligencePanel" not in ui
    assert '"/decision-intelligence/runs"' in router
    assert '"/decision-intelligence/history"' in router
    assert '"/decision-intelligence/calibration"' in router
    assert 'Depends(require_permission("datasets.read"))' in router
