from __future__ import annotations

import os
import ssl
from typing import Any

from app.services import egress_guard
from app.services.control_room.core import BaseAdapter, ExecutionResult

from .auth_factory import auth_headers
from .base import AdapterConfigurationError, AdapterExecutionError
from .circuit_breaker import CartridgeCircuitBreaker


def _first(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _base_url(credentials: dict[str, Any]) -> str:
    value = _first(
        credentials, "base_url", "url", "replicon_base_url", "REPLICON_BASE_URL"
    )
    if not value:
        raise AdapterConfigurationError(
            "Replicon write-back requires base_url/REPLICON_BASE_URL"
        )
    return value.rstrip("/")


def _writeback_path(action_data: dict[str, Any], credentials: dict[str, Any]) -> str:
    details = (
        action_data.get("details")
        if isinstance(action_data.get("details"), dict)
        else {}
    )
    payload = (
        action_data.get("action_payload")
        if isinstance(action_data.get("action_payload"), dict)
        else {}
    )
    replicon = (
        payload.get("replicon") if isinstance(payload.get("replicon"), dict) else {}
    )
    value = (
        action_data.get("writeback_path")
        or action_data.get("endpoint")
        or details.get("writeback_path")
        or details.get("endpoint")
        or replicon.get("writeback_path")
        or replicon.get("endpoint")
        or credentials.get("writeback_path")
        or credentials.get("default_writeback_path")
    )
    if not value:
        raise AdapterConfigurationError(
            "Replicon write-back requires writeback_path/default_writeback_path"
        )
    path = str(value)
    return path if path.startswith("/") else f"/{path}"


def _payload(action_data: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    payload = (
        action_data.get("action_payload")
        if isinstance(action_data.get("action_payload"), dict)
        else {}
    )
    replicon = (
        payload.get("replicon") if isinstance(payload.get("replicon"), dict) else {}
    )
    return {
        "source": "omega_control_room",
        "dry_run": dry_run,
        "template_id": action_data.get("template_id"),
        "template_type": action_data.get("template_type"),
        "idempotency_key": action_data.get("idempotency_key"),
        "item_id": action_data.get("item_id"),
        "entity_id": action_data.get("entity_id"),
        "entity_label": action_data.get("entity_label"),
        "action_kind": action_data.get("action_kind"),
        "source_dataset": action_data.get("source_dataset"),
        "severity": action_data.get("severity"),
        "replicon": replicon,
        "impact": action_data.get("impact"),
    }


def _response_body(response: egress_guard.PinnedHTTPResponse) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:1000]


def _allowed_private_hosts() -> set[str]:
    raw = os.environ.get("CONTROL_ROOM_WRITEBACK_ALLOWED_PRIVATE_HOSTS", "")
    return {item.strip().rstrip(".").lower() for item in raw.split(",") if item.strip()}


def _allowed_private_cidrs() -> list[str]:
    return [
        item.strip()
        for item in os.environ.get(
            "CONTROL_ROOM_WRITEBACK_ALLOWED_PRIVATE_CIDRS", ""
        ).split(",")
        if item.strip()
    ]


class RepliconAdapter(BaseAdapter):
    cartridge_id = "replicon"

    def execute(
        self,
        action_data: dict[str, Any],
        credentials: dict[str, Any],
        dry_run: bool = True,
    ) -> ExecutionResult:
        CartridgeCircuitBreaker.before_call(self.cartridge_id)
        url = f"{_base_url(credentials)}{_writeback_path(action_data, credentials)}"
        headers = auth_headers(
            {
                **credentials,
                "auth_method": credentials.get("auth_method") or "bearer_token",
            }
        )
        headers["Idempotency-Key"] = str(action_data.get("idempotency_key") or "")
        headers["X-Omega-Dry-Run"] = "true" if dry_run else "false"
        timeout = float(credentials.get("timeout") or 20.0)

        try:
            response = egress_guard.pinned_request_sync(
                "POST",
                url,
                label="replicon write-back URL",
                headers=headers,
                json_body=_payload(action_data, dry_run=dry_run),
                timeout=timeout,
                allow_private_hosts=_allowed_private_hosts(),
                allow_private_cidrs=_allowed_private_cidrs(),
            )
        except egress_guard.EgressGuardError as exc:
            if exc.request_dispatched:
                CartridgeCircuitBreaker.record_failure(self.cartridge_id)
                raise AdapterExecutionError(
                    f"replicon response validation error: {exc}",
                    outcome_ambiguous=True,
                ) from exc
            raise AdapterConfigurationError(str(exc), outcome_ambiguous=False) from exc
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            CartridgeCircuitBreaker.record_failure(self.cartridge_id)
            raise AdapterExecutionError(
                f"replicon transport error: {exc}", status_code=503
            ) from exc

        if (
            response.status_code < 200
            or response.status_code >= 600
            or 300 <= response.status_code < 400
        ):
            CartridgeCircuitBreaker.record_failure(self.cartridge_id)
            raise AdapterExecutionError(
                "replicon returned an unconfirmed POST outcome",
                status_code=response.status_code,
                response=response.text[:500],
                outcome_ambiguous=True,
            )
        if response.status_code in {401, 403}:
            CartridgeCircuitBreaker.record_success(self.cartridge_id)
            raise AdapterExecutionError(
                "replicon rejected authentication",
                status_code=response.status_code,
                response=response.text[:500],
            )
        if response.status_code >= 500 or response.status_code == 429:
            CartridgeCircuitBreaker.record_failure(self.cartridge_id)
            raise AdapterExecutionError(
                "replicon remote endpoint failed",
                status_code=response.status_code,
                response=response.text[:500],
            )
        if response.status_code >= 400:
            CartridgeCircuitBreaker.record_success(self.cartridge_id)
            raise AdapterExecutionError(
                "replicon rejected write-back payload",
                status_code=response.status_code,
                response=response.text[:500],
            )

        CartridgeCircuitBreaker.record_success(self.cartridge_id)
        body = _response_body(response)
        remote_id = None
        if isinstance(body, dict):
            remote_id = (
                body.get("id") or body.get("remote_id") or body.get("request_id")
            )
        return ExecutionResult(
            ok=True,
            status="validated" if dry_run else "executed",
            message=f"Replicon write-back {'validated' if dry_run else 'executed'} with HTTP {response.status_code}",
            data={
                "status_code": response.status_code,
                "url": url,
                "external_id": str(remote_id) if remote_id else None,
                "dry_run": dry_run,
                "response": body,
            },
        )
