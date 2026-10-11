from __future__ import annotations

import os

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE entity_config ("
                "cartridge_id TEXT, entity TEXT, enabled BOOLEAN, connection_id TEXT, "
                "odata_entity TEXT, primary_key TEXT, mode TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO entity_config VALUES "
                "('sap_successfactors', 'FOCompany', 1, 'tenant_sf', 'FOCompany', 'externalCode', 'full'),"
                "('sap_successfactors', 'prueba', 0, 'tenant_sf', 'prueba', 'id', 'full'),"
                "('sap_successfactors', 'prueba_sin_conexion', 0, NULL, 'prueba', 'id', 'full')"
            )
        )
    return engine


def _ctx() -> dict:
    from app.core import request_context

    return request_context._sign_security_context(
        {
            "trusted": True,
            "source": "console",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    )


def _patch_catalog(monkeypatch):
    from app.services import catalog_service

    monkeypatch.setattr(catalog_service, "_engine", _engine())
    monkeypatch.setattr(catalog_service, "_seed_if_empty", lambda: None)
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: ({"FOCompany": {"externalCode"}}, None),
    )
    return catalog_service


def test_disabled_entities_are_not_loaded_for_extraction(monkeypatch):
    catalog_service = _patch_catalog(monkeypatch)

    assert [row["entity"] for row in catalog_service.get_all_entities()] == ["FOCompany"]


def test_extract_all_plan_neither_runs_nor_reports_disabled_entities(monkeypatch):
    catalog_service = _patch_catalog(monkeypatch)

    for conn_id in ("tenant_sf", None):
        entities, skipped = catalog_service.get_extract_all_plan(
            conn_id=conn_id,
            security_context=_ctx(),
            target="all",
        )
        planned = {row["entity"] for row in entities} | {
            str(row.get("entity")) for row in skipped
        }
        assert "prueba" not in planned
        assert "prueba_sin_conexion" not in planned
