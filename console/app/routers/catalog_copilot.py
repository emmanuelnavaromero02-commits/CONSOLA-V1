from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends

from app.dependencies import ROLE_ADMIN, ROLE_WORKSPACE_ADMIN, require_any_role
from app.domains.data_platform.catalog_copilot_requests import (
    auto_profile_payload,
    reject_relationship_payload,
)
from app.domains.data_platform.refinement_errors import (
    raise_for_refinement_payload_error,
    upstream_error_detail,
)
from app.domains.data_platform.refinement_invoke import (
    refinement_invoke as refinement_invoke_impl,
)
from app.domains.data_platform.scoped_reads import scoped_read_cache_invalidate
from app.domains.security.internal_auth import (
    internal_outbound_headers as internal_outbound_headers_impl,
)
from app.middleware.request_id import request_id_var
from app.security import get_internal_api_key
from app.services.csrf import require_csrf
from app.services.mcp_payloads import mcp_payload
from app.services.permissions import require_permission

# Absolute paths and no prefix: tests/console_route_source.py reads this file
# with @router. rewritten to @app. for the CSRF and auth-gap audits.
router = APIRouter(tags=["catalog"])


def _hdr_for(server: str) -> dict[str, str]:
    return internal_outbound_headers_impl(
        server,
        internal_api_key=get_internal_api_key(),
        is_production=os.environ.get("APP_ENV", "production").strip().lower()
        in {"production", "prod"},
        request_id=request_id_var.get(),
    )


async def _refinement_invoke(
    tool: str, args: dict, *, timeout: int = 30, user: dict | None = None
) -> Any:
    return await refinement_invoke_impl(
        tool,
        args,
        timeout=timeout,
        user=user,
        httpx_module=httpx,
        hdr_for=_hdr_for,
        mcp_payload=mcp_payload,
        upstream_error_detail=upstream_error_detail,
        raise_for_refinement_payload_error=raise_for_refinement_payload_error,
    )


@router.post(
    "/api/catalog/auto-profile",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.read")),
    ],
)
async def api_catalog_auto_profile(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("datasets.read")),
):
    return await auto_profile_payload(
        body=body,
        user=user,
        refinement_invoke=_refinement_invoke,
        scoped_read_cache_invalidate=scoped_read_cache_invalidate,
    )


@router.post(
    "/api/catalog/relationships/reject",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_catalog_relationship_reject(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("datasets.write")),
):
    return await reject_relationship_payload(
        body=body,
        user=user,
        refinement_invoke=_refinement_invoke,
        scoped_read_cache_invalidate=scoped_read_cache_invalidate,
    )
