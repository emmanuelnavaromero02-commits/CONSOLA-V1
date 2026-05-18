"""Studio API endpoints must be backed by real services, not stub markers."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CSRF = "csrf-test-token"


@pytest.fixture()
def client(monkeypatch):
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    os.environ.setdefault("FIELD_ENCRYPTION_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")

    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from starlette.testclient import TestClient

    from app.dependencies import require_authenticated
    from app.main import app
    from app.routers import studio as studio_router

    app.dependency_overrides[require_authenticated] = lambda: {
        "id": 1,
        "email": "admin@local.ai",
        "role": "admin",
        "workspace_role": "admin",
    }

    manifest = {
        "id": "replicon",
        "name": "Replicon PSA",
        "entities": [{
            "entity": "TimeEntry",
            "display_name": "Time Entry",
            "dag_id": "replicon_timeentry_full",
            "mode": "full",
        }],
        "semantic_model": {
            "vocabulary": [{"term": "Horas", "definition": "Tiempo registrado", "maps_to": "TimeEntry.hours"}],
        },
    }

    async def fake_get_cartridge(cartridge_id):
        return manifest if cartridge_id == "replicon" else None

    async def fake_datasets():
        return [
            {"name": "timeentry_clean", "layer": "silver", "cartridge": "replicon", "sources": ["raw/replicon/TimeEntry"]},
            {"name": "timeentry_master", "layer": "master", "cartridge": "replicon", "sources": ["raw/replicon/TimeEntry"]},
            {"name": "timeentry_gold", "layer": "gold", "cartridge": "replicon", "sources": ["timeentry_clean"]},
        ]

    async def fake_refinement(tool, args, **_kwargs):
        if tool == "query_dataset":
            return {"data": [{"id": 1, "hours": 8}]}
        if tool == "get_schema":
            return {"fields": [{"name": "id"}, {"name": "hours"}]}
        raise AssertionError(f"unexpected refinement tool {tool}")

    async def fake_rag_sources():
        return [{"id": 1, "name": "manual.pdf"}]

    async def fake_invoke(server, tool, args):
        if tool == "superset_list_databases":
            return {"databases": [{"id": 7, "name": "modecissions_gold"}]}
        if tool == "superset_list_datasets":
            return {"datasets": []}
        if tool == "superset_create_dataset":
            return {"dataset_id": 12, "table": args["table_name"]}
        if tool == "airflow_create_dag":
            return {"dag_id": args["dag_id"], "created": "/tmp/dag.py"}
        raise AssertionError(f"unexpected MCP invoke {server}.{tool}")

    async def fake_chat(**_kwargs):
        return {"reply": "Studio conectado", "viewer_urls": [], "messages": []}

    monkeypatch.setattr(studio_router.cartridge_service, "get_cartridge", fake_get_cartridge)
    monkeypatch.setattr(studio_router, "_refinement_datasets", fake_datasets)
    monkeypatch.setattr(studio_router, "_refinement_invoke", fake_refinement)
    monkeypatch.setattr(studio_router, "_rag_sources", fake_rag_sources)
    monkeypatch.setattr(studio_router.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_router.studio_assistant, "chat", fake_chat)

    test_client = TestClient(app)
    test_client.cookies.set("csrf_token", CSRF)
    try:
        yield test_client, studio_router
    finally:
        app.dependency_overrides.pop(require_authenticated, None)


REAL_ENDPOINTS = [
    ("GET", "/api/studio/dag-graph", None),
    ("GET", "/api/studio/templates", None),
    ("GET", "/api/studio/entities", None),
    ("GET", "/api/studio/silver/preview", None),
    ("GET", "/api/studio/gold/preview", None),
    ("GET", "/api/studio/master/preview", None),
    ("GET", "/api/studio/semantic", None),
    ("GET", "/api/studio/rag", None),
    ("POST", "/api/studio/dag-deploy", {"dag_id": "replicon_test_full", "code": "print('ok')"}),
    ("POST", "/api/studio/superset/dataset", {"table_name": "gold_timeentry_gold"}),
    ("POST", "/api/studio/assistant", {"message": "Lista entidades", "cartridge": "replicon"}),
]


@pytest.mark.parametrize(("verb", "path", "payload"), REAL_ENDPOINTS)
def test_studio_endpoints_return_real_payloads(client, verb, path, payload):
    test_client, _studio_router = client
    headers = {"X-CSRF-Token": CSRF}
    response = test_client.request(verb, path, json=payload, headers=headers)
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert body.get("stub") is not True


def test_entity_create_persists_via_cartridge_service(client, monkeypatch):
    test_client, studio_router = client
    calls = []

    async def fake_upsert(cartridge, entity, **fields):
        calls.append((cartridge, entity, fields))

    monkeypatch.setattr(studio_router.cartridge_service, "upsert_entity", fake_upsert)
    response = test_client.post(
        "/api/studio/entity",
        json={"cartridge": "replicon", "entity": "Invoice", "mode": "incremental"},
        headers={"X-CSRF-Token": CSRF},
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0][0:2] == ("replicon", "Invoice")
    assert calls[0][2]["mode"] == "incremental"


def test_spec_upload_parses_yaml_and_persists_entities(client, monkeypatch):
    test_client, studio_router = client
    uploaded = []
    entities = []

    def fake_upload(cartridge, filename, content):
        uploaded.append((cartridge, filename, content))
        return f"cartridges/{cartridge}/specs/{filename}"

    async def fake_upsert(cartridge, entity, **fields):
        entities.append((cartridge, entity, fields))

    monkeypatch.setattr(studio_router.cartridge_service, "upload_spec", fake_upload)
    monkeypatch.setattr(studio_router.cartridge_service, "upsert_entity", fake_upsert)

    response = test_client.post(
        "/api/studio/entities/upload",
        json={
            "cartridge": "replicon",
            "filename": "entities.yaml",
            "content": "entities:\n  Invoice:\n    mode: incremental\n    primary_key: invoice_id\n",
        },
        headers={"X-CSRF-Token": CSRF},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["accepted_count"] == 1
    assert uploaded and uploaded[0][1] == "entities.yaml"
    assert entities[0][1] == "Invoice"
    assert entities[0][2]["primary_key"] == "invoice_id"
