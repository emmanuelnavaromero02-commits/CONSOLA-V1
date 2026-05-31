from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO_ROOT / "console" / "app" / "main.py"
CONSOLE_V1_ROUTERS = REPO_ROOT / "console" / "app" / "routers" / "v1"


def console_route_source() -> str:
    """Return the static console route source after the v1 router split.

    Older guard tests inspected only console/app/main.py because every
    endpoint lived there. The production contract is now main.py plus the v1
    route modules. Normalize @router decorators to @app so existing route
    regex/AST assertions keep checking the same API contract.
    """
    parts = [CONSOLE_MAIN.read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8").replace("@router.", "@app.")
        for path in sorted(CONSOLE_V1_ROUTERS.glob("*.py"))
        if path.name != "__init__.py"
    )
    return "\n".join(parts)
