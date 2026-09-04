"""E2a — el eslabón perdido del Montecarlo WB-TALENTO, conectado.

La casa ya tenía todo: el gold prepara input_variables_json + evidencia, el
servicio acepta wisdom_bit, y el Control Room voltea la tarjeta a 'ready'
cuando existe una simulación persistida. Este corredor solo une las piezas —
y esta suite fija que lo hace FIELMENTE: variables y evidencia pasan tal
cual del dataset al motor, insumos bloqueados no simulan nada, y la semilla
es determinista por generación publicada.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.intelligence import talent_retention_simulation as trs

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _enable_engine_under_test(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MATH_ENGINES_ENABLED", "true")


VARIABLES = {
    "baseline_value": {"type": "fixed", "value": 42.5},
    "expected_delta": {"type": "triangular", "low": -42.5, "mode": -14.88, "high": 4.25},
}
EVIDENCE = [
    {"type": "gold_dataset", "id": "sap_successfactors_talent_operational_features"},
    {"type": "wisdom_bit", "id": "WB-TALENTO"},
]


def _row(status="ready", **extra):
    return {
        "input_status": status,
        "user_status_label": "Analisis listo" if status == "ready" else "En espera de datos",
        "materialized_at": "2026-08-19T15:00:00Z",
        "input_variables_json": json.dumps(VARIABLES),
        "evidence_refs_json": json.dumps(EVIDENCE),
        **extra,
    }


def _manifest(gen=7):
    return {"head_run_id": "run-abc", "head_generation": gen, "complete": True}


def test_faithful_payload_from_prepared_inputs(monkeypatch):
    monkeypatch.setattr(
        trs, "query_gold_dataset_population",
        AsyncMock(return_value=([_row()], _manifest())),
    )
    run = AsyncMock(return_value={"simulation": {"simulation_id": "mc-1"}})
    monkeypatch.setattr(trs.monte_carlo_service, "run_simulation", run)

    out = asyncio.run(trs.run_for_workspace({"id": 1}))
    assert out["status"] == "simulated" and out["simulation_id"] == "mc-1"
    payload = run.await_args.args[1]
    assert payload["source_type"] == "wisdom_bit"
    assert payload["source_id"] == "WB-TALENTO"
    assert payload["input_variables"] == VARIABLES, "las variables del gold, TAL CUAL"
    assert payload["evidence_refs"] == EVIDENCE
    assert payload["output_metric"] == "net_value", "el modelo del dataset: índice proyectado"
    assert payload["breach_threshold"] == 70.0 and payload["breach_direction"] == "above"
    assert payload["iterations"] == 10_000


def test_blocked_inputs_never_simulate(monkeypatch):
    monkeypatch.setattr(
        trs, "query_gold_dataset_population",
        AsyncMock(return_value=([_row(status="blocked")], _manifest())),
    )
    run = AsyncMock()
    monkeypatch.setattr(trs.monte_carlo_service, "run_simulation", run)
    out = asyncio.run(trs.run_for_workspace({"id": 1}))
    assert out == {"status": "blocked", "reason": "En espera de datos"}, (
        "la razón viene del propio dataset — jamás una simulación fabricada"
    )
    run.assert_not_awaited()


def test_no_rows_is_waiting(monkeypatch):
    monkeypatch.setattr(
        trs, "query_gold_dataset_population",
        AsyncMock(return_value=([], _manifest())),
    )
    out = asyncio.run(trs.run_for_workspace({"id": 1}))
    assert out["status"] == "waiting_for_data"


def test_seed_is_deterministic_per_published_generation():
    row = _row()
    a = trs._stable_seed(_manifest(gen=7), row)
    b = trs._stable_seed(_manifest(gen=7), row)
    c = trs._stable_seed(_manifest(gen=8), row)
    assert a == b, "misma generación → misma semilla → misma simulación"
    assert a != c, "nueva generación de datos → nueva semilla"
    assert 0 <= a <= trs.MAX_SEED


def test_best_effort_never_raises(monkeypatch):
    async def boom(user):
        raise RuntimeError("gold down")

    monkeypatch.setattr(trs, "run_for_workspace", boom)
    assert asyncio.run(trs.run_best_effort({"id": 1})) is None


def test_engine_wires_the_simulation_into_gold_refresh():
    src = (
        REPO_ROOT / "console" / "app" / "services" / "intelligence" / "engine.py"
    ).read_text(encoding="utf-8")
    block = src.split("retention_simulation_summary = None", 1)[1].split("return {", 1)[0]
    assert "talent_retention_simulation.run_best_effort" in block
    assert '"talent_retention_simulation": retention_simulation_summary' in src
