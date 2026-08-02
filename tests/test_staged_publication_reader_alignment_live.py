from __future__ import annotations

import pytest

from app.services.intelligence.gold_fetcher import (
    clear_gold_row_cache,
    query_gold_dataset_rows,
)
from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.staged_publication_live import LiveStack
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import staged_publication_live_stack


@pytest.mark.asyncio
async def test_data_options_and_query_advance_on_one_published_generation(
    staged_publication_live_stack: LiveStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    engine = _engine(stack, monkeypatch)
    dataset = {
        "name": "reader_generation_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sources": [],
    }
    user = {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A}
    monkeypatch.setenv("GOLD_DATABASE_URL", stack.reader_dsn)
    monkeypatch.setenv("OMEGA_GOLD_ROW_CACHE_TTL_SECONDS", "0")

    engine.materialize({**dataset, "sql_def": "SELECT 1::INTEGER AS value"}, _scope())
    first_run = str(stack.head(dataset["name"])[0]).replace("-", "")
    assert await query_gold_dataset_rows(dataset["name"], user) == [
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A, "value": 1}
    ]
    options_a, _ = engine.get_rls_filters(
        f"SELECT DISTINCT value FROM pggold.gold_{dataset['name']}", user
    )
    query_a, _ = engine.get_rls_filters(
        f"SELECT * FROM pggold.gold_{dataset['name']} WHERE value = ?", user
    )
    assert f"run_{first_run}" in options_a
    assert f"run_{first_run}" in query_a

    engine.materialize({**dataset, "sql_def": "SELECT 2::INTEGER AS value"}, _scope())
    second_run = str(stack.head(dataset["name"])[0]).replace("-", "")
    clear_gold_row_cache()
    assert await query_gold_dataset_rows(dataset["name"], user) == [
        {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A, "value": 2}
    ]
    options_b, _ = engine.get_rls_filters(
        f"SELECT DISTINCT value FROM pggold.gold_{dataset['name']}", user
    )
    query_b, _ = engine.get_rls_filters(
        f"SELECT * FROM pggold.gold_{dataset['name']} WHERE value = ?", user
    )
    assert first_run not in options_b + query_b
    assert f"run_{second_run}" in options_b
    assert f"run_{second_run}" in query_b
