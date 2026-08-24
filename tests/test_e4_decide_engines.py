"""E4 — los dos motores del Decide que faltaban: permutación y minimax.

Contrato: deterministas (misma entrada+semilla = mismo resultado, con digest),
fail-closed (insuficiente → insufficient_data con razón, jamás un número
fabricado), veredicto matemático no editable por el LLM, y optimalidad exacta
del minimax para el objetivo min-max de regret excluido.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.minimax_allocation import (
    MinimaxValidationError,
    solve_minimax_allocation,
)
from app.services.intelligence.permutation_test import (
    PermutationValidationError,
    run_permutation_test,
)


# ── Permutación ──────────────────────────────────────────────────────────────

def _two_even_categories():
    return [{"key": "a", "weight": 1}, {"key": "b", "weight": 1}]


def test_permutation_is_deterministic_and_seed_sensitive():
    kwargs = dict(
        categories=_two_even_categories(),
        draws=10,
        focus_key="a",
        observed=7,
        iterations=2_000,
        seed=42,
    )
    first = run_permutation_test(**kwargs)
    second = run_permutation_test(**kwargs)
    assert first == second, "misma entrada + misma semilla = mismo resultado"
    other = run_permutation_test(**{**kwargs, "seed": 43})
    assert other["input_digest"] != first["input_digest"]


def test_permutation_extreme_observation_is_systemic():
    """10 de 10 eventos en una categoría de peso 1/2: p ≈ 2^-10 ≈ 0.001."""
    result = run_permutation_test(
        categories=_two_even_categories(),
        draws=10,
        focus_key="a",
        observed=10,
        iterations=10_000,
        seed=7,
    )
    assert result["status"] == "succeeded"
    assert result["p_value"] < 0.01
    assert result["verdict"] == "systemic_pattern"
    assert abs(result["expected"] - 5.0) < 0.3, "esperado ≈ draws * peso relativo"


def test_permutation_observed_zero_is_chance():
    result = run_permutation_test(
        categories=_two_even_categories(),
        draws=10,
        focus_key="a",
        observed=0,
        iterations=1_000,
        seed=1,
    )
    assert result["p_value"] == 1.0, "P(foco >= 0) es siempre 1"
    assert result["verdict"] == "consistent_with_chance"


def test_permutation_demo_case_apizaco():
    """El caso SIN-002 de la demo: 8 de 11 bypass en una planta que solo pesa
    ~14% de las vacantes. Debe salir patrón sistémico, no azar."""
    plantas = [
        {"key": "toluca", "weight": 140},
        {"key": "guadalajara", "weight": 110},
        {"key": "apizaco", "weight": 95},
        {"key": "cedis_mty", "weight": 130},
        {"key": "cedis_gdl", "weight": 88},
        {"key": "brasil", "weight": 120},
    ]
    result = run_permutation_test(
        categories=plantas, draws=11, focus_key="apizaco", observed=8, seed=2026
    )
    assert result["status"] == "succeeded"
    assert result["verdict"] == "systemic_pattern"
    assert result["p_value"] < 0.001


def test_permutation_fails_closed_on_insufficient_inputs():
    thin = run_permutation_test(
        categories=_two_even_categories(),
        draws=3,
        focus_key="a",
        observed=2,
        iterations=1_000,
        seed=0,
    )
    assert thin["status"] == "insufficient_data"
    assert "p_value" not in thin, "sin volumen no se fabrica p-valor"

    zero_weight = run_permutation_test(
        categories=[{"key": "a", "weight": 0}, {"key": "b", "weight": 5}],
        draws=10,
        focus_key="a",
        observed=1,
        iterations=1_000,
        seed=0,
    )
    assert zero_weight["status"] == "insufficient_data"


def test_permutation_validation_errors():
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=[{"key": "a", "weight": 1}, {"key": "a", "weight": 2}],
            draws=10, focus_key="a", observed=1,
        )
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=_two_even_categories(), draws=10, focus_key="zzz", observed=1,
        )
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=_two_even_categories(), draws=5, focus_key="a", observed=9,
        )
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=_two_even_categories(), draws=10, focus_key="a", observed=1,
            seed=-1,
        )


# ── Minimax ──────────────────────────────────────────────────────────────────

def _stars():
    return [
        {"id": "valeria", "risk": 0.9, "impact_weight": 3.0},
        {"id": "joaquin", "risk": 0.8, "impact_weight": 3.0},
        {"id": "marcela", "risk": 0.7, "impact_weight": 1.5},
        {"id": "diego", "risk": 0.6, "impact_weight": 1.5},
        {"id": "emilio", "risk": 0.5, "impact_weight": 0.6},
    ]


def test_minimax_selects_top_regret_and_reports_worst_excluded():
    result = solve_minimax_allocation(candidates=_stars(), capacity=2)
    assert result["status"] == "succeeded"
    assert result["selected"] == ["valeria", "joaquin"]
    # El peor excluido es marcela: 0.7 * 1.5 = 1.05
    assert result["worst_unmitigated_regret"] == pytest.approx(1.05)
    assert result["method"] == "exact_top_k_regret"


def test_minimax_optimality_no_swap_improves_worst_excluded():
    """Exhaustivo sobre el caso base: ninguna otra selección de K=2 logra un
    máximo excluido menor — la prueba de que top-K es exacto, no heurística."""
    from itertools import combinations

    stars = _stars()
    regret = {c["id"]: c["risk"] * c["impact_weight"] for c in stars}
    best = min(
        max(regret[c["id"]] for c in stars if c["id"] not in chosen)
        for chosen in ({a, b} for a, b in combinations([c["id"] for c in stars], 2))
    )
    result = solve_minimax_allocation(candidates=stars, capacity=2)
    assert result["worst_unmitigated_regret"] == pytest.approx(best)


def test_minimax_ties_break_deterministically_by_id():
    tied = [
        {"id": "b", "risk": 0.5, "impact_weight": 2.0},
        {"id": "a", "risk": 0.5, "impact_weight": 2.0},
        {"id": "c", "risk": 0.4, "impact_weight": 1.0},
    ]
    result = solve_minimax_allocation(candidates=tied, capacity=1)
    assert result["selected"] == ["a"], "empate → id ascendente, siempre igual"


def test_minimax_capacity_edges():
    zero = solve_minimax_allocation(candidates=_stars(), capacity=0)
    assert zero["selected"] == []
    assert zero["worst_unmitigated_regret"] == pytest.approx(2.7)  # valeria

    everyone = solve_minimax_allocation(candidates=_stars(), capacity=99)
    assert len(everyone["selected"]) == 5
    assert everyone["worst_unmitigated_regret"] == 0.0

    empty = solve_minimax_allocation(candidates=[], capacity=3)
    assert empty["status"] == "insufficient_data"


def test_minimax_validation_errors():
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": 1.5}], capacity=1
        )
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": 0.5}, {"id": "x", "risk": 0.4}],
            capacity=1,
        )
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": 0.5, "impact_weight": 0}], capacity=1
        )
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(candidates=_stars(), capacity=-1)


def test_minimax_is_deterministic():
    first = solve_minimax_allocation(candidates=_stars(), capacity=3)
    second = solve_minimax_allocation(candidates=list(reversed(_stars())), capacity=3)
    assert first["selected"] == second["selected"], "el orden de entrada no importa"
    assert first["input_digest"] != "", "digest de reproducibilidad presente"


# ── E4-SEC: endurecimiento contra el red-team (NaN/Infinity/bool + DoS) ──────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_permutation_rejects_non_finite_weights(bad):
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=[{"key": "a", "weight": bad}, {"key": "b", "weight": 1}],
            draws=10, focus_key="a", observed=5, seed=1,
        )


def test_permutation_rejects_boolean_numbers():
    for kwargs in (
        dict(draws=True, observed=1),
        dict(draws=10, observed=True),
    ):
        with pytest.raises(PermutationValidationError):
            run_permutation_test(
                categories=[{"key": "a", "weight": 1}, {"key": "b", "weight": 1}],
                focus_key="a", seed=1, **kwargs,
            )
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=[{"key": "a", "weight": True}, {"key": "b", "weight": 1}],
            draws=10, focus_key="a", observed=1, seed=1,
        )


def test_permutation_caps_complexity():
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=[{"key": "a", "weight": 1}, {"key": "b", "weight": 1}],
            draws=10_001, focus_key="a", observed=1, seed=1,
        )
    with pytest.raises(PermutationValidationError):
        run_permutation_test(
            categories=[{"key": f"k{i}", "weight": 1} for i in range(1001)],
            draws=10, focus_key="k0", observed=1, seed=1,
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_minimax_rejects_non_finite(bad):
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": 0.5, "impact_weight": bad}], capacity=1
        )
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": bad, "impact_weight": 1.0}], capacity=1
        )


def test_minimax_rejects_boolean_inputs():
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": 0.5, "impact_weight": 1.0}], capacity=True
        )
    with pytest.raises(MinimaxValidationError):
        solve_minimax_allocation(
            candidates=[{"id": "x", "risk": True, "impact_weight": 1.0}], capacity=1
        )
