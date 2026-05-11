"""
SAP HCM client.

Talks to the on-premise SAP NetWeaver Gateway OData services exposed for
HCM / SAP_HR (e.g. /sap/opu/odata/sap/HRPA_SE_LEAVEREQUEST_SRV/, the
Employee Master service, etc).

Auth: HTTP Basic on every request. ``sap-client`` (mandant) is sent both as
header and query parameter for compatibility with NetWeaver.

If credentials are missing the client refuses to fetch and returns a
structured "degraded" status.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from app.core.config import settings

logger = logging.getLogger(__name__)


class SAPClientError(RuntimeError):
    pass


class SapHcmClient:
    """SAP HCM Gateway OData v2 client (Basic Auth)."""

    CARTRIDGE_ID = "sap_hcm"
    REQUIRED_ENV = ("sap_hcm_base_url", "sap_hcm_user", "sap_hcm_pass")

    def __init__(self) -> None:
        self.base_url = (settings.sap_hcm_base_url or "").rstrip("/")
        self.user = settings.sap_hcm_user
        self.password = settings.sap_hcm_pass
        self.client_mandant = settings.sap_hcm_client or "100"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configuration_status(self) -> dict[str, Any]:
        missing = [name.upper() for name in self.REQUIRED_ENV if not getattr(settings, name)]
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
                f"sap_hcm not configured; missing env: {status['missing']}"
            )

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.user, self.password)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "sap-client": self.client_mandant,
            "x-csrf-token": "fetch",
        }

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}
        try:
            resp = requests.get(
                f"{self.base_url}/$metadata",
                auth=self._auth(),
                headers=self._headers(),
                params={"sap-client": self.client_mandant},
                timeout=30,
            )
            resp.raise_for_status()
            return {
                "status": "ok",
                "configured": True,
                "base_url": self.base_url,
                "client": self.client_mandant,
            }
        except requests.RequestException as exc:
            return {"status": "error", "configured": True, "error": str(exc)}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

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
            "sap-client": self.client_mandant,
        }
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr

        url = f"{self.base_url}/{entity}"
        try:
            resp = requests.get(
                url,
                params=params,
                auth=self._auth(),
                headers=self._headers(),
                timeout=120,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise SAPClientError(f"GET {url} failed: {exc}") from exc

        if isinstance(payload, dict) and "d" in payload:
            inner = payload["d"]
            if isinstance(inner, dict) and "results" in inner:
                return inner["results"]
            return [inner] if isinstance(inner, dict) else list(inner or [])
        if isinstance(payload, dict) and "value" in payload:
            return payload["value"]
        return [payload] if isinstance(payload, dict) else list(payload or [])
