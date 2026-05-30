import importlib
import socket
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_studio_introspect_source_uses_cartridge_connector_schema(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")
    cartridges = importlib.import_module("app.routers.cartridges")
    calls = []

    async def connector_schema(cartridge, user):
        calls.append((cartridge, user))
        return {"connection": {"auth_method": "bearer_token"}, "entities": [{"name": "Project"}]}

    monkeypatch.setattr(cartridges, "connector_schema", connector_schema)
    async def no_live(*_args):
        return [], "no saved credentials in vault"

    monkeypatch.setattr(studio, "_live_introspection", no_live)
    monkeypatch.setattr(studio, "_load_static_entity_specs", lambda _cartridge: [])

    result = await studio._studio_introspect_source(
        {"cartridge_id": "replicon"},
        {"id": "u1", "allowed_cartridges": ["replicon"]},
    )

    assert calls == [("replicon", {"id": "u1", "allowed_cartridges": ["replicon"]})]
    assert result["cartridge_id"] == "replicon"
    assert result["endpoint"] == "/api/cartridges/replicon/connector_schema"
    assert result["connector_schema"] == {
        "connection": {"auth_method": "bearer_token"},
        "entities": [{"name": "Project"}],
    }
    assert result["entities"] == []
    assert result["source"] == "static"
    assert result["reason"] == "live introspection requires studio.write or cartridges.write"


@pytest.mark.asyncio
async def test_studio_introspect_source_returns_live_openapi_fields(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")
    cartridges = importlib.import_module("app.routers.cartridges")
    spec = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "Invoice": {
                    "required": ["id", "total"],
                    "properties": {
                        "id": {"type": "integer", "x-primary-key": True},
                        "total": {"type": "number"},
                        "issued_at": {"type": "string", "format": "date-time"},
                    },
                }
            }
        },
    }

    async def connector_schema(cartridge, user):
        return {"connector": {"api": {}}}

    async def no_vault(_cartridge_id, _conn_id="default"):
        return {}, "no saved credentials in vault"

    monkeypatch.setattr(cartridges, "connector_schema", connector_schema)
    monkeypatch.setattr(studio, "_vault_connection", no_vault)

    result = await studio._studio_introspect_source(
        {"cartridge_id": "replicon", "source_kind": "openapi", "spec": spec},
        {"id": "u1", "role": "workspace_admin", "allowed_cartridges": ["replicon"]},
    )

    assert result["source"] == "live"
    assert result["entities"][0]["name"] == "Invoice"
    assert result["entities"][0]["primary_key"] == "id"
    fields = {field["name"]: field for field in result["entities"][0]["fields"]}
    assert fields["id"]["type"] == "int"
    assert fields["id"]["nullable"] is False
    assert fields["id"]["primary_key"] is True
    assert fields["id"]["source_type"] == "integer"
    assert fields["id"]["source_name"] == "id"
    assert fields["total"]["type"] == "float"
    assert fields["total"]["nullable"] is False
    assert fields["issued_at"]["type"] == "timestamp"


def test_studio_openapi_spec_url_blocks_ssrf_targets(monkeypatch):
    studio = importlib.import_module("app.routers.studio")

    monkeypatch.setenv("APP_ENV", "production")
    assert "https" in studio._validate_external_spec_url("http://example.com/openapi.json")
    assert "not public" in studio._validate_external_spec_url("https://localhost/openapi.json")
    assert "metadata" in studio._validate_external_spec_url("https://169.254.169.254/latest/meta-data")
    assert "metadata" in studio._validate_external_url("https://169.254.169.254/token", label="OAuth2 token URL")

    monkeypatch.setattr(
        studio.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    )
    assert "non-public" in studio._validate_external_spec_url("https://api.example.com/openapi.json")

    monkeypatch.setattr(
        studio.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )
    assert "non-public" in studio._validate_external_spec_url("https://api.example.com/openapi.json")

    monkeypatch.setattr(
        studio.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    assert studio._validate_external_spec_url("https://api.example.com/openapi.json") == ""


@pytest.mark.asyncio
async def test_studio_introspect_source_returns_live_odata_entity_sets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SAP_HCM_BASE_URL", "https://sap.example.com/odata")
    studio = importlib.import_module("app.routers.studio")
    cartridges = importlib.import_module("app.routers.cartridges")
    xml = """<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="Demo" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="BusinessPartner">
        <Key><PropertyRef Name="BusinessPartnerID"/></Key>
        <Property Name="BusinessPartnerID" Type="Edm.String" Nullable="false"/>
        <Property Name="UpdatedAt" Type="Edm.DateTimeOffset"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="A_BusinessPartner" EntityType="Demo.BusinessPartner"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""

    async def connector_schema(cartridge, user):
        return {"connector": {"auth": {"type": "basic"}, "api": {"base_url_env": "SAP_HCM_BASE_URL"}}}

    async def vault_connection(_cartridge_id, _conn_id="default"):
        return {"username": "user", "password": "pass"}, ""

    requests = []

    async def pinned_http_request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return studio._PinnedHTTPResponse(
            status_code=200,
            headers={"content-type": "application/xml"},
            content=xml.encode("utf-8"),
        )

    monkeypatch.setattr(cartridges, "connector_schema", connector_schema)
    monkeypatch.setattr(studio, "_vault_connection", vault_connection)
    monkeypatch.setattr(studio, "_pinned_http_request", pinned_http_request)
    monkeypatch.setattr(
        studio.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )

    result = await studio._studio_introspect_source(
        {"cartridge_id": "sap_hcm", "source_kind": "odata"},
        {"id": "u1", "role": "workspace_admin", "allowed_cartridges": ["sap_hcm"]},
    )

    assert result["source"] == "live"
    assert [entity["name"] for entity in result["entities"]] == ["A_BusinessPartner"]
    assert result["entities"][0]["primary_key"] == "BusinessPartnerID"
    assert requests[0][0] == "GET"
    assert requests[0][1] == "https://sap.example.com/odata/$metadata"
    assert requests[0][2]["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_live_sql_introspection_requires_tables_and_maps_types(monkeypatch):
    studio = importlib.import_module("app.routers.studio")

    no_tables, reason = await studio._live_sql_introspection(
        {},
        {"database_url": "postgresql://user:pass@db.example.com/app"},
    )
    assert no_tables == []
    assert reason == "tables are required for SQL introspection"

    bypass, reason = await studio._live_sql_introspection(
        {"tables": ["invoice"]},
        {"database_url": "postgresql:///app?host=127.0.0.1"},
    )
    assert bypass == []
    assert reason == "database DSN host query parameters are not allowed"

    missing_host, reason = await studio._live_sql_introspection(
        {"tables": ["invoice"]},
        {"database_url": "postgresql:///app"},
    )
    assert missing_host == []
    assert reason == "database host is required"

    rows = [
        {"table_name": "invoice", "column_name": "id", "data_type": "integer", "is_nullable": "NO"},
        {"table_name": "invoice", "column_name": "updated_at", "data_type": "timestamp without time zone", "is_nullable": "YES"},
    ]
    captured = {}

    class Conn:
        async def fetch(self, query, table_names, **kwargs):
            captured["query"] = query
            captured["table_names"] = table_names
            captured["timeout"] = kwargs["timeout"]
            return rows

        async def close(self):
            return None

    async def connect(**kwargs):
        assert kwargs["timeout"] == 8.0
        return Conn()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))
    monkeypatch.setattr(
        studio.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 5432))],
    )

    entities, reason = await studio._live_sql_introspection(
        {"tables": ["invoice"]},
        {"database_url": "postgresql://user:pass@db.example.com/app"},
    )

    assert reason == ""
    assert "table_name = ANY($1::text[])" in captured["query"]
    assert captured["table_names"] == ["invoice"]
    assert captured["timeout"] == 8.0
    assert entities[0]["name"] == "invoice"
    fields = {field["name"]: field for field in entities[0]["fields"]}
    assert fields["id"]["type"] == "int"
    assert fields["id"]["primary_key"] is True
    assert fields["updated_at"]["type"] == "timestamp"


@pytest.mark.asyncio
async def test_studio_assistant_exposes_introspect_source_in_steps_2_and_3(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")
    captured = {}

    async def list_servers():
        return []

    async def chat(**kwargs):
        captured["tools"] = kwargs["tools"]
        captured["system"] = kwargs["system"]
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)

    await studio_assistant.chat(
        "descubre la fuente",
        [],
        step=2,
        manifest={"id": "replicon", "name": "Replicon"},
        actor_role="analyst",
        actor_user={"id": "u1", "email": "u@example.com", "workspace_role": "analyst"},
    )

    tool_names = {tool["name"] for tool in captured["tools"]}
    assert "studio__introspect_source" in tool_names
    assert "studio__generate_dag_code" in tool_names
    assert "studio__validate_dag_code" in tool_names
    assert "studio__introspect_source(cartridge_id)" in captured["system"]
    assert "studio__generate_dag_code" in captured["system"]
    assert "studio__validate_dag_code" in captured["system"]
    assert studio_assistant.filter_tools_for_step(captured["tools"], 3)
    step_3_names = {tool["name"] for tool in studio_assistant.filter_tools_for_step(captured["tools"], 3)}
    assert "studio__generate_dag_code" not in step_3_names
    assert "studio__validate_dag_code" not in step_3_names
    assert not studio_assistant.filter_tools_for_step(captured["tools"], 1)


@pytest.mark.asyncio
async def test_studio_generate_dag_code_returns_ready_python_without_edit_here(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")
    schema = {
        "connector": {
            "id": "sap_s4hana",
            "auth": {"type": "basic", "env_var_user": "SAP_S4_USER", "env_var_pass": "SAP_S4_PASS"},
            "api": {"base_url_env": "SAP_S4_BASE_URL", "page_size_default": 500, "retry_max": 3},
            "watermark": {"field_format": "iso8601"},
            "entities": {"watermark_field_default": "LastChangeDate"},
        }
    }

    result = await studio._studio_generate_dag_code(
        {"cartridge_id": "sap_s4hana", "entity_name": "BusinessPartner", "schema": schema},
        {"id": "u1", "allowed_cartridges": ["*"]},
    )

    code = result["code"]
    assert result["dag_id"] == "sap_s4hana_BusinessPartner_dynamic_extract"
    assert result["connector_kind"] == "odata"
    assert "EDIT_HERE" not in code
    assert "Retry(" in code
    assert "watermark_get" in code
    assert "while next_url" in code
    assert "tags=[\"mode\", CARTRIDGE_ID, \"bronze\", \"studio-generated\", CONNECTOR_KIND]" in code
    assert result["validated"] is True
    assert result["validation"]["valid"] is True
    compile(code, result["dag_id"], "exec")


@pytest.mark.asyncio
async def test_studio_generate_dag_code_uses_introspected_fields(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")
    schema = {
        "connector_schema": {
            "connector": {
                "id": "acme",
                "auth": {"type": "bearer_token", "env_var": "ACME_TOKEN"},
                "api": {"base_url_env": "ACME_BASE_URL"},
            }
        },
        "entities": [{
            "name": "Invoice",
            "fields": [
                {"name": "invoice_id", "type": "int", "nullable": False, "primary_key": True, "source_type": "integer"},
                {"name": "amount", "type": "float", "nullable": False, "primary_key": False, "source_type": "number"},
                {"name": "updated_at", "type": "timestamp", "nullable": True, "primary_key": False, "source_type": "string:date-time"},
            ],
        }],
    }

    result = await studio._studio_generate_dag_code(
        {"cartridge_id": "acme", "entity_name": "Invoice", "schema": schema},
        {"id": "u1", "allowed_cartridges": ["*"]},
    )

    assert result["watermark_field"] == "updated_at"
    assert result["primary_keys"] == ["invoice_id"]
    assert result["select_fields"] == ["invoice_id", "amount", "updated_at"]
    assert '"updated_at"' in result["code"]
    assert result["validated"] is True


@pytest.mark.asyncio
async def test_studio_validate_dag_code_reports_stderr(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")

    valid = await studio._studio_validate_dag_code({"code": "import os\nVALUE = os.getcwd()\n"}, {"id": "u1"})
    invalid = await studio._studio_validate_dag_code({"code": "def broken(:\n    pass\n"}, {"id": "u1"})

    assert valid["valid"] is True
    assert "python_syntax" in valid["checks"]
    assert invalid["valid"] is False
    assert invalid["stderr"]


@pytest.mark.asyncio
async def test_studio_semantic_relations_uses_refinement_catalog(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio = importlib.import_module("app.routers.studio")

    async def refinement_invoke(tool, args, **_kwargs):
        assert tool == "get_data_catalog"
        assert args == {"cartridge": "replicon"}
        return {
            "relationships": [{
                "from_dataset": "timeentry_clean",
                "from_column": "project_id",
                "to_dataset": "project_clean",
                "to_column": "project_id",
                "join_hint": "LEFT",
            }]
        }

    monkeypatch.setattr(studio, "_refinement_invoke", refinement_invoke)
    relations, source = await studio._semantic_relations("replicon", {"semantic_model": {}}, {"id": "u1"})

    assert source == "refinement.get_data_catalog"
    assert relations == [{
        "from_dataset": "timeentry_clean",
        "from_column": "project_id",
        "to_dataset": "project_clean",
        "to_column": "project_id",
        "join_hint": "LEFT",
        "source": "refinement.get_data_catalog",
    }]


def test_create_full_cartridge_manifest_validates_seed_and_integrity(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    cartridge_service = importlib.import_module("app.services.cartridge_service")

    manifest, seed_sql = cartridge_service._normalize_full_cartridge_manifest({
        "id": "acme",
        "name": "ACME",
        "description": "Full cartridge",
        "dags": [{"dag_id": "acme_extract", "description": "Extracts ACME"}],
        "entities": [{"entity": "Invoice", "dag_id": "acme_extract", "description": "Invoices"}],
        "knowledge_bits": [{"kb_id": "invoice_totals", "sql": "SELECT * FROM read_parquet('s3://lakehouse/raw/acme/Invoice/**/*.parquet')"}],
        "agents": [{
            "slug": "acme_analyst",
            "name": "ACME Analyst",
            "description": "Answers ACME questions",
            "allowed_tools": ["refinement__query_dataset"],
        }],
        "semantic_model": {
            "vocabulary": [{"term": "invoice", "definition": "Customer invoice", "maps_to": "Invoice"}],
        },
    })

    assert manifest["knowledge_bits"][0]["kb_id"] == "invoice_totals"
    assert manifest["agents"][0]["slug"] == "acme_analyst"
    assert "INSERT INTO kb_config" in seed_sql
    assert "INSERT INTO agents" in seed_sql


def test_create_full_cartridge_rejects_unknown_entity_dag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    cartridge_service = importlib.import_module("app.services.cartridge_service")

    with pytest.raises(ValueError, match="unknown dag_id"):
        cartridge_service._normalize_full_cartridge_manifest({
            "id": "acme",
            "name": "ACME",
            "dags": [{"dag_id": "acme_extract"}],
            "entities": [{"entity": "Invoice", "dag_id": "missing_dag"}],
        })


def test_studio_step_tools_stay_whitelisted_and_dag_step_slim(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    studio_assistant = importlib.import_module("app.services.studio_assistant")

    literal_step_tools = {
        tool
        for tools in studio_assistant.STEP_TOOLS.values()
        for tool in tools
        if not tool.endswith("*")
    }
    missing = literal_step_tools - studio_assistant.STUDIO_TOOLS_WHITELIST

    assert not missing, f"STEP_TOOLS entries missing from whitelist: {sorted(missing)}"

    expected_local_steps = {
        "introspect_source": [2, 3],
        "generate_dag_code": [2],
        "validate_dag_code": [2],
        "create_full_cartridge": [1],
    }
    for tool, expected_steps in expected_local_steps.items():
        actual_steps = [
            step
            for step in range(1, 8)
            if studio_assistant._matches_pattern(
                tool,
                studio_assistant.STEP_TOOLS["_common"] | studio_assistant.STEP_TOOLS.get(step, set()),
            )
        ]
        assert actual_steps == expected_steps

    synthetic_tools = [
        {"name": f"infra__{tool}", "input_schema": {"type": "object"}}
        for tool in sorted(studio_assistant.STUDIO_TOOLS_WHITELIST)
    ]
    dag_tools = studio_assistant.filter_tools_for_step(synthetic_tools, 2)
    assert len(dag_tools) < 20
