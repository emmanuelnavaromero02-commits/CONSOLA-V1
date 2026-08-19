"""E4 — asignación minimax: a quién proteger con K recursos, minimizando el
peor arrepentimiento.

El motor que el orquestador declaraba como 'constrained_optimizer_candidate —
Requires optimization engine (not yet implemented)' para resource_allocation,
en su forma acotada y EXACTA (demo omega-9box, TAL-001: elegir K estrellas a
retener): cada candidato lleva un arrepentimiento (regret) = riesgo x peso de
impacto — lo que cuesta NO protegerlo. Elegir los K de mayor regret minimiza
el máximo regret de los no protegidos.

Optimalidad (exacta, no heurística): si una selección excluye a un candidato A
e incluye a B con regret(B) < regret(A), intercambiarlos no empeora ningún
excluido y reduce (o mantiene) el máximo excluido; por inducción, el top-K por
regret es óptimo para el objetivo min-max. Con empates, el orden es
determinista (regret desc, id asc).

Doctrina de la casa: determinista, fail-closed (entradas insuficientes →
insufficient_data con razón exacta), digest de reproducibilidad, y el
resultado NO es editable por el LLM (invariante 19 del plan maestro).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

MINIMAX_RULESET_VERSION = "minimax_topk_v1"
MAX_CANDIDATES = 100_000


class MinimaxValidationError(ValueError):
    pass


def _finite_number(value: object, *, field: str) -> float:
    """Numero real finito y NO booleano (rechaza NaN, +/-Infinity y bool):
    NaN burlaba los rangos por comparacion (NaN<=x es False) e Infinity pasaba
    cualquier cota, dejando pasar un regret NaN no serializable."""
    if isinstance(value, bool):
        raise MinimaxValidationError(f"{field} must be a number, not a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MinimaxValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise MinimaxValidationError(f"{field} must be finite (no NaN/Infinity)")
    return number


def _clean_candidates(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise MinimaxValidationError("candidates must be a list")
    if len(raw) > MAX_CANDIDATES:
        raise MinimaxValidationError(f"too many candidates (> {MAX_CANDIDATES})")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise MinimaxValidationError("each candidate must be an object")
        candidate_id = str(item.get("id") or "").strip()
        if not candidate_id:
            raise MinimaxValidationError("candidate id is required")
        if candidate_id in seen:
            raise MinimaxValidationError(f"duplicate candidate id: {candidate_id}")
        seen.add(candidate_id)
        risk = _finite_number(item.get("risk"), field=f"candidate risk ({candidate_id})")
        if not 0.0 <= risk <= 1.0:
            raise MinimaxValidationError(
                f"candidate risk must be in [0, 1]: {candidate_id}"
            )
        impact = _finite_number(
            item.get("impact_weight", 1.0),
            field=f"candidate impact_weight ({candidate_id})",
        )
        if impact <= 0:
            raise MinimaxValidationError(
                f"candidate impact_weight must be > 0: {candidate_id}"
            )
        cleaned.append(
            {
                "id": candidate_id,
                "risk": risk,
                "impact_weight": impact,
                "label": str(item.get("label") or "")[:200] or None,
            }
        )
    return cleaned


def _input_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def solve_minimax_allocation(
    *,
    candidates: list[dict[str, Any]],
    capacity: Any,
) -> dict[str, Any]:
    """Selecciona hasta `capacity` candidatos minimizando el máximo regret de
    los NO seleccionados. Exacto y determinista; sin azar, sin semilla."""
    cleaned = _clean_candidates(candidates)
    if isinstance(capacity, bool):
        raise MinimaxValidationError("capacity must be an integer, not a boolean")
    try:
        capacity_value = int(capacity)
    except (TypeError, ValueError) as exc:
        raise MinimaxValidationError("capacity must be an integer") from exc
    if capacity_value < 0:
        raise MinimaxValidationError("capacity must be >= 0")

    digest = _input_digest(
        {
            "candidates": [
                [c["id"], c["risk"], c["impact_weight"]] for c in cleaned
            ],
            "capacity": capacity_value,
            "ruleset": MINIMAX_RULESET_VERSION,
        }
    )

    if not cleaned:
        return {
            "status": "insufficient_data",
            "reason": "no candidates",
            "capacity": capacity_value,
            "input_digest": digest,
            "ruleset_version": MINIMAX_RULESET_VERSION,
        }

    ranked = sorted(
        (
            {
                **candidate,
                "regret": round(candidate["risk"] * candidate["impact_weight"], 6),
            }
            for candidate in cleaned
        ),
        key=lambda c: (-c["regret"], c["id"]),
    )
    selected = ranked[:capacity_value]
    excluded = ranked[capacity_value:]
    worst_unmitigated = excluded[0]["regret"] if excluded else 0.0
    mitigated = round(sum(c["regret"] for c in selected), 6)
    return {
        "status": "succeeded",
        "capacity": capacity_value,
        "selected": [c["id"] for c in selected],
        "regret_ranking": [
            {"id": c["id"], "regret": c["regret"], "label": c["label"]}
            for c in ranked
        ],
        "worst_unmitigated_regret": worst_unmitigated,
        "mitigated_regret_total": mitigated,
        "excluded_count": len(excluded),
        "method": "exact_top_k_regret",
        "optimality": "exact_for_minimax_excluded_regret",
        "input_digest": digest,
        "ruleset_version": MINIMAX_RULESET_VERSION,
    }
