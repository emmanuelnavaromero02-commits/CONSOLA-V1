"""E4 — test de permutación: ¿la concentración observada es azar o patrón?

El motor que faltaba para la pregunta de sesgo/concentración del ciclo Decide
(demo omega-9box, TAL-002/SIN-002): dados N eventos repartidos entre categorías
con pesos conocidos (p. ej. 11 bypass de escalafón entre plantas proporcional a
sus vacantes), ¿cuántos caerían en la categoría foco solo por azar? Simula
`iterations` repartos con semilla determinista y devuelve el p-valor de cola
superior: P(foco >= observado | azar).

Doctrina de la casa (espejo de monte_carlo.py):
- Determinista: misma entrada + misma semilla = mismo resultado, con digest
  sha-256 de reproducibilidad.
- Fail-closed: entradas insuficientes producen status insufficient_data con la
  razón exacta — jamás un p-valor fabricado.
- El veredicto es matemático y NO editable por el LLM: el lenguaje natural
  solo puede citarlo (invariante 19 del plan maestro).
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any

MAX_SEED = 2**31 - 1
MIN_ITERATIONS = 1_000
MAX_ITERATIONS = 100_000
DEFAULT_ITERATIONS = 10_000
MIN_DRAWS = 5

# Umbrales del veredicto (cola superior). Fijos y versionados: cambiarlos es
# cambiar el contrato del motor, no un ajuste de estilo.
P_SYSTEMIC = 0.01
P_POSSIBLE = 0.05
PERMUTATION_RULESET_VERSION = "permutation_v1"


class PermutationValidationError(ValueError):
    pass


def _clean_seed(value: Any) -> int:
    if isinstance(value, bool):
        raise PermutationValidationError("seed must be an integer")
    try:
        seed = int(value)
    except (TypeError, ValueError) as exc:
        raise PermutationValidationError("seed must be an integer") from exc
    if not 0 <= seed <= MAX_SEED:
        raise PermutationValidationError(f"seed must be between 0 and {MAX_SEED}")
    return seed


def _clean_iterations(value: Any) -> int:
    try:
        iterations = int(value if value is not None else DEFAULT_ITERATIONS)
    except (TypeError, ValueError) as exc:
        raise PermutationValidationError("iterations must be an integer") from exc
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise PermutationValidationError(
            f"iterations must be between {MIN_ITERATIONS} and {MAX_ITERATIONS}"
        )
    return iterations


def _clean_categories(raw: Any) -> list[tuple[str, float]]:
    if not isinstance(raw, list) or not raw:
        raise PermutationValidationError("categories must be a non-empty list")
    cleaned: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise PermutationValidationError("each category must be an object")
        key = str(item.get("key") or "").strip()
        if not key:
            raise PermutationValidationError("category key is required")
        if key in seen:
            raise PermutationValidationError(f"duplicate category key: {key}")
        seen.add(key)
        try:
            weight = float(item.get("weight"))
        except (TypeError, ValueError) as exc:
            raise PermutationValidationError(
                f"category weight must be numeric: {key}"
            ) from exc
        if weight < 0:
            raise PermutationValidationError(f"category weight must be >= 0: {key}")
        cleaned.append((key, weight))
    if sum(weight for _, weight in cleaned) <= 0:
        raise PermutationValidationError("total category weight must be > 0")
    return cleaned


def _input_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_permutation_test(
    *,
    categories: list[dict[str, Any]],
    draws: Any,
    focus_key: str,
    observed: Any,
    iterations: Any = None,
    seed: Any = 0,
) -> dict[str, Any]:
    """Reparte `draws` eventos entre categorías (proporcional al peso) y mide
    con qué frecuencia la categoría foco recibe >= `observed` por puro azar."""
    cleaned = _clean_categories(categories)
    seed_value = _clean_seed(seed)
    iteration_count = _clean_iterations(iterations)
    focus = str(focus_key or "").strip()
    keys = [key for key, _ in cleaned]
    if focus not in keys:
        raise PermutationValidationError(f"focus_key not in categories: {focus}")
    try:
        draw_count = int(draws)
        observed_count = int(observed)
    except (TypeError, ValueError) as exc:
        raise PermutationValidationError("draws and observed must be integers") from exc
    if observed_count < 0 or draw_count < 0:
        raise PermutationValidationError("draws and observed must be >= 0")
    if observed_count > draw_count:
        raise PermutationValidationError("observed cannot exceed draws")

    digest = _input_digest(
        {
            "categories": [[key, weight] for key, weight in cleaned],
            "draws": draw_count,
            "focus_key": focus,
            "observed": observed_count,
            "iterations": iteration_count,
            "seed": seed_value,
            "ruleset": PERMUTATION_RULESET_VERSION,
        }
    )

    # Fail-closed: sin volumen o sin contraste no hay p-valor que valga.
    insufficient_reason = None
    if draw_count < MIN_DRAWS:
        insufficient_reason = f"draws < {MIN_DRAWS}"
    elif len(cleaned) < 2:
        insufficient_reason = "fewer than 2 categories"
    else:
        focus_weight = dict(cleaned)[focus]
        if focus_weight <= 0:
            insufficient_reason = "focus category has zero weight"
    if insufficient_reason:
        return {
            "status": "insufficient_data",
            "reason": insufficient_reason,
            "observed": observed_count,
            "draws": draw_count,
            "iterations": iteration_count,
            "seed": seed_value,
            "input_digest": digest,
            "ruleset_version": PERMUTATION_RULESET_VERSION,
        }

    total = sum(weight for _, weight in cleaned)
    cumulative: list[tuple[float, str]] = []
    running = 0.0
    for key, weight in cleaned:
        running += weight / total
        cumulative.append((running, key))

    rng = random.Random(f"{seed_value}:{digest}")
    tail_hits = 0
    focus_sum = 0
    distribution: dict[int, int] = {}
    for _ in range(iteration_count):
        focus_hits = 0
        for _ in range(draw_count):
            point = rng.random()
            for boundary, key in cumulative:
                if point <= boundary:
                    if key == focus:
                        focus_hits += 1
                    break
        focus_sum += focus_hits
        distribution[focus_hits] = distribution.get(focus_hits, 0) + 1
        if focus_hits >= observed_count:
            tail_hits += 1

    p_value = tail_hits / iteration_count
    expected = focus_sum / iteration_count
    if p_value <= P_SYSTEMIC:
        verdict = "systemic_pattern"
    elif p_value <= P_POSSIBLE:
        verdict = "possible_pattern"
    else:
        verdict = "consistent_with_chance"
    return {
        "status": "succeeded",
        "observed": observed_count,
        "draws": draw_count,
        "expected": round(expected, 4),
        "p_value": round(p_value, 6),
        "tail": "greater_or_equal",
        "verdict": verdict,
        "thresholds": {"systemic": P_SYSTEMIC, "possible": P_POSSIBLE},
        "distribution": {
            str(k): v for k, v in sorted(distribution.items())
        },
        "iterations": iteration_count,
        "seed": seed_value,
        "input_digest": digest,
        "ruleset_version": PERMUTATION_RULESET_VERSION,
    }
