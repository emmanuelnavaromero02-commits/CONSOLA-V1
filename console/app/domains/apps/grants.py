from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.domains.apps.manifests import (
    APP_NAME_RE,
    load_packaged_manifests,
    packaged_manifest,
    served_digest,
)

logger = logging.getLogger(__name__)

_RECONCILIATION_LOCK_PREFIX = "omega:app-grants:"

_SELECT_GRANTS = "SELECT dataset_name FROM public.analytic_app_granted_datasets($1, $2)"


async def lock_workspace_reconciliation(conn: Any, *, workspace_id: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        _RECONCILIATION_LOCK_PREFIX + str(workspace_id),
    )


async def granted_datasets(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    app_name: str,
    manifest_digest: str | None,
) -> list[str]:
    if not manifest_digest or not APP_NAME_RE.fullmatch(str(app_name or "")):
        return []
    if not tenant_id or not workspace_id:
        return []
    rows = await conn.fetch(_SELECT_GRANTS, str(app_name), str(manifest_digest))
    return [str(row["dataset_name"]) for row in rows]


async def has_grant(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    app_name: str,
    manifest_digest: str | None,
    dataset: str,
) -> bool:
    if not dataset:
        return False
    return str(dataset) in await granted_datasets(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        app_name=app_name,
        manifest_digest=manifest_digest,
    )


async def reconcile_app(
    conn: Any,
    *,
    app_name: str,
    served_html: str | None = None,
) -> list[dict[str, str]]:
    manifest = packaged_manifest(app_name)
    if manifest is None:
        return []
    expected = served_digest(app_name, served_html) if served_html is not None else None
    rows = await conn.fetch(
        "SELECT dataset_name, action FROM "
        "public.reconcile_analytic_app_dataset_grants($1, $2)",
        manifest["app_name"],
        expected,
    )
    return [
        {"dataset": str(r["dataset_name"]), "action": str(r["action"])} for r in rows
    ]


async def reconcile_workspace(
    conn: Any,
    *,
    cartridge_id: str | None = None,
    app_html_loader: Callable[[str], Awaitable[str]] | None = None,
) -> dict[str, Any]:
    manifests = load_packaged_manifests()
    summary: dict[str, Any] = {"granted": 0, "revoked": 0, "apps": []}
    for name, manifest in sorted(manifests.items()):
        if cartridge_id and manifest["cartridge_id"] != cartridge_id:
            continue
        html = None
        if app_html_loader is not None:
            html = await app_html_loader(name)
        actions = await reconcile_app(conn, app_name=name, served_html=html)
        granted = sum(1 for a in actions if a["action"] == "granted")
        revoked = sum(1 for a in actions if a["action"] == "revoked")
        summary["granted"] += granted
        summary["revoked"] += revoked
        if actions:
            summary["apps"].append(
                {"app": name, "granted": granted, "revoked": revoked}
            )
    return summary


async def revoke_cartridge_grants(
    conn: Any, *, cartridge_id: str, reason: str = "installation_not_ready"
) -> int:
    return int(
        await conn.fetchval(
            "SELECT public.revoke_analytic_app_cartridge_grants($1, $2)",
            str(cartridge_id), str(reason),
        )
        or 0
    )
