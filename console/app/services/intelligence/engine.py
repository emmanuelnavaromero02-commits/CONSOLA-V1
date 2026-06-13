from __future__ import annotations

from collections import Counter
import time
from typing import Any

from fastapi import HTTPException

from app.services import audit_service
from app.services.intelligence.baseline import build_metric_artifacts
from app.services.intelligence.contracts import load_contracts
from app.services.intelligence.external import list_sources, patch_source, run_sources
from app.services.intelligence.gold_fetcher import query_intelligence_dataset_rows
from app.services.intelligence.history import (
    datasets_from_contracts,
    fail_intelligence_run,
    finish_intelligence_run,
    normalize_run_mode,
    start_intelligence_run,
)
from app.services.intelligence.persistence import (
    get_signal,
    list_signals,
    persist_artifacts,
    record_outcome,
    select_option,
)
from app.services.intelligence.utils import DEFAULT_LIMIT, DatasetFetcher, allowed_cartridges, workspace_scope


def _requested_horizons(body: dict[str, Any] | None) -> list[int] | None:
    raw = (body or {}).get("horizon_days")
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    output: set[int] = set()
    for value in values:
        try:
            horizon = int(value)
        except (TypeError, ValueError):
            continue
        if horizon > 0:
            output.add(horizon)
    return sorted(output)


async def run_intelligence(
    user: dict,
    body: dict[str, Any] | None = None,
    *,
    fetcher: DatasetFetcher | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    tenant_id, workspace_id = workspace_scope(user)
    allowed = allowed_cartridges(user)
    requested_cartridge = str((body or {}).get("cartridge_id") or "").strip()
    if requested_cartridge and allowed is not None and requested_cartridge not in allowed:
        raise HTTPException(403, f"cartridge '{requested_cartridge}' is not active for this workspace")
    cartridge_filter = {requested_cartridge} if requested_cartridge else allowed
    contracts = load_contracts(cartridge_filter)
    datasets_evaluated = datasets_from_contracts(contracts)
    metric_filter = {
        str(value).strip()
        for value in (body or {}).get("metrics", [])
        if str(value or "").strip()
    } if isinstance((body or {}).get("metrics"), list) else set()
    include_external = bool((body or {}).get("include_external"))
    dry_run = bool((body or {}).get("dry_run"))
    run_mode = normalize_run_mode((body or {}).get("run_mode") or (body or {}).get("mode"))
    horizons = _requested_horizons(body)
    fetch = fetcher or query_intelligence_dataset_rows
    artifacts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    should_persist = persist and not dry_run
    run_record: dict[str, Any] | None = None
    if should_persist:
        run_record = await start_intelligence_run(
            user,
            request=body or {},
            source_system=requested_cartridge or None,
            run_mode=run_mode,
            datasets_evaluated=datasets_evaluated,
        )
    for contract in contracts:
        metrics = contract.get("metrics") if isinstance(contract.get("metrics"), list) else []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            if metric_filter and str(metric.get("id")) not in metric_filter:
                continue
            dataset = str(metric.get("dataset") or "").strip()
            if not dataset:
                continue
            try:
                rows = await fetch(dataset, user, DEFAULT_LIMIT)
            except Exception as exc:
                skipped.append(
                    {
                        "cartridge_id": contract.get("cartridge"),
                        "dataset": dataset,
                        "metric": metric.get("id"),
                        "status": "dataset_unavailable",
                        "reason": str(getattr(exc, "detail", exc)),
                    }
                )
                continue
            metric_artifacts, metric_skipped = build_metric_artifacts(
                contract,
                metric,
                rows,
                include_external=include_external,
                horizon_days=horizons,
            )
            artifacts.extend(metric_artifacts)
            skipped.extend(metric_skipped)
    skipped_counts = Counter(str(item.get("status") or "unknown") for item in skipped)
    if should_persist and artifacts:
        try:
            await persist_artifacts(
                tenant_id,
                workspace_id,
                user,
                artifacts,
                intelligence_run_id=int(run_record["id"]) if run_record else None,
                run_ref=str(run_record["run_ref"]) if run_record else None,
            )
        except Exception as exc:
            if run_record:
                duration_ms = int((time.perf_counter() - started) * 1000)
                await fail_intelligence_run(
                    user,
                    run_id=int(run_record["id"]),
                    error=str(getattr(exc, "detail", exc)),
                    duration_ms=duration_ms,
                )
            raise
    if should_persist:
        duration_ms = int((time.perf_counter() - started) * 1000)
        run_status = "completed" if artifacts else "not_ready"
        if run_record:
            run_record = await finish_intelligence_run(
                user,
                run_id=int(run_record["id"]),
                status=run_status,
                artifacts=artifacts,
                skipped=skipped,
                datasets_evaluated=datasets_evaluated,
                duration_ms=duration_ms,
            )
        await audit_service.record_event(
            user.get("id"),
            user.get("email"),
            "intelligence.run",
            "workspace",
            workspace_id,
            metadata={
                "intelligence_run_id": run_record.get("id") if run_record else None,
                "run_ref": run_record.get("run_ref") if run_record else None,
                "run_mode": run_mode,
                "status": run_status,
                "signals": len(artifacts),
                "skipped": len(skipped),
                "skipped_counts": dict(skipped_counts),
                "dataset_unavailable_count": skipped_counts.get("dataset_unavailable", 0),
                "insufficient_history_count": skipped_counts.get("insufficient_history", 0),
                "cartridge_id": requested_cartridge or None,
                "include_external": include_external,
                "horizon_days": horizons,
                "duration_ms": duration_ms,
            },
        )
    return {
        "signals": [artifact["signal"] for artifact in artifacts],
        "artifacts": artifacts,
        "skipped": skipped,
        "contracts": [
            {
                "cartridge": contract.get("cartridge"),
                "domain": contract.get("domain"),
                "metrics": [metric.get("id") for metric in contract.get("metrics", []) if isinstance(metric, dict)],
            }
            for contract in contracts
        ],
        "workspace_id": workspace_id,
        "dry_run": dry_run,
        "run_mode": run_mode,
        "intelligence_run_id": run_record.get("id") if run_record else None,
        "run_ref": run_record.get("run_ref") if run_record else None,
        "dataset_unavailable_count": skipped_counts.get("dataset_unavailable", 0),
        "insufficient_history_count": skipped_counts.get("insufficient_history", 0),
        "skipped_counts": dict(skipped_counts),
    }
