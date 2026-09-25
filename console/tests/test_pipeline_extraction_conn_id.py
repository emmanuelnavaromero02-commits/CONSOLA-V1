from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.domains.pipeline.extract_config import (
    apply_user_scope_to_dag_conf,
    build_mcp_extract_args,
    connection_id_from_vault_payload,
    dag_extract_dag_id_from_metadata,
    dag_run_id_from_idempotency_key,
    entity_declared_in_static_catalog,
    is_transient_airflow_trigger_error,
    resolve_pipeline_sync_conn_id,
)
import app.main as console_main


def test_build_dag_extract_conf_accepts_safe_conn_id():
    conf = console_main._build_dag_extract_conf(
        "sap_successfactors",
        "PerPerson",
        "incremental",
        {"conn_id": "tenant_sf"},
    )

    assert conf["conn_id"] == "tenant_sf"


def test_build_dag_extract_conf_rejects_unsafe_conn_id():
    with pytest.raises(HTTPException) as exc:
        console_main._build_dag_extract_conf(
            "sap_successfactors",
            "PerPerson",
            "incremental",
            {"conn_id": "../../tenant_sf"},
        )

    assert exc.value.status_code == 400


def test_build_mcp_extract_args_defaults_mode_and_accepts_connection_id():
    assert build_mcp_extract_args("Customer", {"connection_id": "crm-main"}) == {
        "entity": "Customer",
        "mode": "incremental",
        "conn_id": "crm-main",
    }


def test_build_mcp_extract_args_rejects_unsafe_connection_id():
    with pytest.raises(HTTPException) as exc:
        build_mcp_extract_args("Customer", {"connection_id": "bad/conn"})

    assert exc.value.status_code == 400


def test_dag_extract_dag_id_from_metadata_returns_configured_dag_id():
    assert (
        dag_extract_dag_id_from_metadata(
            cartridge="sap_successfactors",
            entity="User",
            metadata={"entity": "User", "enabled": True, "dag_id": "sf_extract"},
            static_catalog_contains=lambda _cartridge, _entity: False,
        )
        == "sf_extract"
    )


def test_dag_extract_dag_id_from_metadata_reports_static_orphan():
    with pytest.raises(HTTPException) as exc:
        dag_extract_dag_id_from_metadata(
            cartridge="sap_successfactors",
            entity="PerPhone",
            metadata={"entity": None, "enabled": True, "dag_id": "sf_extract"},
            static_catalog_contains=lambda _cartridge, _entity: True,
        )

    assert exc.value.status_code == 400
    assert "entity_config" in str(exc.value.detail)


def test_dag_extract_dag_id_from_metadata_reports_missing_entity():
    with pytest.raises(HTTPException) as exc:
        dag_extract_dag_id_from_metadata(
            cartridge="replicon",
            entity="Missing",
            metadata={"entity": None, "enabled": True, "dag_id": "replicon_extract"},
            static_catalog_contains=lambda _cartridge, _entity: False,
        )

    assert exc.value.status_code == 404


def test_dag_extract_dag_id_from_metadata_reports_disabled_entity():
    with pytest.raises(HTTPException) as exc:
        dag_extract_dag_id_from_metadata(
            cartridge="replicon",
            entity="Department",
            metadata={
                "entity": "Department",
                "enabled": False,
                "dag_id": "replicon_extract",
            },
            static_catalog_contains=lambda _cartridge, _entity: False,
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
            {"connections": [{"conn_id": ""}], "items": [{"id": "tenant_sf"}]}
        )
        == "tenant_sf"
    )
    run_id = dag_run_id_from_idempotency_key("sap_successfactors_extract", "same-key")
    assert run_id == dag_run_id_from_idempotency_key(
        "sap_successfactors_extract",
        "same-key",
    )
    assert run_id.startswith("console__sap_successfactors_extract__")
    assert is_transient_airflow_trigger_error("Connection refused")
    assert not is_transient_airflow_trigger_error("invalid conf")


@pytest.mark.asyncio
async def test_resolve_pipeline_sync_conn_id_prefers_requested_value():
    async def fail_vault(*_args):
        raise AssertionError("vault should not be called")

    async def fail_entity_config(*_args):
        raise AssertionError("entity_config should not be called")

    assert (
        await resolve_pipeline_sync_conn_id(
            "sap_successfactors",
            "requested_conn",
            {"id": 1},
            vault_payload_loader=fail_vault,
            entity_config_conn_loader=fail_entity_config,
        )
        == "requested_conn"
    )


@pytest.mark.asyncio
async def test_resolve_pipeline_sync_conn_id_uses_vault_before_entity_config():
    async def vault_payload(*_args):
        return {"connections": [{"conn_id": "vault_conn"}]}

    async def fail_entity_config(*_args):
        raise AssertionError("entity_config should not be called")

    assert (
        await resolve_pipeline_sync_conn_id(
            "sap_successfactors",
            None,
            {"id": 1},
            vault_payload_loader=vault_payload,
            entity_config_conn_loader=fail_entity_config,
        )
        == "vault_conn"
    )


@pytest.mark.asyncio
async def test_resolve_pipeline_sync_conn_id_falls_back_to_entity_config():
    async def vault_payload(*_args):
        return None

    async def entity_config_conn_id(*_args):
        return "entity_config_conn"

    assert (
        await resolve_pipeline_sync_conn_id(
            "sap_successfactors",
            None,
            {"id": 1},
            vault_payload_loader=vault_payload,
            entity_config_conn_loader=entity_config_conn_id,
        )
        == "entity_config_conn"
    )


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
