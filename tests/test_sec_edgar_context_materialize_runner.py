from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "refinement/scripts/materialize_sec_edgar_context.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("materialize_sec_edgar_context", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Store:
    def __init__(self):
        self.updated = []

    def get_dataset(self, name):
        layer = "gold" if name == "sec_market_context" else "silver"
        return {"name": name, "layer": layer, "cartridge": "sec_edgar", "sql_def": "SELECT 1"}

    def update_refresh(self, name, row_count):
        self.updated.append((name, row_count))


class Engine:
    def __init__(self):
        self.materialized = []

    def materialize(self, ds, context):
        self.materialized.append((ds["name"], context["tenant_id"], context["workspace_id"]))
        return {"row_count": len(self.materialized), "storage_uri": f"s3://lakehouse/{ds['name']}.parquet"}


def test_runner_materializes_sec_context_in_order():
    module = _load_module()
    store = Store()
    engine = Engine()

    result = module.materialize_sec_edgar_context(
        store=store,
        engine=engine,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )

    assert [name for name, *_ in engine.materialized] == [name for name, _layer in module.DATASET_ORDER]
    assert store.updated == [
        ("sec_company_metadata_latest", 1),
        ("sec_company_facts_normalized", 2),
        ("sec_company_quality", 3),
        ("sec_market_context", 4),
    ]
    assert result["status"] == "PASS"


def test_runner_rejects_unregistered_dataset():
    module = _load_module()

    class Missing(Store):
        def get_dataset(self, name):
            return None

    with pytest.raises(module.SECMaterializationError, match="not registered"):
        module.materialize_sec_edgar_context(
            store=Missing(),
            engine=Engine(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
        )
