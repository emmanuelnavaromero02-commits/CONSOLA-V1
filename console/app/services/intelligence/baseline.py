from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.intelligence.contracts import configured_horizons, validate_metric
from app.services.intelligence.decision_intelligence import (
    build_decision_intelligence,
    build_future_reserved_decision_intelligence,
    build_insufficient_history_decision_intelligence,
)
from app.services.intelligence.evidence import dataset_evidence_pack
from app.services.intelligence.external import build_external_evidence
from app.services.intelligence.hypotheses import hypotheses
from app.services.intelligence.prediction import build_prediction_signal
from app.services.intelligence.scoring import decision_options
from app.services.intelligence.utils import (
    confidence,
    field_or_literal,
    num,
    period_key,
    severity,
    signal_type,
    stable_id,
    time_key,
)


def build_metric_artifacts(
    contract: dict[str, Any],
    metric: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    include_external: bool = False,
    horizon_days: list[int] | None = None,
    external_sources: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validate_metric(contract, metric)
    cartridge_id = str(contract.get("cartridge") or "")
    domain = str(contract.get("domain") or "Operacion")
    metric_id = str(metric["id"])
    metric_name = str(metric.get("name") or metric_id)
    dataset = str(metric["dataset"])
    entity = metric["entity"]
    id_field = str(entity.get("id_field") or "")
    label_field = str(entity.get("label_field") or id_field)
    entity_kind = str(entity.get("kind") or "entity")
    time_field = str(metric["time_field"])
    value_field = str(metric["value_field"])
    expected_behavior = str(metric.get("expected_behavior") or "watch")
    baseline = (
        metric.get("baseline") if isinstance(metric.get("baseline"), dict) else {}
    )
    method = str(baseline.get("method") or "moving_average")
    minimum_history = int(baseline.get("minimum_history") or 2)
    window = int(baseline.get("window") or 6)
    rules = (
        metric.get("signal_rules")
        if isinstance(metric.get("signal_rules"), dict)
        else {}
    )
    warning_pct = float(rules.get("warning_pct") or 0.20)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if id_field == "__all__":
            entity_id = "__all__"
        else:
            raw_entity = row.get(id_field)
            if raw_entity is None or str(raw_entity).strip() == "":
                continue
            entity_id = str(raw_entity)
        if num(row.get(value_field)) is None:
            continue
        grouped[entity_id].append(row)

    artifacts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for entity_id, entity_rows in sorted(grouped.items()):
        ordered = sorted(entity_rows, key=lambda row: time_key(row.get(time_field)))
        latest = ordered[-1]
        actual = num(latest.get(value_field))
        if actual is None:
            continue
        history_values = [
            parsed
            for row in ordered[:-1][-window:]
            if (parsed := num(row.get(value_field))) is not None
        ]
        if len(history_values) < minimum_history:
            missing_fields = [
                field
                for field in (time_field, value_field)
                if field and field not in latest
            ]
            if id_field != "__all__" and id_field not in latest:
                missing_fields.append(id_field)
            skipped.append(
                {
                    "cartridge_id": cartridge_id,
                    "dataset": dataset,
                    "metric": metric_id,
                    "entity_id": entity_id,
                    "status": "insufficient_history",
                    "sample_count": len(history_values),
                    "minimum_history": minimum_history,
                    "decision_intelligence": build_insufficient_history_decision_intelligence(
                        metric=metric,
                        history_points=len(history_values),
                        actual_value=actual,
                        deviation_value=None,
                        missing_fields=sorted(set(missing_fields)),
                    ),
                }
            )
            continue
        expected = sum(history_values) / len(history_values)
        deviation = actual - expected
        deviation_pct = (
            0.0
            if expected == 0 and actual == 0
            else (1.0 if expected == 0 else deviation / abs(expected))
        )
        abs_pct = abs(deviation_pct)
        entity_label = field_or_literal(latest, label_field, entity_id)
        latest_period_key = period_key(latest, time_field)
        base_signal = _base_signal(
            cartridge_id=cartridge_id,
            dataset=dataset,
            domain=domain,
            metric_id=metric_id,
            metric_name=metric_name,
            entity_kind=entity_kind,
            entity_id=entity_id,
            entity_label=entity_label,
            period_key=latest_period_key,
            actual=actual,
            expected=expected,
            deviation=deviation,
            deviation_pct=deviation_pct,
            rules=rules,
            expected_behavior=expected_behavior,
            sample_count=len(history_values),
            signal_subtype="observed",
        )
        if abs_pct >= warning_pct:
            artifacts.append(
                _artifact(
                    contract,
                    metric,
                    base_signal,
                    latest,
                    history_values,
                    method,
                    window,
                    include_external=include_external,
                    external_sources=external_sources,
                )
            )
        else:
            skipped.append(
                {
                    "cartridge_id": cartridge_id,
                    "dataset": dataset,
                    "metric": metric_id,
                    "entity_id": entity_id,
                    "status": "within_threshold",
                    "deviation_pct": round(deviation_pct, 4),
                }
            )
        for horizon in configured_horizons(metric, horizon_days):
            predicted = build_prediction_signal(
                base_signal=base_signal,
                history_values=history_values,
                horizon_days=horizon,
                warning_pct=warning_pct,
                rules=rules,
            )
            if not predicted:
                continue
            artifacts.append(
                _artifact(
                    contract,
                    metric,
                    predicted,
                    latest,
                    history_values,
                    method,
                    window,
                    include_external=include_external,
                    external_sources=external_sources,
                )
            )
    return artifacts, skipped


def _base_signal(
    *,
    cartridge_id: str,
    dataset: str,
    domain: str,
    metric_id: str,
    metric_name: str,
    entity_kind: str,
    entity_id: str,
    entity_label: str,
    period_key: str,
    actual: float,
    expected: float,
    deviation: float,
    deviation_pct: float,
    rules: dict[str, Any],
    expected_behavior: str,
    sample_count: int,
    signal_subtype: str,
) -> dict[str, Any]:
    abs_pct = abs(deviation_pct)
    return {
        "signal_id": stable_id(
            {
                "cartridge": cartridge_id,
                "dataset": dataset,
                "metric": metric_id,
                "entity": entity_id,
                "period": period_key,
            }
        ),
        "cartridge_id": cartridge_id,
        "dataset": dataset,
        "domain": domain,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "entity_label": entity_label,
        "metric": metric_id,
        "metric_name": metric_name,
        "period_key": period_key,
        "actual_value": round(actual, 4),
        "expected_value": round(expected, 4),
        "deviation_value": round(deviation, 4),
        "deviation_pct": round(deviation_pct, 4),
        "severity": severity(abs_pct, rules),
        "signal_type": signal_type(expected_behavior, actual, expected),
        "signal_subtype": signal_subtype,
        "status": "open",
        "confidence": confidence(sample_count, abs_pct),
        "expected_behavior": expected_behavior,
        "prediction_horizon_days": None,
        "predicted_value": None,
        "prediction_method": None,
        "summary": (
            f"{metric_name}: {entity_label} esta {actual:.2f} vs esperado "
            f"{expected:.2f} ({deviation_pct:+.1%})."
        ),
        "source_system": cartridge_id,
        "source_dataset": dataset,
        "gold_table": f"gold_{dataset}",
        "freshness_at": period_key,
    }


def _artifact(
    contract: dict[str, Any],
    metric: dict[str, Any],
    signal: dict[str, Any],
    latest: dict[str, Any],
    history_values: list[float],
    method: str,
    window: int,
    *,
    include_external: bool,
    external_sources: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    dataset = str(metric["dataset"])
    entity = metric["entity"]
    id_field = str(entity.get("id_field") or "")
    time_field = str(metric["time_field"])
    value_field = str(metric["value_field"])
    external_items = (
        build_external_evidence(contract, metric, signal, external_sources)
        if include_external
        else []
    )
    source_system = str(contract.get("cartridge") or signal.get("cartridge_id") or "")
    freshness_at = period_key(latest, time_field)
    signal.update(
        {
            "source_system": source_system,
            "source_dataset": dataset,
            "gold_table": f"gold_{dataset}",
            "freshness_at": freshness_at,
            "freshness_field": time_field,
        }
    )
    evidence = dataset_evidence_pack(
        dataset=dataset,
        id_field=id_field,
        entity_id=str(signal["entity_id"]),
        time_field=time_field,
        value_field=value_field,
        latest=latest,
        history_values=history_values,
        method=method,
        confidence=float(signal["confidence"]),
        source_system=source_system,
        gold_table=f"gold_{dataset}",
        freshness_at=freshness_at,
        external_items=external_items,
    )
    signal["configured_hypotheses"] = (
        metric.get("hypotheses") if isinstance(metric.get("hypotheses"), list) else []
    )
    baseline_payload = {
        "method": method,
        "sample_count": len(history_values),
        "window": window,
        "period_key": signal["period_key"],
        "actual_value": signal["actual_value"],
        "expected_value": signal["expected_value"],
        "predicted_value": signal.get("predicted_value"),
        "prediction_horizon_days": signal.get("prediction_horizon_days"),
        "prediction_method": signal.get("prediction_method"),
        "history_values": [round(value, 4) for value in history_values],
        "confidence": signal["confidence"],
        "source_system": source_system,
        "source_dataset": dataset,
        "gold_table": f"gold_{dataset}",
        "freshness_at": freshness_at,
        "freshness_field": time_field,
    }
    if str(signal.get("signal_subtype") or "").startswith("future_"):
        decision_intelligence = build_future_reserved_decision_intelligence(
            metric=metric,
            signal=signal,
            history_points=len(history_values),
        )
    else:
        decision_intelligence = build_decision_intelligence(
            metric=metric,
            signal=signal,
            baseline=baseline_payload,
            latest=latest,
            history_values=history_values,
        )
    signal["decision_intelligence"] = decision_intelligence
    return {
        "baseline": baseline_payload,
        "signal": signal,
        "evidence_pack": evidence,
        "decision_intelligence": decision_intelligence,
        "hypotheses": hypotheses(signal, evidence["items"]),
        "options": decision_options(signal, metric),
        "outcome": None,
    }
