from __future__ import annotations

from collections import Counter
import time
from typing import Any

from fastapi import HTTPException

from app.services import audit_service
from app.services.intelligence import calibration, calibration_service
from app.services.intelligence.baseline import build_metric_artifacts
from app.services.intelligence.contracts import load_contracts
from app.services.intelligence.external import list_sources, patch_source, run_sources
from app.services.intelligence.gold_fetcher import (
    query_gold_dataset_population,
    query_intelligence_dataset_rows,
)
from app.services.intelligence.gold_control_room import (
    RULESET_VERSION,
    build_generic_gold_artifacts,
)
from app.services.intelligence.history import (
    datasets_from_contracts,
    fail_intelligence_run,
    finish_intelligence_run,
    get_run_by_ref,
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
from app.services.intelligence.outcome_binding import (
    persist_gold_refresh_binding,
    persist_gold_refresh_pending,
    resume_failed_gold_refresh,
    stage_gold_refresh_authority,
)
from app.services.intelligence.publication_trace import (
    capture_publication_trace,
    current_publication_trace,
    publication_trace_active,
)
from app.services.intelligence.utils import (
    DEFAULT_LIMIT,
    DatasetFetcher,
    allowed_cartridges,
    workspace_scope,
)


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


def _requested_dataset_filter(body: dict[str, Any] | None) -> set[str]:
    raw = (body or {}).get("datasets")
    if not isinstance(raw, list):
        return set()
    output: set[str] = set()
    for value in raw:
        text = str(value or "").strip()
        if (
            text
            and len(text) <= 128
            and text.replace("_", "").replace("-", "").isalnum()
        ):
            output.add(text)
    return output


def _requested_calibration_groups(
    contracts: list[dict[str, Any]],
    metric_filter: set[str],
) -> set[str]:
    groups: set[str] = set()
    for contract in contracts:
        source_system = str(contract.get("cartridge") or "")
        metrics = (
            contract.get("metrics") if isinstance(contract.get("metrics"), list) else []
        )
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            metric_id = str(metric.get("id") or "").strip()
            if metric_filter and metric_id not in metric_filter:
                continue
            groups.update(
                calibration.live_calibration_groups(
                    source_system=source_system,
                    metric_id=metric_id,
                )
            )
    return groups


async def _load_live_calibration_states(
    user: dict,
    contracts: list[dict[str, Any]],
    metric_filter: set[str],
) -> dict[str, dict[str, Any]]:
    groups = _requested_calibration_groups(contracts, metric_filter)
    if not groups:
        return {}
    try:
        return await calibration_service.get_state_map_for_live_calibration(
            user,
            groups,
            model_version=calibration.MODEL_VERSION,
        )
    except Exception:
        return {}


async def run_intelligence(
    user: dict,
    body: dict[str, Any] | None = None,
    *,
    fetcher: DatasetFetcher | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    requested_mode = normalize_run_mode(
        (body or {}).get("run_mode") or (body or {}).get("mode")
    )
    if persist and requested_mode == "gold_refresh" and not publication_trace_active():
        with capture_publication_trace():
            return await run_intelligence(
                user,
                body,
                fetcher=fetcher,
                persist=persist,
            )
    started = time.perf_counter()
    tenant_id, workspace_id = workspace_scope(user)
    allowed = allowed_cartridges(user)
    requested_cartridge = str((body or {}).get("cartridge_id") or "").strip()
    if (
        requested_cartridge
        and allowed is not None
        and requested_cartridge not in allowed
    ):
        raise HTTPException(
            403, f"cartridge '{requested_cartridge}' is not active for this workspace"
        )
    cartridge_filter = {requested_cartridge} if requested_cartridge else allowed
    contracts = load_contracts(cartridge_filter)
    dataset_filter = _requested_dataset_filter(body)
    all_datasets_evaluated = datasets_from_contracts(contracts)
    datasets_evaluated = [
        item
        for item in all_datasets_evaluated
        if not dataset_filter or str(item.get("dataset")) in dataset_filter
    ]
    metric_filter = (
        {
            str(value).strip()
            for value in (body or {}).get("metrics", [])
            if str(value or "").strip()
        }
        if isinstance((body or {}).get("metrics"), list)
        else set()
    )
    include_external = bool((body or {}).get("include_external"))
    dry_run = bool((body or {}).get("dry_run"))
    run_mode = normalize_run_mode(
        (body or {}).get("run_mode") or (body or {}).get("mode")
    )
    requested_run_ref = str((body or {}).get("run_ref") or "").strip()
    if run_mode != "gold_refresh":
        requested_run_ref = ""
    horizons = _requested_horizons(body)
    fetch = fetcher or query_intelligence_dataset_rows
    population_manifests: list[dict[str, Any]] = []

    async def _fetch_population(dataset: str) -> list[dict[str, Any]]:
        """E3 — ruta certificada sin cap: poblacion completa + manifiesto exacto.

        Con fetcher inyectado (tests/llamadores legacy) se conserva su contrato
        de 3 argumentos y el manifiesto declara la fuente; la ruta real lee la
        poblacion entera por cursor con COUNT en el mismo snapshot. Si el techo
        operativo corta la lectura, el run queda PARCIAL declarado via skipped
        (population_truncated) — jamas truncamiento silencioso.
        """
        if fetcher is not None:
            rows = await fetch(dataset, user, DEFAULT_LIMIT)
            population_manifests.append(
                {
                    "dataset": dataset,
                    "population_total": len(rows),
                    "rows_fetched": len(rows),
                    "complete": True,
                    "source": "injected_fetcher",
                }
            )
            return rows
        rows, manifest = await query_gold_dataset_population(dataset, user)
        population_manifests.append(manifest)
        if not manifest.get("complete"):
            skipped.append(
                {
                    "cartridge_id": None,
                    "dataset": dataset,
                    "metric": None,
                    "status": "population_truncated",
                    "reason": (
                        f"lectura poblacional cortada por techo operativo: "
                        f"{manifest.get('rows_fetched')}/{manifest.get('population_total')} filas "
                        f"(INTELLIGENCE_POPULATION_MAX_ROWS={manifest.get('ceiling')}); "
                        "run parcial declarado"
                    ),
                }
            )
        return rows

    calibration_states = await _load_live_calibration_states(
        user, contracts, metric_filter
    )
    artifacts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    contract_datasets = {
        str(item.get("dataset") or "").strip()
        for item in all_datasets_evaluated
        if str(item.get("dataset") or "").strip()
    }
    missing_contract_datasets = sorted(dataset_filter - contract_datasets)
    should_persist = persist and not dry_run
    run_record: dict[str, Any] | None = None
    if should_persist:
        if requested_run_ref:
            existing_run = await get_run_by_ref(user, requested_run_ref)
            if existing_run:
                if existing_run.get("status") == "running":
                    if run_mode == "gold_refresh":
                        raise RuntimeError("Gold refresh is already in progress")
                    run_record = existing_run
                elif existing_run.get("status") == "failed":
                    run_record = await resume_failed_gold_refresh(
                        user,
                        run_id=int(existing_run["id"]),
                        run_ref=requested_run_ref,
                        request=body or {},
                    )
                else:
                    if (
                        run_mode == "gold_refresh"
                        and existing_run.get("status") == "binding_pending"
                    ):
                        await persist_gold_refresh_binding(
                            user,
                            expected_run_id=str(
                                ((body or {}).get("metadata") or {}).get(
                                    "pipeline_run_id"
                                )
                                or ""
                            ),
                            expected_run_ref=requested_run_ref,
                            intelligence_result={
                                **existing_run,
                                "intelligence_run_id": existing_run["id"],
                            },
                            publication_trace=current_publication_trace(),
                            expected_datasets=sorted(dataset_filter),
                        )
                        existing_run = {**existing_run, "status": "completed"}
                    skipped_counts = Counter(
                        str(item.get("status") or "unknown") for item in skipped
                    )
                    return {
                        "signals": [],
                        "artifacts": [],
                        "skipped": skipped,
                        "contracts": [
                            {
                                "cartridge": contract.get("cartridge"),
                                "domain": contract.get("domain"),
                                "metrics": [
                                    metric.get("id")
                                    for metric in contract.get("metrics", [])
                                    if isinstance(metric, dict)
                                ],
                            }
                            for contract in contracts
                        ],
                        "workspace_id": workspace_id,
                        "dry_run": dry_run,
                        "run_mode": run_mode,
                        "intelligence_run_id": existing_run.get("id"),
                        "run_ref": existing_run.get("run_ref"),
                        "idempotent": True,
                        "status": existing_run.get("status"),
                        "signals_generated": int(
                            existing_run.get("signals_generated") or 0
                        ),
                        "dataset_unavailable_count": existing_run.get(
                            "dataset_unavailable_count", 0
                        ),
                        "insufficient_history_count": existing_run.get(
                            "insufficient_history_count", 0
                        ),
                        "skipped_counts": dict(skipped_counts),
                        "generic_gold_signal_count": 0,
                        "monte_carlo_counts": {},
                        "math_ruleset_version": RULESET_VERSION,
                    }
        if run_record is None:
            run_record = await start_intelligence_run(
                user,
                request=body or {},
                source_system=requested_cartridge or None,
                run_mode=run_mode,
                datasets_evaluated=datasets_evaluated,
                run_ref=requested_run_ref or None,
            )
    for contract in contracts:
        metrics = (
            contract.get("metrics") if isinstance(contract.get("metrics"), list) else []
        )
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            if metric_filter and str(metric.get("id")) not in metric_filter:
                continue
            dataset = str(metric.get("dataset") or "").strip()
            if not dataset:
                continue
            if dataset_filter and dataset not in dataset_filter:
                continue
            try:
                rows = await _fetch_population(dataset)
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
                calibration_states=calibration_states,
            )
            for artifact in metric_artifacts:
                artifact["signal"]["run_mode"] = run_mode
            artifacts.extend(metric_artifacts)
            skipped.extend(metric_skipped)
    for dataset in missing_contract_datasets:
        try:
            rows = await _fetch_population(dataset)
        except Exception as exc:
            skipped.append(
                {
                    "cartridge_id": requested_cartridge or None,
                    "dataset": dataset,
                    "metric": None,
                    "status": "dataset_unavailable",
                    "reason": str(getattr(exc, "detail", exc)),
                    "control_origin": "generic_gold_signal",
                }
            )
            continue
        generic_artifacts, generic_skipped = build_generic_gold_artifacts(
            cartridge_id=requested_cartridge or "platform",
            dataset=dataset,
            rows=rows,
            build_metric_artifacts=build_metric_artifacts,
        )
        for artifact in generic_artifacts:
            artifact["signal"]["run_mode"] = run_mode
        if generic_artifacts:
            skipped.append(
                {
                    "cartridge_id": requested_cartridge or None,
                    "dataset": dataset,
                    "metric": None,
                    "status": "missing_contract",
                    "reason": "no declared intelligence contract; generic Gold signal fallback used",
                    "generated_signals": len(generic_artifacts),
                    "control_origin": "generic_gold_signal",
                }
            )
        else:
            skipped.append(
                {
                    "cartridge_id": requested_cartridge or None,
                    "dataset": dataset,
                    "metric": None,
                    "status": "missing_contract",
                    "reason": "no intelligence contract metric targets this refreshed Gold dataset",
                    "control_origin": "generic_gold_signal",
                }
            )
        artifacts.extend(generic_artifacts)
        skipped.extend(generic_skipped)
    skipped_counts = Counter(str(item.get("status") or "unknown") for item in skipped)
    monte_carlo_counts = Counter(
        str((artifact.get("monte_carlo") or {}).get("status") or "unknown")
        for artifact in artifacts
        if isinstance(artifact.get("monte_carlo"), dict)
    )
    generic_gold_signal_count = sum(
        1
        for artifact in artifacts
        if (
            artifact.get("control_origin")
            or artifact.get("signal", {}).get("control_origin")
        )
        == "generic_gold_signal"
    )
    gold_refresh_pending = bool(
        should_persist and artifacts and run_mode == "gold_refresh" and run_record
    )
    if gold_refresh_pending:
        try:
            await stage_gold_refresh_authority(
                user,
                expected_run_id=str(
                    ((body or {}).get("metadata") or {}).get("pipeline_run_id") or ""
                ),
                expected_run_ref=str(run_record["run_ref"]),
                intelligence_run_id=int(run_record["id"]),
                publication_trace=current_publication_trace(),
                expected_datasets=sorted(dataset_filter),
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            await fail_intelligence_run(
                user,
                run_id=int(run_record["id"]),
                error="Gold outcome authority unavailable",
                duration_ms=duration_ms,
            )
            raise RuntimeError("Gold outcome authority unavailable") from exc
    if should_persist and artifacts:
        try:
            if gold_refresh_pending:
                duration_ms = int((time.perf_counter() - started) * 1000)
                run_record = await persist_gold_refresh_pending(
                    user,
                    run_id=int(run_record["id"]),
                    run_ref=str(run_record["run_ref"]),
                    artifacts=artifacts,
                    skipped=skipped,
                    datasets_evaluated=datasets_evaluated,
                    duration_ms=duration_ms,
                )
                await persist_gold_refresh_binding(
                    user,
                    expected_run_id=str(
                        ((body or {}).get("metadata") or {}).get("pipeline_run_id")
                        or ""
                    ),
                    expected_run_ref=str(run_record["run_ref"]),
                    intelligence_result={
                        **run_record,
                        "intelligence_run_id": run_record["id"],
                    },
                    publication_trace=current_publication_trace(),
                    expected_datasets=sorted(dataset_filter),
                )
                run_record = {**run_record, "status": "completed"}
            else:
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
        if gold_refresh_pending:
            run_status = str(run_record.get("status") or "binding_pending")
        elif run_record:
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
                "generic_gold_signal_count": generic_gold_signal_count,
                "monte_carlo_counts": dict(monte_carlo_counts),
                "math_ruleset_version": RULESET_VERSION,
                "dataset_unavailable_count": skipped_counts.get(
                    "dataset_unavailable", 0
                ),
                "insufficient_history_count": skipped_counts.get(
                    "insufficient_history", 0
                ),
                "cartridge_id": requested_cartridge or None,
                "include_external": include_external,
                "horizon_days": horizons,
                "datasets_requested": sorted(dataset_filter),
                "duration_ms": duration_ms,
            },
        )
    return {
        "signals": [artifact["signal"] for artifact in artifacts],
        "artifacts": artifacts,
        "skipped": skipped,
        # E3 — manifiesto poblacional: un registro exacto por dataset leido en
        # la ruta certificada (population_total via COUNT en el mismo snapshot,
        # rows_fetched, complete). population_complete=False solo cuando algun
        # dataset quedo declarado parcial (population_truncated en skipped).
        "population": population_manifests,
        "population_complete": all(
            bool(item.get("complete")) for item in population_manifests
        ),
        "contracts": [
            {
                "cartridge": contract.get("cartridge"),
                "domain": contract.get("domain"),
                "metrics": [
                    metric.get("id")
                    for metric in contract.get("metrics", [])
                    if isinstance(metric, dict)
                ],
            }
            for contract in contracts
        ],
        "workspace_id": workspace_id,
        "dry_run": dry_run,
        "run_mode": run_mode,
        "intelligence_run_id": run_record.get("id") if run_record else None,
        "run_ref": run_record.get("run_ref") if run_record else None,
        "idempotent": False,
        "datasets_requested": sorted(dataset_filter),
        "dataset_unavailable_count": skipped_counts.get("dataset_unavailable", 0),
        "insufficient_history_count": skipped_counts.get("insufficient_history", 0),
        "skipped_counts": dict(skipped_counts),
        "generic_gold_signal_count": generic_gold_signal_count,
        "monte_carlo_counts": dict(monte_carlo_counts),
        "math_ruleset_version": RULESET_VERSION,
    }
