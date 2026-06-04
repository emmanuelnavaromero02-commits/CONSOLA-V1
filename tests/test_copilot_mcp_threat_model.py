from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs/security/copilot-mcp-threat-model.md"


def test_copilot_mcp_threat_model_maps_threats_to_controls_and_tests():
    text = DOC.read_text(encoding="utf-8")
    for threat in (
        "Prompt injection",
        "Tool injection",
        "malicious SQL",
        "DAG code generation",
        "RCE-like MCP tools",
        "Vault secret exfiltration",
        "RAG poisoning",
        "MCP registry SSRF",
        "Superset create/import abuse",
        "Scheduled agents",
    ):
        assert threat in text
    for test_name in (
        "tests/test_mcp_tool_policy.py",
        "tests/test_dag_codegen_security.py",
        "tests/test_no_direct_pggold_access.py",
        "tests/test_mcp_response_redaction.py",
        "tests/test_rag_scoped_paths.py",
        "tests/test_mcp_registry_ssrf_guard.py",
        "vault/tests/test_vault_workspace_scope.py",
    ):
        assert test_name in text
    assert "Required Live Drills Before v1.0" in text
    assert "v1.0 publica enterprise: no" in text
