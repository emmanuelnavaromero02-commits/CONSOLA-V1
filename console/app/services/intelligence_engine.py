from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from fastapi import HTTPException

from app.services import audit_service, auth, control_room_service


DatasetFetcher = Callable[[str, dict | None, int], Awaitable[list[dict[str, Any]]]]

DEFAULT_LIMIT = 5000
CONTRACT_PATH = Path("app/config/intelligence.yaml")
SIGNAL_KIND = "intelligence_signal"
TERMINAL_SIGNAL_STATUSES = {"approved", "dismissed", "resolved"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, default=_json_default)


def _public_json(value: Any) -> Any:
    return json.loads(_json_dumps(value))


def _workspace_scope(user: dict | None) -> tuple[str | None, str]:
    workspace_id = (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id")
    tenant_id = (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id")
    if not workspace_id:
        raise HTTPException(400, "active workspace is required")
    return (str(tenant_id) if tenant_id else None), str(workspace_id)


def _allowed_cartridges(user: dict | None) -> set[str] | None:
    if not user:
        return None
    values = user.get("allowed_cartridges") or user.get("active_cartridges")
    if not isinstance(values, list):
        return None
    cleaned = {str(value).strip() for value in values if str(value or "").strip()}
    if not cleaned or "*" in cleaned:
        return None
    return cleaned


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        parsed = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _time_key(value: Any) -> tuple[int, str]:
    if value is None:
        return (0, "")
    if isinstance(value, datetime):
        return (2, value.isoformat())
    if isinstance(value, date):
        return (2, value.isoformat())
    text = str(value).strip()
    if not text:
        return (0, "")
    for suffix in ("Z",):
        if text.endswith(suffix):
            text = text[: -len(suffix)] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return (2, parsed.isoformat())
    except ValueError:
        return (1, text)


def _period_key(row: dict[str, Any], time_field: str) -> str:
    value = row.get(time_field)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _field_or_literal(row: dict[str, Any], field: str | None, fallback: str) -> str:
    if not field:
        return fallback
    if field == "__all__":
        return fallback
    value = row.get(field)
    if value is not None and str(value).strip():
        return str(value)
    return field if field and field not in row else fallback


def _stable_id(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")
    return "intel:" + hashlib.sha256(raw).hexdigest()[:24]


def _severity(abs_pct: float, rules: dict[str, Any]) -> str:
    warning = float(rules.get("warning_pct") or 0.20)
    critical = float(rules.get("critical_pct") or 0.45)
    if abs_pct >= critical:
        return "critical"
    if abs_pct >= max(warning * 1.5, warning + 0.10):
        return "high"
    if abs_pct >= warning:
        return "medium"
    return "low"


def _signal_type(expected_behavior: str, actual: float, expected: float) -> str:
    if actual == expected:
        return "watch"
    higher_is_good = expected_behavior == "higher_is_good"
    lower_is_good = expected_behavior == "lower_is_good"
    if higher_is_good:
        return "opportunity" if actual > expected else "risk"
    if lower_is_good:
        return "risk" if actual > expected else "opportunity"
    return "watch"


def _confidence(sample_count: int, abs_pct: float) -> float:
    base = 0.45 + min(0.25, math.sqrt(max(sample_count, 1)) * 0.08)
    return round(min(0.95, base + min(0.20, abs_pct / 2)), 2)


def load_contracts(cartridge_ids: set[str] | None = None) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    cartridges_dir = _repo_root() / "cartridges"
    for path in sorted(cartridges_dir.glob(f"*/{CONTRACT_PATH}")):
        cartridge_id = path.parts[-4]
        if cartridge_ids and cartridge_id not in cartridge_ids:
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise HTTPException(500, f"invalid intelligence contract: {cartridge_id}") from exc
        if not isinstance(raw, dict):
            continue
        raw["cartridge"] = str(raw.get("cartridge") or cartridge_id)
        raw["_path"] = str(path)
        contracts.append(raw)
    return contracts


def _validate_metric(contract: dict[str, Any], metric: dict[str, Any]) -> None:
    required = ("id", "dataset", "value_field", "time_field", "entity")
    missing = [field for field in required if not metric.get(field)]
    if missing:
        cartridge = contract.get("cartridge") or "unknown"
        raise HTTPException(500, f"intelligence metric missing {','.join(missing)} in {cartridge}")
    entity = metric.get("entity")
    if not isinstance(entity, dict) or not entity.get("id_field"):
        raise HTTPException(500, f"intelligence metric entity is invalid in {metric.get('id')}")


def _hypotheses(signal: dict[str, Any], evidence_pack_id: int | None = None) -> list[dict[str, Any]]:
    signal_type = signal["signal_type"]
    expected_behavior = signal["expected_behavior"]
    confidence = float(signal["confidence"])
    entity_label = signal["entity_label"]
    metric_name = signal["metric_name"]
    if signal_type == "opportunity":
        title = "Incremento capturable frente al patron normal"
        rationale = (
            f"{entity_label} esta por arriba del baseline en {metric_name}; "
            "la prioridad es validar si el aumento es repetible y capturable."
        )
    elif expected_behavior == "lower_is_good":
        title = "Acumulacion por encima del comportamiento normal"
        rationale = (
            f"{entity_label} supera el baseline en {metric_name}; "
            "puede indicar backlog, costo, atraso o exposicion operativa."
        )
    else:
        title = "Caida frente al patron historico"
        rationale = (
            f"{entity_label} queda por debajo del baseline en {metric_name}; "
            "puede indicar perdida de demanda, capacidad o ejecucion."
        )
    return [
        {
            "hypothesis_key": "baseline_deviation",
            "title": title,
            "rationale": rationale,
            "confidence": round(max(0.30, confidence - 0.05), 2),
            "evidence_pack_id": evidence_pack_id,
        }
    ]


def _decision_options(signal: dict[str, Any], metric: dict[str, Any]) -> list[dict[str, Any]]:
    templates = metric.get("action_templates") if isinstance(metric.get("action_templates"), list) else []
    options: list[dict[str, Any]] = []
    impact_base = abs(float(signal.get("deviation_value") or 0))
    confidence = float(signal.get("confidence") or 0.50)
    for template in templates:
        if not isinstance(template, dict):
            continue
        option_id = str(template.get("id") or template.get("action_kind") or "option").strip()
        if not option_id:
            continue
        impact_multiplier = _num(template.get("impact_multiplier")) or 0
        cost = _num(template.get("cost")) or 0
        risk = _num(template.get("risk")) or 0
        time_cost = _num(template.get("time_cost")) or 0
        expected_impact = round(impact_base * impact_multiplier, 2)
        score = round(expected_impact * confidence - cost - risk - time_cost, 2)
        options.append(
            {
                "option_id": option_id,
                "label": str(template.get("label") or option_id.replace("_", " ").title()),
                "action_kind": str(template.get("action_kind") or option_id),
                "impact_expected": expected_impact,
                "confidence": confidence,
                "cost": cost,
                "risk": risk,
                "time_cost": time_cost,
                "score": score,
                "selected": False,
                "score_explanation": (
                    "score = impacto_esperado * confianza - costo - riesgo - tiempo"
                ),
            }
        )
    options.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    return options[:3]


def build_metric_artifacts(
    contract: dict[str, Any],
    metric: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _validate_metric(contract, metric)
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
    baseline = metric.get("baseline") if isinstance(metric.get("baseline"), dict) else {}
    method = str(baseline.get("method") or "moving_average")
    minimum_history = int(baseline.get("minimum_history") or 2)
    window = int(baseline.get("window") or 6)
    rules = metric.get("signal_rules") if isinstance(metric.get("signal_rules"), dict) else {}
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
        if _num(row.get(value_field)) is None:
            continue
        grouped[entity_id].append(row)

    artifacts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for entity_id, entity_rows in sorted(grouped.items()):
        ordered = sorted(entity_rows, key=lambda row: _time_key(row.get(time_field)))
        latest = ordered[-1]
        actual = _num(latest.get(value_field))
        if actual is None:
            continue
        history_values = [
            _num(row.get(value_field))
            for row in ordered[:-1][-window:]
            if _num(row.get(value_field)) is not None
        ]
        if len(history_values) < minimum_history:
            skipped.append(
                {
                    "cartridge_id": cartridge_id,
                    "dataset": dataset,
                    "metric": metric_id,
                    "entity_id": entity_id,
                    "status": "insufficient_history",
                    "sample_count": len(history_values),
                    "minimum_history": minimum_history,
                }
            )
            continue
        expected = sum(history_values) / len(history_values)
        deviation = actual - expected
        if expected == 0:
            deviation_pct = 0.0 if actual == 0 else 1.0
        else:
            deviation_pct = deviation / abs(expected)
        abs_pct = abs(deviation_pct)
        if abs_pct < warning_pct:
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
            continue
        entity_label = _field_or_literal(latest, label_field, entity_id)
        period_key = _period_key(latest, time_field)
        confidence = _confidence(len(history_values), abs_pct)
        severity = _severity(abs_pct, rules)
        signal_type = _signal_type(expected_behavior, actual, expected)
        signal_id = _stable_id(
            {
                "cartridge": cartridge_id,
                "dataset": dataset,
                "metric": metric_id,
                "entity": entity_id,
                "period": period_key,
            }
        )
        signal = {
            "signal_id": signal_id,
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
            "severity": severity,
            "signal_type": signal_type,
            "status": "open",
            "confidence": confidence,
            "expected_behavior": expected_behavior,
            "summary": (
                f"{metric_name}: {entity_label} esta {actual:.2f} vs esperado "
                f"{expected:.2f} ({deviation_pct:+.1%})."
            ),
        }
        evidence = {
            "summary": f"Baseline {method} con {len(history_values)} muestras historicas.",
            "confidence": confidence,
            "items": [
                {
                    "source_type": "dataset",
                    "source_ref": dataset,
                    "query_text": (
                        f"SELECT {time_field}, {id_field}, {value_field} "
                        f"FROM {dataset} WHERE {id_field} = '{entity_id}'"
                    ),
                    "data": {
                        "latest": _public_json(latest),
                        "history_values": [round(value, 4) for value in history_values],
                    },
                    "supports_hypothesis": "baseline_deviation",
                    "strength": confidence,
                }
            ],
        }
        baseline_payload = {
            "method": method,
            "sample_count": len(history_values),
            "window": window,
            "period_key": period_key,
            "actual_value": signal["actual_value"],
            "expected_value": signal["expected_value"],
            "history_values": [round(value, 4) for value in history_values],
            "confidence": confidence,
        }
        options = _decision_options(signal, metric)
        artifacts.append(
            {
                "baseline": baseline_payload,
                "signal": signal,
                "evidence_pack": evidence,
                "hypotheses": _hypotheses(signal),
                "options": options,
                "outcome": None,
            }
        )
    return artifacts, skipped


async def run_intelligence(
    user: dict,
    body: dict[str, Any] | None = None,
    *,
    fetcher: DatasetFetcher | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    allowed = _allowed_cartridges(user)
    requested_cartridge = str((body or {}).get("cartridge_id") or "").strip()
    cartridge_filter = {requested_cartridge} if requested_cartridge else allowed
    contracts = load_contracts(cartridge_filter)
    metric_filter = {
        str(value).strip()
        for value in (body or {}).get("metrics", [])
        if str(value or "").strip()
    } if isinstance((body or {}).get("metrics"), list) else set()
    fetch = fetcher or control_room_service.query_dataset_rows
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
            metric_artifacts, metric_skipped = build_metric_artifacts(contract, metric, rows)
            artifacts.extend(metric_artifacts)
            skipped.extend(metric_skipped)
    if persist and artifacts:
        await _persist_artifacts(tenant_id, workspace_id, user, artifacts)
    if persist:
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
    }


async def _persist_artifacts(
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
    artifacts: list[dict[str, Any]],
) -> None:
    pool = await auth.pool()
    for artifact in artifacts:
        signal = artifact["signal"]
        baseline = artifact["baseline"]
        evidence_pack = artifact["evidence_pack"]
        hypotheses = artifact["hypotheses"]
        options = artifact["options"]
        baseline_id = await _persist_baseline(pool, tenant_id, workspace_id, signal, baseline)
        signal["baseline_id"] = baseline_id
        pack_id = await _persist_evidence(pool, tenant_id, workspace_id, signal, evidence_pack)
        evidence_pack["id"] = pack_id
        for hypothesis in hypotheses:
            hypothesis["evidence_pack_id"] = pack_id
        await _persist_signal(pool, tenant_id, workspace_id, signal)
        await _persist_hypotheses(pool, tenant_id, workspace_id, signal, hypotheses)
        await _persist_options(pool, tenant_id, workspace_id, signal, options)
        await _publish_control_room_item(
            pool,
            tenant_id,
            workspace_id,
            user,
            {
                **artifact,
                "signal": signal,
                "evidence_pack": evidence_pack,
                "hypotheses": hypotheses,
                "options": options,
            },
        )


async def _persist_baseline(pool: Any, tenant_id: str | None, workspace_id: str, signal: dict[str, Any], baseline: dict[str, Any]) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO metric_baselines (
            tenant_id, workspace_id, cartridge_id, dataset, metric, entity_kind,
            entity_id, entity_label, period_key, method, actual_value,
            expected_value, sample_count, window_days, confidence, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16::jsonb)
        ON CONFLICT (workspace_id, cartridge_id, dataset, metric, entity_id, period_key, method)
        DO UPDATE SET
            actual_value = EXCLUDED.actual_value,
            expected_value = EXCLUDED.expected_value,
            sample_count = EXCLUDED.sample_count,
            window_days = EXCLUDED.window_days,
            confidence = EXCLUDED.confidence,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        signal["cartridge_id"],
        signal["dataset"],
        signal["metric"],
        signal["entity_kind"],
        signal["entity_id"],
        signal["entity_label"],
        signal["period_key"],
        baseline["method"],
        signal["actual_value"],
        signal["expected_value"],
        baseline["sample_count"],
        baseline["window"],
        baseline["confidence"],
        _json_dumps(baseline),
    )
    return int(row["id"])


async def _persist_signal(pool: Any, tenant_id: str | None, workspace_id: str, signal: dict[str, Any]) -> None:
    await pool.execute(
        """
        INSERT INTO intelligence_signals (
            signal_id, tenant_id, workspace_id, cartridge_id, dataset, domain,
            entity_kind, entity_id, entity_label, metric, period_key,
            actual_value, expected_value, deviation_value, deviation_pct,
            severity, signal_type, status, baseline_id, confidence, summary, metadata
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11,
            $12, $13, $14, $15,
            $16, $17, 'open', $18, $19, $20, $21::jsonb
        )
        ON CONFLICT (workspace_id, signal_id) DO UPDATE
        SET actual_value = EXCLUDED.actual_value,
            expected_value = EXCLUDED.expected_value,
            deviation_value = EXCLUDED.deviation_value,
            deviation_pct = EXCLUDED.deviation_pct,
            severity = EXCLUDED.severity,
            signal_type = EXCLUDED.signal_type,
            baseline_id = EXCLUDED.baseline_id,
            confidence = EXCLUDED.confidence,
            summary = EXCLUDED.summary,
            metadata = EXCLUDED.metadata,
            updated_at = NOW(),
            status = CASE
                WHEN intelligence_signals.status = ANY($22::text[])
                THEN intelligence_signals.status
                ELSE intelligence_signals.status
            END
        """,
        signal["signal_id"],
        tenant_id,
        workspace_id,
        signal["cartridge_id"],
        signal["dataset"],
        signal["domain"],
        signal["entity_kind"],
        signal["entity_id"],
        signal["entity_label"],
        signal["metric"],
        signal["period_key"],
        signal["actual_value"],
        signal["expected_value"],
        signal["deviation_value"],
        signal["deviation_pct"],
        signal["severity"],
        signal["signal_type"],
        signal.get("baseline_id"),
        signal["confidence"],
        signal["summary"],
        _json_dumps({"metric_name": signal.get("metric_name"), "expected_behavior": signal.get("expected_behavior")}),
        sorted(TERMINAL_SIGNAL_STATUSES),
    )


async def _persist_evidence(pool: Any, tenant_id: str | None, workspace_id: str, signal: dict[str, Any], pack: dict[str, Any]) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO evidence_packs (tenant_id, workspace_id, signal_id, summary, confidence, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        signal["signal_id"],
        pack["summary"],
        pack["confidence"],
        _json_dumps({"metric": signal["metric"], "dataset": signal["dataset"]}),
    )
    pack_id = int(row["id"])
    for item in pack.get("items", []):
        await pool.execute(
            """
            INSERT INTO evidence_items (
                tenant_id, workspace_id, evidence_pack_id, source_type,
                source_ref, query_text, data, supports_hypothesis, strength,
                metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
            """,
            tenant_id,
            workspace_id,
            pack_id,
            item.get("source_type") or "dataset",
            item.get("source_ref") or signal["dataset"],
            item.get("query_text"),
            _json_dumps(item.get("data") or {}),
            item.get("supports_hypothesis"),
            item.get("strength") or pack["confidence"],
            _json_dumps(item.get("metadata") or {}),
        )
    return pack_id


async def _persist_hypotheses(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    hypotheses: list[dict[str, Any]],
) -> None:
    for hypothesis in hypotheses:
        await pool.execute(
            """
            INSERT INTO hypotheses (
                tenant_id, workspace_id, signal_id, hypothesis_key, title,
                rationale, confidence, evidence_pack_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (workspace_id, signal_id, hypothesis_key) DO UPDATE
            SET title = EXCLUDED.title,
                rationale = EXCLUDED.rationale,
                confidence = EXCLUDED.confidence,
                evidence_pack_id = EXCLUDED.evidence_pack_id,
                metadata = EXCLUDED.metadata
            """,
            tenant_id,
            workspace_id,
            signal["signal_id"],
            hypothesis["hypothesis_key"],
            hypothesis["title"],
            hypothesis["rationale"],
            hypothesis["confidence"],
            hypothesis.get("evidence_pack_id"),
            _json_dumps(hypothesis.get("metadata") or {}),
        )


async def _persist_options(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    signal: dict[str, Any],
    options: list[dict[str, Any]],
) -> None:
    for option in options:
        await pool.execute(
            """
            INSERT INTO decision_options (
                tenant_id, workspace_id, signal_id, option_id, label, action_kind,
                impact_expected, confidence, cost, risk, time_cost, score, selected, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, FALSE, $13::jsonb)
            ON CONFLICT (workspace_id, signal_id, option_id) DO UPDATE
            SET label = EXCLUDED.label,
                action_kind = EXCLUDED.action_kind,
                impact_expected = EXCLUDED.impact_expected,
                confidence = EXCLUDED.confidence,
                cost = EXCLUDED.cost,
                risk = EXCLUDED.risk,
                time_cost = EXCLUDED.time_cost,
                score = EXCLUDED.score,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            tenant_id,
            workspace_id,
            signal["signal_id"],
            option["option_id"],
            option["label"],
            option["action_kind"],
            option["impact_expected"],
            option["confidence"],
            option["cost"],
            option["risk"],
            option["time_cost"],
            option["score"],
            _json_dumps({"score_explanation": option.get("score_explanation")}),
        )


async def _publish_control_room_item(
    pool: Any,
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
    artifact: dict[str, Any],
) -> None:
    signal = artifact["signal"]
    top_hypothesis = (artifact.get("hypotheses") or [{}])[0]
    top_option = (artifact.get("options") or [{}])[0]
    metadata = {
        "module": "Intelligence Engine",
        "description": signal["summary"],
        "recommendation": top_option.get("label") or "Revisar evidencia y decidir siguiente paso.",
        "root_cause": top_hypothesis.get("title") or "Desviacion contra baseline.",
        "impact": f"Desviacion {signal['deviation_value']:.2f} en {signal['metric_name']}.",
        "details": {
            "actual_value": signal["actual_value"],
            "expected_value": signal["expected_value"],
            "deviation_pct": signal["deviation_pct"],
            "evidence_pack_id": artifact.get("evidence_pack", {}).get("id"),
            "source": "intelligence_engine",
        },
        "sql": (artifact.get("evidence_pack", {}).get("items") or [{}])[0].get("query_text"),
        "intelligence": _public_json(artifact),
    }
    priority_score = int(max(0, min(100, round(float(signal["confidence"]) * 45 + abs(float(signal["deviation_pct"])) * 55))))
    await pool.execute(
        """
        INSERT INTO control_room_items (
            tenant_id, workspace_id, item_id, cartridge_id, domain, source_dataset,
            item_kind, title, severity, status, entity_kind, entity_id,
            entity_label, anomaly_type, metadata, impact_estimate,
            impact_currency, confidence, priority_score, selected_option_id,
            execution_status, first_seen_at, last_seen_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, 'open', $10, $11,
            $12, $13, $14::jsonb, $15,
            'USD', $16, $17, NULL,
            'not_started', NOW(), NOW()
        )
        ON CONFLICT (workspace_id, item_id) DO UPDATE
        SET cartridge_id = EXCLUDED.cartridge_id,
            domain = EXCLUDED.domain,
            source_dataset = EXCLUDED.source_dataset,
            item_kind = EXCLUDED.item_kind,
            title = EXCLUDED.title,
            severity = EXCLUDED.severity,
            entity_kind = EXCLUDED.entity_kind,
            entity_id = EXCLUDED.entity_id,
            entity_label = EXCLUDED.entity_label,
            anomaly_type = EXCLUDED.anomaly_type,
            metadata = control_room_items.metadata || EXCLUDED.metadata,
            impact_estimate = EXCLUDED.impact_estimate,
            confidence = EXCLUDED.confidence,
            priority_score = EXCLUDED.priority_score,
            last_seen_at = NOW(),
            status = CASE
                WHEN control_room_items.status = ANY($18::text[])
                THEN control_room_items.status
                ELSE control_room_items.status
            END
        """,
        tenant_id,
        workspace_id,
        signal["signal_id"],
        signal["cartridge_id"],
        signal["domain"],
        signal["dataset"],
        SIGNAL_KIND,
        signal["summary"],
        signal["severity"],
        signal["entity_kind"],
        signal["entity_id"],
        signal["entity_label"],
        signal["metric"],
        _json_dumps(metadata),
        abs(float(signal["deviation_value"])),
        signal["confidence"],
        priority_score,
        sorted(TERMINAL_SIGNAL_STATUSES),
    )
    await pool.execute(
        """
        INSERT INTO control_room_item_events (
            tenant_id, workspace_id, item_id, event_type, actor_id, actor_email, metadata
        )
        VALUES ($1, $2, $3, 'intelligence_signal_created', $4, $5, $6::jsonb)
        """,
        tenant_id,
        workspace_id,
        signal["signal_id"],
        user.get("id"),
        user.get("email"),
        _json_dumps({"metric": signal["metric"], "severity": signal["severity"]}),
    )


async def list_signals(user: dict, *, limit: int = 100) -> dict[str, Any]:
    _, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT signal_id, cartridge_id, dataset, domain, entity_kind, entity_id,
               entity_label, metric, period_key, actual_value, expected_value,
               deviation_value, deviation_pct, severity, signal_type, status,
               confidence, summary, metadata, created_at, updated_at
          FROM intelligence_signals
         WHERE workspace_id = $1
         ORDER BY updated_at DESC, severity DESC
         LIMIT $2
        """,
        workspace_id,
        max(1, min(int(limit or 100), 500)),
    )
    return {"signals": [_row_to_signal(row) for row in rows]}


async def get_signal(user: dict, signal_id: str) -> dict[str, Any]:
    _, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        SELECT signal_id, cartridge_id, dataset, domain, entity_kind, entity_id,
               entity_label, metric, period_key, actual_value, expected_value,
               deviation_value, deviation_pct, severity, signal_type, status,
               confidence, summary, metadata, created_at, updated_at
          FROM intelligence_signals
         WHERE workspace_id = $1
           AND signal_id = $2
        """,
        workspace_id,
        signal_id,
    )
    if not row:
        raise HTTPException(404, "intelligence signal not found")
    packs = await pool.fetch(
        """
        SELECT id, summary, confidence, metadata, created_at
          FROM evidence_packs
         WHERE workspace_id = $1
           AND signal_id = $2
         ORDER BY created_at DESC
         LIMIT 1
        """,
        workspace_id,
        signal_id,
    )
    evidence_pack = None
    if packs:
        pack = dict(packs[0])
        items = await pool.fetch(
            """
            SELECT id, source_type, source_ref, query_text, data, supports_hypothesis,
                   strength, metadata, created_at
              FROM evidence_items
             WHERE workspace_id = $1
               AND evidence_pack_id = $2
             ORDER BY strength DESC, id
            """,
            workspace_id,
            pack["id"],
        )
        evidence_pack = {**_public_json(pack), "items": [_public_json(dict(item)) for item in items]}
    hypotheses = await pool.fetch(
        """
        SELECT hypothesis_key, title, rationale, confidence, evidence_pack_id, metadata, created_at
          FROM hypotheses
         WHERE workspace_id = $1
           AND signal_id = $2
         ORDER BY confidence DESC
        """,
        workspace_id,
        signal_id,
    )
    options = await pool.fetch(
        """
        SELECT option_id, label, action_kind, impact_expected, confidence, cost,
               risk, time_cost, score, selected, metadata, created_at, updated_at
          FROM decision_options
         WHERE workspace_id = $1
           AND signal_id = $2
         ORDER BY score DESC
        """,
        workspace_id,
        signal_id,
    )
    outcomes = await pool.fetch(
        """
        SELECT id, option_id, action_taken, predicted_value, actual_value,
               prediction_error, outcome_summary, learned_rule, metadata, created_at
          FROM prediction_outcomes
         WHERE workspace_id = $1
           AND signal_id = $2
         ORDER BY created_at DESC
         LIMIT 5
        """,
        workspace_id,
        signal_id,
    )
    return {
        "signal": _row_to_signal(row),
        "evidence_pack": evidence_pack,
        "hypotheses": [_public_json(dict(item)) for item in hypotheses],
        "options": [_public_json(dict(item)) for item in options],
        "outcomes": [_public_json(dict(item)) for item in outcomes],
    }


async def select_option(user: dict, signal_id: str, option_id: str) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    option = await pool.fetchrow(
        """
        SELECT option_id, label, score
          FROM decision_options
         WHERE workspace_id = $1
           AND signal_id = $2
           AND option_id = $3
        """,
        workspace_id,
        signal_id,
        option_id,
    )
    if not option:
        raise HTTPException(404, "decision option not found")
    await pool.execute(
        "UPDATE decision_options SET selected = FALSE WHERE workspace_id = $1 AND signal_id = $2",
        workspace_id,
        signal_id,
    )
    await pool.execute(
        """
        UPDATE decision_options
           SET selected = TRUE, updated_at = NOW()
         WHERE workspace_id = $1
           AND signal_id = $2
           AND option_id = $3
        """,
        workspace_id,
        signal_id,
        option_id,
    )
    await pool.execute(
        """
        UPDATE control_room_items
           SET selected_option_id = $3,
               status = CASE WHEN status = 'open' THEN 'in_review' ELSE status END,
               metadata = metadata || $4::jsonb,
               last_seen_at = NOW()
         WHERE workspace_id = $1
           AND item_id = $2
        """,
        workspace_id,
        signal_id,
        option_id,
        _json_dumps({"selected_option_id": option_id}),
    )
    await audit_service.record_event(
        user.get("id"),
        user.get("email"),
        "intelligence.option.select",
        "intelligence_signal",
        signal_id,
        metadata={"tenant_id": tenant_id, "workspace_id": workspace_id, "option_id": option_id},
    )
    return {"selected": _public_json(dict(option))}


async def record_outcome(user: dict, signal_id: str, body: dict[str, Any]) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    signal = await pool.fetchrow(
        "SELECT actual_value, expected_value, metric, cartridge_id FROM intelligence_signals WHERE workspace_id = $1 AND signal_id = $2",
        workspace_id,
        signal_id,
    )
    if not signal:
        raise HTTPException(404, "intelligence signal not found")
    signal_data = dict(signal)
    option_id = str(body.get("option_id") or "").strip() or None
    action_taken = str(body.get("action_taken") or body.get("action") or "").strip()
    if not action_taken:
        raise HTTPException(400, "action_taken is required")
    actual_value = _num(body.get("actual_value"))
    predicted_value = _num(body.get("predicted_value"))
    if predicted_value is None:
        predicted_value = _num(signal_data.get("actual_value"))
    prediction_error = None
    if actual_value is not None and predicted_value is not None:
        prediction_error = round(actual_value - predicted_value, 4)
    learned_rule = str(body.get("learned_rule") or "").strip() or None
    if not learned_rule and prediction_error is not None:
        learned_rule = f"Resultado medido con error {prediction_error:.2f} para {signal_data['metric']}."
    outcome_summary = str(body.get("outcome_summary") or body.get("summary") or learned_rule or "Outcome registrado.").strip()
    row = await pool.fetchrow(
        """
        INSERT INTO prediction_outcomes (
            tenant_id, workspace_id, signal_id, option_id, action_taken,
            predicted_value, actual_value, prediction_error, outcome_summary,
            learned_rule, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
        RETURNING *
        """,
        tenant_id,
        workspace_id,
        signal_id,
        option_id,
        action_taken,
        predicted_value,
        actual_value,
        prediction_error,
        outcome_summary,
        learned_rule,
        _json_dumps({"reported_by": user.get("email")}),
    )
    if learned_rule:
        await pool.execute(
            """
            INSERT INTO control_room_lessons (
                tenant_id, workspace_id, item_id, cartridge_id, anomaly_type, rule, confidence, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, 0.70, $7::jsonb)
            """,
            tenant_id,
            workspace_id,
            signal_id,
            signal_data["cartridge_id"],
            signal_data["metric"],
            learned_rule,
            _json_dumps({"source": "prediction_outcome"}),
        )
    await pool.execute(
        """
        UPDATE control_room_items
           SET metadata = metadata || $3::jsonb,
               last_seen_at = NOW()
         WHERE workspace_id = $1
           AND item_id = $2
        """,
        workspace_id,
        signal_id,
        _json_dumps({"intelligence_outcome": _public_json(dict(row)), "lessons": [learned_rule] if learned_rule else []}),
    )
    await audit_service.record_event(
        user.get("id"),
        user.get("email"),
        "intelligence.outcome.record",
        "intelligence_signal",
        signal_id,
        metadata={"tenant_id": tenant_id, "workspace_id": workspace_id, "option_id": option_id},
    )
    return {"outcome": _public_json(dict(row))}


def _row_to_signal(row: Any) -> dict[str, Any]:
    data = dict(row)
    metadata = data.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    data["metadata"] = metadata if isinstance(metadata, dict) else {}
    return _public_json(data)
