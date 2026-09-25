from __future__ import annotations

from typing import Any


def hypotheses(signal: dict[str, Any], evidence_items: list[dict[str, Any]], evidence_pack_id: int | None = None) -> list[dict[str, Any]]:
    generated = [_baseline_hypothesis(signal, evidence_pack_id)]
    support_keys = {str(item.get("supports_hypothesis") or "") for item in evidence_items}
    external_items = [item for item in evidence_items if item.get("source_type") == "external"]
    if "external_event_correlation" in support_keys:
        generated.append(_external_event_hypothesis(signal, external_items, evidence_pack_id))
    if "external_unavailable" in support_keys:
        generated.append(_data_gap_hypothesis(signal, evidence_pack_id))
    configured = signal.get("configured_hypotheses")
    if isinstance(configured, list):
        generated.extend(_configured_hypotheses(configured, signal, evidence_pack_id))
    deduped: dict[str, dict[str, Any]] = {}
    for item in generated:
        key = str(item.get("hypothesis_key") or "")
        if key and key not in deduped:
            deduped[key] = item
    return sorted(deduped.values(), key=lambda row: float(row.get("confidence") or 0), reverse=True)


def _baseline_hypothesis(signal: dict[str, Any], evidence_pack_id: int | None) -> dict[str, Any]:
    signal_type = signal["signal_type"]
    expected_behavior = signal["expected_behavior"]
    confidence = float(signal["confidence"])
    entity_label = signal["entity_label"]
    metric_name = signal["metric_name"]
    if signal.get("signal_subtype") in {"future_risk", "future_opportunity"}:
        title = "Tendencia futura fuera del baseline"
        rationale = (
            f"{entity_label} se proyecta fuera del baseline en {metric_name}; "
            "la prioridad es validar si la tendencia se sostiene antes del periodo objetivo."
        )
    elif signal_type == "opportunity":
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
    return {
        "hypothesis_key": "baseline_deviation",
        "title": title,
        "rationale": rationale,
        "confidence": round(max(0.30, confidence - 0.05), 2),
        "evidence_pack_id": evidence_pack_id,
    }


def _external_event_hypothesis(signal: dict[str, Any], items: list[dict[str, Any]], evidence_pack_id: int | None) -> dict[str, Any]:
    confidence = min(0.88, float(signal.get("confidence") or 0.5) + 0.08)
    titles = []
    for item in items:
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        title = data.get("title")
        if title:
            titles.append(str(title))
    reason = "; ".join(titles[:2]) if titles else "hay evidencia externa configurada en el periodo."
    return {
        "hypothesis_key": "external_event_correlation",
        "title": "Contexto externo correlacionado con la senal",
        "rationale": f"La senal puede estar explicada o amplificada por contexto externo: {reason}",
        "confidence": round(confidence, 2),
        "evidence_pack_id": evidence_pack_id,
    }



def _data_gap_hypothesis(signal: dict[str, Any], evidence_pack_id: int | None) -> dict[str, Any]:
    return {
        "hypothesis_key": "data_quality_or_context_gap",
        "title": "Contexto externo incompleto",
        "rationale": (
            "La senal interna es valida, pero falta una fuente externa configurada para explicar causa probable."
        ),
        "confidence": round(max(0.20, float(signal.get("confidence") or 0.5) - 0.25), 2),
        "evidence_pack_id": evidence_pack_id,
    }


def _configured_hypotheses(configured: list[Any], signal: dict[str, Any], evidence_pack_id: int | None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in configured:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("hypothesis_key") or "").strip()
        if not key:
            continue
        output.append(
            {
                "hypothesis_key": key,
                "title": str(item.get("title") or key.replace("_", " ").title()),
                "rationale": str(item.get("rationale") or item.get("description") or signal.get("summary") or ""),
                "confidence": round(max(0.20, min(0.85, float(item.get("confidence") or signal.get("confidence") or 0.5))), 2),
                "evidence_pack_id": evidence_pack_id,
                "metadata": {"source": "intelligence_contract"},
            }
        )
    return output
