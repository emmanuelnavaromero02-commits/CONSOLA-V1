from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


async def run_sync_control_room_gold_refresh(
    *,
    cartridge: str,
    row: dict[str, Any],
    child_rows: list[dict[str, Any]],
    gold_refresh_summary: dict[str, Any],
    user: dict[str, Any] | None,
    build_security_context: Any,
    sync_gold_refresh_dataset_names: Any,
    sync_gold_refresh_airflow_run_id: Any,
    sync_control_room: Any,
    run_intelligence: Any | None = None,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    datasets = sync_gold_refresh_dataset_names(gold_refresh_summary)
    if not datasets:
        return sync_control_room.gold_refresh_skipped_payload(checked_at)
    ctx = build_security_context(user)
    tenant_id = str(
        ctx.get("tenant_id") or ctx.get("active_tenant_id") or ""
    ).strip()
    workspace_id = str(
        ctx.get("workspace_id") or ctx.get("active_workspace_id") or ""
    ).strip()
    if not tenant_id or not workspace_id:
        return sync_control_room.gold_refresh_missing_scope_payload(
            checked_at=checked_at,
            datasets=datasets,
        )
    airflow_dag_run_id = sync_gold_refresh_airflow_run_id(row, child_rows)
    run_ref = sync_control_room.gold_refresh_run_ref(
        workspace_id=workspace_id,
        cartridge=cartridge,
        airflow_dag_run_id=airflow_dag_run_id,
    )
    payload = sync_control_room.gold_refresh_intelligence_payload(
        cartridge=cartridge,
        datasets=datasets,
        row=row,
        airflow_dag_run_id=airflow_dag_run_id,
        run_ref=run_ref,
        gold_refresh_summary=gold_refresh_summary,
    )
    try:
        if run_intelligence is None:
            from app.services import intelligence_engine

            run_intelligence = intelligence_engine.run_intelligence
        result = await run_intelligence(user, payload, persist=True)
    except Exception as exc:  # noqa: BLE001
        return sync_control_room.gold_refresh_error_payload(
            checked_at=checked_at,
            run_ref=run_ref,
            datasets=datasets,
            exc=exc,
        )
    return sync_control_room.gold_refresh_result_payload(
        checked_at=checked_at,
        run_ref=run_ref,
        datasets=datasets,
        result=result if isinstance(result, dict) else None,
    )
