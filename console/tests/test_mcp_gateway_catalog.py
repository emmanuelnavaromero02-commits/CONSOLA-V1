from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.mcp_gateway import catalog


CONSOLE_APP = Path(__file__).resolve().parents[1] / "app"
REPO = Path(__file__).resolve().parents[2]
GATEWAY_SOURCES = [
    *sorted((CONSOLE_APP / "services" / "mcp_gateway").glob("*.py")),
    CONSOLE_APP / "routers" / "mcp_gateway.py",
    CONSOLE_APP / "routers" / "access_tokens.py",
    CONSOLE_APP / "domains" / "security" / "access_token_auth.py",
    CONSOLE_APP / "services" / "access_tokens.py",
    REPO / "scripts" / "omega_mcp_bridge" / "omega_mcp_bridge.py",
    REPO / "scripts" / "ia_gateway_smoke.py",
]
FORBIDDEN_COPY = re.compile(r"(?<![A-Za-z])(cartucho|cartridge|RAG|MCP|payload|chunk)(?![A-Za-z])", re.IGNORECASE)


def _walk(schema):
    yield schema
    if isinstance(schema, dict):
        for value in schema.values():
            yield from _walk(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk(value)


def test_catalog_has_the_ten_curated_actions():
    assert [action.name for action in catalog.ACTIONS] == [
        "consultar_contexto",
        "consultar_salud_pipelines",
        "consultar_control_room_resumen",
        "consultar_matriz_talento_9box",
        "consultar_kpis_sap_b1",
        "buscar_documentos_empresa",
        "listar_tablas_disponibles",
        "consultar_extraccion",
        "ejecutar_extraccion",
        "crear_app_analitica",
    ]
    assert "execute" not in catalog.CATALOG
    assert {action.name for action in catalog.ACTIONS if action.scope == "acciones"} == {
        "ejecutar_extraccion",
        "crear_app_analitica",
    }
    assert catalog.LONG_RUNNING_ACTIONS == {"ejecutar_extraccion", "crear_app_analitica"}


@pytest.mark.parametrize("action", catalog.ACTIONS, ids=lambda action: action.name)
def test_every_action_has_bounded_spanish_metadata(action):
    assert re.fullmatch(r"[a-z][a-z0-9_]{2,63}", action.name)
    assert 0 < len(action.description) <= catalog.MAX_DESCRIPTION_CHARS
    assert 0 < len(action.title) <= 80
    assert not FORBIDDEN_COPY.search(action.description), action.name
    assert not FORBIDDEN_COPY.search(action.title), action.name
    assert action.read_only == (action.scope == "lectura")
    assert action.consequential == (not action.read_only)
    assert action.mcp_annotations["openWorldHint"] is False
    assert action.mcp_annotations["readOnlyHint"] is action.read_only
    assert action.mcp_annotations["destructiveHint"] is False
    assert 0 < action.timeout_seconds <= 300


@pytest.mark.parametrize("action", catalog.ACTIONS, ids=lambda action: action.name)
def test_every_schema_is_closed_bounded_and_untitled(action):
    schema = action.input_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    for node in _walk(schema):
        if not isinstance(node, dict):
            continue
        assert "title" not in node or not isinstance(node["title"], str)
        if node.get("type") == "string" and "enum" not in node:
            assert "maxLength" in node or "pattern" in node, node
        if node.get("type") == "integer":
            assert "minimum" in node and "maximum" in node, node
        if node.get("type") == "array":
            assert "maxItems" in node, node
    for prop in schema.get("properties", {}).values():
        assert len(prop.get("description", "")) <= catalog.MAX_DESCRIPTION_CHARS
    assert action.schema_copy() is not action.input_schema


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (catalog.ConsultarKpisSapB1Args, {"area": "finanzas", "top_n": 11}),
        (catalog.ConsultarKpisSapB1Args, {"area": "nomina"}),
        (catalog.BuscarDocumentosEmpresaArgs, {"consulta": "ab"}),
        (catalog.BuscarDocumentosEmpresaArgs, {"consulta": "x" * 501}),
        (catalog.BuscarDocumentosEmpresaArgs, {"consulta": "margen", "max_resultados": 11}),
        (catalog.ConsultarExtraccionArgs, {"fuente": "sap b1", "ejecucion_id": "a b"}),
        (catalog.EjecutarExtraccionArgs, {"fuente": "sap b1", "clave_idempotencia": "x" * 65}),
        (catalog.EjecutarExtraccionArgs, {"fuente": "sap b1", "clave_idempotencia": "a/b"}),
        (catalog.CrearAppAnaliticaArgs, {"nombre": "ab", "objetivo": "x" * 20, "tablas": ["t"]}),
        (catalog.CrearAppAnaliticaArgs, {"nombre": "app_x", "objetivo": "x" * 20, "tablas": []}),
        (catalog.CrearAppAnaliticaArgs, {"nombre": "app_x", "objetivo": "x" * 20, "tablas": ["a"] * 6}),
        (catalog.CrearAppAnaliticaArgs, {"nombre": "app_x", "objetivo": "x" * 20, "tablas": ["a-b"]}),
        (catalog.ListarTablasDisponiblesArgs, {"capa": "bronze"}),
        (catalog.ConsultarContextoArgs, {"extra": True}),
    ],
)
def test_argument_models_reject_out_of_bounds_values(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_scope_rules():
    read = catalog.get_action("consultar_contexto")
    write = catalog.get_action("ejecutar_extraccion")
    assert catalog.scope_allows(read, frozenset({"lectura"}))
    assert catalog.scope_allows(read, frozenset({"acciones", "lectura"}))
    assert not catalog.scope_allows(write, frozenset({"lectura"}))
    assert catalog.scope_allows(write, frozenset({"acciones", "lectura"}))
    assert not catalog.scope_allows(read, frozenset())


def test_visible_actions_filter_by_scope_permission_and_source():
    viewer = {
        "id": 1,
        "role": "user",
        "workspace_role": "viewer",
        "active_workspace_id": "w",
        "allowed_cartridges": ["sap_b1"],
        "access_token_scopes": ["lectura"],
    }
    names = {action.name for action in catalog.visible_actions(viewer)}
    assert "consultar_kpis_sap_b1" in names
    assert "consultar_matriz_talento_9box" not in names
    assert "ejecutar_extraccion" not in names
    admin = {**viewer, "workspace_role": "workspace_admin", "access_token_scopes": ["acciones", "lectura"]}
    admin_names = {action.name for action in catalog.visible_actions(admin)}
    assert {"ejecutar_extraccion", "crear_app_analitica"} <= admin_names
    bare = {**viewer, "workspace_role": None, "role": "auditor"}
    assert {action.name for action in catalog.visible_actions(bare)} == {"consultar_contexto"}


@pytest.mark.parametrize("path", GATEWAY_SOURCES, ids=lambda path: path.name)
def test_gateway_sources_carry_no_url_literals_or_token_literals(path):
    assert path.exists(), path
    text = path.read_text(encoding="utf-8")
    assert "http://" not in text
    assert "https://" not in text
    assert re.search(r"omega_pat_[A-Za-z0-9]{43}", text) is None
