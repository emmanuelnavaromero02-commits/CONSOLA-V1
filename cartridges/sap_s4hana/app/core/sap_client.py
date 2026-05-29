"""
SAP S/4HANA client.

Talks to the SAP S/4HANA OData APIs exposed at /sap/opu/odata/sap/<API_NAME>/.
Examples (whitelisted in app/config/entities.yaml):

  /sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner
  /sap/opu/odata/sap/API_PRODUCT_SRV/A_Product
  /sap/opu/odata/sap/API_GLACCOUNTLINEITEM_SRV/YY1_GLAccountLineItem

Auth: HTTP Basic (technical user). For Cloud edition the same client also
works against the SAP API Hub trial host with an extra "APIKey" header,
which is honoured if SAP_S4_API_KEY is set.

If credentials are missing the client refuses to fetch and returns a
structured "degraded" status — it never invents data.

Environment variables (canonical):
    SAP_S4_BASE_URL, SAP_S4_USER, SAP_S4_PASS,
    SAP_S4_CLIENT_MANDANT (optional, default "100"),
    SAP_S4_API_KEY        (optional, SAP API Hub only).

Legacy short names (S4_BASE_URL, S4_USER, S4_PASS, S4_CLIENT_MANDANT,
S4_API_KEY) are still accepted via fallback in ``app.core.config``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.auth import HTTPBasicAuth

from app.core.config import settings
from app.core.vault_client import get_secret_for_worker

logger = logging.getLogger(__name__)
# urllib3 retry logger is noisy by default; INFO surfaces retries
# without flooding DEBUG output during normal operation.
logging.getLogger("urllib3.util.retry").setLevel(logging.INFO)



# Sprint v1.17: shared by the 3 SAP cartridges (no shared lib between
# cartridges → copied textually into each). Exponential backoff for
# transient errors so a 429 from a busy SAP mandant or a 503 during
# maintenance no longer kills the whole extraction DAG.
#
# Backoff schedule with the defaults: sleep before retry N is
#   backoff_factor * (2 ** (N - 1)) seconds
# i.e. 2s, 4s, 8s between attempts. urllib3 honors any `Retry-After`
# header the upstream returns and overrides the exponential schedule
# when one is present (respect_retry_after_header=True).
def _make_retry_session(max_retries: int = 3, backoff_factor: float = 2.0) -> requests.Session:
    """Return a requests.Session with exponential backoff for transient errors.

    Retries on 429 (rate limit), 500/502/503/504 (gateway). Sleeps are
    backoff_factor * (2 ** (n-1)) seconds between attempts: 2s, 4s, 8s
    with the defaults. Respects Retry-After header automatically.
    """
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session



class SAPClientError(RuntimeError):
    pass


class SapS4Client:
    """SAP S/4HANA OData v2 client (Basic Auth)."""

    CARTRIDGE_ID = "sap_s4hana"
    REQUIRED_ENV = ("sap_s4_base_url", "sap_s4_user", "sap_s4_pass")

    # Sprint v1.17: exponential-retry session config (see _make_retry_session).
    _RETRY_MAX = 3
    _RETRY_BACKOFF_FACTOR = 2.0


    def __init__(self) -> None:
        self._session = _make_retry_session(self._RETRY_MAX, self._RETRY_BACKOFF_FACTOR)
        self.base_url = (
            get_secret_for_worker("sap_s4hana", "SAP_S4_BASE_URL")
            or settings.sap_s4_base_url
            or ""
        ).rstrip("/")
        self.user = get_secret_for_worker("sap_s4hana", "SAP_S4_USER") or settings.sap_s4_user
        self.password = get_secret_for_worker("sap_s4hana", "SAP_S4_PASS") or settings.sap_s4_pass
        self.client_mandant = (
            get_secret_for_worker("sap_s4hana", "SAP_S4_CLIENT_MANDANT")
            or settings.sap_s4_client_mandant
            or "100"
        )
        self.api_key = (
            get_secret_for_worker("sap_s4hana", "SAP_S4_API_KEY")
            or settings.sap_s4_api_key
            or os.environ.get("S4_API_KEY", "")
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configuration_status(self) -> dict[str, Any]:
        required = {
            "SAP_S4_BASE_URL": self.base_url,
            "SAP_S4_USER": self.user,
            "SAP_S4_PASS": self.password,
        }
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

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.user, self.password)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "sap-client": self.client_mandant,
        }
        if self.api_key:
            headers["APIKey"] = self.api_key
        return headers

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        """Probe connectivity using the first catalogued entity's $metadata.

        S/4HANA exposes one ``$metadata`` document per OData service
        (``API_BUSINESS_PARTNER``, ``API_SALES_ORDER_SRV``, ...) — there is
        no single root metadata. We resolve a catalogued entity, derive its
        service path, and call ``<base>/<service>/$metadata``.
        """
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}

        # Pick a service path from the local catalogue. Fall back to the
        # generic root only if the catalogue is empty.
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
            # odata_entity is "<SERVICE>/<EntitySet>" — keep just the service segment
            probe_path = odata_path.split("/", 1)[0] if "/" in odata_path else odata_path
            break

        probe_url = f"{self.base_url}/{probe_path}/$metadata" if probe_path \
            else f"{self.base_url}/$metadata"

        try:
            resp = self._session.get(
                probe_url,
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
                "probe": probe_url,
                "client": self.client_mandant,
            }
        except requests.RequestException as exc:
            return {
                "status": "error",
                "configured": True,
                "probe": probe_url,
                "error": str(exc),
            }

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
            "$inlinecount": "allpages",
            "sap-client": self.client_mandant,
        }
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr

        url = f"{self.base_url}/{entity}"
        try:
            resp = self._session.get(
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
