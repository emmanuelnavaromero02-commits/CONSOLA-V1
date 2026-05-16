from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.sap_client import SapHcmClient
from app.mcp_server import mcp

router = APIRouter(prefix="/health", tags=["health"])

_SERVICE = "sap_hcm"


@router.get("")
async def health(request: Request):
    # v1.43.2 (Codex P1-5): /health mirrors real startup state.
    state = request.app.state
    ok = getattr(state, "startup_ok", False)
    errors = list(getattr(state, "startup_errors", []) or [])
    if not ok:
        return JSONResponse(
            {
                "ok": False,
                "service": _SERVICE,
                "reason": "startup_failed",
                "startup_errors": errors,
            },
            status_code=503,
        )

    # v1.43.4 (Codex C2): /health must additionally reflect the MCP
    # contract. v1.43.3 shipped with fastmcp 2.5.0 pinned + 3.x API
    # in main.py — every /mcp/tools request 500'd while the
    # container reported HEALTHY because /health only checked
    # startup_ok. The console's mcp_servers row then had healthy=f
    # and tool_count=0, contradicting `docker compose ps`. Make
    # /health probe the MCP surface so the two views agree.
    try:
        tools = await mcp.list_tools()
    except Exception as exc:  # pragma: no cover — defensive
        return JSONResponse(
            {
                "ok": False,
                "service": _SERVICE,
                "reason": "mcp_unreachable",
                "error": str(exc),
            },
            status_code=503,
        )
    if not tools:
        return JSONResponse(
            {
                "ok": False,
                "service": _SERVICE,
                "reason": "mcp_no_tools_registered",
                "tool_count": 0,
            },
            status_code=503,
        )
    return JSONResponse(
        {
            "ok": True,
            "service": _SERVICE,
            "tool_count": len(tools),
            "startup_errors": [],
        },
        status_code=200,
    )


@router.get("/sap_hcm", dependencies=[Depends(verify_api_key)])
def health_sap_hcm() -> dict:
    info = SapHcmClient().test_connection()
    return {"service": "sap_hcm", **info}
