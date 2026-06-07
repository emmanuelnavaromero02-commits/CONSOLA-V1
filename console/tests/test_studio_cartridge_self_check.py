from __future__ import annotations

import importlib
import os

import pytest


@pytest.mark.asyncio
async def test_cartridge_self_check_reports_real_gaps(monkeypatch):
    os.environ["APP_ENV"] = "test"
    studio = importlib.import_module("app.routers.studio")
    cartridges = importlib.import_module("app.routers.cartridges")

    async def get_cartridge(cartridge_id):
        assert cartridge_id == "hubspot"
        return {
            "id": "hubspot",
            "name": "HubSpot",
            "entities": [
                {"entity": "deals", "dag_id": ""},
                {"entity": "contacts", "dag_id": "hubspot_contacts_extract"},
            ],
            "dags": [{"dag_id": "hubspot_contacts_extract"}],
            "knowledge_bits": [],
            "assistant_hints": "",
        }

    async def connector_schema(cartridge_id, user):
        return {"connector": {"id": cartridge_id}}

    async def vault_connection(_cartridge_id, _conn_id="default", _user=None):
        return {}, "no saved credentials in vault"

    async def list_entities(cartridge):
        assert cartridge == "hubspot"
        return [
            {"entity": "deals", "spec": {"fields": []}},
            {"entity": "contacts", "spec": {"fields": [{"name": "id", "primary_key": True}]}},
        ]

    async def invoke(_server, tool, args, **_kwargs):
        if tool == "airflow_list_dags":
            return {"dags": [{"dag_id": "hubspot_contacts_extract"}]}
        if tool == "cartridge_list_jobs":
            return {"jobs": []}
        raise AssertionError(tool)

    async def list_servers():
        return [{"id": "hubspot", "healthy": False, "url": "http://hubspot:8210"}]

    async def refinement_datasets(_user):
        return [{"name": "deals_clean", "layer": "silver", "cartridge": "hubspot"}]

    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(cartridges, "connector_schema", connector_schema)
    monkeypatch.setattr(studio, "_vault_connection", vault_connection)
    monkeypatch.setattr(studio.studio_entities, "list_entities", list_entities)
    monkeypatch.setattr(studio.mcp_registry, "invoke", invoke)
    monkeypatch.setattr(studio.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio, "_refinement_datasets", refinement_datasets)

    result = await studio._studio_cartridge_self_check(
        {"cartridge_id": "hubspot"},
        {"id": 7, "allowed_cartridges": ["hubspot"]},
    )

    messages = [item["message"] for item in result["warnings"]]
    assert result["status"] == "warning"
    assert result["score"] < 100
    assert "Conexion Vault scoped no encontrada" in messages
    assert "/operations/vault" in repr(result)
    assert "password" not in repr(result).lower()
    assert "Entidad deals sin fields tipados" in messages
    assert "Entidad deals sin primary_key" in messages
    assert "Entidad deals sin dag_id" in messages
    assert "MCP server del cartucho registrado pero no healthy" in messages


@pytest.mark.asyncio
async def test_cartridge_self_check_uses_scoped_saml_vault_connection(monkeypatch):
    os.environ["APP_ENV"] = "test"
    studio = importlib.import_module("app.routers.studio")
    cartridges = importlib.import_module("app.routers.cartridges")

    async def get_cartridge(cartridge_id):
        assert cartridge_id == "sap_successfactors"
        return {
            "id": "sap_successfactors",
            "name": "SAP SuccessFactors",
            "entities": [{"entity": "PerPerson", "dag_id": "sf_perperson_extract"}],
            "dags": [{"dag_id": "sf_perperson_extract"}],
            "knowledge_bits": [{"id": "kb-sf"}],
            "assistant_hints": "Use scoped SAML Vault connection.",
        }

    async def connector_schema(cartridge_id, user):
        return {"connector": {"id": cartridge_id, "auth": {"type": "saml_bearer_assertion"}}}

    async def list_entities(cartridge):
        assert cartridge == "sap_successfactors"
        return [
            {
                "entity": "PerPerson",
                "spec": {"fields": [{"name": "personIdExternal", "primary_key": True}]},
            }
        ]

    async def invoke(_server, tool, args, **_kwargs):
        if tool == "airflow_list_dags":
            return {"dags": [{"dag_id": "sf_perperson_extract"}]}
        if tool == "cartridge_list_jobs":
            return {"jobs": [{"id": "job-1"}]}
        raise AssertionError(tool)

    async def list_servers():
        return [{"id": "sap_successfactors", "healthy": True, "url": "http://sf:8203"}]

    async def refinement_datasets(_user):
        return [
            {"name": "sap_successfactors_employee_360", "layer": "gold", "cartridge": "sap_successfactors"},
            {"name": "sap_successfactors_person_silver", "layer": "silver", "cartridge": "sap_successfactors"},
        ]

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            if url.endswith("/connections/sap_successfactors/default"):
                return Response(404, {})
            if url.endswith("/connections/sap_successfactors"):
                return Response(200, {"connections": [{"conn_id": "femsa_sf", "auth_method": "saml_bearer_assertion"}]})
            if url.endswith("/connections/sap_successfactors/femsa_sf"):
                return Response(
                    200,
                    {
                        "conn_id": "femsa_sf",
                        "auth_method": "saml_bearer_assertion",
                        "base_url": "https://api68sales.successfactors.com",
                    },
                )
            raise AssertionError(url)

    monkeypatch.setattr(studio.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(studio, "_vault_headers_for_user", lambda _user: {})
    monkeypatch.setattr(studio.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(cartridges, "connector_schema", connector_schema)
    monkeypatch.setattr(studio.studio_entities, "list_entities", list_entities)
    monkeypatch.setattr(studio.mcp_registry, "invoke", invoke)
    monkeypatch.setattr(studio.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio, "_refinement_datasets", refinement_datasets)

    result = await studio._studio_cartridge_self_check(
        {"cartridge_id": "sap_successfactors"},
        {"id": 7, "allowed_cartridges": ["sap_successfactors"]},
    )

    messages = [item["message"] for item in result["warnings"]]
    assert "Conexion Vault scoped no encontrada" not in messages
    assert result["evidence"]["vault"]["connection_id"] == "femsa_sf"
    assert result["evidence"]["vault"]["auth_method"] == "saml_bearer_assertion"
    assert "password" not in repr(result).lower()


@pytest.mark.asyncio
async def test_goal_run_visibility_is_revalidated(monkeypatch):
    studio = importlib.import_module("app.routers.studio")
    seen = {}

    async def get_goal_run_status(goal_run_id, user):
        seen["goal_run_id"] = goal_run_id
        seen["user"] = user
        return {"goal_run": {"cartridge_id": "hubspot"}, "steps": []}

    def require_visible(user, cartridge_id):
        seen["visible_user"] = user
        seen["visible_cartridge_id"] = cartridge_id

    monkeypatch.setattr(studio.studio_goal_runs, "get_goal_run_status", get_goal_run_status)
    monkeypatch.setattr(studio, "_require_cartridge_visible", require_visible)

    result = await studio._require_goal_run_visible("run-1", {"id": 7})

    assert result["goal_run"]["cartridge_id"] == "hubspot"
    assert seen == {
        "goal_run_id": "run-1",
        "user": {"id": 7},
        "visible_user": {"id": 7},
        "visible_cartridge_id": "hubspot",
    }
