from __future__ import annotations

from typing import Any

from app.services import audit_service
from app.services.intelligence.baseline import build_metric_artifacts
from app.services.intelligence.contracts import load_contracts
from app.services.intelligence.external import list_sources, patch_source, run_sources
from app.services.intelligence.gold_fetcher import query_intelligence_dataset_rows
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
    tenant_id, workspace_id = workspace_scope(user)
    allowed = allowed_cartridges(user)
    requested_cartridge = str((body or {}).get("cartridge_id") or "").strip()
    cartridge_filter = {requested_cartridge} if requested_cartridge else allowed
    contracts = load_contracts(cartridge_filter)
    metric_filter = {
        str(value).strip()
        for value in (body or {}).get("metrics", [])
        if str(value or "").strip()
    } if isinstance((body or {}).get("metrics"), list) else set()
    include_external = bool((body or {}).get("include_external"))
    dry_run = bool((body or {}).get("dry_run"))
    horizons = _requested_horizons(body)
    fetch = fetcher or query_intelligence_dataset_rows
    artifacts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
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
    should_persist = persist and not dry_run
    if should_persist and artifacts:
        await persist_artifacts(tenant_id, workspace_id, user, artifacts)
    if should_persist:
        await audit_service.record_event(
            user.get("id"),
            user.get("email"),
            "intelligence.run",
            "workspace",
            workspace_id,
            metadata={
                "signals": len(artifacts),
                "skipped": len(skipped),
                "cartridge_id": requested_cartridge or None,
                "include_external": include_external,
                "horizon_days": horizons,
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
    }
