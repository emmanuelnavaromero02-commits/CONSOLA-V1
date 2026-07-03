from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.domains.pipeline.extract_config import (
    apply_user_scope_to_dag_conf,
    connection_id_from_vault_payload,
    dag_run_id_from_idempotency_key,
    entity_declared_in_static_catalog,
    is_transient_airflow_trigger_error,
)
import app.main as console_main


def test_build_dag_extract_conf_accepts_safe_conn_id():
    conf = console_main._build_dag_extract_conf(
        "sap_successfactors",
        "PerPerson",
        "incremental",
        {"conn_id": "femsa_sf"},
    )

    assert conf["conn_id"] == "femsa_sf"


def test_build_dag_extract_conf_rejects_unsafe_conn_id():
    with pytest.raises(HTTPException) as exc:
        console_main._build_dag_extract_conf(
            "sap_successfactors",
            "PerPerson",
            "incremental",
            {"conn_id": "../../femsa_sf"},
        )

    assert exc.value.status_code == 400


def test_pipeline_extract_metadata_selects_connection_id():
    source = Path(console_main.__file__).read_text(encoding="utf-8")

    assert "e.connection_id AS connection_id" in source
    assert re.search(
        r'extract_conf\["conn_id"\]\s*=\s*_normalize_pipeline_conn_id\(\s*metadata\.get\("connection_id"\)\s*\)',
        source,
    )


def test_extract_config_helpers_parse_vault_and_idempotency():
    assert (
        connection_id_from_vault_payload(
            {"connections": [{"conn_id": ""}], "items": [{"id": "femsa_sf"}]}
        )
        == "femsa_sf"
    )
    run_id = dag_run_id_from_idempotency_key("sap_successfactors_extract", "same-key")
    assert run_id == dag_run_id_from_idempotency_key(
        "sap_successfactors_extract",
        "same-key",
    )
    assert run_id.startswith("console__sap_successfactors_extract__")
    assert is_transient_airflow_trigger_error("Connection refused")
    assert not is_transient_airflow_trigger_error("invalid conf")


def test_apply_user_scope_to_dag_conf_adds_security_context():
    scoped = apply_user_scope_to_dag_conf(
        {"entity": "User"},
        {"id": 1},
        security_context_builder=lambda user: {
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": user["id"],
        },
    )

    assert scoped["tenant_id"] == "tenant-1"
    assert scoped["workspace_id"] == "workspace-1"
    assert scoped["security_context"]["user_id"] == 1


def test_apply_user_scope_to_dag_conf_rejects_scope_mismatch():
    with pytest.raises(HTTPException) as exc:
        apply_user_scope_to_dag_conf(
            {"tenant_id": "tenant-2"},
            {"id": 1},
            security_context_builder=lambda _user: {
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
            },
        )

    assert exc.value.status_code == 403


def test_entity_declared_in_static_catalog_reads_local_yaml(tmp_path):
    config_dir = tmp_path / "cartridges" / "sap_successfactors" / "app" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "entities.yaml").write_text(
        "entities:\n  - entity: User\n  - name: EmpJob\n",
        encoding="utf-8",
    )

    assert entity_declared_in_static_catalog(
        "sap_successfactors",
        "EmpJob",
        repo_root=tmp_path,
        registry_root=tmp_path / "registry",
    )
    assert not entity_declared_in_static_catalog(
        "sap_successfactors",
        "Missing",
        repo_root=tmp_path,
        registry_root=tmp_path / "registry",
    )
