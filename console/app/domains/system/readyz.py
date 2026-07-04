from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

TRUEISH_VALUES = {"1", "true", "yes", "on"}
FALSEISH_VALUES = {"0", "false", "no", "off"}

CheckMap = dict[str, dict[str, Any]]


def _is_trueish(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUEISH_VALUES


def _is_falseish(value: Any) -> bool:
    return str(value or "").strip().lower() in FALSEISH_VALUES


async def build_readyz_checks(
    *,
    app: Any,
    query_params: Mapping[str, Any],
    authenticated_user: dict[str, Any] | None,
    environ: Mapping[str, str],
    refinement_url: str,
    mcp_infra_url: str,
    vault_url: Callable[[], str],
    startup_readiness_status: Callable[[Any], dict[str, Any]],
    get_db_pool: Callable[[], Awaitable[Any]],
    dependency_health: Callable[[str, str, str | None], Awaitable[dict[str, Any]]],
    control_room_data_check: Callable[..., Awaitable[dict[str, Any]]],
    intelligence_readiness: Callable[..., Awaitable[dict[str, Any]]],
    is_production_env: Callable[[], bool],
    warn: Callable[..., None],
) -> tuple[CheckMap, bool]:
    checks: CheckMap = {}
    checks["startup"] = startup_readiness_status(app)
    if checks["startup"].get("status") != "up":
        return checks, False

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = {"status": "up"}
    except Exception as exc:
        warn("readiness probe failed for postgres", exc_info=True)
        checks["postgres"] = {"status": "down", "error": type(exc).__name__}

    deps = {
        "refinement": (f"{refinement_url.rstrip('/')}/healthz", "REFINEMENT"),
        "mcp-infra": (f"{mcp_infra_url.rstrip('/')}/healthz", "MCP_INFRA"),
        "vault": (f"{vault_url()}/healthz", "VAULT"),
    }
    for name, (url, server) in deps.items():
        checks[name] = await dependency_health(name, url, server)

    require_data = _is_trueish(environ.get("CONTROL_ROOM_REQUIRE_DATA_READY")) or _is_trueish(
        query_params.get("require_data")
    )
    require_intelligence_param = str(query_params.get("require_intelligence") or "").strip().lower()
    intelligence_opt_out_allowed = (
        not is_production_env()
        and _is_falseish(require_intelligence_param)
    )
    require_intelligence_data = (
        _is_trueish(environ.get("CONTROL_ROOM_REQUIRE_INTELLIGENCE_READY"))
        or _is_trueish(require_intelligence_param)
        or (require_data and is_production_env() and not intelligence_opt_out_allowed)
    )

    if require_data:
        checks["control_room_data"] = await control_room_data_check(require_data=require_data)
    else:
        checks["control_room_data"] = {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }

    if require_intelligence_data:
        try:
            checks["intelligence_data"] = await intelligence_readiness(
                authenticated_user,
                require_data=require_intelligence_data,
            )
        except Exception as exc:
            warn("readiness probe failed for intelligence_data", exc_info=True)
            checks["intelligence_data"] = {
                "status": "degraded",
                "required": require_intelligence_data,
                "error": type(exc).__name__,
            }
    else:
        checks["intelligence_data"] = {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }

    dependency_ok = all(
        check.get("status") == "up"
        for name, check in checks.items()
        if name not in {"control_room_data", "intelligence_data"}
    )
    data_ok = (
        checks["control_room_data"].get("status") == "up"
        and (
            checks["intelligence_data"].get("status") == "up"
            or not require_intelligence_data
        )
    ) or not require_data
    return checks, dependency_ok and data_ok
