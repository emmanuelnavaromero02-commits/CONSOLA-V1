from __future__ import annotations

import os
from typing import Any


def connector_payload(connector_schema: dict[str, Any]) -> dict[str, Any]:
    connector = (
        connector_schema.get("connector")
        if isinstance(connector_schema.get("connector"), dict)
        else connector_schema
    )
    return connector if isinstance(connector, dict) else {}


def connection_value(connection: dict[str, Any], *names: str) -> str:
    for name in names:
        value = connection.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def env_or_connection(connection: dict[str, Any], env_name: str, *names: str) -> str:
    return os.environ.get(env_name or "", "") or connection_value(connection, *names)


def base_url_for_source(connector: dict[str, Any], connection: dict[str, Any]) -> str:
    api = connector.get("api") if isinstance(connector.get("api"), dict) else {}
    env_name = str(api.get("base_url_env") or "")
    return env_or_connection(connection, env_name, "base_url", "url").rstrip("/")


def service_metadata_paths(entity_specs: list[dict[str, Any]]) -> list[str]:
    paths = [""]
    for item in entity_specs:
        odata_entity = str(item.get("service_path") or item.get("odata_entity") or "")
        if "/" not in odata_entity:
            continue
        service = odata_entity.split("/", 1)[0].strip("/")
        if service and service not in paths:
            paths.append(service)
    return paths[:8]


def looks_odata(cartridge_id: str, connector: dict[str, Any], args: dict[str, Any]) -> bool:
    explicit = str(args.get("source_kind") or args.get("kind") or "").lower()
    if explicit in {"odata", "sap"}:
        return True
    auth = connector.get("auth") if isinstance(connector.get("auth"), dict) else {}
    if str(auth.get("type") or "").lower() == "database":
        return False
    if cartridge_id.startswith("sap_"):
        return True
    return str(auth.get("type") or "").lower() in {
        "basic",
        "oauth2_client_credentials",
    }
