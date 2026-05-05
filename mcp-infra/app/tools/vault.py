"""
MCP tools — Vault connection & secret management.

These tools let the Studio AI configure cartridge API credentials
without editing files or restarting containers.

Tools exposed:
  vault_list_connections(cartridge_id)
  vault_set_connection(cartridge_id, conn_id, base_url, auth_method, token, **extra)
  vault_get_connection(cartridge_id, conn_id)    — credentials masked
  vault_delete_connection(cartridge_id, conn_id)
  vault_set_secret(scope, key, value)
  vault_list_secrets(scope)
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.registry import tool

_VAULT = settings.vault_url.rstrip("/")


def _vault_get(path: str) -> dict:
    r = httpx.get(f"{_VAULT}{path}", timeout=10)
    r.raise_for_status()
    return r.json()


def _vault_put(path: str, body: dict) -> dict:
    r = httpx.put(f"{_VAULT}{path}", json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def _vault_delete(path: str) -> dict:
    r = httpx.delete(f"{_VAULT}{path}", timeout=10)
    r.raise_for_status()
    return r.json()


# ── Connection tools ──────────────────────────────────────────────────────────

@tool(
    name="vault_list_connections",
    description=(
        "List all API connections configured for a cartridge. "
        "Credentials are masked (shown as ***). "
        "Use this to see what connections exist before configuring a new one."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {
                "type": "string",
                "description": "Cartridge identifier, e.g. 'replicon'",
            }
        },
        "required": ["cartridge_id"],
    },
)
async def vault_list_connections(cartridge_id: str) -> dict:
    return _vault_get(f"/connections/{cartridge_id}")


@tool(
    name="vault_set_connection",
    description=(
        "Create or update an API connection for a cartridge. "
        "Stores base_url, auth_method and credentials in the Vault. "
        "Call this to configure a cartridge before running its first extraction. "
        "Example: vault_set_connection('replicon', 'analytics', "
        "'https://na5.replicon.com/analytics', 'bearer_token', token='abc123')"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {
                "type": "string",
                "description": "Cartridge identifier, e.g. 'replicon'",
            },
            "conn_id": {
                "type": "string",
                "description": "Connection identifier within the cartridge, e.g. 'analytics' or 'services'",
            },
            "base_url": {
                "type": "string",
                "description": "Base URL of the API endpoint, e.g. 'https://na5.replicon.com/analytics'",
            },
            "auth_method": {
                "type": "string",
                "enum": ["bearer_token", "basic_auth", "api_key"],
                "description": "Authentication method",
            },
            "token": {
                "type": "string",
                "description": "Bearer token (required when auth_method=bearer_token)",
            },
            "username": {
                "type": "string",
                "description": "Username (required when auth_method=basic_auth)",
            },
            "password": {
                "type": "string",
                "description": "Password (required when auth_method=basic_auth)",
            },
            "api_key": {
                "type": "string",
                "description": "API key (required when auth_method=api_key)",
            },
            "api_key_header": {
                "type": "string",
                "description": "Header name for API key, e.g. 'X-Api-Key'",
            },
        },
        "required": ["cartridge_id", "conn_id", "base_url", "auth_method"],
    },
)
async def vault_set_connection(
    cartridge_id: str,
    conn_id: str,
    base_url: str,
    auth_method: str,
    token: str = "",
    username: str = "",
    password: str = "",
    api_key: str = "",
    api_key_header: str = "",
) -> dict:
    body: dict = {"base_url": base_url, "auth_method": auth_method}
    if token:
        body["token"] = token
    if username:
        body["username"] = username
    if password:
        body["password"] = password
    if api_key:
        body["api_key"] = api_key
    if api_key_header:
        body["api_key_header"] = api_key_header
    return _vault_put(f"/connections/{cartridge_id}/{conn_id}", body)


@tool(
    name="vault_get_connection",
    description=(
        "Show the configuration for a specific API connection. "
        "Credentials are masked (shown as ***). "
        "Use this to verify a connection is configured correctly."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "conn_id": {"type": "string"},
        },
        "required": ["cartridge_id", "conn_id"],
    },
)
async def vault_get_connection(cartridge_id: str, conn_id: str) -> dict:
    data = _vault_get(f"/connections/{cartridge_id}/{conn_id}")
    # mask sensitive fields before returning to AI
    masked = {}
    sensitive = {"token", "password", "secret", "api_key", "api_secret"}
    for k, v in data.items():
        masked[k] = "***" if any(s in k.lower() for s in sensitive) else v
    return masked


@tool(
    name="vault_delete_connection",
    description="Delete an API connection from the Vault.",
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "conn_id": {"type": "string"},
        },
        "required": ["cartridge_id", "conn_id"],
    },
)
async def vault_delete_connection(cartridge_id: str, conn_id: str) -> dict:
    return _vault_delete(f"/connections/{cartridge_id}/{conn_id}")


# ── Secret tools ──────────────────────────────────────────────────────────────

@tool(
    name="vault_list_secrets",
    description=(
        "List secret key names for a given scope. "
        "Only key names are returned, not values."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "Scope name, e.g. 'replicon', 'console'",
            }
        },
        "required": ["scope"],
    },
)
async def vault_list_secrets(scope: str) -> dict:
    return _vault_get(f"/secrets/{scope}")


@tool(
    name="vault_set_secret",
    description="Store a secret value in the Vault under a given scope and key.",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "description": "e.g. 'replicon'"},
            "key": {"type": "string", "description": "Secret key name"},
            "value": {"type": "string", "description": "Secret value"},
        },
        "required": ["scope", "key", "value"],
    },
)
async def vault_set_secret(scope: str, key: str, value: str) -> dict:
    return _vault_put(f"/secrets/{scope}/{key}", {"value": value})
