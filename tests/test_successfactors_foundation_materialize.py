from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "refinement" / "scripts" / "materialize_successfactors_foundation.py"
REFINEMENT_DOCKERFILE = REPO / "refinement" / "Dockerfile"


def _load_script():
    spec = importlib.util.spec_from_file_location("materialize_successfactors_foundation", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeStore:
    def __init__(self, layers: dict[str, str] | None = None):
        self.layers = layers or {}
        self.refreshes: list[tuple[str, int]] = []

    def get_dataset(self, name: str) -> dict:
        return {
            "name": name,
            "layer": self.layers.get(name, "gold"),
            "cartridge": "sap_successfactors",
            "sources": [],
            "sql_def": "SELECT 1 AS ok",
        }

    def update_refresh(self, name: str, row_count: int):
        self.refreshes.append((name, row_count))


class _FakeEngine:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def materialize(self, ds: dict, user_context: dict) -> dict:
        self.calls.append((ds["name"], user_context))
        return {
            "name": ds["name"],
            "layer": ds["layer"],
            "row_count": len(self.calls),
            "storage_uri": f"postgres_gold:gold_{ds['name']}",
        }


def test_successfactors_foundation_materializes_gold_in_dependency_order():
    module = _load_script()
    store = _FakeStore()
    engine = _FakeEngine()

    result = module.materialize_foundation(
        store=store,
        engine=engine,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        datasets=module.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER,
    )

    assert [name for name, _ctx in engine.calls] == module.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER
    assert store.refreshes == [
        (name, idx)
        for idx, name in enumerate(module.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER, start=1)
    ]
    assert result["status"] == "PASS"
    assert result["tenant_id"] == "tenant-a"
    assert result["workspace_id"] == "workspace-a"
    assert all(item["status"] == "PASS" for item in result["datasets"])
    assert all(ctx["_server_trusted_context"] is True for _name, ctx in engine.calls)


def test_successfactors_foundation_can_materialize_talent_when_explicit():
    module = _load_script()
    store = _FakeStore()
    engine = _FakeEngine()

    result = module.materialize_foundation(
        store=store,
        engine=engine,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        datasets=module.SUCCESSFACTORS_GOLD_TALENT_ORDER,
    )

    assert [name for name, _ctx in engine.calls] == module.SUCCESSFACTORS_GOLD_TALENT_ORDER
    assert result["status"] == "PASS"
    assert all(item["status"] == "PASS" for item in result["datasets"])


def test_successfactors_foundation_refuses_non_gold_dataset_before_writes():
    module = _load_script()
    store = _FakeStore(layers={"sap_successfactors_employee_360": "silver"})
    engine = _FakeEngine()

    with pytest.raises(module.MaterializationContractError):
        module.materialize_foundation(
            store=store,
            engine=engine,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            datasets=["sap_successfactors_employee_360"],
        )

    assert engine.calls == []
    assert store.refreshes == []


def test_successfactors_foundation_missing_scope_is_blocked_not_pass(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            "--evidence-dir",
            str(tmp_path / "evidence"),
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 2
    summary = json.loads((tmp_path / "evidence" / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"
    assert "OMEGA_TENANT_ID" in summary["unblock_command"]
    assert "OMEGA_WORKSPACE_ID" in summary["unblock_command"]


def test_successfactors_foundation_runner_is_copied_into_refinement_image():
    dockerfile = REFINEMENT_DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY --chown=appuser:appuser scripts/ scripts/" in dockerfile
