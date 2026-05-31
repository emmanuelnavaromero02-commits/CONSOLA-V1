from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.hubspot_client import HubSpotClient
from app.mcp_server import mcp

router = APIRouter(prefix="/health", tags=["health"])
_SERVICE = "hubspot"


@router.get("")
async def health(request: Request):
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
    try:
        tools = await mcp.list_tools()
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "service": _SERVICE, "reason": "mcp_unreachable", "error": str(exc)},
            status_code=503,
        )
    if not tools:
        return JSONResponse(
            {"ok": False, "service": _SERVICE, "reason": "mcp_no_tools_registered", "tool_count": 0},
            status_code=503,
        )
    return JSONResponse(
        {"ok": True, "service": _SERVICE, "tool_count": len(tools), "startup_errors": []},
        status_code=200,
    )


@router.get("/hubspot", dependencies=[Depends(verify_api_key)])
def health_hubspot():
    try:
        client = HubSpotClient()
        info = client.test_connection()
        status = str(info.get("status") or "").strip().lower()
        ok = status == "ok"
        return JSONResponse(
            {"ok": ok, "service": "hubspot", **info},
            status_code=200 if ok else 503,
        )
    except EnvironmentError as exc:
        return JSONResponse(
            {
                "ok": False,
                "service": "hubspot",
                "configured": False,
                "error": str(exc),
            },
            status_code=503,
        )
