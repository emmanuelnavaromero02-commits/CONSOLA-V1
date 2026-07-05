from __future__ import annotations

from app.domains.studio.source_metadata import (
    base_url_for_source,
    connection_value,
    connector_payload,
    env_or_connection,
    looks_odata,
    service_metadata_paths,
)


def test_connector_payload_accepts_wrapped_or_direct_schema():
    wrapped = {"connector": {"auth": {"type": "basic"}}}
    direct = {"auth": {"type": "bearer"}}

    assert connector_payload(wrapped) == {"auth": {"type": "basic"}}
    assert connector_payload(direct) == direct
    assert connector_payload({"connector": "invalid"}) == {"connector": "invalid"}


def test_connection_value_and_env_override(monkeypatch):
    connection = {"username": "vault-user", "base_url": "https://vault.example/api/"}

    assert connection_value(connection, "missing", "username") == "vault-user"
    assert connection_value(connection, "missing") == ""

    monkeypatch.setenv("STUDIO_TEST_BASE_URL", "https://env.example/api/")
    assert (
        env_or_connection(connection, "STUDIO_TEST_BASE_URL", "base_url")
        == "https://env.example/api/"
    )
    assert (
        env_or_connection(connection, "MISSING_ENV", "base_url")
        == "https://vault.example/api/"
    )


def test_base_url_for_source_uses_api_env_then_connection(monkeypatch):
    connector = {"api": {"base_url_env": "STUDIO_TEST_SOURCE_URL"}}
    connection = {"base_url": "https://vault.example/api/"}

    assert base_url_for_source(connector, connection) == "https://vault.example/api"

    monkeypatch.setenv("STUDIO_TEST_SOURCE_URL", "https://env.example/odata/")
    assert base_url_for_source(connector, connection) == "https://env.example/odata"


def test_service_metadata_paths_extracts_distinct_service_prefixes():
    assert service_metadata_paths(
        [
            {"odata_entity": "learning/v1/Items"},
            {"service_path": "learning/v1/Assignments"},
            {"odata_entity": "User"},
            {"odata_entity": "career/CareerWorksheet"},
            {"odata_entity": "learning/v1/History"},
        ]
    ) == ["", "learning", "career"]


def test_looks_odata_from_explicit_sap_cartridge_or_auth_type():
    assert looks_odata("custom", {}, {"source_kind": "odata"}) is True
    assert looks_odata("sap_successfactors", {}, {}) is True
    assert looks_odata("custom", {"auth": {"type": "basic"}}, {}) is True
    assert looks_odata("custom", {"auth": {"type": "oauth2_client_credentials"}}, {}) is True
    assert looks_odata("custom", {"auth": {"type": "bearer"}}, {}) is False
