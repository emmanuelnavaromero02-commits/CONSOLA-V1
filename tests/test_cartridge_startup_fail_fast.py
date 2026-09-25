from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = [
    ("replicon",           "replicon"),
    ("hubspot",            "hubspot"),
    ("sap_hcm",            "sap_hcm"),
    ("sap_s4hana",         "sap_s4hana"),
    ("sap_b1",             "sap_b1"),
    ("sap_successfactors", "sap_successfactors"),
]


def _isolated_cartridge(name: str):
    sys.path[:] = [
        p for p in sys.path
        if not any(s in p for s in ("/cartridges/", "/console", "/vault",
                                     "/workspace", "/mcp-infra",
                                     "/refinement"))
    ]
    sys.path.insert(0, str(REPO / "cartridges" / name))
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    return importlib.import_module("app.main")


@pytest.fixture
def env_for_cartridges(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "x" * 64)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", "x" * 64)
    monkeypatch.setenv("APP_ENV", "test")
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://x:y@nohost:5432/x")
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql+psycopg2://x:y@nohost:5433/x_gold")
    monkeypatch.setenv("PG_USER", "x")
    monkeypatch.setenv("PG_PASSWORD", "y")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-minio-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-minio-secret")


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_lifespan_sets_startup_state_keys(
    env_for_cartridges, cartridge, service_label,
):
    main_py = (REPO / "cartridges" / cartridge / "app" / "main.py").read_text()
    assert "app.state.startup_ok" in main_py, (
        f"{cartridge}/app/main.py must set app.state.startup_ok in lifespan"
    )
    assert "app.state.startup_errors" in main_py, (
        f"{cartridge}/app/main.py must set app.state.startup_errors in lifespan"
    )


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_health_route_reads_startup_state(cartridge, service_label):
    health_py = (
        REPO / "cartridges" / cartridge / "app" / "api" / "routes_health.py"
    ).read_text()
    assert "startup_ok" in health_py, (
        f"{cartridge} /health must consult startup_ok"
    )
    assert "503" in health_py, (
        f"{cartridge} /health must return 503 when startup failed"
    )
    assert f'"{service_label}"' in health_py or f"'{service_label}'" in health_py


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_health_returns_503_when_startup_failed(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _boom():
        raise RuntimeError("simulated DB outage")
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _boom)

    with TestClient(main_mod.app) as client:
        r = client.get("/health")

    assert r.status_code == 503, (
        f"{cartridge} /health must return 503 when startup_ok=False, got {r.status_code}"
    )
    body = r.json()
    assert body["ok"] is False
    assert body["service"] == service_label
    assert any("job_runner" in e for e in body["startup_errors"]), body


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_health_returns_200_when_startup_clean(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if getattr(main_mod, "catalog_service", None) is not None:
        monkeypatch.setattr(
            main_mod.catalog_service, "_seed_if_empty", lambda: None,
        )

    with TestClient(main_mod.app) as client:
        r = client.get("/health")

    assert r.status_code == 200, (
        f"{cartridge} /health must return 200 when every step is clean, "
        f"got {r.status_code} body={r.text!r}"
    )
    body = r.json()
    assert body["ok"] is True
    assert body["startup_errors"] == []


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
@pytest.mark.parametrize("path,method", [
    ("/mcp/tools",  "GET"),
    ("/mcp/invoke", "POST"),
    ("/mcp/rpc",    "POST"),
])
def test_mcp_endpoints_return_503_when_startup_failed(
    env_for_cartridges, cartridge, service_label, path, method, monkeypatch,
):
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _boom():
        raise RuntimeError("simulated DB outage")
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _boom)

    headers = {
        "X-Internal-Api-Key": "x" * 64,
        "X-Internal-Service": "console",
    }
    with TestClient(main_mod.app) as client:
        if method == "GET":
            r = client.get(path, headers=headers)
        else:
            r = client.post(path, headers=headers, json={})

    assert r.status_code == 503, (
        f"{cartridge} {method} {path} must return 503 when startup_ok=False, "
        f"got {r.status_code} body={r.text!r}"
    )


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_mcp_rpc_503_body_is_valid_json_with_apostrophe_error(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    from fastapi.testclient import TestClient
    import json as _json

    main_mod = _isolated_cartridge(cartridge)

    async def _boom():
        raise RuntimeError("can't connect: path C:\\db; tëst")
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _boom)

    headers = {
        "X-Internal-Api-Key": "x" * 64,
        "X-Internal-Service": "console",
    }
    with TestClient(main_mod.app) as client:
        r = client.post("/mcp/rpc", headers=headers, json={})

    assert r.status_code == 503
    body = _json.loads(r.text)
    assert body["error"] == "cartridge_not_ready"
    if cartridge == "sap_successfactors":
        assert "job_runner_failed" in body["startup_errors"]
        assert set(body["startup_errors"]) <= {
            "job_runner_failed",
            "catalog_seed_failed",
            "startup_component_failed",
        }
        assert "can't connect" not in r.text
        assert "C:\\db" not in r.text
        assert "tëst" not in r.text
    else:
        assert any("can't connect" in e for e in body["startup_errors"]), body
        assert any("C:\\db" in e for e in body["startup_errors"]), body
        assert any("tëst" in e for e in body["startup_errors"]), body


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_health_returns_200_with_tool_count_when_healthy(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if cartridge != "replicon" and hasattr(main_mod, "catalog_service"):
        monkeypatch.setattr(
            main_mod.catalog_service, "_seed_if_empty", lambda: None,
        )

    with TestClient(main_mod.app) as client:
        r = client.get("/health")

    assert r.status_code == 200, (
        f"{cartridge} /health must be 200 with tools registered, "
        f"got {r.status_code} body={r.text!r}"
    )
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == service_label
    assert "tool_count" in body, (
        f"{cartridge} /health success body must include tool_count "
        f"(v1.43.4 C2). got keys={list(body)}"
    )
    assert isinstance(body["tool_count"], int) and body["tool_count"] > 0, (
        f"{cartridge} /health tool_count must be a positive int — "
        f"the MCP server must have tools registered to count as healthy"
    )


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_health_returns_503_when_mcp_has_no_tools(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if cartridge != "replicon" and hasattr(main_mod, "catalog_service"):
        monkeypatch.setattr(
            main_mod.catalog_service, "_seed_if_empty", lambda: None,
        )

    async def _no_tools():
        return []
    rh = importlib.import_module("app.api.routes_health")
    monkeypatch.setattr(rh.mcp, "list_tools", _no_tools)

    with TestClient(main_mod.app) as client:
        r = client.get("/health")

    assert r.status_code == 503, (
        f"{cartridge} /health must be 503 when no tools registered, "
        f"got {r.status_code} body={r.text!r}"
    )
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "mcp_no_tools_registered", body
    assert body["tool_count"] == 0, body


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_health_returns_503_when_mcp_list_tools_raises(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if cartridge != "replicon" and hasattr(main_mod, "catalog_service"):
        monkeypatch.setattr(
            main_mod.catalog_service, "_seed_if_empty", lambda: None,
        )

    async def _boom():
        raise AttributeError(
            "'FastMCP' object has no attribute 'list_tools'"
        )
    rh = importlib.import_module("app.api.routes_health")
    monkeypatch.setattr(rh.mcp, "list_tools", _boom)

    with TestClient(main_mod.app) as client:
        r = client.get("/health")

    assert r.status_code == 503, (
        f"{cartridge} /health must be 503 when list_tools raises"
    )
    body = r.json()
    assert body["reason"] == "mcp_unreachable", body
    if cartridge == "sap_successfactors":
        assert body["error"] == "mcp_probe_failed", body
        assert "list_tools" not in r.text
    else:
        assert "list_tools" in body["error"], body


def test_successfactors_startup_and_health_never_echo_exception_text(
    env_for_cartridges, monkeypatch, caplog,
):
    from fastapi.testclient import TestClient

    sentinel = "SECRET_SENTINEL access=AKIA_TEST dsn=postgresql://user:pass@db"
    main_mod = _isolated_cartridge("sap_successfactors")

    async def _boom():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _boom)
    monkeypatch.setattr(main_mod.catalog_service, "_seed_if_empty", lambda: None)

    with TestClient(main_mod.app) as client:
        health_response = client.get("/health")
        rpc_response = client.post("/mcp/rpc", json={})

    assert main_mod.app.state.startup_errors == ["job_runner_failed"]
    assert health_response.status_code == 503
    assert health_response.json()["startup_errors"] == ["job_runner_failed"]
    assert rpc_response.status_code == 503
    assert rpc_response.json()["startup_errors"] == ["job_runner_failed"]
    assert sentinel not in health_response.text
    assert sentinel not in rpc_response.text
    assert sentinel not in caplog.text


def test_successfactors_mcp_health_probe_never_echoes_exception_text(
    env_for_cartridges, monkeypatch,
):
    from fastapi.testclient import TestClient

    sentinel = "SECRET_SENTINEL signed_url=https://storage.invalid/?signature=secret"
    main_mod = _isolated_cartridge("sap_successfactors")

    async def _ok():
        return None

    async def _boom():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    monkeypatch.setattr(main_mod.catalog_service, "_seed_if_empty", lambda: None)
    routes_health = importlib.import_module("app.api.routes_health")
    monkeypatch.setattr(routes_health.mcp, "list_tools", _boom)

    with TestClient(main_mod.app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["error"] == "mcp_probe_failed"
    assert sentinel not in response.text
