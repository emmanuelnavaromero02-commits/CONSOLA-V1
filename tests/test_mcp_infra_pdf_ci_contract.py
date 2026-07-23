from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/mcp-infra-pdf-security.yml"


def test_pdf_security_workflow_runs_real_functional_tests_on_every_pr() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in source
    assert "paths:" not in source
    assert "tests/test_mcp_infra_pdf_ingest.py" in source
    assert "tests/test_mcp_infra_pdf_capacity.py" in source
    assert "tests/test_mcp_infra_pdf_compose_capacity.py" in source
    assert "tests/test_pypdf_security.py" in source
    assert "timeout-minutes:" in source
    assert "continue-on-error" not in source
