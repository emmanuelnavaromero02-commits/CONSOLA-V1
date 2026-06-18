from __future__ import annotations

import asyncio
import os
import ssl
from typing import Any

from app.services import egress_guard

from .auth_factory import auth_headers
from .base import AdapterConfigurationError, AdapterExecutionError, BaseAdapter, ExecutionResult
from .circuit_breaker import CartridgeCircuitBreaker


def _base_url(credentials: dict[str, Any]) -> str:
    value = credentials.get("base_url") or credentials.get("url")
    if not value:
        raise AdapterConfigurationError("connection is missing base_url")
    return str(value).rstrip("/")


def _writeback_path(action_data: dict[str, Any], credentials: dict[str, Any]) -> str:
    details = action_data.get("details") if isinstance(action_data.get("details"), dict) else {}
    value = (
        action_data.get("writeback_path")
        or action_data.get("endpoint")
        or details.get("writeback_path")
        or details.get("endpoint")
        or credentials.get("writeback_path")
        or credentials.get("default_writeback_path")
    )
    if not value:
        raise AdapterConfigurationError("connection is missing writeback_path/default_writeback_path")
    path = str(value)
    return path if path.startswith("/") else f"/{path}"


def _json_payload(action_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": action_data.get("template_id"),
        "item_id": action_data.get("item_id"),
        "entity": action_data.get("entity") or action_data.get("entity_id"),
        "title": action_data.get("title"),
        "payload": action_data,
    }


def _allowed_private_hosts() -> set[str]:
    raw = os.environ.get("CONTROL_ROOM_WRITEBACK_ALLOWED_PRIVATE_HOSTS", "")
    return {item.strip().rstrip(".").lower() for item in raw.split(",") if item.strip()}


def _allowed_private_cidrs() -> list[str]:
    return [item.strip() for item in os.environ.get("CONTROL_ROOM_WRITEBACK_ALLOWED_PRIVATE_CIDRS", "").split(",") if item.strip()]


class HttpWriteBackAdapter(BaseAdapter):
    cartridge_id = "external"
    timeout_seconds = 30.0

    async def _post_json(
        self,
        *,
        credentials: dict[str, Any],
        action_data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> ExecutionResult:
        CartridgeCircuitBreaker.before_call(self.cartridge_id)
        url = f"{_base_url(credentials)}{_writeback_path(action_data, credentials)}"
        request_headers = auth_headers(credentials)
        if headers:
            request_headers.update(headers)

        try:
            response = await egress_guard.pinned_request(
                "POST",
                url,
                label="write-back URL",
                headers=request_headers,
                json_body=_json_payload(action_data),
                timeout=self.timeout_seconds,
                allow_private_hosts=_allowed_private_hosts(),
                allow_private_cidrs=_allowed_private_cidrs(),
            )
        except egress_guard.EgressGuardError as exc:
            raise AdapterConfigurationError(str(exc)) from exc
        except (OSError, TimeoutError, asyncio.TimeoutError, ssl.SSLError) as exc:
            CartridgeCircuitBreaker.record_failure(self.cartridge_id)
            raise AdapterExecutionError(f"{self.cartridge_id} transport error: {exc}", status_code=503) from exc

        if response.status_code in {401, 403}:
            CartridgeCircuitBreaker.record_success(self.cartridge_id)
            raise AdapterExecutionError(
                f"{self.cartridge_id} rejected authentication",
                status_code=response.status_code,
                response=response.text[:500],
            )
        if response.status_code >= 500 or response.status_code == 429:
            CartridgeCircuitBreaker.record_failure(self.cartridge_id)
            raise AdapterExecutionError(
                f"{self.cartridge_id} remote endpoint failed",
                status_code=response.status_code,
                response=response.text[:500],
            )
        if response.status_code >= 400:
            CartridgeCircuitBreaker.record_success(self.cartridge_id)
            raise AdapterExecutionError(
                f"{self.cartridge_id} rejected write-back payload",
                status_code=response.status_code,
                response=response.text[:500],
            )

        CartridgeCircuitBreaker.record_success(self.cartridge_id)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        return ExecutionResult(
            success=True,
            message=f"{self.cartridge_id} write-back executed",
            status_code=response.status_code,
            remote_id=str(body.get("id") or body.get("remote_id") or "") or None,
            response=body,
        )

    async def execute(self, action_data: dict[str, Any], credentials: dict[str, Any]) -> ExecutionResult:
        return await self._post_json(credentials=credentials, action_data=action_data)
