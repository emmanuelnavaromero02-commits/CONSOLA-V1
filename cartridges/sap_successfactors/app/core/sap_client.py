"""
SAP SuccessFactors OData v2 client.

Auth: OAuth2 client_credentials per SAP docs:
  https://help.sap.com/docs/successfactors-platform/sap-successfactors-platform/oauth-token-authentication

If credentials are missing the client REFUSES to fetch and returns a
structured "degraded" error - it never invents data.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.auth_factory import auth_trace, build_auth_headers
from app.core.config import settings
from app.core.settings_proxy import get_setting
from app.core.vault_client import get_connection_for_worker, get_secret_for_worker

logger = logging.getLogger(__name__)
# urllib3 retry logger is noisy by default; INFO surfaces retries
# without flooding DEBUG output during normal operation.
logging.getLogger("urllib3.util.retry").setLevel(logging.INFO)

CLIENT_CREDENTIALS_AUTH_METHODS = {"oauth2", "oauth2_client_credentials", "client_credentials"}
SAML_BEARER_AUTH_METHODS = {"saml_bearer_assertion", "saml2_bearer", "oauth2_saml_bearer"}
SAML_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:saml2-bearer"
DEFAULT_SF_PRIVATE_KEY_PATH = "/run/secrets/sf_epiuse_iaappliance_connector.pem"
SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
ET.register_namespace("saml", SAML_NS)
ET.register_namespace("ds", DSIG_NS)


def _canonicalize(element: ET.Element) -> bytes:
    xml = ET.tostring(element, encoding="unicode")
    return ET.canonicalize(xml, strip_text=False).encode("utf-8")



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


    def __init__(self) -> None:
        self._session = _make_retry_session(self._RETRY_MAX, self._RETRY_BACKOFF_FACTOR)
        self._vault_connection = get_connection_for_worker("sap_successfactors")
        self.base_url = (
            get_secret_for_worker("sap_successfactors", "SF_BASE_URL")
            or get_setting("sap_successfactors_base_url", default=settings.sf_base_url, env_fallback="SF_BASE_URL")
            or ""
        ).rstrip("/")
        self.token_url = (
            get_secret_for_worker("sap_successfactors", "SF_TOKEN_URL")
            or get_setting("sap_successfactors_token_url", default=settings.sf_token_url, env_fallback="SF_TOKEN_URL")
        )
        self.client_id = (
            get_secret_for_worker("sap_successfactors", "SF_CLIENT_ID")
            or get_setting("sap_successfactors_client_id", default=settings.sf_client_id, env_fallback="SF_CLIENT_ID")
        )
        self.client_secret = (
            get_secret_for_worker("sap_successfactors", "SF_CLIENT_SECRET")
            or get_setting(
                "sap_successfactors_client_secret",
                default=settings.sf_client_secret,
                env_fallback="SF_CLIENT_SECRET",
            )
        )
        self.company_id = (
            get_secret_for_worker("sap_successfactors", "SF_COMPANY_ID")
            or get_setting("sap_successfactors_company_id", default=settings.sf_company_id, env_fallback="SF_COMPANY_ID")
        )
        self.auth_method = str(
            get_secret_for_worker("sap_successfactors", "SF_AUTH_METHOD")
            or self._vault_connection.get("auth_method")
            or get_setting(
                "sap_successfactors_auth_method",
                default=settings.sf_auth_method,
                env_fallback="SF_AUTH_METHOD",
            )
            or "oauth2_client_credentials",
        ).strip().lower().replace("-", "_")
        self.admin_user = (
            get_secret_for_worker("sap_successfactors", "SF_ADMIN_USER")
            or self._vault_connection.get("admin_user")
            or get_setting(
                "sap_successfactors_admin_user",
                default=settings.sf_admin_user,
                env_fallback="SF_ADMIN_USER",
            )
            or ""
        )
        self.private_key_path = (
            get_secret_for_worker("sap_successfactors", "SF_PRIVATE_KEY_PATH")
            or self._vault_connection.get("private_key_path")
            or get_setting(
                "sap_successfactors_private_key_path",
                default=settings.sf_private_key_path or DEFAULT_SF_PRIVATE_KEY_PATH,
                env_fallback="SF_PRIVATE_KEY_PATH",
            )
            or DEFAULT_SF_PRIVATE_KEY_PATH
        )
        self._private_key_pem = (
            get_secret_for_worker("sap_successfactors", "SF_PRIVATE_KEY_PEM")
            or self._vault_connection.get("private_key_pem")
            or os.getenv("SF_PRIVATE_KEY_PEM")
        )
        static_token = (
            get_secret_for_worker("sap_successfactors", "SF_ACCESS_TOKEN")
            or get_secret_for_worker("sap_successfactors", "SF_API_KEY")
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
            assertion = self._build_saml_bearer_assertion()
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

    def _load_saml_private_key(self):
        pem = self._private_key_pem.encode("utf-8") if self._private_key_pem else None
        if pem is None:
            path = Path(self.private_key_path)
            try:
                pem = path.read_bytes()
            except OSError as exc:
                raise SAPClientError(f"SAML bearer private key is not readable at {path}") from exc
        try:
            return serialization.load_pem_private_key(pem, password=None)
        except ValueError as exc:
            raise SAPClientError("SAML bearer private key is not a valid PEM private key") from exc

    def _build_saml_bearer_assertion(self) -> str:
        now = int(time.time())
        expires_at = now + 300
        assertion_id = f"_{uuid.uuid4().hex}"

        assertion = ET.Element(
            f"{{{SAML_NS}}}Assertion",
            {
                "ID": assertion_id,
                "Version": "2.0",
                "IssueInstant": str(now),
            },
        )
        issuer = ET.SubElement(assertion, f"{{{SAML_NS}}}Issuer")
        issuer.text = self.client_id

        subject = ET.SubElement(assertion, f"{{{SAML_NS}}}Subject")
        name_id = ET.SubElement(subject, f"{{{SAML_NS}}}NameID")
        name_id.text = self.admin_user
        subject_confirmation = ET.SubElement(
            subject,
            f"{{{SAML_NS}}}SubjectConfirmation",
            {"Method": "urn:oasis:names:tc:SAML:2.0:cm:bearer"},
        )
        ET.SubElement(
            subject_confirmation,
            f"{{{SAML_NS}}}SubjectConfirmationData",
            {
                "NotOnOrAfter": str(expires_at),
                "Recipient": self.token_url,
            },
        )

        conditions = ET.SubElement(assertion, f"{{{SAML_NS}}}Conditions", {"NotOnOrAfter": str(expires_at)})
        audience_restriction = ET.SubElement(conditions, f"{{{SAML_NS}}}AudienceRestriction")
        audience = ET.SubElement(audience_restriction, f"{{{SAML_NS}}}Audience")
        audience.text = self.token_url

        digest_value = base64.b64encode(hashlib.sha256(_canonicalize(assertion)).digest()).decode("ascii")
        signature = ET.Element(f"{{{DSIG_NS}}}Signature")
        signed_info = ET.SubElement(signature, f"{{{DSIG_NS}}}SignedInfo")
        ET.SubElement(
            signed_info,
            f"{{{DSIG_NS}}}CanonicalizationMethod",
            {"Algorithm": "http://www.w3.org/2006/12/xml-c14n11"},
        )
        ET.SubElement(
            signed_info,
            f"{{{DSIG_NS}}}SignatureMethod",
            {"Algorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"},
        )
        reference = ET.SubElement(signed_info, f"{{{DSIG_NS}}}Reference", {"URI": f"#{assertion_id}"})
        transforms = ET.SubElement(reference, f"{{{DSIG_NS}}}Transforms")
        ET.SubElement(transforms, f"{{{DSIG_NS}}}Transform", {"Algorithm": "http://www.w3.org/2000/09/xmldsig#enveloped-signature"})
        ET.SubElement(
            reference,
            f"{{{DSIG_NS}}}DigestMethod",
            {"Algorithm": "http://www.w3.org/2001/04/xmlenc#sha256"},
        )
        digest = ET.SubElement(reference, f"{{{DSIG_NS}}}DigestValue")
        digest.text = digest_value

        private_key = self._load_saml_private_key()
        signature_bytes = private_key.sign(
            _canonicalize(signed_info),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        signature_value = ET.SubElement(signature, f"{{{DSIG_NS}}}SignatureValue")
        signature_value.text = base64.b64encode(signature_bytes).decode("ascii")
        assertion.insert(1, signature)
        return base64.b64encode(ET.tostring(assertion, encoding="utf-8", xml_declaration=True)).decode("ascii")

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
            resp = self._session.get(
                f"{self.base_url}/$metadata",
                headers=self._headers(),
                params={"$format": "json"},
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
