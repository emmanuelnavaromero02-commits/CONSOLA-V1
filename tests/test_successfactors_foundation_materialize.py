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
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    refinement_path = str(SCRIPT.parents[1])
    sys.path = [
        path
        for path in sys.path
        if "cartridges/sap_successfactors" not in path.replace("\\", "/")
    ]
    if refinement_path in sys.path:
        sys.path.remove(refinement_path)
    sys.path.insert(0, refinement_path)
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


class _FallbackEngine(_FakeEngine):
    def materialize(self, ds: dict, user_context: dict) -> dict:
        self.calls.append((ds["name"], user_context, ds["sql_def"]))
        if len(self.calls) == 1:
            raise RuntimeError("No files found that match read_parquet source")
        return {
            "name": ds["name"],
            "layer": ds["layer"],
            "row_count": 1,
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


def test_successfactors_foundation_phase_helpers_keep_talent_layers_ordered():
    module = _load_script()

    assert module._datasets_for_phase("foundation", None) == module.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER
    assert module._datasets_for_phase("talent_contract", None) == module.SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER
    assert module._datasets_for_phase("talent_operational", None) == module.SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER
    assert module._datasets_for_phase("all", None) == (
        module.SUCCESSFACTORS_GOLD_FOUNDATION_ORDER + module.SUCCESSFACTORS_GOLD_TALENT_ORDER
    )
    assert module.SUCCESSFACTORS_GOLD_TALENT_ORDER[-2:] == [
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_simulation_inputs",
    ]
    assert module.SUCCESSFACTORS_GOLD_TALENT_ORDER.index(
        "sap_successfactors_talent_benchmark_internal"
    ) < module.SUCCESSFACTORS_GOLD_TALENT_ORDER.index("sap_successfactors_talent_readiness")
    assert module.SUCCESSFACTORS_GOLD_TALENT_ORDER.index(
        "sap_successfactors_talent_learning_certification_status"
    ) < module.SUCCESSFACTORS_GOLD_TALENT_ORDER.index(
        "sap_successfactors_talent_operational_features"
    )
    assert module.SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER[-2:] == [
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_simulation_inputs",
    ]
    assert module._datasets_for_phase("foundation", "sap_successfactors_talent_readiness") == [
        "sap_successfactors_talent_readiness"
    ]


def test_successfactors_foundation_uses_talent_operational_fallback_for_missing_source():
    module = _load_script()
    store = _FakeStore()
    engine = _FallbackEngine()

    result = module.materialize_foundation(
        store=store,
        engine=engine,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        datasets=["sap_successfactors_talent_learning_certification_status"],
    )

    assert result["status"] == "PARTIAL"
    assert result["datasets"][0]["status"] == "PARTIAL"
    assert result["datasets"][0]["fallback"] is True
    assert "missing_materialized_dependency" == result["datasets"][0]["reason"]
    assert len(engine.calls) == 2
    assert "insufficient_data" in engine.calls[1][2]


def test_successfactors_foundation_uses_foundation_fallback_for_missing_source():
    module = _load_script()
    store = _FakeStore()
    engine = _FallbackEngine()

    result = module.materialize_foundation(
        store=store,
        engine=engine,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        datasets=["sap_successfactors_employee_360"],
    )

    assert result["status"] == "PARTIAL"
    assert result["datasets"][0]["status"] == "PARTIAL"
    assert result["datasets"][0]["fallback"] is True
    assert result["datasets"][0]["reason"] == "missing_materialized_dependency"
    assert len(engine.calls) == 2
    assert "is_active" in engine.calls[1][2]


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

    assert "COPY --chown=appuser:appuser refinement/scripts/ scripts/" in dockerfile
