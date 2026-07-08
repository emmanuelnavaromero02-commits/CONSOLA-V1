from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_console_dag_conf_carries_backend_security_context():
    # The scoped DAG-conf builder was modularized out of main.py into the
    # pipeline domain; assert against its current home.
    source = (REPO_ROOT / "console/app/domains/pipeline/extract_config.py").read_text(encoding="utf-8")

    assert "scoped[\"security_context\"] = ctx" in source
    assert "tenant_id" in source
    assert "workspace_id" in source


def test_replicon_refresh_chain_propagates_workspace_scope():
    source = (REPO_ROOT / "cartridges/replicon/dags/replicon_extract.py").read_text(encoding="utf-8")

    assert "get_current_context" in source
    assert "refresh_conf" in source
    assert "tenant_id" in source
    assert "workspace_id" in source
    assert "security_context" in source
    assert 'json={"conf": refresh_conf}' in source
