"""Sprint v1.43.2 (Codex P1-5) — cartridge /health mirrors startup state.

Pre-v1.43.2, every cartridge's lifespan swallowed schema/migration
exceptions and /health unconditionally returned ``{"ok": True}``.
Result: a cartridge that failed to migrate its jobs table would still
be marked healthy by Kubernetes and accept traffic — but every
schedule attempt would crash at the DB layer.

The fix tracks per-step startup results in ``app.state`` and the
/health endpoint returns 503 with the captured errors if any step
failed.

These tests run each cartridge's ASGI app via TestClient with the
lifespan executed end-to-end and assert the contract.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = [
    ("replicon",           "replicon"),
    ("sap_hcm",            "sap_hcm"),
    ("sap_s4hana",         "sap_s4hana"),
    ("sap_successfactors", "sap_successfactors"),
]


def _isolated_cartridge(name: str):
    """Import ``cartridges/<name>/app/main.py`` with a clean sys.path."""
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
    """Cartridges crash at import without these. Provide stubs that
    let the module load — we're testing /health behaviour, not the
    SAP/Replicon network layer."""
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "x" * 64)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", "x" * 64)
    monkeypatch.setenv("APP_ENV", "test")
    # SAP cartridges read field encryption + DB credentials at import.
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://x:y@nohost:5432/x")
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql+psycopg2://x:y@nohost:5433/x_gold")
    monkeypatch.setenv("PG_USER", "x")
    monkeypatch.setenv("PG_PASSWORD", "y")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-minio-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-minio-secret")


# ── Structural test: every cartridge declares the contract ────────────────

@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_lifespan_sets_startup_state_keys(
    env_for_cartridges, cartridge, service_label,
):
    """Static check that ``app.state.startup_ok`` and
    ``startup_errors`` are written by lifespan. We grep the source —
    the alternative (actually running lifespan + DB) is brittle in CI."""
    main_py = (REPO / "cartridges" / cartridge / "app" / "main.py").read_text()
    assert "app.state.startup_ok" in main_py, (
        f"{cartridge}/app/main.py must set app.state.startup_ok in lifespan"
    )
    assert "app.state.startup_errors" in main_py, (
        f"{cartridge}/app/main.py must set app.state.startup_errors in lifespan"
    )


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_health_route_reads_startup_state(cartridge, service_label):
    """The /health endpoint must consult app.state.startup_ok and
    return 503 when it's False. Grep is sufficient — keeps the test
    deterministic in CI without a live DB."""
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


# ── Behavioural test: run lifespan + hit /health ──────────────────────────

@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_cartridge_health_returns_503_when_startup_failed(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    """Drive lifespan with a broken job_runner. /health must return
    503 and list the failure in startup_errors."""
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    # Sabotage job_runner.ensure_schema so the lifespan records a
    # failure but still completes.
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
    """The healthy path: every lifespan step succeeds → /health = 200."""
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    # SAP cartridges also call catalog_service._seed_if_empty().
    if cartridge != "replicon":
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


# ── v1.43.2 (LLM R1 hardening): /mcp/* fail-closed when startup_ok=False ──

@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
@pytest.mark.parametrize("path,method", [
    ("/mcp/tools",  "GET"),
    ("/mcp/invoke", "POST"),
    ("/mcp/rpc",    "POST"),
])
def test_mcp_endpoints_return_503_when_startup_failed(
    env_for_cartridges, cartridge, service_label, path, method, monkeypatch,
):
    """When the cartridge's startup recorded a failure, every /mcp/*
    surface — REST adapters AND the mounted JSON-RPC app — must
    refuse traffic with 503. Pre-R1 they'd serve normally and let a
    peer trigger the very schema gap that flagged startup as broken."""
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
    """v1.43.2 (LLM R2 hardening): the ASGI guard's 503 body must be
    well-formed JSON even when the error message contains an
    apostrophe / non-ASCII / backslash. Pre-R2 the body was built by
    Python repr (``str(list).replace("'", '"')``) which broke JSON
    parsing for any payload containing ``'`` inside a string."""
    from fastapi.testclient import TestClient
    import json as _json

    main_mod = _isolated_cartridge(cartridge)

    async def _boom():
        # Apostrophe + backslash + non-ASCII to stress the JSON encoder.
        raise RuntimeError("can't connect: path C:\\db; tëst")
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _boom)

    headers = {
        "X-Internal-Api-Key": "x" * 64,
        "X-Internal-Service": "console",
    }
    with TestClient(main_mod.app) as client:
        r = client.post("/mcp/rpc", headers=headers, json={})

    assert r.status_code == 503
    # Must round-trip as valid JSON; the apostrophe + backslash + non-ASCII
    # must survive.
    body = _json.loads(r.text)
    assert body["error"] == "cartridge_not_ready"
    assert any("can't connect" in e for e in body["startup_errors"]), body
    assert any("C:\\db" in e for e in body["startup_errors"]), body
    assert any("tëst" in e for e in body["startup_errors"]), body


# ── v1.43.4 (Codex C2): /health reflects MCP contract state ───────────────


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_health_returns_200_with_tool_count_when_healthy(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    """v1.43.4 (Codex C2): /health success body must include a
    positive ``tool_count`` derived from the MCP server's
    ``list_tools()`` call. Before this hotfix /health only checked
    startup_ok, so a cartridge with a broken /mcp/tools surface
    could still report healthy.
    """
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if cartridge != "replicon":
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
        f"(v1.43.4 Codex C2). got keys={list(body)}"
    )
    assert isinstance(body["tool_count"], int) and body["tool_count"] > 0, (
        f"{cartridge} /health tool_count must be a positive int — "
        f"the MCP server must have tools registered to count as healthy"
    )


@pytest.mark.parametrize("cartridge,service_label", CARTRIDGES)
def test_health_returns_503_when_mcp_has_no_tools(
    env_for_cartridges, cartridge, service_label, monkeypatch,
):
    """If the MCP server somehow ends up with zero tools registered
    (regression in tool decorators, bad import order, etc.), /health
    must return 503 with reason=mcp_no_tools_registered. This catches
    a quiet failure mode that v1.43.4 (Codex C2) explicitly targets.
    """
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if cartridge != "replicon":
        monkeypatch.setattr(
            main_mod.catalog_service, "_seed_if_empty", lambda: None,
        )

    # Replace mcp.list_tools to return [].
    async def _no_tools():
        return []
    # Import the cartridge's routes_health module and patch its `mcp`.
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
    """If mcp.list_tools() raises (e.g. AttributeError because of a
    fastmcp major-version mismatch — the exact Codex C1 failure
    mode), /health must surface that as 503 with
    reason=mcp_unreachable instead of pretending healthy.
    """
    from fastapi.testclient import TestClient

    main_mod = _isolated_cartridge(cartridge)

    async def _ok():
        return None
    monkeypatch.setattr(main_mod.job_runner, "ensure_schema", _ok)
    monkeypatch.setattr(main_mod.job_runner, "cleanup_stale", _ok)
    if cartridge != "replicon":
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
    assert "list_tools" in body["error"], body
