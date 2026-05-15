"""Sprint v1.40.3 — Replicon /mcp/* and /skills/* must be behind the internal guard."""
from __future__ import annotations

import pytest

from tests.conftest import load_cartridge_app


API_KEY = "test-secret-key-not-default"
VALID_HEADERS = {
    "X-Api-Key": API_KEY,
    "X-Internal-Service": "console",
}


def _client():
    from fastapi.testclient import TestClient

    main = load_cartridge_app("replicon")
    return TestClient(main.app, raise_server_exceptions=False)


def test_replicon_mcp_rpc_requires_auth():
    with _client() as client:
        resp = client.get("/mcp/rpc/")
    assert resp.status_code == 401


def test_replicon_mcp_tools_requires_auth():
    with _client() as client:
        resp = client.get("/mcp/tools")
    assert resp.status_code == 401


def test_replicon_mcp_invoke_requires_auth():
    with _client() as client:
        resp = client.post("/mcp/invoke", json={})
    assert resp.status_code == 401


def test_replicon_mcp_reload_requires_auth():
    with _client() as client:
        resp = client.post("/mcp-reload")
    assert resp.status_code == 401


def test_replicon_skills_entities_requires_auth():
    with _client() as client:
        resp = client.get("/skills/entities")
    assert resp.status_code == 401


def test_replicon_skills_run_full_load_requires_auth():
    with _client() as client:
        resp = client.post("/skills/run_full_load/User")
    assert resp.status_code == 401


def test_replicon_skills_run_full_load_all_requires_auth():
    with _client() as client:
        resp = client.post("/skills/run_full_load_all")
    assert resp.status_code == 401


def test_replicon_skills_knowledge_bits_requires_auth():
    with _client() as client:
        resp = client.get("/skills/knowledge_bits")
    assert resp.status_code == 401


def test_replicon_accepts_valid_internal_api_key():
    with _client() as client:
        resp = client.get("/mcp/rpc/", headers=VALID_HEADERS)
    assert resp.status_code != 401
