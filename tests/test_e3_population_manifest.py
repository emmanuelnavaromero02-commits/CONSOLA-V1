"""E3 — cero límites ocultos: manifiesto poblacional exacto en la ruta certificada.

Contrato que fija esta suite:
- El motor de señales genérico ya NO lee con el cap de 5.000 (DEFAULT_LIMIT):
  la ruta certificada lee la población COMPLETA por cursor con COUNT(*) en el
  mismo snapshot (query_gold_dataset_population) y devuelve un manifiesto por
  dataset: population_total, rows_fetched, complete.
- Si el techo operativo (INTELLIGENCE_POPULATION_MAX_ROWS) corta la lectura,
  el run queda PARCIAL DECLARADO (skipped: population_truncated con los
  conteos exactos) — jamás truncamiento silencioso.
- La cuadratura es fail-closed: lectura completa != COUNT del mismo snapshot
  truena con 500, nunca un parcial disfrazado de completo.
- El cap de 5.000 sobrevive SOLO como protección de previews (doctrina F12).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_certified_route_no_longer_uses_the_5000_cap():
    engine = read("console/app/services/intelligence/engine.py")
    # Los dos sitios de lectura del run (métricas con contrato y datasets sin
    # contrato) usan la ruta poblacional.
    assert engine.count("await _fetch_population(dataset)") == 2
    # El único uso restante de DEFAULT_LIMIT es la compatibilidad con
    # fetchers inyectados (tests/llamadores legacy), dentro del helper.
    compat = engine.split("if fetcher is not None:", 1)[1]
    assert "await fetch(dataset, user, DEFAULT_LIMIT)" in compat
    assert engine.count("await fetch(dataset, user, DEFAULT_LIMIT)") == 1


def test_population_reader_counts_and_fails_closed():
    fetcher = read("console/app/services/intelligence/gold_fetcher.py")
    block = fetcher.split("async def query_gold_dataset_population", 1)[1].split(
        "async def query_intelligence_dataset_rows", 1
    )[0]
    assert "SELECT COUNT(*) FROM" in block, "el total sale de COUNT, no de len(rows)"
    assert "repeatable_read" in block, "COUNT y lectura en el MISMO snapshot"
    assert "conn.cursor(" in block, "lectura completa por lotes, no una sola query capada"
    assert "population accounting mismatch" in block, "cuadratura fail-closed"
    assert "LIMIT" not in block.split("SELECT COUNT")[0], "sin cap oculto en la ruta"
    # El cap de preview sigue existiendo, documentado como preview-only (F12).
    assert "safe_limit = max(1, min(int(limit or 5000), 5000))" in fetcher


def _user() -> dict:
    return {
        "id": 1,
        "email": "e3@test.local",
        "role": "admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
    }


def test_injected_fetcher_still_works_and_declares_manifest():
    """Compatibilidad: los llamadores que inyectan fetcher conservan su
    contrato, y el resultado declara el manifiesto con fuente inyectada."""
    from app.services.intelligence import engine as eng

    async def fake_fetch(dataset, user, limit):
        return [
            {"entity_id": "a", "period": "2026-07", "value": 10},
            {"entity_id": "a", "period": "2026-08", "value": 12},
        ]

    result = asyncio.run(
        eng.run_intelligence(_user(), {"dry_run": True}, fetcher=fake_fetch, persist=False)
    )
    manifests = result.get("population")
    assert isinstance(manifests, list) and manifests, "manifiesto ausente"
    assert all(m.get("source") == "injected_fetcher" for m in manifests)
    assert all(m.get("complete") is True for m in manifests)
    assert result.get("population_complete") is True


def test_truncated_population_is_declared_partial_never_silent(monkeypatch):
    """Techo operativo alcanzado → population_truncated en skipped con los
    conteos exactos y population_complete=False."""
    from app.services.intelligence import engine as eng

    async def fake_population(dataset, user):
        rows = [
            {"entity_id": str(i), "period": "2026-08", "value": i} for i in range(5)
        ]
        manifest = {
            "dataset": dataset,
            "population_total": 12345,
            "rows_fetched": len(rows),
            "complete": False,
            "ceiling": 5,
            "source": "gold_population_cursor",
        }
        return rows, manifest

    monkeypatch.setattr(eng, "query_gold_dataset_population", fake_population)
    result = asyncio.run(
        eng.run_intelligence(_user(), {"dry_run": True}, persist=False)
    )
    assert result.get("population_complete") is False
    truncated = [
        item
        for item in result.get("skipped", [])
        if item.get("status") == "population_truncated"
    ]
    assert truncated, "el corte por techo DEBE declararse en skipped"
    reason = str(truncated[0].get("reason") or "")
    assert "5/12345" in reason, "la razón lleva los conteos exactos"
    assert "INTELLIGENCE_POPULATION_MAX_ROWS" in reason
