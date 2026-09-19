from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.attested_monitor_alerts import (
    load_attested_monitor_alerts,
)
from app.services.control_room.business_projection import project_business_item


@dataclass(frozen=True)
class SurfaceScope:
    tenant_id: str
    workspace_id: str


@dataclass(frozen=True)
class SurfaceSnapshot:
    generated_at: datetime
    scope: SurfaceScope
    items: tuple[Mapping[str, object], ...]
    diagnostics: tuple[Mapping[str, object], ...]
    sources: tuple[Mapping[str, object], ...]
    installations: tuple[Mapping[str, object], ...]
    # Mission 5: narratives of attested monitor alerts, keyed by item id. Kept
    # beside the items rather than inside them, so narrative text can never
    # take part in an item's eligibility or observation checks.
    narratives: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


def _scope(user: Mapping[str, object]) -> SurfaceScope:
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace_id = str(
        user.get("active_workspace_id") or user.get("workspace_id") or ""
    ).strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(status_code=404, detail="workspace scope not found")
    return SurfaceScope(tenant_id=tenant_id, workspace_id=workspace_id)


def _rows(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _assert_row_scope(
    rows: Sequence[Mapping[str, object]],
    scope: SurfaceScope,
) -> None:
    for row in rows:
        tenant_id = str(row.get("tenant_id") or "").strip()
        workspace_id = str(row.get("workspace_id") or "").strip()
        if tenant_id and tenant_id != scope.tenant_id:
            raise HTTPException(status_code=404, detail="workspace scope not found")
        if workspace_id and workspace_id != scope.workspace_id:
            raise HTTPException(status_code=404, detail="workspace scope not found")


def validate_snapshot_scope(snapshot: SurfaceSnapshot) -> None:
    _assert_row_scope(snapshot.items, snapshot.scope)
    _assert_row_scope(snapshot.diagnostics, snapshot.scope)
    _assert_row_scope(snapshot.sources, snapshot.scope)
    _assert_row_scope(snapshot.installations, snapshot.scope)


async def collect_surface_snapshot(
    user: Mapping[str, object],
) -> SurfaceSnapshot:
    scope = _scope(user)
    payload = await control_room_service._collect_items(
        dict(user),
        fetcher=control_room_service.query_dataset_rows,
        include_source_state_items=True,
        persist=False,
        use_catalog=True,
        item_projector=project_business_item,
    )
    live_items = _rows(payload.get("items"))
    # Mission 5: attested scheduled-monitor alerts are not Gold rows, so the
    # live collection above never produces them. They join the same snapshot
    # and pass the same scope validation below.
    monitor_alerts = await load_attested_monitor_alerts(user)
    live_ids = {str(item.get("id") or "") for item in live_items}
    monitor_items = tuple(
        item
        for item in monitor_alerts.items
        if str(item.get("id") or "") not in live_ids
    )
    kept_ids = {str(item.get("id") or "") for item in monitor_items}
    snapshot = SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=scope,
        items=(*live_items, *monitor_items),
        diagnostics=_rows(payload.get("diagnostics")),
        sources=_rows(payload.get("sources")),
        installations=_rows(payload.get("installations")),
        narratives={
            item_id: narrative
            for item_id, narrative in monitor_alerts.narratives.items()
            if item_id in kept_ids
        },
    )
    validate_snapshot_scope(snapshot)
    return snapshot


__all__ = (
    "SurfaceScope",
    "SurfaceSnapshot",
    "collect_surface_snapshot",
    "validate_snapshot_scope",
)
