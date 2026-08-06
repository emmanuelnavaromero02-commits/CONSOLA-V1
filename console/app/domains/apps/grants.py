"""Reading and reconciling the durable dataset grants.

Authority lives in ``analytic_app_dataset_grants`` and nowhere else. This module
only reads it, and asks the server-owned SECURITY DEFINER function to write it
from reviewed packaged manifests. No path here accepts a dataset name that came
from an app's HTML, its stored ``datasets_used`` or a runtime payload.
"""

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

_SELECT_GRANTS = """
    SELECT dataset_name
      FROM analytic_app_dataset_grants
     WHERE tenant_id = $1::uuid
       AND workspace_id = $2::uuid
       AND app_name = $3
       AND manifest_digest = $4
       AND revoked_at IS NULL
     ORDER BY dataset_name
"""


async def granted_datasets(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    app_name: str,
    manifest_digest: str | None,
) -> list[str]:
    """Datasets this app may read in this scope, under this exact digest.

    A missing digest means the app is not packaged, or what is being served no
    longer matches what was reviewed. Either way the answer is nothing — fail
    closed, rather than falling back to a laxer source.
    """
    if not manifest_digest or not APP_NAME_RE.fullmatch(str(app_name or "")):
        return []
    if not tenant_id or not workspace_id:
        return []
    rows = await conn.fetch(
        _SELECT_GRANTS, str(tenant_id), str(workspace_id), str(app_name),
        str(manifest_digest),
    )
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
    """One dataset, one answer. Used by the app-scoped endpoint, which is the
    definitive authority and must not trust the wrapper's own filtering."""
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
    served_html: str,
) -> list[dict[str, str]]:
    """Bring one packaged app's grants in line with its reviewed manifest.

    The dataset list comes from the packaged manifest; the digest covers the
    HTML actually being served. A user-created or unknown app is a no-op: it
    gets no grants, ever, from this path.
    """
    manifest = packaged_manifest(app_name)
    if manifest is None:
        return []
    digest = served_digest(app_name, served_html)
    if digest is None:
        return []
    rows = await conn.fetch(
        "SELECT dataset_name, action FROM reconcile_analytic_app_dataset_grants("
        "$1, $2, $3::text[], $4, $5)",
        manifest["app_name"],
        manifest["cartridge_id"],
        list(manifest["datasets"]),
        digest,
        "server:packaged_manifest",
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
    """Reconcile every packaged app of a cartridge for the caller's scope.

    Called from the server-owned activation/installation flow, so a workspace
    gets its grants when the cartridge becomes available — not when a user
    happens to open an app. Opening an app must never be what approves it.
    """
    manifests = load_packaged_manifests()
    summary: dict[str, Any] = {"granted": 0, "revoked": 0, "apps": []}
    for name, manifest in sorted(manifests.items()):
        if cartridge_id and manifest["cartridge_id"] != cartridge_id:
            continue
        html = manifest["packaged_html"]
        if app_html_loader is not None:
            try:
                html = await app_html_loader(name)
            except Exception:
                logger.debug("[app-grants] no served html for %s", name, exc_info=True)
                continue
        try:
            actions = await reconcile_app(conn, app_name=name, served_html=html)
        except Exception:
            # A workspace without this cartridge installed is refused by the
            # database guard; that is expected, not an error worth raising.
            logger.debug("[app-grants] skipped %s", name, exc_info=True)
            continue
        granted = sum(1 for a in actions if a["action"] == "granted")
        revoked = sum(1 for a in actions if a["action"] == "revoked")
        summary["granted"] += granted
        summary["revoked"] += revoked
        if actions:
            summary["apps"].append(
                {"app": name, "granted": granted, "revoked": revoked}
            )
    return summary


async def revoke_grant(
    conn: Any, *, app_name: str, dataset: str, reason: str = "explicit_revocation"
) -> int:
    return int(
        await conn.fetchval(
            "SELECT revoke_analytic_app_dataset_grant($1, $2, $3, $4)",
            str(app_name), str(dataset), "server:revocation", str(reason),
        )
        or 0
    )
