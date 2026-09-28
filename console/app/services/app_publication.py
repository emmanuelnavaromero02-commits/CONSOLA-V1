from __future__ import annotations

import hashlib
import logging

from app.domains.apps.grants import reconcile_workspace_app
from app.domains.apps.manifests import (
    APP_NAME_RE,
    WORKSPACE_CARTRIDGE_ID,
    manifest_digest,
)
from app.domains.apps.payloads import DATASET_NAME_RE
from app.services.db_pool import get_db_pool

logger = logging.getLogger(__name__)


class AppPublicationError(ValueError):
    """A workspace app publication could not be registered."""


def html_sha256(html: str) -> str:
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


def scope_from_user(user: dict | None) -> tuple[str, str]:
    user = user or {}
    tenant = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace = str(
        user.get("active_workspace_id") or user.get("workspace_id") or ""
    ).strip()
    return tenant, workspace


async def register_workspace_app(
    user_scope: tuple[str, str],
    app_name: str,
    html: str,
    datasets: list[str],
) -> str:
    tenant_id, workspace_id = (str(user_scope[0] or ""), str(user_scope[1] or ""))
    if not tenant_id or not workspace_id:
        raise AppPublicationError("workspace scope is required to register the app")
    if not APP_NAME_RE.fullmatch(str(app_name or "")):
        raise AppPublicationError("app name is invalid")
    clean_datasets = sorted(
        {str(item) for item in (datasets or []) if isinstance(item, str)}
    )
    if any(not DATASET_NAME_RE.fullmatch(item) for item in clean_datasets):
        raise AppPublicationError("dataset name is invalid")
    if not isinstance(html, str) or not html.strip():
        raise AppPublicationError("html is required")

    digest = manifest_digest(
        app_name=str(app_name),
        cartridge_id=WORKSPACE_CARTRIDGE_ID,
        datasets=clean_datasets,
        html=html,
    )
    sha = html_sha256(html)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), "
                "set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            stored = await conn.fetchval(
                "SELECT public.register_workspace_app_manifest($1, $2, $3::text[], $4)",
                str(app_name),
                sha,
                clean_datasets,
                digest,
            )
            if str(stored or "") != digest:
                raise AppPublicationError("registered digest mismatch")
            actions = await reconcile_workspace_app(conn, app_name=str(app_name))
    logger.info(
        "[app-publication] registered %s (%d datasets, %d grant actions)",
        app_name,
        len(clean_datasets),
        len(actions),
    )
    return digest
