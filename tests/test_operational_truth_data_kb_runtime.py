from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from tests.test_operational_truth_data_kb_config import ROOT, _Connection, _packaged


class _Engine:
    def __init__(self, connection: _Connection, *, fail_begin: bool = False) -> None:
        self.connection = connection
        self.fail_begin = fail_begin

    def begin(self):
        if self.fail_begin:
            raise RuntimeError("reconciliation unavailable")
        return self.connection

    def connect(self):
        return self.connection


def _load_replicon_runtime(monkeypatch):
    import cartridges.replicon.app.services.kb_config_reconciliation as reconciliation

    app = types.ModuleType("app")
    core = types.ModuleType("app.core")
    services = types.ModuleType("app.services")
    monkeypatch.setitem(sys.modules, "app", app)
    monkeypatch.setitem(sys.modules, "app.core", core)
    monkeypatch.setitem(sys.modules, "app.services", services)

    def module(name: str, **attributes):
        value = types.ModuleType(name)
        for key, attribute in attributes.items():
            setattr(value, key, attribute)
        monkeypatch.setitem(sys.modules, name, value)

    module(
        "app.core.config",
        settings=types.SimpleNamespace(database_url="", minio_bucket="b"),
    )
    module("app.core.pg_client", get_connection=lambda: None)
    module(
        "app.core.request_context",
        SecurityContextError=ValueError,
        get_security_context=lambda: {},
        require_tenant_workspace_scope=lambda value: value,
        scoped_prefix=lambda _value: "tenant_id=t/workspace_id=w/",
    )
    module("app.core.sql_guard", validate_kb_sql=lambda *_a, **_k: (True, None))
    module("app.services.kb_config_reconciliation", **vars(reconciliation))
    module(
        "app.services.duckdb_service",
        run_kb_sql=lambda _sql: (_ for _ in ()).throw(AssertionError("executed")),
        write_kb_parquet=lambda *_a: "",
        write_kb_to_postgres=lambda *_a: None,
    )

    def load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, loaded)
        spec.loader.exec_module(loaded)
        return loaded

    service_root = ROOT / "cartridges/replicon/app/services"
    catalog = load("app.services.catalog_service", service_root / "catalog_service.py")
    load(
        "app.services.base_currency_frame",
        service_root / "base_currency_frame.py",
    )
    load(
        "app.services.kb_materialization",
        service_root / "kb_materialization.py",
    )
    kb_service = load("app.services.kb_service", service_root / "kb_service.py")
    return reconciliation, catalog, kb_service


@pytest.mark.parametrize("kind", ["unknown", "legacy_reconcile_failed"])
def test_catalog_and_kb_runtime_never_execute_unsafe_reserved_sql(
    monkeypatch, kind: str
) -> None:
    reconciliation, catalog, kb_service = _load_replicon_runtime(monkeypatch)
    current = _packaged()["kb_wip_mensual"]
    stored_sql = "SELECT 'unknown'" if kind == "unknown" else "SELECT 'known legacy'"
    if kind == "legacy_reconcile_failed":
        monkeypatch.setitem(
            reconciliation.LEGACY_PACKAGE_SQL_DIGESTS,
            "kb_wip_mensual",
            {hashlib.sha256(stored_sql.encode()).hexdigest()},
        )
    original = {
        "cartridge_id": "replicon",
        "kb_id": "kb_wip_mensual",
        "name": "stored",
        "description": "unchanged",
        "sql": stored_sql,
        "pg_table": "stored",
        "output_path": "stored",
        "enabled": True,
    }
    connection = _Connection({"kb_wip_mensual": dict(original)})
    monkeypatch.setattr(
        catalog,
        "_get_engine",
        lambda: _Engine(connection, fail_begin=kind.endswith("failed")),
    )
    monkeypatch.setattr(catalog, "_yaml_kbs", lambda: [current])
    calls = {"create": 0, "execute": 0}
    monkeypatch.setattr(
        kb_service, "_create_kb_run", lambda *_a: calls.__setitem__("create", 1)
    )
    monkeypatch.setattr(
        kb_service, "run_kb_sql", lambda *_a: calls.__setitem__("execute", 1)
    )

    config = catalog.get_kb_config("kb_wip_mensual")
    result = kb_service.run_knowledge_bit("kb_wip_mensual")

    assert connection.rows["kb_wip_mensual"] == original
    assert config["enabled"] is False
    assert config["runtime_status"] == "insufficient_data"
    assert result["status"] == "partial"
    assert calls == {"create": 0, "execute": 0}


def test_catalog_upgrades_known_legacy_then_current_is_write_free(monkeypatch) -> None:
    reconciliation, catalog, _kb_service = _load_replicon_runtime(monkeypatch)
    current = _packaged()["kb_wip_resumen"]
    legacy_sql = "SELECT 'known legacy'"
    monkeypatch.setitem(
        reconciliation.LEGACY_PACKAGE_SQL_DIGESTS,
        "kb_wip_resumen",
        {hashlib.sha256(legacy_sql.encode()).hexdigest()},
    )
    row = {"kb_id": "kb_wip_resumen", "sql": legacy_sql, "enabled": True}
    connection = _Connection({"kb_wip_resumen": row})
    monkeypatch.setattr(catalog, "_get_engine", lambda: _Engine(connection))
    monkeypatch.setattr(catalog, "_yaml_kbs", lambda: [current])

    upgraded = catalog.get_kb_config("kb_wip_resumen")
    writes = len(connection.writes)
    current_again = catalog.get_kb_config("kb_wip_resumen")

    assert upgraded["sql"] == current["sql"] == current_again["sql"]
    assert len(connection.writes) == writes == 1
