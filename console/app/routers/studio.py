"""Studio API routes backed by real platform services.

These endpoints are the canonical ``/api/studio/*`` surface used by the
legacy :8000 Studio UI. When a downstream system has no data yet, the endpoint
returns an explicit empty result with source metadata instead of pretending the
feature worked.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import html
import ipaddress
import json
import os
import re
import socket
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import ROLE_ADMIN, require_authenticated, require_global_any_role
from app.middleware.request_id import request_id_var
from app.security import get_internal_api_key
from app.services.security_context import build_security_context, rls_user_context
from app.services import (
    audit_service,
    cartridge_factory_pipeline,
    cartridge_service,
    dag_code_generator,
    dag_templates,
    mcp_registry,
    schema_introspect,
    studio_assistant,
    studio_entities,
    studio_goal_runs,
    superset_client,
)
from app.services.csrf import require_csrf
from app.services.permissions import has_permission, require_permission


router = APIRouter(prefix="/api/studio", tags=["Studio"])
require_studio_read = require_permission("studio.read")
require_studio_write = require_permission("studio.write")
require_studio_global_admin = require_global_any_role("owner", "super_admin", ROLE_ADMIN)

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8300")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SAFE_DAG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_SPEC_BYTES = 2 * 1024 * 1024
_MAX_METADATA_BYTES = 5 * 1024 * 1024
_BLOCKED_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


@dataclass
class _PinnedHTTPResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    def json(self) -> dict[str, Any]:
        payload = json.loads(self.text)
        return payload if isinstance(payload, dict) else {}


def _key_for(server: str) -> str:
    pair = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        return pair
    if os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}:
        raise RuntimeError(f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server}; legacy fallback disabled in production")
    return get_internal_api_key()


def _hdr_for(server: str) -> dict[str, str]:
    headers = {"x-api-key": _key_for(server), "x-internal-service": "console"}
    rid = request_id_var.get()
    if rid:
        headers["x-request-id"] = rid
    return headers


def _vault_headers_for_user(user: dict | None) -> dict[str, str]:
    return {
        **_hdr_for("VAULT"),
        "x-security-context": json.dumps(build_security_context(user), ensure_ascii=False),
    }


def _rls_user_context(user: dict | None) -> dict[str, Any]:
    return rls_user_context(user)


def _mcp_payload(tool: str, args: dict[str, Any], user: dict | None = None) -> dict[str, Any]:
    payload = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = build_security_context(user)
    return payload


def _clean_identifier(value: str, *, label: str) -> str:
    ident = (value or "").strip()
    if not _IDENT_RE.fullmatch(ident):
        raise HTTPException(400, f"Invalid {label}: use letters, numbers and underscores only")
    return ident


def _schema_entities_from_fields(fields_by_entity: dict[str, list[schema_introspect.Field]]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for name, fields in sorted(fields_by_entity.items()):
        try:
            entity = _clean_identifier(str(name), label="entity")
        except HTTPException:
            continue
        primary_key = next((field["name"] for field in fields if field.get("primary_key")), "")
        entities.append({
            "name": entity,
            "entity": entity,
            "display_name": entity,
            "primary_key": primary_key,
            "fields": fields,
        })
    return entities


def _load_static_entity_specs(cartridge_id: str) -> list[dict[str, Any]]:
    candidates = [
        Path(f"/registry/cartridges/{cartridge_id}/app/config/entities.yaml"),
        Path(__file__).resolve().parents[3] / "cartridges" / cartridge_id / "app" / "config" / "entities.yaml",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return []
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_entities = parsed.get("entities") if isinstance(parsed, dict) else []
    return [dict(item) for item in raw_entities if isinstance(item, dict)]


def _fields_from_static_entity(entity: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    existing = entity.get("fields")
    if isinstance(existing, list):
        for field in existing:
            if isinstance(field, dict) and field.get("name"):
                fields.append(schema_introspect.normalize_field(field))
    select_fields = entity.get("select_fields")
    if isinstance(select_fields, list):
        seen = {field["name"] for field in fields}
        primary_key = str(entity.get("primary_key") or "")
        for name in select_fields:
            name = str(name)
            if name in seen:
                continue
            lower = name.lower()
            guessed_type = "timestamp" if any(fragment in lower for fragment in ("date", "time", "aedtm")) else "string"
            fields.append({
                "name": name,
                "type": guessed_type,
                "nullable": True,
                "primary_key": name == primary_key,
                "source_type": "static_select_field",
            })
    properties = entity.get("properties")
    if isinstance(properties, list):
        seen = {field["name"] for field in fields}
        primary_key = str(entity.get("primary_key") or entity.get("id_field") or "")
        for name in properties:
            name = str(name)
            if not name or name in seen:
                continue
            lower = name.lower()
            guessed_type = "timestamp" if any(fragment in lower for fragment in ("date", "time", "updated", "modified")) else "string"
            fields.append({
                "name": name,
                "type": guessed_type,
                "nullable": True,
                "primary_key": name == primary_key,
                "source_type": "static_property",
            })
            seen.add(name)
    if not fields and entity.get("primary_key"):
        fields.append({
            "name": str(entity["primary_key"]),
            "type": "string",
            "nullable": False,
            "primary_key": True,
            "source_type": "static_primary_key",
        })
    if entity.get("watermark_field") and all(f["name"] != entity["watermark_field"] for f in fields):
        fields.append({
            "name": str(entity["watermark_field"]),
            "type": "timestamp",
            "nullable": True,
            "primary_key": False,
            "source_type": "static_watermark_field",
        })
    return fields


def _static_introspection_payload(cartridge_id: str, connector_schema: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    for item in _load_static_entity_specs(cartridge_id):
        name = item.get("entity") or item.get("name")
        if not name:
            continue
        try:
            entity = _clean_identifier(str(name), label="entity")
        except HTTPException:
            continue
        fields = _fields_from_static_entity(item)
        entities.append({
            "name": entity,
            "entity": entity,
            "display_name": item.get("display_name") or item.get("title") or entity,
            "description": item.get("description") or "",
            "mode": item.get("mode") or "full",
            "primary_key": item.get("primary_key") or next((f["name"] for f in fields if f.get("primary_key")), ""),
            "watermark_field": item.get("watermark_field") or "",
            "odata_entity": item.get("odata_entity") or "",
            "fields": fields,
        })
    return {
        "cartridge_id": cartridge_id,
        "endpoint": f"/api/cartridges/{cartridge_id}/connector_schema",
        "connector_schema": connector_schema,
        "entities": entities,
        "source": "static",
        "reason": reason or "live introspection unavailable; returned connector.yaml/entities.yaml fallback",
    }


async def _vault_connection(cartridge_id: str, conn_id: str = "default", user: dict | None = None) -> tuple[dict[str, Any], str]:
    try:
        async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=8.0) as client:
            response = await client.get(
                f"{VAULT_URL.rstrip('/')}/connections/{quote(cartridge_id, safe='')}/{quote(conn_id, safe='')}"
            )
            if response.status_code == 404 and conn_id == "default":
                list_response = await client.get(
                    f"{VAULT_URL.rstrip('/')}/connections/{quote(cartridge_id, safe='')}"
                )
                if list_response.status_code in {404, 204}:
                    return {}, "no scoped Vault connection"
                if list_response.status_code >= 400:
                    return {}, f"vault returned HTTP {list_response.status_code}"
                try:
                    list_payload = list_response.json()
                except ValueError:
                    return {}, "vault returned invalid JSON"
                raw_connections = list_payload.get("connections") if isinstance(list_payload, dict) else []
                if not isinstance(raw_connections, list):
                    return {}, "no scoped Vault connection"
                first = next(
                    (
                        item
                        for item in raw_connections
                        if isinstance(item, dict)
                        and str(item.get("conn_id") or item.get("id") or "").strip()
                    ),
                    None,
                )
                if not first:
                    return {}, "no scoped Vault connection"
                selected_conn_id = str(first.get("conn_id") or first.get("id")).strip()
                response = await client.get(
                    f"{VAULT_URL.rstrip('/')}/connections/{quote(cartridge_id, safe='')}/{quote(selected_conn_id, safe='')}"
                )
    except Exception as exc:
        return {}, f"vault unavailable: {type(exc).__name__}"
    if response.status_code == 404:
        return {}, "no scoped Vault connection"
    if response.status_code >= 400:
        return {}, f"vault returned HTTP {response.status_code}"
    payload = response.json()
    if not isinstance(payload, dict):
        return {}, "vault returned invalid JSON"
    if not str(payload.get("conn_id") or payload.get("id") or "").strip():
        payload["conn_id"] = conn_id
    return payload, ""


def _connector_payload(connector_schema: dict[str, Any]) -> dict[str, Any]:
    connector = connector_schema.get("connector") if isinstance(connector_schema.get("connector"), dict) else connector_schema
    return connector if isinstance(connector, dict) else {}


def _connection_value(connection: dict[str, Any], *names: str) -> str:
    for name in names:
        value = connection.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _env_or_connection(connection: dict[str, Any], env_name: str, *names: str) -> str:
    return os.environ.get(env_name or "", "") or _connection_value(connection, *names)


async def _source_auth(
    connector: dict[str, Any],
    connection: dict[str, Any],
    source_base_url: str = "",
) -> tuple[dict[str, str], str]:
    auth = connector.get("auth") if isinstance(connector.get("auth"), dict) else {}
    auth_type = str(connection.get("auth_method") or auth.get("type") or "").lower()
    if auth_type in {"basic", "basic_auth"}:
        user = _env_or_connection(connection, str(auth.get("env_var_user") or ""), "username", "user")
        password = _env_or_connection(connection, str(auth.get("env_var_pass") or ""), "password", "pass")
        if not user or not password:
            return {}, "missing basic credentials"
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        return {"Accept": "application/xml,application/json", "Authorization": f"Basic {token}"}, ""
    if auth_type in {"bearer", "bearer_token", "api_key"}:
        env_name = str(auth.get("env_var") or auth.get("env_var_api_key") or "")
        token = _env_or_connection(connection, env_name, "token", "api_key", "apikey")
        if not token:
            return {}, "missing bearer/api key"
        header_name = str(auth.get("header") or ("Authorization" if auth_type != "api_key" else "X-API-Key"))
        prefix = str(auth.get("prefix") or ("Bearer " if header_name.lower() == "authorization" else ""))
        return {"Accept": "application/xml,application/json", header_name: f"{prefix}{token}"}, ""
    if auth_type in {"oauth2", "oauth2_client_credentials", "client_credentials"}:
        token_url = _env_or_connection(connection, str(auth.get("token_url_env") or ""), "token_url")
        client_id = _env_or_connection(connection, str(auth.get("client_id_env") or ""), "client_id")
        client_secret = _env_or_connection(connection, str(auth.get("client_secret_env") or ""), "client_secret")
        if not token_url or not client_id or not client_secret:
            return {}, "missing oauth2 client credentials"
        if not (
            source_base_url and _same_url_host(token_url, source_base_url)
        ) and not _host_allowed_by_env(token_url, "STUDIO_INTROSPECT_ALLOWED_TOKEN_HOSTS"):
            return {}, "OAuth2 token URL host must match source base URL or STUDIO_INTROSPECT_ALLOWED_TOKEN_HOSTS"
        try:
            response = await _pinned_http_request(
                "POST",
                token_url,
                label="OAuth2 token URL",
                headers={
                    "Accept": "application/json",
                    "Authorization": "Basic "
                    + base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii"),
                },
                body=urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
                content_type="application/x-www-form-urlencoded",
                max_bytes=128 * 1024,
                allow_configured_private=True,
            )
        except Exception as exc:
            return {}, f"oauth2 token request failed: {type(exc).__name__}"
        if response.is_redirect:
            return {}, "OAuth2 token redirects are blocked"
        if response.status_code >= 400:
            return {}, f"oauth2 token endpoint returned HTTP {response.status_code}"
        token = response.json().get("access_token")
        if not token:
            return {}, "oauth2 token response missing access_token"
        return {"Accept": "application/xml,application/json", "Authorization": f"Bearer {token}"}, ""
    return {"Accept": "application/xml,application/json"}, ""


def _base_url_for_source(connector: dict[str, Any], connection: dict[str, Any]) -> str:
    api = connector.get("api") if isinstance(connector.get("api"), dict) else {}
    env_name = str(api.get("base_url_env") or "")
    return _env_or_connection(connection, env_name, "base_url", "url").rstrip("/")


def _service_metadata_paths(cartridge_id: str) -> list[str]:
    paths = [""]
    for item in _load_static_entity_specs(cartridge_id):
        odata_entity = str(item.get("service_path") or item.get("odata_entity") or "")
        if "/" not in odata_entity:
            continue
        service = odata_entity.split("/", 1)[0].strip("/")
        if service and service not in paths:
            paths.append(service)
    return paths[:8]


def _looks_odata(cartridge_id: str, connector: dict[str, Any], args: dict[str, Any]) -> bool:
    explicit = str(args.get("source_kind") or args.get("kind") or "").lower()
    if explicit in {"odata", "sap"}:
        return True
    if cartridge_id.startswith("sap_"):
        return True
    auth = connector.get("auth") if isinstance(connector.get("auth"), dict) else {}
    return str(auth.get("type") or "").lower() in {"basic", "oauth2_client_credentials"}


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _configured_outbound_private_hosts() -> set[str]:
    raw = os.environ.get("STUDIO_INTROSPECT_ALLOWED_PRIVATE_HOSTS", "")
    return {host.strip().rstrip(".").lower() for host in raw.split(",") if host.strip()}


def _configured_outbound_private_cidrs() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    raw = os.environ.get("STUDIO_INTROSPECT_ALLOWED_PRIVATE_CIDRS", "")
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _resolve_external_url_address(
    url: str,
    *,
    label: str,
    allow_configured_private: bool = False,
) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"{label} must use http or https", ""
    if _is_production_env() and parsed.scheme != "https":
        return f"{label} must use https in production", ""
    if parsed.username or parsed.password:
        return f"{label} must not include credentials", ""
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return f"{label} host is required", ""
    allowed_hosts = _configured_outbound_private_hosts() if allow_configured_private else set()
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        if host not in allowed_hosts:
            return f"{label} host is not public", ""
    if host in {"metadata.google.internal", "instance-data", "169.254.169.254"}:
        return f"{label} metadata hosts are blocked", ""
    try:
        addresses = [
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        ]
    except socket.gaierror:
        return f"{label} host could not be resolved", ""
    except Exception as exc:
        return f"{label} validation failed: {type(exc).__name__}", ""
    allowed_cidrs = _configured_outbound_private_cidrs() if allow_configured_private else []
    first_address = ""
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"{label} resolved to an invalid address", ""
        if allow_configured_private and (host in allowed_hosts or any(ip in cidr for cidr in allowed_cidrs)):
            first_address = first_address or address
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or ip in _BLOCKED_SHARED_ADDRESS_SPACE
        ):
            return f"{label} resolved to a non-public address", ""
        first_address = first_address or address
    if first_address:
        return "", first_address
    return f"{label} host could not be resolved", ""


def _validate_external_url(url: str, *, label: str, allow_configured_private: bool = False) -> str:
    reason, _address = _resolve_external_url_address(
        url,
        label=label,
        allow_configured_private=allow_configured_private,
    )
    return reason


def _validate_external_spec_url(url: str) -> str:
    return _validate_external_url(url, label="OpenAPI spec URL")


def _same_url_host(left: str, right: str) -> bool:
    left_host = (urlparse(left).hostname or "").rstrip(".").lower()
    right_host = (urlparse(right).hostname or "").rstrip(".").lower()
    return bool(left_host and right_host and left_host == right_host)


def _host_allowed_by_env(url: str, env_name: str) -> bool:
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    allowed = {
        item.strip().rstrip(".").lower()
        for item in os.environ.get(env_name, "").split(",")
        if item.strip()
    }
    return bool(host and host in allowed)


async def _read_pinned_http_response(reader: asyncio.StreamReader, max_bytes: int) -> _PinnedHTTPResponse:
    header_bytes = await reader.readuntil(b"\r\n\r\n")
    if len(header_bytes) > 65536:
        raise ValueError("response headers too large")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    lines = header_text.split("\r\n")
    status_parts = lines[0].split(" ", 2)
    status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    content_length = headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise ValueError("response exceeds size limit")
    body = bytearray()
    if headers.get("transfer-encoding", "").lower() == "chunked":
        while True:
            line = await reader.readline()
            chunk_size = int(line.split(b";", 1)[0].strip() or b"0", 16)
            if chunk_size == 0:
                await reader.readline()
                break
            if len(body) + chunk_size > max_bytes:
                raise ValueError("response exceeds size limit")
            body.extend(await reader.readexactly(chunk_size))
            await reader.readexactly(2)
    elif content_length and content_length.isdigit():
        body.extend(await reader.readexactly(int(content_length)))
    else:
        while True:
            chunk = await reader.read(min(65536, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ValueError("response exceeds size limit")
    return _PinnedHTTPResponse(status_code=status_code, headers=headers, content=bytes(body))


async def _pinned_http_request(
    method: str,
    url: str,
    *,
    label: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    content_type: str = "",
    max_bytes: int = _MAX_SPEC_BYTES,
    allow_configured_private: bool = False,
) -> _PinnedHTTPResponse:
    reason, address = _resolve_external_url_address(
        url,
        label=label,
        allow_configured_private=allow_configured_private,
    )
    if reason:
        raise ValueError(reason)
    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    ssl_context = ssl.create_default_context() if parsed.scheme == "https" else None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host=address,
            port=port,
            ssl=ssl_context,
            server_hostname=host if ssl_context else None,
        ),
        timeout=15.0,
    )
    try:
        request_headers = {
            "Host": host if parsed.port in (None, 80, 443) else f"{host}:{parsed.port}",
            "Connection": "close",
            "Accept": "*/*",
            **(headers or {}),
        }
        if body:
            request_headers["Content-Length"] = str(len(body))
            if content_type:
                request_headers["Content-Type"] = content_type
        header_blob = "".join(f"{name}: {value}\r\n" for name, value in request_headers.items())
        writer.write(f"{method.upper()} {path} HTTP/1.1\r\n{header_blob}\r\n".encode("utf-8") + body)
        await asyncio.wait_for(writer.drain(), timeout=15.0)
        return await asyncio.wait_for(_read_pinned_http_response(reader, max_bytes), timeout=15.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _live_odata_introspection(
    cartridge_id: str,
    connector: dict[str, Any],
    connection: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    base_url = _base_url_for_source(connector, connection)
    if not base_url:
        return [], "missing base_url"
    blocked_reason = _validate_external_url(base_url, label="source base URL", allow_configured_private=True)
    if blocked_reason:
        return [], blocked_reason
    headers, auth_reason = await _source_auth(connector, connection, base_url)
    if auth_reason:
        return [], auth_reason
    merged: dict[str, list[schema_introspect.Field]] = {}
    failures: list[str] = []
    for service_path in _service_metadata_paths(cartridge_id):
        metadata_url = urljoin(
            f"{base_url.rstrip('/')}/",
            f"{service_path.strip('/')}/$metadata" if service_path else "$metadata",
        )
        try:
            response = await _pinned_http_request(
                "GET",
                metadata_url,
                label="metadata URL",
                headers=headers,
                max_bytes=_MAX_METADATA_BYTES,
                allow_configured_private=True,
            )
        except Exception as exc:
            failures.append(f"{service_path or '$metadata'}: {type(exc).__name__}")
            continue
        if response.status_code in {401, 403}:
            return [], f"metadata auth failed with HTTP {response.status_code}"
        if response.is_redirect:
            return [], "metadata redirects are blocked"
        if response.status_code >= 400:
            failures.append(f"{service_path or '$metadata'}: HTTP {response.status_code}")
            continue
        fields = schema_introspect.parse_odata_metadata(response.text)
        entity_sets = schema_introspect.parse_odata_entity_sets(response.text)
        if entity_sets:
            fields = {name: fields[name] for name in entity_sets if name in fields}
        merged.update(fields)
    if merged:
        return _schema_entities_from_fields(merged), ""
    return [], "; ".join(failures) or "metadata response had no entity fields"


def _openapi_spec_from_args(args: dict[str, Any]) -> dict[str, Any] | None:
    spec = args.get("spec")
    if isinstance(spec, dict):
        return spec
    text = args.get("spec_content") or args.get("openapi")
    if isinstance(text, str) and text.strip():
        if len(text.encode("utf-8")) > _MAX_SPEC_BYTES:
            return None
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def _live_openapi_introspection(args: dict[str, Any], connector: dict[str, Any], connection: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw_text = args.get("spec_content") or args.get("openapi")
    if isinstance(raw_text, str) and len(raw_text.encode("utf-8")) > _MAX_SPEC_BYTES:
        return [], "OpenAPI spec content exceeds size limit"
    spec = _openapi_spec_from_args(args)
    api = connector.get("api") if isinstance(connector.get("api"), dict) else {}
    spec_url = str(args.get("spec_url") or args.get("openapi_url") or api.get("spec_url") or "").strip()
    if spec is None and spec_url:
        blocked_reason = _validate_external_spec_url(spec_url)
        if blocked_reason:
            return [], blocked_reason
        headers = {"Accept": "application/json,yaml,text/yaml"}
        base_url = _base_url_for_source(connector, connection)
        if base_url and _same_url_host(spec_url, base_url):
            auth_headers, auth_reason = await _source_auth(connector, connection, base_url)
            if not auth_reason:
                headers = {**headers, **auth_headers}
        try:
            response = await _pinned_http_request(
                "GET",
                spec_url,
                label="OpenAPI spec URL",
                headers=headers,
                max_bytes=_MAX_SPEC_BYTES,
            )
        except Exception as exc:
            return [], f"OpenAPI spec URL request failed: {type(exc).__name__}"
        if response.is_redirect:
            return [], "OpenAPI spec URL redirects are blocked"
        if response.status_code >= 400:
            return [], f"OpenAPI spec URL returned HTTP {response.status_code}"
        try:
            spec = yaml.safe_load(response.text)
        except yaml.YAMLError as exc:
            return [], f"OpenAPI spec parse failed: {exc.__class__.__name__}"
    if not isinstance(spec, dict):
        return [], "no OpenAPI spec supplied"
    fields = schema_introspect.parse_openapi_fields(spec)
    if not fields:
        return [], "OpenAPI spec contained no object fields"
    return _schema_entities_from_fields(fields), ""


async def _live_sql_introspection(args: dict[str, Any], connection: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    dsn = _connection_value(connection, "database_url", "dsn", "url")
    if not dsn:
        return [], "missing database_url/dsn"
    table_filter = {
        str(item).strip()
        for item in (args.get("tables") or args.get("entities") or [])
        if str(item).strip()
    }
    if not table_filter:
        return [], "tables are required for SQL introspection"
    if any(not _IDENT_RE.fullmatch(table) for table in table_filter):
        return [], "table names must use letters, numbers and underscores only"
    parsed_dsn = urlparse(dsn)
    dsn_query = parse_qs(parsed_dsn.query, keep_blank_values=True)
    if "host" in dsn_query or "hostaddr" in dsn_query:
        return [], "database DSN host query parameters are not allowed"
    dsn_host = parsed_dsn.hostname
    if not dsn_host:
        return [], "database host is required"
    dsn_check = _validate_external_url(
        f"https://{dsn_host}:{parsed_dsn.port or 5432}",
        label="database host",
        allow_configured_private=True,
    )
    if dsn_check:
        return [], dsn_check
    try:
        import asyncpg  # type: ignore
    except Exception:
        return [], "asyncpg unavailable"
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=8.0)
        try:
            rows = await conn.fetch(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND table_name = ANY($1::text[])
                ORDER BY table_name, ordinal_position
                """,
                sorted(table_filter),
                timeout=8.0,
            )
        finally:
            await conn.close()
    except Exception as exc:
        return [], f"database schema query failed: {type(exc).__name__}"
    by_table: dict[str, list[schema_introspect.Field]] = {}
    for row in rows:
        table_name = str(row["table_name"])
        source_type = str(row["data_type"])
        by_table.setdefault(table_name, []).append(schema_introspect.normalize_field({
            "name": row["column_name"],
            "source_type": source_type,
            "type": source_type,
            "nullable": str(row["is_nullable"]).upper() != "NO",
            "primary_key": str(row["column_name"]).lower() in {"id", f"{table_name.lower()}_id"},
        }))
    if not by_table:
        return [], "database returned no table columns"
    return _schema_entities_from_fields(by_table), ""


async def _live_introspection(
    cartridge_id: str,
    connector_schema: dict[str, Any],
    args: dict[str, Any],
    user: dict | None,
) -> tuple[list[dict[str, Any]], str]:
    connector = _connector_payload(connector_schema)
    connection, reason = await _vault_connection(cartridge_id, str(args.get("conn_id") or "default"), user)
    kind = str(args.get("source_kind") or args.get("kind") or "").lower()
    has_spec = _openapi_spec_from_args(args) is not None
    has_base_url = bool(_base_url_for_source(connector, connection))
    if not connection and not has_spec and not has_base_url and kind not in {"openapi", "rest"}:
        return [], reason or "no credentials available"
    if kind in {"sql", "db", "database", "postgres"}:
        return await _live_sql_introspection(args, connection)
    if _looks_odata(cartridge_id, connector, args):
        return await _live_odata_introspection(cartridge_id, connector, connection)
    return await _live_openapi_introspection(args, connector, connection)


async def _studio_introspect_source(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    """Studio assistant tool: live schema discovery with static fallback."""
    if user is None:
        raise HTTPException(401, "Authentication required")
    cartridge_id = _clean_identifier(str(args.get("cartridge_id") or ""), label="cartridge_id")
    _require_cartridge_visible(user, cartridge_id)
    from app.routers import cartridges as cartridges_router

    schema = await cartridges_router.connector_schema(cartridge_id, user=user)
    can_live = has_permission(user, "studio.write") or has_permission(user, "cartridges.write")
    live_entities: list[dict[str, Any]] = []
    reason = "live introspection requires studio.write or cartridges.write"
    if can_live:
        live_entities, reason = await _live_introspection(cartridge_id, schema, args, user)
    if live_entities:
        return {
            "cartridge_id": cartridge_id,
            "endpoint": f"/api/studio/introspect-source",
            "connector_schema": schema,
            "entities": live_entities,
            "source": "live",
            "reason": "",
        }
    fallback = _static_introspection_payload(cartridge_id, schema, reason=reason)
    fallback["endpoint"] = f"/api/cartridges/{cartridge_id}/connector_schema"
    return {
        **fallback,
    }


studio_assistant.register_local_tool(
    "introspect_source",
    description=(
        "Introspecciona la fuente del cartucho. Primero intenta esquema vivo "
        "($metadata/OpenAPI/information_schema) con credenciales de Vault; si falla, "
        "cae al connector.yaml/entities.yaml estático."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {
                "type": "string",
                "description": "ID del cartucho activo, por ejemplo replicon, sap_hcm o sap_s4hana.",
            },
            "source_kind": {
                "type": "string",
                "description": "Opcional: odata, openapi o sql.",
            },
            "spec_url": {
                "type": "string",
                "description": "URL opcional de OpenAPI/JSON schema si el cartucho no publica $metadata.",
            },
            "spec_content": {
                "type": "string",
                "description": "Contenido OpenAPI YAML/JSON opcional subido por el usuario.",
            },
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tablas relevantes para introspección SQL/information_schema.",
            },
        },
        "required": ["cartridge_id"],
    },
    handler=_studio_introspect_source,
)


def _autopilot_inline_descriptor(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str, str]:
    descriptor = args.get("descriptor")
    if isinstance(descriptor, dict):
        return descriptor, "provided_descriptor", ""

    spec = _openapi_spec_from_args(args)
    if isinstance(spec, dict):
        return {"kind": "openapi", "spec": spec, "auth_type": args.get("auth_type")}, "inline_openapi", ""

    edmx = args.get("edmx") or args.get("metadata")
    if isinstance(edmx, str) and edmx.strip():
        if len(edmx.encode("utf-8")) > _MAX_METADATA_BYTES:
            return None, "inline_odata", "OData metadata content exceeds size limit"
        return {"kind": "odata", "edmx": edmx, "auth_type": args.get("auth_type")}, "inline_odata", ""

    columns = args.get("columns") or args.get("information_schema")
    if isinstance(columns, list) and columns:
        return {"kind": "sql", "columns": columns, "auth_type": args.get("auth_type")}, "inline_sql", ""

    csv_text = args.get("csv") or args.get("text")
    if isinstance(csv_text, str) and csv_text.strip():
        if len(csv_text.encode("utf-8")) > _MAX_SPEC_BYTES:
            return None, "inline_csv", "CSV content exceeds size limit"
        return {"kind": "file_csv", "csv": csv_text, "auth_type": args.get("auth_type")}, "inline_csv", ""

    if "sample" in args:
        kind = str(args.get("source_kind") or args.get("kind") or "rest_sample").strip().lower()
        return {
            "kind": kind or "rest_sample",
            "sample": args.get("sample"),
            "entity_name": args.get("entity_name") or "records",
            "auth_type": args.get("auth_type"),
            "paginated": args.get("paginated"),
            "async_export": args.get("async_export"),
            "webhook": args.get("webhook"),
        }, "inline_sample", ""

    return None, "none", "no inline descriptor supplied"


async def _autopilot_openapi_url_descriptor(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    spec_url = str(args.get("spec_url") or args.get("openapi_url") or "").strip()
    if not spec_url:
        return None, "no OpenAPI spec URL supplied"
    blocked_reason = _validate_external_spec_url(spec_url)
    if blocked_reason:
        return None, blocked_reason
    try:
        response = await _pinned_http_request(
            "GET",
            spec_url,
            label="OpenAPI spec URL",
            headers={"Accept": "application/json,yaml,text/yaml"},
            max_bytes=_MAX_SPEC_BYTES,
        )
    except Exception as exc:
        return None, f"OpenAPI spec URL request failed: {type(exc).__name__}"
    if response.is_redirect:
        return None, "OpenAPI spec URL redirects are blocked"
    if response.status_code >= 400:
        return None, f"OpenAPI spec URL returned HTTP {response.status_code}"
    try:
        parsed = yaml.safe_load(response.text)
    except yaml.YAMLError as exc:
        return None, f"OpenAPI spec parse failed: {exc.__class__.__name__}"
    if not isinstance(parsed, dict):
        return None, "OpenAPI spec URL did not return an object"
    return {"kind": "openapi", "spec": parsed, "auth_type": args.get("auth_type")}, ""


def _autopilot_source_kind(cartridge_id: str, connector: dict[str, Any], args: dict[str, Any], source: str) -> str:
    kind = str(args.get("source_kind") or args.get("kind") or "").strip().lower()
    if kind:
        return kind
    if _looks_odata(cartridge_id, connector, args):
        return "odata"
    if source == "live_sql":
        return "sql"
    if source in {"live_introspection", "static_introspection"}:
        return "openapi"
    return "rest_sample"


async def _autopilot_live_descriptor(
    args: dict[str, Any],
    user: dict,
    source_cartridge_id: str,
) -> tuple[dict[str, Any] | None, str, str]:
    if not source_cartridge_id:
        return None, "none", "no source cartridge supplied for live introspection"
    cartridge_id = _clean_identifier(source_cartridge_id, label="source_cartridge_id")
    _require_cartridge_visible(user, cartridge_id)
    from app.routers import cartridges as cartridges_router

    schema = await cartridges_router.connector_schema(cartridge_id, user=user)
    connector = _connector_payload(schema)
    can_live = has_permission(user, "studio.write") or has_permission(user, "cartridges.write")
    reason = "live introspection requires studio.write or cartridges.write"
    if can_live:
        live_entities, reason = await _live_introspection(cartridge_id, schema, args, user)
        if live_entities:
            kind = _autopilot_source_kind(cartridge_id, connector, args, "live_introspection")
            return {
                "kind": kind,
                "entities": live_entities,
                "auth_type": (connector.get("auth") or {}).get("type") or (connector.get("connection") or {}).get("auth_method"),
            }, "live_introspection", ""

    fallback = _static_introspection_payload(cartridge_id, schema, reason=reason)
    if fallback.get("entities"):
        kind = _autopilot_source_kind(cartridge_id, connector, args, "static_introspection")
        return {
            "kind": kind,
            "entities": fallback["entities"],
            "auth_type": (connector.get("auth") or {}).get("type") or (connector.get("connection") or {}).get("auth_method"),
        }, "static_introspection", fallback.get("reason") or reason
    return None, "live_introspection", reason or fallback.get("reason") or "no entities available from live/static introspection"


def _autopilot_blueprint_preview(blueprint: dict[str, Any] | None) -> dict[str, Any]:
    bp = blueprint if isinstance(blueprint, dict) else {}
    return {
        "id": bp.get("id"),
        "name": bp.get("name"),
        "entities": [e.get("entity") or e.get("name") for e in (bp.get("entities") or []) if isinstance(e, dict)],
        "datasets": [d.get("name") for d in (bp.get("datasets") or []) if isinstance(d, dict)],
        "dags": [d.get("dag_id") for d in (bp.get("dags") or []) if isinstance(d, dict)],
        "kbs": len(bp.get("kbs") or bp.get("knowledge_bits") or []),
        "agents": len(bp.get("agents") or []),
    }


async def _studio_autopilot_build_cartridge(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    """Studio assistant tool: sentence/spec/source -> dry-run cartridge blueprint."""
    if user is None:
        raise HTTPException(401, "Authentication required")

    args = args or {}
    intent = str(args.get("intent") or args.get("message") or "").strip()
    parsed_intent = cartridge_factory_pipeline.cartridge_intent.parse_build_intent(intent) if intent else {}
    inferred_source_id = ""
    if isinstance(parsed_intent.get("primary_source"), dict):
        inferred_source_id = str(parsed_intent["primary_source"].get("id") or "")
    source_cartridge_id = str(args.get("source_cartridge_id") or args.get("cartridge_id") or inferred_source_id or "").strip()

    descriptor, source, reason = _autopilot_inline_descriptor(args)
    if descriptor is None and (args.get("spec_url") or args.get("openapi_url")):
        descriptor, reason = await _autopilot_openapi_url_descriptor(args)
        source = "openapi_url" if descriptor else "openapi_url"
    if descriptor is None and source_cartridge_id and args.get("use_live", True) is not False:
        descriptor, source, reason = await _autopilot_live_descriptor(args, user, source_cartridge_id)

    if descriptor is None:
        return {
            "ok": False,
            "source": source,
            "reason": reason or "no descriptor/spec/sample/live introspection available",
            "dry_run": True,
        }

    explicit_id = str(args.get("target_cartridge_id") or args.get("new_cartridge_id") or args.get("cartridge_id") or "").strip()
    explicit_name = str(args.get("name") or args.get("cartridge_name") or "").strip()
    domain = str(args.get("domain") or args.get("category") or "").strip() or (
        str((parsed_intent.get("primary_source") or {}).get("domain") or "custom") if isinstance(parsed_intent, dict) else "custom"
    )

    if explicit_id and explicit_name:
        plan = cartridge_factory_pipeline.plan_from_descriptor(
            descriptor,
            cartridge_id=explicit_id,
            name=explicit_name,
            domain=domain,
        )
        if parsed_intent:
            plan["intent"] = parsed_intent
    elif intent:
        plan = cartridge_factory_pipeline.plan_from_intent(intent, descriptor)
        if not plan.get("ok") and explicit_id:
            plan = cartridge_factory_pipeline.plan_from_descriptor(
                descriptor,
                cartridge_id=explicit_id,
                name=explicit_name or explicit_id.replace("_", " ").title(),
                domain=domain,
            )
            if parsed_intent:
                plan["intent"] = parsed_intent
    else:
        if not explicit_id:
            return {
                "ok": False,
                "source": source,
                "reason": "cartridge_id/name or intent is required to build a cartridge blueprint",
                "dry_run": True,
            }
        plan = cartridge_factory_pipeline.plan_from_descriptor(
            descriptor,
            cartridge_id=explicit_id,
            name=explicit_name or explicit_id.replace("_", " ").title(),
            domain=domain,
        )

    blueprint = plan.get("blueprint") if isinstance(plan, dict) else None
    apply_requested = bool(args.get("apply") or args.get("write") or args.get("create"))
    response = {
        "ok": bool(plan.get("ok")) if isinstance(plan, dict) else False,
        "source": source,
        "source_reason": reason,
        "dry_run": True,
        "plan": plan,
        "blueprint": blueprint,
        "summary": plan.get("summary") if isinstance(plan, dict) else None,
        "next_action": (
            "Review the returned blueprint. To write it, run create_full_cartridge "
            "through a Studio goal run approval."
        ),
    }
    if apply_requested and blueprint:
        response.update({
            "approval_required": True,
            "tool": "studio__create_full_cartridge",
            "risk_level": "write",
            "reason": "Autopilot generated the cartridge blueprint but does not write directly.",
            "args_preview": _autopilot_blueprint_preview(blueprint),
        })
    return response


studio_assistant.register_local_tool(
    "autopilot_build_cartridge",
    description=(
        "Convierte una frase, spec OpenAPI/OData/SQL/CSV/sample o introspección viva "
        "en un blueprint completo de cartucho. Dry-run por defecto; no escribe en DB."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "intent": {"type": "string", "description": "Instrucción del usuario, por ejemplo: conecta HubSpot y crea forecast."},
            "cartridge_id": {"type": "string", "description": "Cartucho origen existente para introspección o id destino si se pasa name."},
            "source_cartridge_id": {"type": "string", "description": "Cartucho existente del cual introspectar esquema vivo/fallback."},
            "target_cartridge_id": {"type": "string", "description": "ID destino del cartucho a generar."},
            "name": {"type": "string", "description": "Nombre visible del cartucho destino."},
            "domain": {"type": "string", "description": "Dominio: crm, finance, hcm, custom, etc."},
            "descriptor": {"type": "object", "description": "Descriptor ya cargado: OpenAPI, EDMX, CSV, SQL columns, sample o entities."},
            "spec": {"type": "object", "description": "OpenAPI/Swagger spec como objeto."},
            "spec_content": {"type": "string", "description": "OpenAPI YAML/JSON como texto."},
            "spec_url": {"type": "string", "description": "URL HTTPS pública de OpenAPI/Swagger."},
            "edmx": {"type": "string", "description": "Contenido OData $metadata."},
            "sample": {"description": "Payload JSON de ejemplo para REST."},
            "columns": {"type": "array", "items": {"type": "object"}, "description": "Filas information_schema para SQL."},
            "csv": {"type": "string", "description": "CSV de muestra."},
            "source_kind": {"type": "string", "description": "openapi, odata, sql, file_csv, rest_sample, graphql o soap."},
            "dry_run": {"type": "boolean", "default": True},
            "apply": {"type": "boolean", "default": False, "description": "Si true, devuelve approval_required; no escribe directo."},
        },
    },
    handler=_studio_autopilot_build_cartridge,
)


async def _studio_generate_dag_code(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    """Studio assistant tool: generate ready-to-review DAG code from connector schema."""
    if user is None:
        raise HTTPException(401, "Authentication required")
    cartridge_id = _clean_identifier(str(args.get("cartridge_id") or ""), label="cartridge_id")
    entity_name = _clean_identifier(str(args.get("entity_name") or ""), label="entity_name")
    schema = args.get("schema")
    if not isinstance(schema, dict):
        raise HTTPException(400, "schema must be an object returned by introspect_source")
    validation_error = args.get("validation_error")
    _require_cartridge_visible(user, cartridge_id)
    try:
        result = dag_code_generator.generate_validated_dag_code(
            cartridge_id,
            entity_name,
            schema,
            validation_error=validation_error if isinstance(validation_error, str) else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result.get("validated"):
        validation = result.get("validation") or {}
        raise HTTPException(
            422,
            {
                "error": "Generated DAG code failed validation after 2 attempts",
                "stderr": validation.get("stderr") or "Unknown validation error",
                "validation_attempts": result.get("validation_attempts") or [],
            },
        )
    return result


studio_assistant.register_local_tool(
    "generate_dag_code",
    description=(
        "Genera código Python completo de un DAG de Airflow a partir del "
        "connector_schema introspectado. No usa plantillas EDIT_HERE."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {
                "type": "string",
                "description": "ID del cartucho activo.",
            },
            "entity_name": {
                "type": "string",
                "description": "Entidad para la que se generará el DAG.",
            },
            "schema": {
                "type": "object",
                "description": "Objeto devuelto por studio__introspect_source o su connector_schema.",
            },
            "validation_error": {
                "type": "string",
                "description": "stderr de validate_dag_code cuando se intenta reparar una generación fallida.",
            },
        },
        "required": ["cartridge_id", "entity_name", "schema"],
    },
    handler=_studio_generate_dag_code,
)


async def _studio_validate_dag_code(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    """Studio assistant tool: validate generated DAG code before showing it."""
    if user is None:
        raise HTTPException(401, "Authentication required")
    code = args.get("code")
    if not isinstance(code, str):
        raise HTTPException(400, "code must be a string")
    return dag_code_generator.validate_dag_code(code)


studio_assistant.register_local_tool(
    "validate_dag_code",
    description=(
        "Valida en segundo plano un DAG generado: sintaxis Python e imports básicos. "
        "Debe ejecutarse antes de mostrar código al usuario."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Código Python completo del DAG generado.",
            },
        },
        "required": ["code"],
    },
    handler=_studio_validate_dag_code,
)


async def _studio_create_full_cartridge(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    """Studio assistant tool: create a complete cartridge with validated seed SQL."""
    if user is None:
        raise HTTPException(401, "Authentication required")
    try:
        return await cartridge_service.create_full_cartridge(args, actor_user=user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


studio_assistant.register_local_tool(
    "create_full_cartridge",
    description=(
        "Crea un cartucho completo con conexiones, DAGs, entidades, vocabulario, "
        "KBs, herramientas, apps y agentes. Valida integridad y seed.sql antes de escribir."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "version": {"type": "string"},
            "description": {"type": "string"},
            "pattern": {"type": "string"},
            "category": {"type": "string"},
            "bronze_path": {"type": "string"},
            "assistant_hints": {"type": "string"},
            "connections": {"type": "array", "items": {"type": "object"}},
            "dags": {"type": "array", "items": {"type": "object"}},
            "entities": {"type": "array", "items": {"type": "object"}},
            "semantic_model": {"type": "object"},
            "knowledge_bits": {"type": "array", "items": {"type": "object"}},
            "custom_tools": {"type": "array", "items": {"type": "object"}},
            "analytic_apps": {"type": "array", "items": {"type": "object"}},
            "agents": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["id", "name"],
    },
    handler=_studio_create_full_cartridge,
)


def _clean_filename(value: str) -> str:
    name = _SAFE_FILENAME_RE.sub("_", os.path.basename(value or "spec.yaml")).strip("._")
    return name or "spec.yaml"


def _clean_dag_id(value: str) -> str:
    dag_id = (value or "").strip()
    if not _SAFE_DAG_ID_RE.fullmatch(dag_id):
        raise HTTPException(400, "Invalid dag_id: use letters, numbers and underscores only")
    return dag_id


def _require_cartridge_visible(user: dict | None, cartridge_id: str) -> None:
    if user is None:
        return
    ctx = build_security_context(user)
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if "*" in allowed:
        return
    if str(cartridge_id).strip() not in allowed:
        raise HTTPException(403, "cartridge not allowed")


def _manifest_dag_ids(manifest: dict | None) -> set[str]:
    ids: set[str] = set()
    if not manifest:
        return ids
    for dag in manifest.get("dags") or []:
        if isinstance(dag, dict) and dag.get("dag_id"):
            ids.add(str(dag["dag_id"]))
        elif isinstance(dag, str):
            ids.add(dag)
    for entity in manifest.get("entities") or []:
        if isinstance(entity, dict) and entity.get("dag_id"):
            ids.add(str(entity["dag_id"]))
    return ids


def _downstream_error(service: str, status_code: int) -> HTTPException:
    return HTTPException(status_code, f"{service} request failed with HTTP {status_code}")


def _limit_param(request: Request, *, default: int = 20, maximum: int = 200) -> int:
    raw = request.query_params.get("limit", str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "limit must be an integer") from exc
    return min(max(value, 1), maximum)


async def _optional_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _refinement_invoke(tool: str, args: dict[str, Any], *, timeout: int = 60, user: dict | None = None) -> dict:
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=timeout) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(tool, args, user),
        )
    if response.status_code >= 400:
        raise _downstream_error("Refinement", response.status_code)
    return response.json()


async def _refinement_datasets(user: dict | None) -> list[dict]:
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=15) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload("list_datasets", {}, user),
        )
    if response.status_code >= 400:
        raise _downstream_error("Refinement", response.status_code)
    payload = response.json()
    datasets = payload.get("datasets") if isinstance(payload, dict) else []
    return datasets if isinstance(datasets, list) else []


async def _rag_sources(user: dict | None) -> list[dict]:
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=10) as client:
        response = await client.post(
            f"{MCP_INFRA_URL.rstrip('/')}/mcp/invoke",
            json=_mcp_payload("list_rag_sources", {}, user),
        )
    if response.status_code >= 400:
        raise _downstream_error("MCP infra", response.status_code)
    payload = response.json()
    result = payload.get("result") if isinstance(payload, dict) else None
    source_payload = result if isinstance(result, dict) else payload
    sources = source_payload.get("sources") or source_payload.get("results") or []
    return sources if isinstance(sources, list) else []


def _manifest_relations(manifest: dict | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    semantic = manifest.get("semantic_model") if isinstance(manifest.get("semantic_model"), dict) else {}
    raw_relations = (
        semantic.get("relationships")
        or semantic.get("relations")
        or manifest.get("relationships")
        or manifest.get("relations")
        or []
    )
    if not isinstance(raw_relations, list):
        return []
    relations: list[dict[str, Any]] = []
    for rel in raw_relations:
        if not isinstance(rel, dict):
            continue
        source = rel.get("from_dataset") or rel.get("source") or rel.get("from")
        target = rel.get("to_dataset") or rel.get("target") or rel.get("to")
        if not source or not target:
            continue
        relations.append({
            "from_dataset": source,
            "from_column": rel.get("from_column") or rel.get("source_column") or "",
            "to_dataset": target,
            "to_column": rel.get("to_column") or rel.get("target_column") or "",
            "join_hint": rel.get("join_hint") or rel.get("type") or "",
            "description": rel.get("description") or "",
            "transform": rel.get("transform"),
            "source": "manifest",
        })
    return relations


async def _semantic_relations(cartridge: str, manifest: dict | None, user: dict | None) -> tuple[list[dict[str, Any]], str]:
    try:
        catalog = await _refinement_invoke(
            "get_data_catalog",
            {"cartridge": cartridge},
            timeout=30,
            user=user,
        )
        relationships = catalog.get("relationships") if isinstance(catalog, dict) else []
        if isinstance(relationships, list) and relationships:
            return [
                {**dict(rel), "source": "refinement.get_data_catalog"}
                for rel in relationships
                if isinstance(rel, dict)
            ], "refinement.get_data_catalog"
    except Exception:
        pass
    return _manifest_relations(manifest), "manifest"


def _diag_item(message: str, *, evidence: str = "", severity: str = "warning") -> dict[str, Any]:
    return {"message": message, "evidence": evidence, "severity": severity}


def _entity_identity(entity: dict[str, Any]) -> str:
    return str(entity.get("entity") or entity.get("name") or entity.get("id") or "").strip()


def _vault_connection_evidence(connection: dict[str, Any]) -> dict[str, Any]:
    conn_id = str(connection.get("conn_id") or connection.get("id") or "").strip()
    auth_method = str(connection.get("auth_method") or "").strip()
    evidence: dict[str, Any] = {
        "status": "present",
        "connection_id": conn_id,
    }
    if auth_method:
        evidence["auth_method"] = auth_method
    if connection.get("base_url") or connection.get("url"):
        evidence["base_url_configured"] = True
    return evidence


async def _studio_cartridge_self_check_impl(cartridge_id: str, user: dict | None) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    next_actions: list[str] = []
    evidence: dict[str, Any] = {"cartridge_id": cartridge_id}

    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        blockers.append(_diag_item("Manifest del cartucho no encontrado", evidence="cartridge_service.get_cartridge", severity="critical"))
        return {
            "cartridge_id": cartridge_id,
            "status": "blocked",
            "score": 0,
            "blockers": blockers,
            "warnings": warnings,
            "next_actions": ["Registrar el cartucho antes de usar Studio Assistant"],
            "evidence": evidence,
        }
    evidence["manifest"] = {
        "name": manifest.get("name"),
        "entities": len(manifest.get("entities") or []),
        "dags": len(manifest.get("dags") or []),
        "has_hints": bool((manifest.get("assistant_hints") or "").strip()),
    }

    try:
        from app.routers import cartridges as cartridges_router

        connector_schema = await cartridges_router.connector_schema(cartridge_id, user=user)
        evidence["connector_schema"] = "ok" if connector_schema else "empty"
        if not connector_schema:
            warnings.append(_diag_item("connector_schema vacío", evidence=f"/api/cartridges/{cartridge_id}/connector_schema"))
    except Exception as exc:
        blockers.append(_diag_item("connector_schema no disponible", evidence=type(exc).__name__, severity="critical"))

    vault_connection, vault_reason = await _vault_connection(cartridge_id, "default", user)
    evidence["vault"] = (
        _vault_connection_evidence(vault_connection)
        if vault_connection
        else {"status": "missing", "reason": vault_reason or "no scoped Vault connection", "configure_url": "/operations/vault"}
    )
    if not vault_connection:
        warnings.append(_diag_item(
            "Conexion Vault scoped no encontrada",
            evidence=f"{vault_reason or 'missing'}; configurar en /operations/vault",
        ))

    entities = manifest.get("entities") if isinstance(manifest.get("entities"), list) else []
    if not entities:
        blockers.append(_diag_item("El cartucho no tiene entidades declaradas", evidence="manifest.entities", severity="critical"))
    studio_specs: dict[str, dict[str, Any]] = {}
    try:
        for item in await studio_entities.list_entities(cartridge=cartridge_id):
            name = _entity_identity(item)
            spec = item.get("spec") if isinstance(item.get("spec"), dict) else item
            if name:
                studio_specs[name] = spec
    except Exception as exc:
        warnings.append(_diag_item("No se pudieron leer studio_entities", evidence=type(exc).__name__))

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = _entity_identity(entity)
        if not name:
            warnings.append(_diag_item("Entidad sin nombre válido", evidence="manifest.entities[]"))
            continue
        spec = studio_specs.get(name) or entity
        fields = spec.get("fields") if isinstance(spec.get("fields"), list) else []
        primary_key = spec.get("primary_key") or spec.get("id_field") or next(
            (field.get("name") for field in fields if isinstance(field, dict) and field.get("primary_key")),
            "",
        )
        if not fields:
            warnings.append(_diag_item(f"Entidad {name} sin fields tipados", evidence="studio_entities.spec.fields"))
        if not primary_key:
            warnings.append(_diag_item(f"Entidad {name} sin primary_key", evidence="primary_key/id_field"))
        if not entity.get("dag_id"):
            warnings.append(_diag_item(f"Entidad {name} sin dag_id", evidence="manifest.entities[].dag_id"))

    dag_ids = _manifest_dag_ids(manifest)
    evidence["expected_dags"] = sorted(dag_ids)
    try:
        airflow = await mcp_registry.invoke("infra", "airflow_list_dags", {}, user=user)
        raw_dags = airflow.get("dags") if isinstance(airflow, dict) else []
        seen = {
            str(item.get("dag_id") or item.get("id") or item)
            for item in (raw_dags if isinstance(raw_dags, list) else [])
        }
        missing = sorted(dag_ids - seen) if seen else []
        evidence["airflow_dags_seen"] = len(seen)
        if missing:
            warnings.append(_diag_item("DAGs esperados no aparecen en Airflow", evidence=", ".join(missing)))
    except Exception as exc:
        warnings.append(_diag_item("Airflow no disponible para validar DAGs", evidence=type(exc).__name__))

    try:
        jobs = await mcp_registry.invoke("infra", "cartridge_list_jobs", {"cartridge_id": cartridge_id, "limit": 5}, user=user)
        evidence["recent_jobs"] = len((jobs or {}).get("jobs") or (jobs or {}).get("runs") or [])
    except Exception as exc:
        warnings.append(_diag_item("No se pudieron leer jobs recientes", evidence=type(exc).__name__))

    try:
        datasets = [ds for ds in await _refinement_datasets(user) if str(ds.get("cartridge") or "") == cartridge_id]
        silver = [ds for ds in datasets if str(ds.get("layer") or "").lower() == "silver"]
        gold = [ds for ds in datasets if str(ds.get("layer") or "").lower() == "gold"]
        evidence["datasets"] = {"total": len(datasets), "silver": len(silver), "gold": len(gold)}
        if not silver:
            warnings.append(_diag_item("No hay datasets Silver registrados para el cartucho", evidence="refinement.list_datasets"))
        if not gold:
            warnings.append(_diag_item("No hay datasets Gold registrados para el cartucho", evidence="refinement.list_datasets"))
    except Exception as exc:
        warnings.append(_diag_item("Refinement no disponible para validar Silver/Gold", evidence=type(exc).__name__))

    if not (manifest.get("knowledge_bits") or []):
        warnings.append(_diag_item("El cartucho no declara KBs", evidence="manifest.knowledge_bits"))
    if not (manifest.get("assistant_hints") or "").strip():
        warnings.append(_diag_item("El cartucho no tiene assistant_hints", evidence="cartridges.assistant_hints"))

    try:
        servers = await mcp_registry.list_servers()
        server = next((srv for srv in servers if str(srv.get("id")) == cartridge_id), None)
        if not server:
            warnings.append(_diag_item("El cartucho no aparece como MCP server registrado", evidence="mcp_servers"))
        elif not bool(server.get("healthy")):
            warnings.append(_diag_item("MCP server del cartucho registrado pero no healthy", evidence=str(server.get("url") or "")))
        evidence["mcp_server"] = {
            "registered": bool(server),
            "healthy": bool((server or {}).get("healthy")),
            "url": (server or {}).get("url"),
        }
    except Exception as exc:
        warnings.append(_diag_item("No se pudo validar registry MCP", evidence=type(exc).__name__))

    for item in blockers:
        next_actions.append(item["message"])
    for item in warnings[:5]:
        next_actions.append(item["message"])

    score = max(0, 100 - (len(blockers) * 25) - (len(warnings) * 5))
    status = "blocked" if blockers else ("warning" if warnings else "ok")
    return {
        "cartridge_id": cartridge_id,
        "status": status,
        "score": score,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions[:8],
        "evidence": evidence,
    }


async def _execute_studio_goal_step(
    run: dict[str, Any],
    step: dict[str, Any],
    user: dict | None,
) -> dict[str, Any]:
    cartridge_id = str(run.get("cartridge_id") or (step.get("args") or {}).get("cartridge_id") or "")
    step_key = str(step.get("step_key") or "")
    if step_key == "load_manifest":
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
        return {
            "ok": True,
            "source": "cartridge_service.get_cartridge",
            "entity_count": len(manifest.get("entities") or []),
            "dag_count": len(manifest.get("dags") or []),
        }
    if step_key == "connector_schema":
        from app.routers import cartridges as cartridges_router

        schema = await cartridges_router.connector_schema(cartridge_id, user=user)
        return {
            "ok": bool(schema),
            "source": f"/api/cartridges/{cartridge_id}/connector_schema",
            "keys": sorted(schema.keys())[:20] if isinstance(schema, dict) else [],
        }
    if step_key == "vault_credentials":
        connection, reason = await _vault_connection(cartridge_id, "default", user)
        evidence = _vault_connection_evidence(connection) if connection else {}
        return {
            "ok": bool(connection),
            "source": "vault.connections",
            "status": "present" if connection else "missing",
            "connection_id": evidence.get("connection_id") or "",
            "auth_method": evidence.get("auth_method") or "",
            "reason": "" if connection else reason,
            "configure_url": "" if connection else "/operations/vault",
        }
    if step_key == "introspect_source":
        result = await _studio_introspect_source({"cartridge_id": cartridge_id}, user)
        return {
            "ok": bool(result.get("entities")),
            "source": result.get("source"),
            "reason": result.get("reason") or "",
            "entity_count": len(result.get("entities") or []),
        }
    if step_key == "compare_entities":
        check = await _studio_cartridge_self_check_impl(cartridge_id, user)
        entity_warnings = [
            item
            for item in check.get("warnings", [])
            if "Entidad" in str(item.get("message") or "")
        ]
        return {
            "ok": not entity_warnings,
            "source": "studio.cartridge_self_check",
            "warnings": entity_warnings,
        }
    if step_key == "validate_dags":
        check = await _studio_cartridge_self_check_impl(cartridge_id, user)
        dag_warnings = [
            item
            for item in check.get("warnings", [])
            if "DAG" in str(item.get("message") or "") or "Airflow" in str(item.get("message") or "")
        ]
        return {"ok": not dag_warnings, "source": "studio.cartridge_self_check", "warnings": dag_warnings}
    if step_key == "extraction_smoke":
        mode = str((step.get("args") or {}).get("mode") or "incremental")
        result = await mcp_registry.invoke(
            "infra",
            "cartridge_extract_all",
            {"cartridge_id": cartridge_id, "mode": mode},
            user=user,
        )
        return {"ok": True, "source": "infra.cartridge_extract_all", "result": result}
    if step_key == "medallion_layers":
        datasets = [ds for ds in await _refinement_datasets(user) if str(ds.get("cartridge") or "") == cartridge_id]
        return {
            "ok": bool(datasets),
            "source": "refinement.list_datasets",
            "silver": len([ds for ds in datasets if str(ds.get("layer") or "").lower() == "silver"]),
            "gold": len([ds for ds in datasets if str(ds.get("layer") or "").lower() == "gold"]),
            "datasets": [ds.get("name") for ds in datasets[:20]],
        }
    if step_key == "marketplace_visibility":
        check = await _studio_cartridge_self_check_impl(cartridge_id, user)
        mcp_info = (check.get("evidence") or {}).get("mcp_server") or {}
        return {"ok": bool(mcp_info.get("registered")), "source": "mcp_registry.list_servers", "mcp_server": mcp_info}
    if step_key == "final_report":
        return await _studio_cartridge_self_check_impl(cartridge_id, user)
    return {"ok": True, "source": "studio_goal_runs", "message": f"No custom executor for {step_key}"}


async def _studio_cartridge_self_check(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    cartridge_id = _clean_identifier(str(args.get("cartridge_id") or args.get("cartridge") or ""), label="cartridge_id")
    _require_cartridge_visible(user, cartridge_id)
    return await _studio_cartridge_self_check_impl(cartridge_id, user)


async def _studio_create_goal_run(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    cartridge_id = _clean_identifier(str(args.get("cartridge_id") or args.get("cartridge") or ""), label="cartridge_id")
    _require_cartridge_visible(user, cartridge_id)
    intent = str(args.get("intent") or args.get("goal") or f"Validar cartucho {cartridge_id} para producción").strip()
    auto_plan = bool(args.get("auto_plan", True))
    return await studio_goal_runs.create_goal_run(cartridge_id, intent, user, auto_plan=auto_plan)


async def _require_goal_run_visible(goal_run_id: str, user: dict | None) -> dict[str, Any]:
    status = await studio_goal_runs.get_goal_run_status(goal_run_id, user)
    cartridge_id = str((status.get("goal_run") or {}).get("cartridge_id") or "")
    _require_cartridge_visible(user, cartridge_id)
    return status


async def _studio_plan_goal_run(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    goal_run_id = str(args.get("goal_run_id") or args.get("id") or "")
    await _require_goal_run_visible(goal_run_id, user)
    return await studio_goal_runs.plan_goal_run(goal_run_id, user)


async def _studio_execute_goal_run(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    goal_run_id = str(args.get("goal_run_id") or args.get("id") or "")
    await _require_goal_run_visible(goal_run_id, user)
    return await studio_goal_runs.execute_goal_run(goal_run_id, user, executor=_execute_studio_goal_step)


async def _studio_get_goal_run_status(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    goal_run_id = str(args.get("goal_run_id") or args.get("id") or "")
    return await _require_goal_run_visible(goal_run_id, user)


async def _studio_approve_goal_step(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    await _require_goal_run_visible(str(args.get("goal_run_id") or ""), user)
    return await studio_goal_runs.approve_goal_step(
        str(args.get("goal_run_id") or ""),
        int(args.get("step_id")),
        user,
        approval_key=str(args.get("approval_key") or ""),
    )


async def _studio_reject_goal_step(args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    if user is None:
        raise HTTPException(401, "Authentication required")
    await _require_goal_run_visible(str(args.get("goal_run_id") or ""), user)
    return await studio_goal_runs.reject_goal_step(
        str(args.get("goal_run_id") or ""),
        int(args.get("step_id")),
        user,
        reason=str(args.get("reason") or ""),
        approval_key=str(args.get("approval_key") or ""),
    )


studio_assistant.register_local_tool(
    "cartridge_self_check",
    description=(
        "Diagnóstico proactivo del cartucho: manifest, connector_schema, Vault, "
        "entidades, DAGs, Silver/Gold, KBs, hints y registry MCP."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string", "description": "ID del cartucho a diagnosticar."},
        },
        "required": ["cartridge_id"],
    },
    handler=_studio_cartridge_self_check,
)


studio_assistant.register_local_tool(
    "create_goal_run",
    description="Crea un goal run durable para un objetivo amplio de Studio y lo planea por defecto.",
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "intent": {"type": "string"},
            "auto_plan": {"type": "boolean", "default": True},
        },
        "required": ["cartridge_id", "intent"],
    },
    handler=_studio_create_goal_run,
)


studio_assistant.register_local_tool(
    "plan_goal_run",
    description="Materializa los pasos estándar de un goal run de Studio si aún está sin plan.",
    input_schema={
        "type": "object",
        "properties": {"goal_run_id": {"type": "string"}},
        "required": ["goal_run_id"],
    },
    handler=_studio_plan_goal_run,
)


studio_assistant.register_local_tool(
    "execute_goal_run",
    description="Ejecuta secuencialmente un goal run hasta completarlo, fallar o requerir aprobación.",
    input_schema={
        "type": "object",
        "properties": {"goal_run_id": {"type": "string"}},
        "required": ["goal_run_id"],
    },
    handler=_studio_execute_goal_run,
)


studio_assistant.register_local_tool(
    "get_goal_run_status",
    description="Consulta estado, pasos, evidencia y approvals pendientes de un goal run.",
    input_schema={
        "type": "object",
        "properties": {"goal_run_id": {"type": "string"}},
        "required": ["goal_run_id"],
    },
    handler=_studio_get_goal_run_status,
)


studio_assistant.register_local_tool(
    "approve_goal_step",
    description="Aprueba un paso waiting_approval de un goal run de Studio.",
    input_schema={
        "type": "object",
        "properties": {
            "goal_run_id": {"type": "string"},
            "step_id": {"type": "integer"},
            "approval_key": {"type": "string"},
        },
        "required": ["goal_run_id", "step_id", "approval_key"],
    },
    handler=_studio_approve_goal_step,
)


studio_assistant.register_local_tool(
    "reject_goal_step",
    description="Rechaza y salta un paso waiting_approval de un goal run de Studio.",
    input_schema={
        "type": "object",
        "properties": {
            "goal_run_id": {"type": "string"},
            "step_id": {"type": "integer"},
            "approval_key": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["goal_run_id", "step_id", "approval_key"],
    },
    handler=_studio_reject_goal_step,
)


def _svg_for_graph(nodes: list[dict], edges: list[dict]) -> str:
    width = max(760, len(nodes) * 150)
    height = 360
    positions: dict[str, tuple[int, int]] = {}
    lanes = {"cartridge": 40, "entity": 130, "dag": 220, "dataset": 310}
    counters: dict[str, int] = {}
    for node in nodes:
        kind = node.get("kind", "entity")
        counters[kind] = counters.get(kind, 0) + 1
        x = 40 + (counters[kind] - 1) * 180
        positions[node["id"]] = (x, lanes.get(kind, 130))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Inter,Arial,sans-serif;font-size:12px} .node{fill:#fff;stroke:#2563eb;stroke-width:1.5}.edge{stroke:#64748b;stroke-width:1.2;marker-end:url(#arrow)}</style>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#64748b"/></marker></defs>',
    ]
    for edge in edges:
        src = positions.get(edge.get("source"))
        dst = positions.get(edge.get("target"))
        if not src or not dst:
            continue
        parts.append(
            f'<line class="edge" x1="{src[0] + 120}" y1="{src[1] + 20}" '
            f'x2="{dst[0]}" y2="{dst[1] + 20}"/>'
        )
    for node in nodes:
        x, y = positions[node["id"]]
        label = html.escape(str(node.get("label") or node["id"])[:28])
        parts.append(f'<rect class="node" x="{x}" y="{y}" rx="6" width="120" height="40"/>')
        parts.append(f'<text x="{x + 10}" y="{y + 25}">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _dataset_table_name(ds: dict) -> str:
    layer = (ds.get("layer") or "").strip().lower()
    name = _clean_identifier(str(ds.get("name") or ""), label="dataset name")
    if layer == "gold" and not name.startswith("gold_"):
        return f"{layer}_{name}"
    return name


def _extract_entities_from_spec(content: str) -> list[dict]:
    stripped = content or ""
    if stripped.lstrip().startswith("<"):
        fields_by_entity = schema_introspect.parse_odata_metadata(stripped)
        if not fields_by_entity:
            raise HTTPException(400, "OData metadata inválido o sin EntityType/Property")
        return [
            {
                "entity": entity,
                "display_name": entity,
                "mode": "full",
                "primary_key": next((field["name"] for field in fields if field.get("primary_key")), ""),
                "dag_id": "",
                "trigger_type": "manual",
                "cron_expression": "",
                "description": "Imported from OData metadata",
                "enabled": True,
                "fields": fields,
            }
            for entity, fields in sorted(fields_by_entity.items())
        ]
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            raise HTTPException(400, f"Spec YAML/JSON inválido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "Spec must be a YAML/JSON object")

    entities: list[dict] = []
    fields_by_entity = schema_introspect.parse_openapi_fields(parsed)
    raw_entities = parsed.get("entities")
    if isinstance(raw_entities, dict):
        for name, cfg in raw_entities.items():
            cfg = cfg if isinstance(cfg, dict) else {}
            if fields_by_entity.get(str(name)) and not cfg.get("fields"):
                cfg = {**cfg, "fields": fields_by_entity[str(name)]}
            entities.append({"entity": str(name), **cfg})
    elif isinstance(raw_entities, list):
        for item in raw_entities:
            if isinstance(item, str):
                entities.append({"entity": item, "fields": fields_by_entity.get(item) or []})
            elif isinstance(item, dict):
                name = item.get("entity") or item.get("name") or item.get("id")
                if name:
                    if fields_by_entity.get(str(name)) and not item.get("fields"):
                        item = {**item, "fields": fields_by_entity[str(name)]}
                    entities.append({"entity": str(name), **item})

    paths = parsed.get("paths")
    if isinstance(paths, dict):
        for path in paths:
            segments = [
                s for s in str(path).strip("/").split("/")
                if s and not (s.startswith("{") and s.endswith("}"))
            ]
            if segments:
                name = re.sub(r"[^A-Za-z0-9_]", "_", segments[-1]).strip("_")
                if name:
                    entities.append({
                        "entity": name,
                        "description": f"Imported from path {path}",
                        "fields": fields_by_entity.get(name) or [],
                    })

    schemas = ((parsed.get("components") or {}).get("schemas") or {})
    if isinstance(schemas, dict):
        for name in schemas:
            entities.append({
                "entity": str(name),
                "description": "Imported from OpenAPI schema",
                "fields": fields_by_entity.get(str(name)) or [],
            })

    by_entity: dict[str, dict[str, Any]] = {}
    out: list[dict] = []
    for item in entities:
        entity = _clean_identifier(str(item.get("entity") or item.get("name") or item.get("id") or ""), label="entity")
        primary_key = item.get("primary_key") or item.get("id_field") or next(
            (field.get("name") for field in item.get("fields") or [] if isinstance(field, dict) and field.get("primary_key")),
            "",
        )
        normalised = {
            "entity": entity,
            "display_name": item.get("display_name") or item.get("title") or entity,
            "mode": item.get("mode") or "full",
            "primary_key": primary_key,
            "dag_id": item.get("dag_id") or "",
            "trigger_type": item.get("trigger_type") or "manual",
            "cron_expression": item.get("cron_expression") or "",
            "description": item.get("description") or "",
            "enabled": bool(item.get("enabled", True)),
            "fields": item.get("fields") or [],
        }
        existing = by_entity.get(entity)
        if existing:
            if normalised["fields"] and not existing.get("fields"):
                existing["fields"] = normalised["fields"]
            if normalised["primary_key"] and not existing.get("primary_key"):
                existing["primary_key"] = normalised["primary_key"]
            continue
        by_entity[entity] = normalised
        out.append(normalised)
    return out


@router.get("/dag-graph", dependencies=[Depends(require_studio_read)])
async def dag_graph(
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    _require_cartridge_visible(user, cartridge)
    manifest = await cartridge_service.get_cartridge(cartridge)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")

    nodes = [{"id": f"cartridge:{cartridge}", "kind": "cartridge", "label": manifest["name"]}]
    edges: list[dict] = []
    for entity in manifest.get("entities") or []:
        entity_name = entity.get("entity") or entity.get("id") or entity.get("name")
        if not entity_name:
            continue
        entity_name = _clean_identifier(str(entity_name), label="entity")
        entity_id = f"entity:{entity_name}"
        nodes.append({"id": entity_id, "kind": "entity", "label": entity.get("display_name") or entity_name})
        edges.append({"source": f"cartridge:{cartridge}", "target": entity_id})
        if entity.get("dag_id"):
            dag_id = f"dag:{entity['dag_id']}"
            if not any(n["id"] == dag_id for n in nodes):
                nodes.append({"id": dag_id, "kind": "dag", "label": entity["dag_id"]})
            edges.append({"source": entity_id, "target": dag_id})

    try:
        for ds in await _refinement_datasets(user):
            if ds.get("cartridge") and ds.get("cartridge") != cartridge:
                continue
            ds_id = f"dataset:{ds.get('layer')}:{ds.get('name')}"
            nodes.append({"id": ds_id, "kind": "dataset", "label": f"{ds.get('layer')}:{ds.get('name')}"})
            for source in ds.get("sources") or []:
                parts = str(source).split("/")
                if len(parts) >= 3 and parts[0] == "raw" and parts[1] == cartridge:
                    edges.append({"source": f"entity:{parts[2]}", "target": ds_id})
    except Exception:
        pass

    return {"format": "svg", "svg": _svg_for_graph(nodes, edges), "nodes": nodes, "edges": edges}


@router.get("/dags", dependencies=[Depends(require_studio_read)])
async def dags_list(
    cartridge: str | None = None,
    user: dict = Depends(require_authenticated),
):
    if cartridge:
        _require_cartridge_visible(user, cartridge)
    manifest = await cartridge_service.get_cartridge(cartridge) if cartridge else None
    registered = _manifest_dag_ids(manifest)
    result = await mcp_registry.invoke("infra", "airflow_list_dags", {}, user=user)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(502, f"Airflow DAG list failed: {result['error']}")
    raw_dags = result.get("dags") if isinstance(result, dict) else result
    if not isinstance(raw_dags, list):
        raw_dags = []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_dags:
        dag = item if isinstance(item, dict) else {"dag_id": str(item)}
        dag_id = str(dag.get("dag_id") or dag.get("id") or "").strip()
        if not dag_id:
            continue
        if cartridge and not (
            dag_id.startswith(f"{cartridge}_")
            or dag_id in registered
            or dag.get("cartridge_id") == cartridge
        ):
            continue
        seen.add(dag_id)
        normalized.append({
            "dag_id": dag_id,
            "id": dag_id,
            "is_paused": bool(dag.get("is_paused", dag.get("paused", False))),
            "is_active": bool(dag.get("is_active", dag.get("active", True))),
            "tags": dag.get("tags") or [],
            "cartridge_id": dag.get("cartridge_id") or cartridge,
        })

    for dag_id in sorted(registered - seen):
        normalized.append({
            "dag_id": dag_id,
            "id": dag_id,
            "is_paused": False,
            "is_active": True,
            "tags": [],
            "cartridge_id": cartridge,
            "registered_only": True,
        })

    normalized.sort(key=lambda dag: dag["dag_id"])
    return {"cartridge": cartridge, "dags": normalized, "total": len(normalized)}


@router.get("/dags/{dag_id}/source", dependencies=[Depends(require_studio_read)])
async def dag_source(
    dag_id: str,
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    _require_cartridge_visible(user, cartridge)
    safe_dag_id = _clean_dag_id(dag_id)
    result = await mcp_registry.invoke("infra", "dag_get_source", {
        "cartridge_id": cartridge,
        "dag_id": safe_dag_id,
    }, user=user)
    if isinstance(result, dict) and result.get("error"):
        return {"dag_id": safe_dag_id, "found": False, "source_code": "", "error": result["error"]}
    return {
        "dag_id": safe_dag_id,
        "found": bool(result.get("found")) if isinstance(result, dict) else False,
        "source_code": result.get("source_code") if isinstance(result, dict) else "",
        "path": result.get("path") if isinstance(result, dict) else None,
    }


@router.delete("/dags/{dag_id}", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def dag_delete(
    dag_id: str,
    cartridge: str = "replicon",
    _global_admin: dict = Depends(require_studio_global_admin),
    user: dict = Depends(require_authenticated),
):
    safe_dag_id = _clean_dag_id(dag_id)
    safe_cartridge = _clean_identifier(cartridge, label="cartridge")
    manifest = await cartridge_service.get_cartridge(safe_cartridge)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{safe_cartridge}' not found")
    registered = _manifest_dag_ids(manifest)
    if safe_dag_id not in registered and not safe_dag_id.startswith(f"{safe_cartridge}_"):
        raise HTTPException(403, f"DAG '{safe_dag_id}' does not belong to cartridge '{safe_cartridge}'")
    result = await mcp_registry.invoke("infra", "airflow_delete_dag", {
        "dag_id": safe_dag_id,
        "cartridge_id": safe_cartridge,
    }, user=user)
    if isinstance(result, dict) and result.get("error"):
        if "ALLOW_RCE_TOOLS" in str(result.get("error")):
            raise HTTPException(
                403,
                "Eliminar DAG requiere ALLOW_RCE_TOOLS=true en el entorno local.",
            )
        raise HTTPException(502, f"Airflow delete failed: {result['error']}")
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="studio.dag.delete",
        resource_type="airflow_dag",
        resource_id=safe_dag_id,
        status="success",
        metadata={"cartridge": safe_cartridge},
    )
    return {"deleted": True, "dag_id": safe_dag_id, "result": result}


@router.post("/dag-deploy", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def dag_deploy(
    request: Request,
    _global_admin: dict = Depends(require_studio_global_admin),
    user: dict = Depends(require_authenticated),
):
    body = await _optional_json(request)
    cartridge = body.get("cartridge") or body.get("cartridge_id") or "replicon"
    entity = body.get("entity") or "Entity"
    dag_id = body.get("dag_id")
    code = body.get("code")
    if not code and body.get("template_id"):
        code = dag_templates.get_code(body["template_id"], cartridge=cartridge, entity=entity)
        dag_id = dag_id or f"{cartridge}_{entity}_full"
    if not dag_id or not code:
        return {
            "status": "needs_input",
            "message": "dag_id and code are required, or provide template_id + cartridge + entity",
            "dag_id": dag_id,
        }
    safe_cartridge = _clean_identifier(cartridge, label="cartridge")
    _require_cartridge_visible(user, safe_cartridge)
    manifest = await cartridge_service.get_cartridge(safe_cartridge)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{safe_cartridge}' not found")
    safe_dag_id = _clean_dag_id(dag_id)
    registered = _manifest_dag_ids(manifest)
    if safe_dag_id in registered:
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="studio.dag.deploy",
            resource_type="airflow_dag",
            resource_id=safe_dag_id,
            status="skipped",
            metadata={
                "cartridge": safe_cartridge,
                "entity": entity,
                "reason": "packaged_dag_managed_by_cartridge",
            },
        )
        return {
            "status": "managed",
            "dag_id": safe_dag_id,
            "message": "DAG empaquetado: se gestiona desde el cartucho, no desde airflow/dags.",
        }
    if not safe_dag_id.startswith(f"{safe_cartridge}_"):
        raise HTTPException(403, f"DAG '{safe_dag_id}' does not belong to cartridge '{safe_cartridge}'")
    result = await mcp_registry.invoke("infra", "airflow_create_dag", {
        "dag_id": safe_dag_id,
        "code": code,
        "cartridge_id": safe_cartridge,
        "description": body.get("description"),
    }, user=user)
    if isinstance(result, dict) and result.get("error"):
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="studio.dag.deploy",
            resource_type="airflow_dag",
            resource_id=safe_dag_id,
            status="failed",
            metadata={"cartridge": safe_cartridge, "entity": entity, "error": result.get("error")},
        )
        if "ALLOW_RCE_TOOLS" in str(result.get("error")):
            raise HTTPException(
                403,
                "Deploy a Airflow requiere ALLOW_RCE_TOOLS=true en el entorno local.",
            )
        return {"status": "failed", "dag_id": safe_dag_id, "error": result["error"]}
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="studio.dag.deploy",
        resource_type="airflow_dag",
        resource_id=safe_dag_id,
        status="success",
        metadata={"cartridge": safe_cartridge, "entity": entity},
    )
    return {"status": "deployed", "dag_id": safe_dag_id, "result": result}


@router.get("/templates", dependencies=[Depends(require_studio_read)])
async def templates(user: dict = Depends(require_authenticated)):
    return {"templates": dag_templates.get_all()}


@router.get("/entities", dependencies=[Depends(require_studio_read)])
async def entities_list(
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    _require_cartridge_visible(user, cartridge)
    if not await cartridge_service.get_cartridge(cartridge):
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")
    entities = await studio_entities.list_entities(cartridge=cartridge)
    return {"cartridge": cartridge, "entities": entities, "total": len(entities)}


@router.post("/introspect-source", dependencies=[Depends(require_csrf), Depends(require_studio_read)])
async def introspect_source(
    request: Request,
    user: dict = Depends(require_authenticated),
):
    body = await _optional_json(request)
    cartridge = body.get("cartridge_id") or body.get("cartridge") or request.query_params.get("cartridge") or "replicon"
    result = await _studio_introspect_source({**body, "cartridge_id": cartridge}, user)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="studio.source.introspect",
        resource_type="cartridge",
        resource_id=str(cartridge),
        status="success" if result.get("source") == "live" else "fallback",
        metadata={
            "source": result.get("source"),
            "entity_count": len(result.get("entities") or []),
            "reason": str(result.get("reason") or "")[:200],
        },
    )
    return result


@router.post("/entities/upload", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def entities_upload(
    request: Request,
    _global_admin: dict = Depends(require_studio_global_admin),
    user: dict = Depends(require_authenticated),
):
    content_type = request.headers.get("content-type", "")
    cartridge = request.query_params.get("cartridge") or "replicon"
    filename = "spec.yaml"
    content = ""

    if "multipart/form-data" in content_type:
        form = await request.form()
        cartridge = str(form.get("cartridge") or form.get("cartridge_id") or cartridge)
        uploaded = form.get("file") or form.get("spec")
        if uploaded is not None and hasattr(uploaded, "read"):
            raw = await uploaded.read()
            if len(raw) > _MAX_SPEC_BYTES:
                raise HTTPException(413, "spec file too large")
            filename = _clean_filename(getattr(uploaded, "filename", None) or filename)
            content = raw.decode("utf-8", errors="replace")
    else:
        body = await _optional_json(request)
        cartridge = body.get("cartridge") or body.get("cartridge_id") or cartridge
        filename = _clean_filename(body.get("filename") or filename)
        content = body.get("content") or body.get("yaml") or body.get("spec") or ""
        if len(content.encode("utf-8")) > _MAX_SPEC_BYTES:
            raise HTTPException(413, "spec content too large")

    if not content.strip():
        return {"accepted": False, "accepted_count": 0, "error": "spec content is required"}
    if not await cartridge_service.get_cartridge(cartridge):
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")

    try:
        result = await studio_entities.upload_spec(content, user, default_cartridge=cartridge)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    spec_key = None
    if not result["errors"]:
        spec_key = cartridge_service.upload_spec(cartridge, filename, content)
    return {
        "accepted": not result["errors"],
        "accepted_count": len(result["created"]),
        "uploaded": spec_key,
        "entities": [e["name"] for e in result["created"]],
        "created": result["created"],
        "errors": result["errors"],
    }


@router.post("/entity", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def entity(
    request: Request,
    _global_admin: dict = Depends(require_studio_global_admin),
    user: dict = Depends(require_authenticated),
):
    body = await _optional_json(request)
    cartridge = body.get("cartridge") or body.get("cartridge_id") or "replicon"
    entity_name = body.get("entity") or body.get("name")
    if not entity_name:
        return {"created": False, "error": "entity is required"}
    entity_name = _clean_identifier(str(entity_name), label="entity")
    spec = body.get("spec") if isinstance(body.get("spec"), dict) else {}
    spec = {
        **spec,
        "name": entity_name,
        "cartridge": cartridge,
        "fields": spec.get("fields") or body.get("fields") or [{"name": body.get("primary_key") or "id", "type": "string", "primary_key": True}],
        "display_name": body.get("display_name") or body.get("title") or entity_name,
        "mode": body.get("mode") or "full",
        "primary_key": body.get("primary_key") or "",
        "dag_id": body.get("dag_id") or "",
        "trigger_type": body.get("trigger_type") or "manual",
        "cron_expression": body.get("cron_expression") or "",
        "description": body.get("description") or "",
        "enabled": body.get("enabled", True),
    }
    try:
        created = await studio_entities.create_entity(entity_name, cartridge, spec, user)
    except (ValueError, studio_entities.DuplicateEntityError, studio_entities.UnknownCartridgeError) as exc:
        raise studio_entities.to_http_error(exc) from exc
    return {"created": True, "entity_id": entity_name, "entity": created}


async def _layer_preview(layer: str, request: Request, user: dict) -> dict:
    layer = layer.lower()
    limit = _limit_param(request)
    cartridge = request.query_params.get("cartridge") or "replicon"
    if cartridge:
        _require_cartridge_visible(user, cartridge)
    requested = request.query_params.get("dataset")
    datasets = await _refinement_datasets(user)
    candidates = [
        ds for ds in datasets
        if (ds.get("layer") or "").lower() == layer
        and (not cartridge or not ds.get("cartridge") or ds.get("cartridge") == cartridge)
    ]
    if requested:
        candidates = [ds for ds in candidates if ds.get("name") == requested]
    if not candidates:
        return {
            "layer": layer,
            "columns": [],
            "rows": [],
            "total": 0,
            "available": False,
            "reason": f"No {layer} datasets registered for cartridge {cartridge}",
        }
    ds = candidates[0]
    result = await _refinement_invoke(
        "query_dataset",
        {"name": ds["name"], "limit": limit, "user_context": _rls_user_context(user)},
        timeout=60,
        user=user,
    )
    rows = result.get("data") or result.get("rows") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    else:
        schema = await _refinement_invoke("get_schema", {"name": ds["name"]}, timeout=30, user=user)
        columns = [f.get("name") for f in schema.get("fields", []) if f.get("name")]
    return {
        "layer": layer,
        "dataset": ds["name"],
        "columns": columns,
        "rows": rows if isinstance(rows, list) else [],
        "total": len(rows) if isinstance(rows, list) else 0,
        "source": "refinement.query_dataset",
    }


@router.get("/silver/preview", dependencies=[Depends(require_studio_read)])
async def silver_preview(request: Request, user: dict = Depends(require_authenticated)):
    return await _layer_preview("silver", request, user)


@router.get("/gold/preview", dependencies=[Depends(require_studio_read)])
async def gold_preview(request: Request, user: dict = Depends(require_authenticated)):
    return await _layer_preview("gold", request, user)


@router.get("/master/preview", dependencies=[Depends(require_studio_read)])
async def master_preview(request: Request, user: dict = Depends(require_authenticated)):
    """Backward-compatible URL. The platform now serves this from Gold;
    serve the equivalent Gold preview for callers that still hit this path."""
    return await _layer_preview("gold", request, user)


@router.post("/superset/dataset", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def superset_dataset(
    request: Request,
    _global_admin: dict = Depends(require_studio_global_admin),
    user: dict = Depends(require_authenticated),
):
    body = await _optional_json(request)
    database_id = body.get("database_id")
    table_name = body.get("table_name") or body.get("dataset_name")
    schema = body.get("schema") or "public"

    if not table_name:
        gold = [ds for ds in await _refinement_datasets(user) if (ds.get("layer") or "").lower() == "gold"]
        if not gold:
            return {"created": False, "available": False, "error": "No Gold datasets available for Superset"}
        table_name = _dataset_table_name(gold[0])

    client = superset_client.client_from_env()
    if not client.configured:
        raise HTTPException(503, "Superset not configured")
    table_name = _clean_identifier(str(table_name), label="table_name")
    schema = _clean_identifier(str(schema), label="schema")
    if schema != "public":
        raise HTTPException(400, "Only schema 'public' is allowed for Studio-created Superset datasets")
    datasets = [
        ds for ds in await _refinement_datasets(user)
        if (ds.get("layer") or "").lower() == "gold"
    ]
    allowed_tables = {_dataset_table_name(ds) for ds in datasets}
    if table_name not in allowed_tables:
        raise HTTPException(400, f"Table '{table_name}' is not a registered Gold dataset")

    try:
        dbs = await client.list_databases()
        if database_id:
            if not any(str(db.get("id")) == str(database_id) for db in dbs):
                raise HTTPException(400, "database_id is not registered in Superset")
        else:
            match = next((db for db in dbs if db.get("name") in {"modecissions_gold", "Postgres Gold"}), None)
            match = match or (dbs[0] if dbs else None)
            if not match:
                gold_uri = os.environ.get("SUPERSET_GOLD_SQLALCHEMY_URI", "").strip()
                if not gold_uri:
                    raise HTTPException(503, "No Superset database connection registered")
                match = await client.create_database("modecissions_gold", gold_uri)
            database_id = match["id"]
        result = await client.create_dataset(int(database_id), table_name, schema=schema)
    except superset_client.SupersetConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except superset_client.SupersetRequestError as exc:
        message = str(exc)
        if exc.status_code == 422 and "could not be found" in message.lower():
            return {
                "created": False,
                "available": False,
                "needs_materialization": True,
                "table": table_name,
                "schema": schema,
                "message": f"Materializa primero el dataset Gold '{table_name}' y vuelve a crear el dataset en Superset.",
            }
        raise HTTPException(exc.status_code, str(exc)) from exc
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="studio.superset.dataset_create",
        resource_type="superset_dataset",
        resource_id=f"{schema}.{table_name}",
        status="success",
        metadata={"database_id": int(database_id), "existing": bool(result.get("existing"))},
    )
    return {"created": not result.get("existing"), **result}


@router.get("/semantic", dependencies=[Depends(require_studio_read)])
async def semantic(
    cartridge: str = "replicon",
    user: dict = Depends(require_authenticated),
):
    _require_cartridge_visible(user, cartridge)
    manifest = await cartridge_service.get_cartridge(cartridge)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")
    relations, relation_source = await _semantic_relations(cartridge, manifest, user)
    return {
        "cartridge": cartridge,
        "server": manifest,
        "entities": manifest.get("entities") or [],
        "vocabulary": (manifest.get("semantic_model") or {}).get("vocabulary") or [],
        "relations": relations,
        "relation_source": relation_source,
    }


@router.get("/rag", dependencies=[Depends(require_studio_read)])
async def rag(user: dict = Depends(require_authenticated)):
    sources = await _rag_sources(user)
    return {"sources": sources, "corpus": sources, "docs_indexed": len(sources) if isinstance(sources, list) else 0}


@router.post("/assistant", dependencies=[Depends(require_csrf), Depends(require_studio_write)])
async def assistant(
    request: Request,
    _global_admin: dict = Depends(require_studio_global_admin),
    user: dict = Depends(require_authenticated),
):
    body = await _optional_json(request)
    message = (body.get("message") or body.get("prompt") or "").strip()
    if not message:
        return {"reply": "Escribe una instrucción para el asistente de Studio.", "session_id": body.get("session_id")}
    cartridge_id = body.get("cartridge_id") or body.get("cartridge") or "replicon"
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    result = await studio_assistant.chat(
        message=message,
        history=body.get("history", []),
        step=int(body.get("step") or 1),
        manifest=manifest,
        actor_role=user.get("workspace_role") or user.get("role"),
        actor_user=user,
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="studio.assistant.message",
        resource_type="studio_assistant",
        resource_id=cartridge_id,
        status="success",
        metadata={"step": int(body.get("step") or 1), "message_len": len(message)},
    )
    result["session_id"] = body.get("session_id")
    return result
