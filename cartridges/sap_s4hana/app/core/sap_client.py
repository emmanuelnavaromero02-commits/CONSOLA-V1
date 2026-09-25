from __future__ import annotations

import logging
import os
from typing import Any

import requests
from urllib3.util.retry import Retry

from app.core.auth_factory import auth_trace, build_auth_headers, normalize_auth_method
from app.core.config import settings
from app.core.egress_guard import guarded_session
from app.core.vault_client import get_connection_for_worker, get_secret_for_worker

logger = logging.getLogger(__name__)
logging.getLogger("urllib3.util.retry").setLevel(logging.INFO)


def _make_retry_session(max_retries: int = 3, backoff_factor: float = 2.0) -> requests.Session:
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    return guarded_session(retries=retry)


class SAPClientError(RuntimeError):
    pass


class CircuitBreakerOpen(SAPClientError):
    pass


class CartridgeCircuitBreaker:
    failures = 0
    threshold = 3
    state = "HEALTHY"

    @classmethod
    def before_request(cls) -> None:
        if cls.failures >= cls.threshold:
            cls.state = "UNHEALTHY"
            raise CircuitBreakerOpen("sap_s4hana circuit breaker is UNHEALTHY")

    @classmethod
    def record_success(cls) -> None:
        cls.failures = 0
        cls.state = "HEALTHY"

    @classmethod
    def record_failure(cls) -> None:
        cls.failures += 1
        if cls.failures >= cls.threshold:
            cls.state = "UNHEALTHY"

    @classmethod
    def snapshot(cls) -> dict[str, Any]:
        return {"state": cls.state, "failures": cls.failures, "threshold": cls.threshold}

    @classmethod
    def reset(cls) -> None:
        cls.failures = 0
        cls.state = "HEALTHY"


class SapS4Client:

    CARTRIDGE_ID = "sap_s4hana"
    REQUIRED_ENV = ("sap_s4_base_url", "sap_s4_user", "sap_s4_pass")

    _RETRY_MAX = 3
    _RETRY_BACKOFF_FACTOR = 2.0


    def __init__(self, security_context: str | None = None) -> None:
        self._session = _make_retry_session(self._RETRY_MAX, self._RETRY_BACKOFF_FACTOR)
        self._security_context = (security_context or "").strip() or None
        self._vault_connection = get_connection_for_worker("sap_s4hana", security_context=self._security_context)
        self.base_url = (
            get_secret_for_worker("sap_s4hana", "SAP_S4_BASE_URL", security_context=self._security_context)
            or settings.sap_s4_base_url
            or ""
        ).rstrip("/")
        self.user = get_secret_for_worker("sap_s4hana", "SAP_S4_USER", security_context=self._security_context) or settings.sap_s4_user
        self.password = get_secret_for_worker("sap_s4hana", "SAP_S4_PASS", security_context=self._security_context) or settings.sap_s4_pass
        self.client_mandant = (
            get_secret_for_worker("sap_s4hana", "SAP_S4_CLIENT_MANDANT", security_context=self._security_context)
            or settings.sap_s4_client_mandant
            or "100"
        )
        self.api_key = (
            get_secret_for_worker("sap_s4hana", "SAP_S4_API_KEY", security_context=self._security_context)
            or settings.sap_s4_api_key
            or os.environ.get("S4_API_KEY", "")
        )
        self.token = get_secret_for_worker("sap_s4hana", "SAP_S4_TOKEN", security_context=self._security_context)
        self.auth_method = normalize_auth_method(
            self._vault_connection.get("auth_method"),
            "basic",
        )
        self._auth_payload = {
            **self._vault_connection,
            "auth_method": self.auth_method,
            "user": self.user,
            "username": self.user,
            "password": self.password,
            "api_key": self.api_key or self._vault_connection.get("api_key") or self.token,
            "token": self.token or self._vault_connection.get("token") or self.api_key,
        }


    def configuration_status(self) -> dict[str, Any]:
        required = {"SAP_S4_BASE_URL": self.base_url}
        if self.auth_method == "basic":
            required.update({"SAP_S4_USER": self.user, "SAP_S4_PASS": self.password})
        elif self.auth_method == "api_key":
            required["SAP_S4_API_KEY"] = self.api_key or self.token or self._vault_connection.get("token")
        elif self.auth_method == "bearer_token":
            required["SAP_S4_TOKEN"] = self.token or self.api_key or self._vault_connection.get("token")
        missing = [name for name, value in required.items() if not value]
        return {
            "cartridge": self.CARTRIDGE_ID,
            "configured": not missing,
            "missing": missing,
            "base_url": self.base_url or None,
            "client": self.client_mandant,
        }

    def _require_configured(self) -> None:
        status = self.configuration_status()
        if not status["configured"]:
            raise SAPClientError(
                f"sap_s4hana not configured; missing env: {status['missing']}"
            )


    def _headers(self) -> dict[str, str]:
        headers, _, _ = build_auth_headers(
            self._auth_payload,
            default_method="basic",
            default_api_key_header="APIKey",
            base_headers={
                "Accept": "application/json",
                "sap-client": self.client_mandant,
            },
        )
        if self.api_key and self.auth_method != "api_key":
            headers["APIKey"] = self.api_key
        return headers

    def _log_auth(self, status_code: int | None = None) -> None:
        _, method, header_names = build_auth_headers(
            self._auth_payload,
            default_method="basic",
            default_api_key_header="APIKey",
            base_headers={"Accept": "application/json"},
        )
        if self.api_key and method != "api_key":
            header_names = tuple(dict.fromkeys((*header_names, "APIKey")))
        suffix = f" -> Respuesta del servidor {status_code}" if status_code is not None else ""
        logger.warning("%s%s", auth_trace(method, header_names), suffix)


    def test_connection(self) -> dict[str, Any]:
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}

        try:
            from app.services.catalog_service import get_all_entities
            catalogue = get_all_entities() or []
        except Exception:
            catalogue = []

        probe_path = ""
        for entry in catalogue:
            odata_path = entry.get("odata_entity") or entry.get("entity")
            if not odata_path:
                continue
            probe_path = odata_path.split("/", 1)[0] if "/" in odata_path else odata_path
            break

        probe_url = f"{self.base_url}/{probe_path}/$metadata" if probe_path \
            else f"{self.base_url}/$metadata"

        try:
            CartridgeCircuitBreaker.before_request()
            logger.warning("SAP S/4HANA outbound GET %s", probe_url)
            resp = self._session.get(
                probe_url,
                headers=self._headers(),
                params={"sap-client": self.client_mandant},
                timeout=30,
            )
            self._log_auth(resp.status_code)
            if resp.status_code in {401, 403}:
                CartridgeCircuitBreaker.record_success()
                return {
                    "status": "auth_error",
                    "reachable": True,
                    "configured": True,
                    "base_url": self.base_url,
                    "probe": probe_url,
                    "http_status": resp.status_code,
                    "client": self.client_mandant,
                    "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
                }
            if resp.status_code >= 500 or resp.status_code == 429:
                CartridgeCircuitBreaker.record_failure()
                resp.raise_for_status()
            resp.raise_for_status()
            CartridgeCircuitBreaker.record_success()
            return {
                "status": "ok",
                "reachable": True,
                "configured": True,
                "base_url": self.base_url,
                "probe": probe_url,
                "client": self.client_mandant,
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except CircuitBreakerOpen as exc:
            return {
                "status": "unhealthy",
                "reachable": False,
                "configured": True,
                "probe": probe_url,
                "error": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except requests.RequestException as exc:
            if not isinstance(exc, requests.HTTPError):
                CartridgeCircuitBreaker.record_failure()
            return {
                "status": "error",
                "reachable": False,
                "configured": True,
                "probe": probe_url,
                "error": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }


    def list_tables(self) -> list[dict[str, Any]]:
        from app.services.catalog_service import get_all_entities
        return [
            {"id": e.get("entity"), "name": e.get("entity"),
             "description": e.get("description", "")}
            for e in get_all_entities() if e.get("entity")
        ]

    def get_table_schema(self, table_id: str) -> dict[str, Any]:
        from app.services.catalog_service import get_entity_config
        cfg = get_entity_config(table_id)
        if not cfg:
            return {"error": f"Entity '{table_id}' not in local catalog"}
        return {
            "entity": cfg.get("entity"),
            "fields": cfg.get("select_fields", []),
            "watermark_field": cfg.get("watermark_field"),
        }


    def fetch_entity(
        self,
        entity: str,
        select: list[str] | None = None,
        page_size: int = 500,
        skip: int = 0,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_configured()

        params: dict[str, Any] = {
            "$format": "json",
            "$top": page_size,
            "$skip": skip,
            "$inlinecount": "allpages",
            "sap-client": self.client_mandant,
        }
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr

        url = f"{self.base_url}/{entity}"
        try:
            CartridgeCircuitBreaker.before_request()
            logger.warning("SAP S/4HANA outbound GET %s", url)
            resp = self._session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=120,
            )
            self._log_auth(resp.status_code)
            if resp.status_code in {401, 403}:
                CartridgeCircuitBreaker.record_success()
                resp.raise_for_status()
            if resp.status_code >= 500 or resp.status_code == 429:
                CartridgeCircuitBreaker.record_failure()
                resp.raise_for_status()
            resp.raise_for_status()
            CartridgeCircuitBreaker.record_success()
            payload = resp.json()
        except CircuitBreakerOpen:
            raise
        except requests.RequestException as exc:
            if not isinstance(exc, requests.HTTPError):
                CartridgeCircuitBreaker.record_failure()
            raise SAPClientError(f"GET {url} failed: {exc}") from exc

        if isinstance(payload, dict) and "d" in payload:
            inner = payload["d"]
            if isinstance(inner, dict) and "results" in inner:
                return inner["results"]
            return [inner] if isinstance(inner, dict) else list(inner or [])
        if isinstance(payload, dict) and "value" in payload:
            return payload["value"]
        return [payload] if isinstance(payload, dict) else list(payload or [])
