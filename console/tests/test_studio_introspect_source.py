import importlib

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

    result = await studio._studio_introspect_source({"cartridge_id": "replicon"}, {"id": "u1"})

    assert calls == [("replicon", {"id": "u1"})]
    assert result == {
        "cartridge_id": "replicon",
        "endpoint": "/api/cartridges/replicon/connector_schema",
        "connector_schema": {
            "connection": {"auth_method": "bearer_token"},
            "entities": [{"name": "Project"}],
        },
    }


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
