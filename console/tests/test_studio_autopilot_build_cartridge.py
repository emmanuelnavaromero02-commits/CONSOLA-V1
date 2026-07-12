import importlib
import json

import pytest


@pytest.mark.asyncio
async def test_studio_autopilot_builds_blueprint_from_inline_sample(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")

    result = await studio._studio_autopilot_build_cartridge(
        {
            "intent": "conectame HubSpot y dame un dashboard de forecast",
            "source_kind": "rest_sample",
            "sample": {
                "results": [{
                    "deal_id": "d1",
                    "amount": 5000.0,
                    "owner_email": "rep@example.com",
                    "hs_lastmodifieddate": "2026-05-30T10:00:00Z",
                    "stage": "open",
                }],
                "next": "cursor1",
            },
        },
        {"id": "u1", "role": "workspace_admin", "allowed_cartridges": ["*"]},
    )

    assert result["ok"] is True, result
    assert result["source"] == "inline_sample"
    assert result["dry_run"] is True
    assert result["blueprint"]["id"] == "hubspot"
    assert result["summary"]["entities"] >= 1
    assert result["summary"]["gold"] >= 1
    assert "owner_email" in result["summary"]["pii_protected"]


@pytest.mark.asyncio
async def test_studio_autopilot_fetches_openapi_url_and_returns_dry_run(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")
    spec = """
openapi: 3.0.0
components:
  schemas:
    Invoice:
      required: [id, total]
      properties:
        id:
          type: integer
          x-primary-key: true
        total:
          type: number
        updated_at:
          type: string
          format: date-time
"""

    async def pinned_http_request(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://api.example.com/openapi.yaml"
        return studio._PinnedHTTPResponse(
            status_code=200,
            headers={"content-type": "text/yaml"},
            content=spec.encode("utf-8"),
        )

    monkeypatch.setattr(studio, "_validate_external_spec_url", lambda _url: "")
    monkeypatch.setattr(studio, "_pinned_http_request", pinned_http_request)

    result = await studio._studio_autopilot_build_cartridge(
        {
            "target_cartridge_id": "acme_finance",
            "name": "ACME Finance",
            "domain": "finance",
            "spec_url": "https://api.example.com/openapi.yaml",
        },
        {"id": "u1", "role": "workspace_admin", "allowed_cartridges": ["*"]},
    )

    assert result["ok"] is True, result
    assert result["source"] == "openapi_url"
    assert result["blueprint"]["id"] == "acme_finance"
    assert result["blueprint"]["entities"][0]["entity"] == "Invoice"
    assert result["dry_run"] is True


@pytest.mark.asyncio
async def test_studio_autopilot_apply_requests_approval_without_writing(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")

    async def fail_write(*_args, **_kwargs):
        raise AssertionError("autopilot dry-run must not write directly")

    monkeypatch.setattr(studio.cartridge_service, "create_full_cartridge", fail_write)

    result = await studio._studio_autopilot_build_cartridge(
        {
            "target_cartridge_id": "acme",
            "name": "ACME",
            "apply": True,
            "descriptor": {
                "kind": "rest_sample",
                "entity_name": "orders",
                "sample": {"results": [{"order_id": "o1", "amount": 10.0}]},
            },
        },
        {"id": "u1", "role": "analyst", "allowed_cartridges": ["*"]},
    )

    assert result["ok"] is True, result
    assert result["approval_required"] is True
    assert result["tool"] == "studio__create_full_cartridge"
    assert result["args_preview"]["id"] == "acme"


@pytest.mark.asyncio
async def test_studio_autopilot_apply_writes_directly_for_workspace_admin(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")
    captured = {}

    async def create_full_cartridge(args, actor_user=None):
        captured["args"] = args
        captured["actor_user"] = actor_user
        return {"created": True, "counts": {"entities": 1, "datasets": 2}}

    monkeypatch.setattr(studio.cartridge_service, "create_full_cartridge", create_full_cartridge)

    result = await studio._studio_autopilot_build_cartridge(
        {
            "target_cartridge_id": "acme",
            "name": "ACME",
            "apply": True,
            "descriptor": {
                "kind": "rest_sample",
                "entity_name": "orders",
                "sample": {"results": [{"order_id": "o1", "amount": 10.0}]},
            },
        },
        {"id": "u1", "role": "workspace_admin", "allowed_cartridges": ["*"]},
    )

    assert result["ok"] is True, result
    assert result["dry_run"] is False
    assert result["applied"] is True
    assert "approval_required" not in result
    assert captured["args"]["id"] == "acme"
    assert captured["actor_user"]["role"] == "workspace_admin"


@pytest.mark.asyncio
async def test_studio_create_full_cartridge_marks_partial_when_datasets_not_confirmed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")

    async def incomplete_create(_args, actor_user=None):
        return {
            "created": True,
            "counts": {"entities": 1},
        }

    monkeypatch.setattr(studio.cartridge_service, "create_full_cartridge", incomplete_create)

    result = await studio._studio_create_full_cartridge(
        {
            "id": "acme",
            "name": "ACME",
            "datasets": [
                {"name": "silver_orders", "layer": "silver", "sql": "SELECT 1 AS order_id", "sources": ["orders"]},
                {"name": "gold_orders_metrics", "layer": "gold", "sql": "SELECT COUNT(*) AS c FROM silver_orders", "sources": ["silver_orders"]},
            ],
        },
        {"id": "u1", "role": "workspace_admin", "allowed_cartridges": ["*"]},
    )

    assert result["status"] == "partial"
    assert result["missing_datasets"] == ["silver_orders", "gold_orders_metrics"]
    assert "did not confirm" in result["reason"]


def test_create_full_cartridge_normalizer_preserves_autopilot_datasets():
    cartridge_service = importlib.import_module("app.services.cartridge_service")
    bp = importlib.import_module("app.services.cartridge_autopilot").build_blueprint(
        cartridge_id="acme",
        name="ACME",
        entities=[{
            "name": "orders",
            "fields": [
                {"name": "order_id", "type": "string", "primary_key": True, "nullable": False},
                {"name": "amount", "type": "number", "nullable": True},
            ],
        }],
    )

    manifest, seed_sql = cartridge_service._normalize_full_cartridge_manifest(bp)

    dataset_names = [dataset["name"] for dataset in manifest["datasets"]]
    assert "silver_orders" in dataset_names
    assert "gold_orders_metrics" in dataset_names
    assert "INSERT INTO datasets" in seed_sql
    assert "silver_orders" in seed_sql
    assert len(manifest["datasets"]) == len(bp["datasets"])
    assert all(dataset["sql_def"] for dataset in manifest["datasets"])


@pytest.mark.asyncio
async def test_studio_assistant_exposes_autopilot_tool(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")
    captured = {}

    async def list_servers():
        return []

    async def chat(**kwargs):
        captured["tools"] = kwargs["tools"]
        captured["system"] = kwargs["system"]
        captured["tool_server_map"] = kwargs["tool_server_map"]
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)

    await studio_assistant.chat(
        "crea un cartucho desde esta spec",
        [],
        step=1,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="analyst",
        actor_user={"id": "u1", "email": "u@example.com", "workspace_role": "analyst"},
    )

    tool_names = {tool["name"] for tool in captured["tools"]}
    assert "studio__autopilot_build_cartridge" in tool_names
    assert captured["tool_server_map"]["studio__autopilot_build_cartridge"] == "studio"
    assert captured["tool_server_map"]["autopilot_build_cartridge"] == "studio"
    assert "studio__autopilot_build_cartridge" in captured["system"]


@pytest.mark.asyncio
async def test_studio_assistant_routes_bare_local_autopilot_tool_name(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")

    async def list_servers():
        return []

    async def record_event(**_kwargs):
        return None

    async def chat(**kwargs):
        result = await kwargs["invoke_tool"](
            "",
            "autopilot_build_cartridge",
            {
                "target_cartridge_id": "acme_crm",
                "name": "ACME CRM",
                "domain": "crm",
                "descriptor": {
                    "kind": "rest_sample",
                    "entity_name": "deals",
                    "sample": {
                        "results": [{
                            "deal_id": "d1",
                            "amount": 1000.0,
                            "updated_at": "2026-05-30T10:00:00Z",
                        }],
                    },
                },
            },
        )
        return json.dumps({
            "ok": result["ok"],
            "source": result["source"],
            "blueprint_id": result["blueprint"]["id"],
        }), [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.audit_service, "record_event", record_event)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)

    result = await studio_assistant.chat(
        "crea un cartucho ACME CRM desde un sample",
        [],
        step=1,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="workspace_admin",
        actor_user={"id": "u1", "email": "u@example.com", "workspace_role": "workspace_admin"},
    )

    payload = json.loads(result["reply"])
    assert payload == {
        "ok": True,
        "source": "provided_descriptor",
        "blueprint_id": "acme_crm",
    }
