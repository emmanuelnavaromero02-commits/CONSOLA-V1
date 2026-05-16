from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.replicon_client import RepliconClient
from app.mcp_server import mcp

router = APIRouter(prefix="/health", tags=["health"])

_SERVICE = "replicon"


@router.get("")
async def health(request: Request):
    # v1.43.2 (Codex P1-5): /health must mirror real startup state.
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


@router.get("/replicon")
def health_replicon() -> dict:
    client = RepliconClient()
    info = client.test_connection()
    return {"ok": True, "service": "replicon", **info}
