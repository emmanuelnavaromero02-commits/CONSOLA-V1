"""
Salesforce REST/SOQL client.

Auth: OAuth2. Supports three flows, picked from the Vault connection's
``auth_method`` (default username-password, the most common for server-to-server
Salesforce integrations):

  * ``oauth2_password`` / ``password``  → grant_type=password (user + token)
  * ``oauth2_client_credentials``       → grant_type=client_credentials
  * ``bearer`` / ``token``              → a static session/access token

Data is read through the SOQL Query REST resource
(``/services/data/<v>/query``) with cursor pagination via ``nextRecordsUrl``
(queryMore). The client maps Salesforce's cursor model onto the platform's
skip/page_size extraction loop: it buffers each REST batch and hands out
``page_size`` slices, refilling from ``nextRecordsUrl`` when the buffer drains.

If credentials are missing the client REFUSES to fetch and returns a
structured "degraded" status — it never invents data.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.auth_factory import auth_trace, build_auth_headers
from app.core.config import settings
from app.core.settings_proxy import get_setting
from app.core.vault_client import get_connection_for_worker, get_secret_for_worker

logger = logging.getLogger(__name__)
logging.getLogger("urllib3.util.retry").setLevel(logging.INFO)


def _make_retry_session(max_retries: int = 3, backoff_factor: float = 2.0) -> requests.Session:
    """requests.Session with exponential backoff for transient errors.

    Retries 429 (Salesforce rate limit / REQUEST_LIMIT_EXCEEDED) and
    500/502/503/504. Sleeps backoff_factor*(2**(n-1)) → 2s, 4s, 8s. Honors
    any ``Retry-After`` header the upstream returns.
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


class SalesforceClientError(RuntimeError):
    pass


def _odata_filter_to_soql(filter_expr: str | None) -> str | None:
    """Translate the platform's OData-style watermark filter to a SOQL WHERE.

    The extraction loop emits ``<field> gt '<value>'``. Salesforce SOQL wants
    datetime literals UNquoted (``LastModifiedDate > 2024-01-01T00:00:00Z``)
    and string literals quoted. We do a best-effort translation; the service
    re-applies the watermark client-side, so an imperfect server filter is safe.
    """
    if not filter_expr:
        return None
    ops = {" gt ": " > ", " ge ": " >= ", " lt ": " < ", " le ": " <= ", " eq ": " = "}
    expr = filter_expr
    for odata_op, soql_op in ops.items():
        expr = expr.replace(odata_op, soql_op)
    # Unquote ISO-8601 datetime literals (Salesforce requires them bare).
    # Leave non-datetime quoted strings as-is.
    import re

    def _unquote_datetime(match: "re.Match[str]") -> str:
        val = match.group(1)
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", val):
            return val
        # Escape embedded single quotes to prevent SOQL injection.
        return "'" + val.replace("'", "\\'") + "'"

    return re.sub(r"'([^']*)'", _unquote_datetime, expr)


class SalesforceClient:
    """Salesforce SOQL client over the REST Query API with OAuth2."""

    CARTRIDGE_ID = "salesforce"

    _RETRY_MAX = 3
    _RETRY_BACKOFF_FACTOR = 2.0

    def __init__(self, security_context: str | None = None) -> None:
        self._session = _make_retry_session(self._RETRY_MAX, self._RETRY_BACKOFF_FACTOR)
        self._security_context = (security_context or "").strip() or None
        self._vault_connection = get_connection_for_worker("salesforce", security_context=self._security_context)

        self.base_url = (
            get_secret_for_worker("salesforce", "SF_BASE_URL", security_context=self._security_context)
            or get_setting("salesforce_base_url", default=settings.sf_base_url, env_fallback="SF_BASE_URL")
            or ""
        ).rstrip("/")
        self.token_url = (
            get_secret_for_worker("salesforce", "SF_TOKEN_URL", security_context=self._security_context)
            or settings.sf_token_url
            or "https://login.salesforce.com/services/oauth2/token"
        )
        self.client_id = (
            get_secret_for_worker("salesforce", "SF_CLIENT_ID", security_context=self._security_context)
            or get_setting("salesforce_client_id", default=settings.sf_client_id, env_fallback="SF_CLIENT_ID")
        )
        self.client_secret = (
            get_secret_for_worker("salesforce", "SF_CLIENT_SECRET", security_context=self._security_context)
            or get_setting(
                "salesforce_client_secret",
                default=settings.sf_client_secret,
                env_fallback="SF_CLIENT_SECRET",
            )
        )
        self.username = (
            get_secret_for_worker("salesforce", "SF_USERNAME", security_context=self._security_context) or settings.sf_username
        )
        self.password = (
            get_secret_for_worker("salesforce", "SF_PASSWORD", security_context=self._security_context) or settings.sf_password
        )
        self.security_token = (
            get_secret_for_worker("salesforce", "SF_SECURITY_TOKEN", security_context=self._security_context)
            or settings.sf_security_token
        )
        self.api_version = (
            get_secret_for_worker("salesforce", "SF_API_VERSION", security_context=self._security_context)
            or settings.sf_api_version
            or "v60.0"
        )
        self.auth_method = str(
            self._vault_connection.get("auth_method") or "oauth2_password",
        ).strip().lower().replace("-", "_")
        static_token = (
            get_secret_for_worker("salesforce", "SF_ACCESS_TOKEN", security_context=self._security_context)
            or get_secret_for_worker("salesforce", "SF_API_KEY", security_context=self._security_context)
        )
        self._auth_payload = {
            **self._vault_connection,
            "auth_method": self.auth_method,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "token": static_token or self._vault_connection.get("token"),
            "access_token": static_token or self._vault_connection.get("access_token"),
        }
        self._static_token = static_token
        self._token: str | None = None
        self._token_expires_at = 0.0
        # Per-entity SOQL cursors: {entity: {"buffer": [...], "next": url|None}}
        self._cursors: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Configuration / introspection
    # ------------------------------------------------------------------

    def configuration_status(self) -> dict[str, Any]:
        required: dict[str, Any] = {"SF_BASE_URL": self.base_url}
        if self.auth_method in {"oauth2_password", "password", "oauth2"}:
            required.update({
                "SF_CLIENT_ID": self.client_id,
                "SF_CLIENT_SECRET": self.client_secret,
                "SF_USERNAME": self.username,
                "SF_PASSWORD": self.password,
            })
        elif self.auth_method in {"oauth2_client_credentials", "client_credentials"}:
            required.update({
                "SF_CLIENT_ID": self.client_id,
                "SF_CLIENT_SECRET": self.client_secret,
                "SF_TOKEN_URL": self.token_url,
            })
        elif self.auth_method in {"bearer", "bearer_token", "token"}:
            required["SF_ACCESS_TOKEN"] = self._static_token or self._auth_payload.get("token")
        missing = [name for name, value in required.items() if not value]
        return {
            "cartridge": self.CARTRIDGE_ID,
            "configured": not missing,
            "missing": missing,
            "base_url": self.base_url or None,
        }

    def _require_configured(self) -> None:
        status = self.configuration_status()
        if not status["configured"]:
            raise SalesforceClientError(
                f"salesforce not configured; missing env: {status['missing']}"
            )

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._static_token:
            return self._static_token
        if self._token and time.time() < self._token_expires_at:
            return self._token

        self._require_configured()
        if self.auth_method in {"oauth2_client_credentials", "client_credentials"}:
            data = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        elif self.auth_method in {"oauth2_password", "password", "oauth2"}:
            data = {
                "grant_type": "password",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "username": self.username,
                "password": f"{self.password}{self.security_token}",
            }
        else:
            # bearer / token methods must supply a static SF_ACCESS_TOKEN, which
            # is returned at the top of this method; reaching here means none was
            # configured — fail clearly instead of POSTing a malformed grant.
            raise SalesforceClientError(
                f"auth_method '{self.auth_method}' needs a static token "
                "(SF_ACCESS_TOKEN); none configured"
            )
        try:
            logger.warning("Salesforce outbound POST %s", self.token_url)
            logger.warning("%s", auth_trace(self.auth_method, ("Authorization",)))
            resp = self._session.post(
                self.token_url,
                data=data,
                headers={"Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise SalesforceClientError(f"OAuth token request failed: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            # Don't echo the body — a token response can carry refresh_token /
            # id_token / signed id even when access_token is absent.
            raise SalesforceClientError(
                f"OAuth response missing access_token (response keys: {sorted(payload.keys())})"
            )
        # Salesforce returns the org instance_url; prefer it for subsequent calls.
        instance_url = payload.get("instance_url")
        if instance_url:
            self.base_url = instance_url.rstrip("/")
        self._token = token
        # Salesforce tokens have no expires_in; refresh defensively each hour.
        self._token_expires_at = time.time() + 3600 - 60
        return token

    def _headers(self) -> dict[str, str]:
        if self.auth_method in {"bearer", "bearer_token", "token"} and self._static_token:
            headers, _, _ = build_auth_headers(
                self._auth_payload,
                default_method="bearer_token",
                base_headers={"Accept": "application/json"},
            )
            return headers
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}
        try:
            url = f"{self.base_url}/services/data/{self.api_version}/limits"
            logger.warning("Salesforce outbound GET %s", url)
            resp = self._session.get(url, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            return {"status": "ok", "configured": True, "base_url": self.base_url, "message": "Connection successful"}
        except requests.RequestException as exc:
            return {"status": "error", "configured": True, "error": str(exc)}

    # ------------------------------------------------------------------
    # Discovery (local catalog is the source of truth)
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
    # Fetch (SOQL Query API with cursor pagination)
    # ------------------------------------------------------------------

    def _build_soql(self, entity: str, select: list[str] | None, filter_expr: str | None) -> str:
        fields = ", ".join(select) if select else "FIELDS(STANDARD)"
        soql = f"SELECT {fields} FROM {entity}"
        where = _odata_filter_to_soql(filter_expr)
        if where:
            soql += f" WHERE {where}"
        if not select:
            # FIELDS(STANDARD) requires a bounded result set.
            soql += " LIMIT 200"
        return soql

    def _query(self, soql: str) -> dict[str, Any]:
        url = f"{self.base_url}/services/data/{self.api_version}/query?q={quote(soql)}"
        try:
            logger.warning("Salesforce outbound GET %s/query (SOQL)", self.api_version)
            resp = self._session.get(url, headers=self._headers(), timeout=120)
            if resp.status_code == 401:
                self._token = None
                self._token_expires_at = 0.0
                resp = self._session.get(url, headers=self._headers(), timeout=120)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise SalesforceClientError(f"SOQL query failed: {exc}") from exc

    def _query_more(self, next_url: str) -> dict[str, Any]:
        url = f"{self.base_url}{next_url}"
        try:
            resp = self._session.get(url, headers=self._headers(), timeout=120)
            if resp.status_code == 401:
                self._token = None
                self._token_expires_at = 0.0
                resp = self._session.get(url, headers=self._headers(), timeout=120)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise SalesforceClientError(f"SOQL queryMore failed: {exc}") from exc

    @staticmethod
    def _strip_attributes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop Salesforce's per-record ``attributes`` envelope (type/url)."""
        cleaned = []
        for rec in records:
            row = {k: v for k, v in rec.items() if k != "attributes"}
            cleaned.append(row)
        return cleaned

    def fetch_entity(
        self,
        entity: str,
        select: list[str] | None = None,
        page_size: int = 2000,
        skip: int = 0,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``page_size`` rows for ``entity``.

        ``skip == 0`` starts a fresh SOQL query; ``skip > 0`` continues the
        cursor opened on the first call (the platform loop increments skip by
        page_size each iteration). Returns ``[]`` when the cursor is exhausted.
        """
        self._require_configured()

        cursor = self._cursors.get(entity)
        if skip == 0 or cursor is None:
            payload = self._query(self._build_soql(entity, select, filter_expr))
            cursor = {
                "buffer": self._strip_attributes(payload.get("records", [])),
                "next": payload.get("nextRecordsUrl"),
            }
            self._cursors[entity] = cursor

        # Refill from queryMore until we can serve a page or the cursor ends.
        while len(cursor["buffer"]) < page_size and cursor["next"]:
            payload = self._query_more(cursor["next"])
            cursor["buffer"].extend(self._strip_attributes(payload.get("records", [])))
            cursor["next"] = payload.get("nextRecordsUrl")

        page = cursor["buffer"][:page_size]
        cursor["buffer"] = cursor["buffer"][page_size:]
        if not page and not cursor["next"]:
            self._cursors.pop(entity, None)
        return page
