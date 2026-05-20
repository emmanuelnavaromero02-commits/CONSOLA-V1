from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.replicon_client import RepliconClient
from app.mcp_server import mcp

router = APIRouter(prefix="/health", tags=["health"])
_SERVICE = "replicon"


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


@router.get("/replicon", dependencies=[Depends(verify_api_key)])
def health_replicon() -> dict:
    try:
        client = RepliconClient()
        info = client.test_connection()
        return {"ok": True, "service": "replicon", **info}
    except EnvironmentError as exc:
        return JSONResponse(
            {
                "ok": False,
                "service": "replicon",
                "configured": False,
                "error": str(exc),
            },
            status_code=503,
        )
