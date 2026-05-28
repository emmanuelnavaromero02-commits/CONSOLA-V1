from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lineage_viewer_keeps_directional_graph_and_node_details():
    src = read("console-next/src/app/(shell)/viewer/page.tsx")
    assert 'id="lineage-arrow-impact"' in src
    assert 'id="lineage-arrow-source"' in src
    assert 'markerEnd=' in src
    assert "data-lineage-relation" in src
    assert "collectReachable" in src
    assert "Impacto downstream" in src
    assert "layoutLineageGraph" in src
    assert "Nodo seleccionado" in src
    assert "DependencyList" in src
    assert "Abrir dataset" in src


def test_agents_console_restores_editor_tools_schedule_runs_and_test_flow():
    src = read("console-next/src/components/agents/AgentsConsole.tsx")
    for label in ("Configuración", "Tools", "RAG", "Tareas", "Ejecuciones", "Probar"):
        assert label in src
    for helper in (
        "listAgentToolCatalog",
        "createAgent",
        "updateAgent",
        "listAgentRuns",
        "getAgentRun",
        "invokeAgent",
    ):
        assert helper in src
    assert "allowed_tools" in src
    assert "variables" in src
    assert "schedule" in src


def test_agent_api_client_covers_full_legacy_surface():
    src = read("console-next/src/lib/admin-surfaces.ts")
    for endpoint in (
        "/api/agents/_tool-catalog",
        "/api/agents",
        "/api/agents/${encodeURIComponent(id)}",
        "/api/agents/${encodeURIComponent(id)}/runs",
        "/api/agent-runs/${encodeURIComponent(String(runId))}",
        "/api/agents/${encodeURIComponent(id)}/invoke",
    ):
        assert endpoint in src
