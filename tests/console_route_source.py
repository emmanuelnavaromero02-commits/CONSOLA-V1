from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO_ROOT / "console" / "app" / "main.py"
CONSOLE_V1_ROUTERS = REPO_ROOT / "console" / "app" / "routers" / "v1"
CONSOLE_DOMAINS = REPO_ROOT / "console" / "app" / "domains"
CONSOLE_ROUTERS = REPO_ROOT / "console" / "app" / "routers"
CONSOLE_ROUTER_MODULES = (
    CONSOLE_ROUTERS / "pipeline_operations.py",
    CONSOLE_ROUTERS / "control_room_live.py",
    CONSOLE_ROUTERS / "control_room_actions.py",
    CONSOLE_ROUTERS / "control_room_council.py",
    CONSOLE_ROUTERS / "pipeline_automations.py",
)


def console_route_source() -> str:
    parts = [CONSOLE_MAIN.read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8").replace("@router.", "@app.")
        for path in sorted(CONSOLE_V1_ROUTERS.glob("*.py"))
        if path.name != "__init__.py"
    )
    parts.extend(
        path.read_text(encoding="utf-8")
        for path in sorted((CONSOLE_DOMAINS / "decisions").glob("*.py"))
        if path.name != "__init__.py"
    )
    parts.extend(
        path.read_text(encoding="utf-8").replace("@router.", "@app.")
        for path in CONSOLE_ROUTER_MODULES
    )
    return "\n".join(parts)
