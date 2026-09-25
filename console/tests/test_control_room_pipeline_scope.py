from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_console_dag_conf_carries_backend_security_context():
    source = (REPO_ROOT / "console/app/domains/pipeline/extract_config.py").read_text(
        encoding="utf-8"
    )

    assert 'scoped["security_context"] = ctx' in source
    assert "tenant_id" in source
    assert "workspace_id" in source


def test_replicon_refresh_chain_propagates_workspace_scope():
    source = (REPO_ROOT / "cartridges/replicon/dags/replicon_extract.py").read_text(
        encoding="utf-8"
    )

    assert "get_current_context" in source
    assert "refresh_conf" in source
    assert "tenant_id" in source
    assert "workspace_id" in source
    assert "security_context" in source
    assert "build_dataset_refresh_trigger" in source
    dag_source = source.split("def replicon_extract", 1)[1]
    assert dag_source.index("authorize_refresh_chain") < dag_source.index("def extract")
    trigger = dag_source.split("def trigger_refresh_chain", 1)[1]
    assert "build_dataset_refresh_admission" not in trigger
    assert 'json={"conf": refresh_conf, "dag_run_id": dag_run_id}' in source
