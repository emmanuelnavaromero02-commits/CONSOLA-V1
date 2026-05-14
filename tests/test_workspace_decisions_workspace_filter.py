"""Sprint v1.32 — workspace decisions must be scoped by workspace_id."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_workspace_main(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "workspace"))
    monkeypatch.setenv("INTERNAL_API_KEY", "workspace_test_internal_key_with_more_than_32_chars")
    return importlib.import_module("app.main")


class FakePG:
    def __init__(self):
        self.fetch_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((sql, params))
        return []


@pytest.mark.asyncio
async def test_decisions_list_filters_by_current_workspace(monkeypatch):
    main = _load_workspace_main(monkeypatch)
    fake_pg = FakePG()

    async def fake_pg_factory():
        return fake_pg

    monkeypatch.setattr(main, "pg", fake_pg_factory)
    request = SimpleNamespace(
        state=SimpleNamespace(
            user={"id": 7, "role": "user", "active_workspace_id": "workspace-a"}
        )
    )

    result = await main.api_decisions_list(request)

    assert result == {"decisions": []}
    sql, params = fake_pg.fetch_calls[0]
    assert "workspace_id = $1" in sql
    assert params[0] == "workspace-a"


def test_decisions_mutation_queries_are_workspace_scoped():
    source = (Path(__file__).resolve().parents[1] / "workspace/app/main.py").read_text()

    assert "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2" in source
    assert "(title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)" in source
    assert "WHERE id = {decision_ref} AND workspace_id = {workspace_ref} RETURNING *" in source
    assert "DELETE FROM decisions WHERE id = $1 AND workspace_id = $2" in source


def test_decisions_workspace_id_migration_exists():
    sql = (Path(__file__).resolve().parents[1] / "infra/init/33_decisions_workspace_id.sql").read_text()

    assert "ALTER TABLE decisions" in sql
    assert "ADD COLUMN IF NOT EXISTS workspace_id" in sql
    assert "idx_decisions_workspace_id" in sql
