from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.sap_client import SapS4Client
from app.mcp_server import mcp

router = APIRouter(prefix="/health", tags=["health"])

_SERVICE = "sap_s4hana"


@router.get("")
async def health(request: Request):
    # v1.43.2 (Codex P1-5): mirror real startup state.
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

    # v1.43.4 (Codex C2): /health probes MCP surface — see
    # cartridges/sap_hcm/app/api/routes_health.py for full rationale.
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


@router.get("/sap_s4hana", dependencies=[Depends(verify_api_key)])
def health_sap_s4hana() -> dict:
    info = SapS4Client().test_connection()
    return {"service": "sap_s4hana", **info}
