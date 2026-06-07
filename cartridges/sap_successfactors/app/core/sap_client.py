"""
SAP SuccessFactors OData v2 client.

Auth: OAuth2 client_credentials per SAP docs:
  https://help.sap.com/docs/successfactors-platform/sap-successfactors-platform/oauth-token-authentication

If credentials are missing the client REFUSES to fetch and returns a
structured "degraded" error - it never invents data.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.auth_factory import auth_trace, build_auth_headers
from app.core.config import settings
from app.core.settings_proxy import get_setting
from app.core.vault_client import _candidate_fields, get_connection_for_worker, get_secret_for_worker

logger = logging.getLogger(__name__)
# urllib3 retry logger is noisy by default; INFO surfaces retries
# without flooding DEBUG output during normal operation.
logging.getLogger("urllib3.util.retry").setLevel(logging.INFO)

CLIENT_CREDENTIALS_AUTH_METHODS = {"oauth2", "oauth2_client_credentials", "client_credentials"}
SAML_BEARER_AUTH_METHODS = {"saml_bearer_assertion", "saml2_bearer", "oauth2_saml_bearer"}
SAML_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:saml2-bearer"
DEFAULT_SF_PRIVATE_KEY_PATH = "/run/secrets/sf_epiuse_iaappliance_connector.pem"
_PEM_ARMOR_RE = re.compile(r"-----BEGIN [^-]+-----|-----END [^-]+-----")


def _normalize_config_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {'""', "''"}:
        return ""
    return text


def _get_setting_or_env(key: str, *, default: str = "", env_fallback: str | None = None) -> str:
    value = _normalize_config_value(get_setting(key, default="", env_fallback=None))
    if value:
        return value
    if env_fallback:
        value = _normalize_config_value(os.getenv(env_fallback))
        if value:
            return value
    return _normalize_config_value(default)


def _successfactors_idp_private_key_payload(private_key_text: str) -> str:
    """Return the private_key form expected by SuccessFactors /oauth/idp.

    The endpoint expects the raw base64 key body on one line. Users commonly
    paste a full PEM block into Vault; preserve already-raw values while
    stripping PEM armor and whitespace when present.
    """
    text = _normalize_config_value(private_key_text)
    if "-----BEGIN " in text or "-----END " in text:
        text = _PEM_ARMOR_RE.sub("", text)
    return re.sub(r"\s+", "", text)


def _normalize_odata_base_url(base_url: str) -> str:
    """Return the SuccessFactors OData v2 service root.

    Vault connections often store the tenant host root
    (https://apiXX.sales.successfactors.com). The OData metadata and entity
    APIs live under /odata/v2; preserve already-explicit paths so custom
    deployments are not rewritten unexpectedly.
    """
    url = _normalize_config_value(base_url).rstrip("/")
    if not url:
        return ""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    if not path:
        return urlunsplit((parsed.scheme, parsed.netloc, "/odata/v2", "", ""))
    if path.lower().endswith("/odata/v2"):
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return url



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
            raise CircuitBreakerOpen("sap_successfactors circuit breaker is UNHEALTHY")

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


class SapSfClient:
    """SuccessFactors OData v2 client with OAuth2 auth variants."""

    CARTRIDGE_ID = "sap_successfactors"
    REQUIRED_ENV = ("sf_base_url", "sf_client_id", "sf_client_secret", "sf_token_url", "sf_company_id")

    # Sprint v1.17: exponential-retry session config (see _make_retry_session).
    _RETRY_MAX = 3
    _RETRY_BACKOFF_FACTOR = 2.0


    def __init__(self, conn_id: str | None = None, security_context: str | None = None) -> None:
        self._session = _make_retry_session(self._RETRY_MAX, self._RETRY_BACKOFF_FACTOR)
        self._conn_id = (conn_id or "").strip() or None
        self._security_context = (security_context or "").strip() or None
        self._vault_connection = get_connection_for_worker(
            "sap_successfactors",
            conn_id=self._conn_id,
            security_context=self._security_context,
        )

        def vault_connection_secret(env_var_name: str) -> str:
            for field in _candidate_fields(env_var_name):
                value = _normalize_config_value(self._vault_connection.get(field))
                if value:
                    return value
            return ""

        def worker_secret(env_var_name: str) -> str:
            # An explicit Vault connection is selected by the user and must win
            # over container-level defaults such as SF_AUTH_METHOD. Those env
            # defaults remain the fallback for legacy/default flows.
            if self._conn_id:
                value = vault_connection_secret(env_var_name)
                if value:
                    return value
            return get_secret_for_worker(
                "sap_successfactors",
                env_var_name,
                conn_id=self._conn_id,
                security_context=self._security_context,
            )

        self.base_url = _normalize_odata_base_url(
            worker_secret("SF_BASE_URL")
            or _get_setting_or_env(
                "sap_successfactors_base_url",
                default=settings.sf_base_url,
                env_fallback="SF_BASE_URL",
            )
            or ""
        )
        self.token_url = (
            worker_secret("SF_TOKEN_URL")
            or _get_setting_or_env(
                "sap_successfactors_token_url",
                default=settings.sf_token_url,
                env_fallback="SF_TOKEN_URL",
            )
        )
        self.idp_url = (
            worker_secret("SF_IDP_URL")
            or self._vault_connection.get("idp_url")
            or _get_setting_or_env(
                "sap_successfactors_idp_url",
                default=settings.sf_idp_url,
                env_fallback="SF_IDP_URL",
            )
            or self._derive_idp_url(self.token_url)
        )
        self.client_id = (
            worker_secret("SF_CLIENT_ID")
            or _get_setting_or_env(
                "sap_successfactors_client_id",
                default=settings.sf_client_id,
                env_fallback="SF_CLIENT_ID",
            )
        )
        self.client_secret = (
            worker_secret("SF_CLIENT_SECRET")
            or _get_setting_or_env(
                "sap_successfactors_client_secret",
                default=settings.sf_client_secret,
                env_fallback="SF_CLIENT_SECRET",
            )
        )
        self.company_id = (
            worker_secret("SF_COMPANY_ID")
            or _get_setting_or_env(
                "sap_successfactors_company_id",
                default=settings.sf_company_id,
                env_fallback="SF_COMPANY_ID",
            )
        )
        self.auth_method = str(
            worker_secret("SF_AUTH_METHOD")
            or self._vault_connection.get("auth_method")
            or _get_setting_or_env(
                "sap_successfactors_auth_method",
                default=settings.sf_auth_method,
                env_fallback="SF_AUTH_METHOD",
            )
            or "oauth2_client_credentials",
        ).strip().lower().replace("-", "_")
        self.admin_user = (
            worker_secret("SF_ADMIN_USER")
            or self._vault_connection.get("admin_user")
            or _get_setting_or_env(
                "sap_successfactors_admin_user",
                default=settings.sf_admin_user,
                env_fallback="SF_ADMIN_USER",
            )
            or ""
        )
        self.private_key_path = (
            worker_secret("SF_PRIVATE_KEY_PATH")
            or self._vault_connection.get("private_key_path")
            or _get_setting_or_env(
                "sap_successfactors_private_key_path",
                default=settings.sf_private_key_path or DEFAULT_SF_PRIVATE_KEY_PATH,
                env_fallback="SF_PRIVATE_KEY_PATH",
            )
            or DEFAULT_SF_PRIVATE_KEY_PATH
        )
        self._private_key_pem = (
            worker_secret("SF_PRIVATE_KEY_PEM")
            or self._vault_connection.get("private_key_pem")
            or os.getenv("SF_PRIVATE_KEY_PEM")
        )
        static_token = (
            worker_secret("SF_ACCESS_TOKEN")
            or worker_secret("SF_API_KEY")
        )
        self._auth_payload = {
            **self._vault_connection,
            "auth_method": self.auth_method,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "admin_user": self.admin_user,
            "private_key_path": self.private_key_path,
            "token": static_token or self._vault_connection.get("token"),
            "access_token": static_token or self._vault_connection.get("access_token"),
            "api_key": static_token or self._vault_connection.get("api_key"),
        }
        self._token: str | None = None
        self._token_expires_at = 0.0

    # ------------------------------------------------------------------
    # Configuration / introspection
    # ------------------------------------------------------------------

    def configuration_status(self) -> dict[str, Any]:
        required = {"SF_BASE_URL": self.base_url}
        if self.auth_method in CLIENT_CREDENTIALS_AUTH_METHODS:
            required.update({
                "SF_CLIENT_ID": self.client_id,
                "SF_CLIENT_SECRET": self.client_secret,
                "SF_TOKEN_URL": self.token_url,
                "SF_COMPANY_ID": self.company_id,
            })
        elif self.auth_method in SAML_BEARER_AUTH_METHODS:
            key_available = bool(self._private_key_pem) or bool(self.private_key_path and Path(self.private_key_path).is_file())
            required.update({
                "SF_CLIENT_ID": self.client_id,
                "SF_TOKEN_URL": self.token_url,
                "SF_IDP_URL": self.idp_url,
                "SF_COMPANY_ID": self.company_id,
                "SF_ADMIN_USER": self.admin_user,
                "SF_PRIVATE_KEY_PATH_OR_PEM": key_available,
            })
        elif self.auth_method in {"bearer", "bearer_token", "token"}:
            required["SF_ACCESS_TOKEN"] = self._auth_payload.get("token") or self._auth_payload.get("access_token")
        elif self.auth_method in {"api_key", "apikey", "x_api_key"}:
            required["SF_API_KEY"] = self._auth_payload.get("api_key") or self._auth_payload.get("token")
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
            raise SAPClientError(
                f"sap_successfactors not configured; missing env: {status['missing']}"
            )

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_idp_url(token_url: str) -> str:
        token_url = (token_url or "").strip()
        if not token_url:
            return ""
        parsed = urlsplit(token_url)
        path = parsed.path.rstrip("/")
        if path.endswith("/oauth/token"):
            idp_path = path[: -len("/token")] + "/idp"
        elif path.endswith("/token"):
            idp_path = path[: -len("/token")] + "/idp"
        else:
            idp_path = f"{path}/oauth/idp" if path else "/oauth/idp"
        return urlunsplit((parsed.scheme, parsed.netloc, idp_path, "", ""))

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        if self.auth_method in SAML_BEARER_AUTH_METHODS:
            return self._get_saml_bearer_token()
        return self._get_client_credentials_token()

    def _get_client_credentials_token(self) -> str:
        self._require_configured()
        try:
            CartridgeCircuitBreaker.before_request()
            logger.warning("SAP SuccessFactors outbound POST %s", self.token_url)
            logger.warning(
                "%s",
                auth_trace("oauth2_client_credentials", ("Authorization",)),
            )
            resp = self._session.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "company_id": self.company_id,
                },
                auth=(self.client_id, self.client_secret),
                headers={"Accept": "application/json"},
                timeout=30,
            )
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
            raise SAPClientError(f"OAuth token request failed: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            raise SAPClientError(f"OAuth response missing access_token: {payload}")
        expires_in = int(payload.get("expires_in", 3600))
        self._token = token
        self._token_expires_at = time.time() + expires_in - 60
        return token

    def _get_saml_bearer_token(self) -> str:
        self._require_configured()
        try:
            CartridgeCircuitBreaker.before_request()
            assertion = self._request_saml_assertion_from_successfactors()
            logger.warning("SAP SuccessFactors outbound POST %s", self.token_url)
            logger.warning("%s", auth_trace("oauth2_saml_bearer_assertion", ("assertion",)))
            resp = self._session.post(
                self.token_url,
                data={
                    "grant_type": SAML_BEARER_GRANT_TYPE,
                    "company_id": self.company_id,
                    "client_id": self.client_id,
                    "assertion": assertion,
                },
                headers={"Accept": "application/json"},
                timeout=30,
            )
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
            raise SAPClientError(f"SAML bearer token request failed: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            raise SAPClientError(f"SAML bearer response missing access_token: {payload}")
        expires_in = int(payload.get("expires_in", 3600))
        self._token = token
        self._token_expires_at = time.time() + expires_in - 60
        return token

    def _load_saml_private_key_text(self) -> str:
        if self._private_key_pem:
            return str(self._private_key_pem)
        try:
            path = Path(self.private_key_path)
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SAPClientError(f"SAML bearer private key is not readable at {path}") from exc

    def _request_saml_assertion_from_successfactors(self) -> str:
        private_key = _successfactors_idp_private_key_payload(self._load_saml_private_key_text())
        logger.warning("SAP SuccessFactors outbound POST %s", self.idp_url)
        logger.warning("%s", auth_trace("successfactors_oauth_idp", ("private_key",)))
        resp = self._session.post(
            self.idp_url,
            data={
                "client_id": self.client_id,
                "user_id": self.admin_user,
                "token_url": self.token_url,
                "private_key": private_key,
            },
            headers={"Accept": "text/plain, application/json"},
            timeout=30,
        )
        if not resp.ok:
            headers = dict(resp.headers)
            for sensitive_header in ("set-cookie", "Set-Cookie", "authorization", "Authorization"):
                if sensitive_header in headers:
                    headers[sensitive_header] = "***REDACTED***"
            logger.error(
                "SAP SuccessFactors /oauth/idp non-2xx status=%s headers=%s body=%s",
                resp.status_code,
                headers,
                resp.text,
            )
        if resp.status_code in {401, 403}:
            CartridgeCircuitBreaker.record_success()
            resp.raise_for_status()
        if resp.status_code >= 500 or resp.status_code == 429:
            CartridgeCircuitBreaker.record_failure()
            resp.raise_for_status()
        resp.raise_for_status()
        assertion = self._extract_saml_assertion_response(resp)
        if not assertion:
            raise SAPClientError("SuccessFactors /oauth/idp response missing SAML assertion")
        return assertion

    @staticmethod
    def _extract_saml_assertion_response(resp: requests.Response) -> str:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "json" in content_type:
            payload = resp.json()
            if isinstance(payload, dict):
                assertion = payload.get("assertion") or payload.get("saml_assertion") or payload.get("SAMLAssertion")
                return str(assertion or "").strip()
        return resp.text.strip()

    def _headers(self) -> dict[str, str]:
        if self.auth_method in CLIENT_CREDENTIALS_AUTH_METHODS | SAML_BEARER_AUTH_METHODS:
            return {
                "Authorization": f"Bearer {self._get_token()}",
                "Accept": "application/json",
            }
        headers, _, _ = build_auth_headers(
            self._auth_payload,
            default_method="bearer_token",
            default_api_key_header="X-API-Key",
            base_headers={"Accept": "application/json"},
        )
        return headers

    def _log_auth(self, status_code: int | None = None) -> None:
        if self.auth_method in CLIENT_CREDENTIALS_AUTH_METHODS:
            method, header_names = "oauth2_client_credentials", ("Authorization",)
        elif self.auth_method in SAML_BEARER_AUTH_METHODS:
            method, header_names = "oauth2_saml_bearer_assertion", ("Authorization",)
        else:
            _, method, header_names = build_auth_headers(
                self._auth_payload,
                default_method="bearer_token",
                default_api_key_header="X-API-Key",
                base_headers={"Accept": "application/json"},
            )
        suffix = f" -> Respuesta del servidor {status_code}" if status_code is not None else ""
        logger.warning("%s%s", auth_trace(method, header_names), suffix)

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}
        try:
            CartridgeCircuitBreaker.before_request()
            logger.warning("SAP SuccessFactors outbound GET %s", f"{self.base_url}/$metadata")
            headers = dict(self._headers())
            headers["Accept"] = "application/xml, text/xml, */*"
            resp = self._session.get(
                f"{self.base_url}/$metadata",
                headers=headers,
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
                    "http_status": resp.status_code,
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
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except CircuitBreakerOpen as exc:
            return {
                "status": "unhealthy",
                "reachable": False,
                "configured": True,
                "base_url": self.base_url,
                "error": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except SAPClientError as exc:
            text = str(exc)
            auth_error = "401" in text or "403" in text
            return {
                "status": "auth_error" if auth_error else "error",
                "reachable": auth_error,
                "configured": True,
                "base_url": self.base_url,
                "error": text,
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except requests.RequestException as exc:
            if not isinstance(exc, requests.HTTPError):
                CartridgeCircuitBreaker.record_failure()
            return {
                "status": "error",
                "reachable": False,
                "configured": True,
                "error": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }

    # ------------------------------------------------------------------
    # Discovery (uses local catalog as the source of truth)
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
        page_size: int = 200,
        skip: int = 0,
        filter_expr: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """OData v2 GET with pagination. Refuses to run if not configured."""
        self._require_configured()

        params: dict[str, Any] = {
            "$format": "json",
            "$top": page_size,
            "$skip": skip,
        }
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date

        url = f"{self.base_url}/{entity}"
        try:
            CartridgeCircuitBreaker.before_request()
            logger.warning("SAP SuccessFactors outbound GET %s", url)
            resp = self._session.get(url, params=params, headers=self._headers(), timeout=120)
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
