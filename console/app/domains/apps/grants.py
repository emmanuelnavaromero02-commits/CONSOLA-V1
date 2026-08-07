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

# One authoritative call. Reading the grant here and checking the installation
# separately would leave a TOCTOU window: a cartridge can stop being ready
# between the two statements, and the read would still be served. The SQL
# function joins grant, active manifest, ready installation and the workspace's
# own dataset row in a single query.
_SELECT_GRANTS = "SELECT dataset_name FROM public.analytic_app_granted_datasets($1, $2)"


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
    served_html: str | None = None,
) -> list[dict[str, str]]:
    """Bring one packaged app's grants in line with the registry.

    Passes the app name and nothing else. The cartridge, the digest, the
    dataset list and the actor are all resolved inside the database from
    ``analytic_app_manifests`` — a caller able to supply any of them could
    grant itself anything, which is exactly the hole this closes.

    ``served_html`` is used only to state which revision the caller believes is
    current; the database compares it and aborts on a mismatch. It never
    selects the datasets.
    """
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
    """Reconcile every packaged app of a cartridge for the caller's scope.

    Strict: any database error propagates and rolls the caller's transaction
    back. Swallowing them meant an installation could be marked ready with its
    grants half-written and the audit trail recording success — an activation
    that silently produced a broken authority state is worse than one that
    fails.

    A user-created app is skipped by the database (no registry row) and a
    workspace whose Gold is not materialised simply gets fewer grants; neither
    is an error.
    """
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
    """Revoke every grant a cartridge holds in the caller's scope.

    Called in the same transaction that takes an installation out of ready, so
    a paused, revoked or failed cartridge stops authorising immediately rather
    than at the next reconciliation.
    """
    return int(
        await conn.fetchval(
            "SELECT public.revoke_analytic_app_cartridge_grants($1, $2)",
            str(cartridge_id), str(reason),
        )
        or 0
    )
