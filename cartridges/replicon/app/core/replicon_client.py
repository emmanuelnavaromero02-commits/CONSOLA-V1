from __future__ import annotations

import io
import logging
import time
from urllib.parse import urlsplit
from typing import Any

import pandas as pd
import requests

from app.core.config import settings
from app.core.auth_factory import auth_trace, build_auth_headers
from app.core.vault_client import get_replicon_connection

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY = 1.0  # doubles each attempt: 1, 2, 4, 8, 16 s
logger = logging.getLogger(__name__)


class CircuitBreakerOpen(RuntimeError):
    pass


class CartridgeCircuitBreaker:
    failures = 0
    threshold = 3
    state = "HEALTHY"

    @classmethod
    def before_request(cls) -> None:
        if cls.failures >= cls.threshold:
            cls.state = "UNHEALTHY"
            raise CircuitBreakerOpen("replicon circuit breaker is UNHEALTHY")

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


class RepliconClient:
    """
    Client for the Replicon Analytics BI API.

    Authentication: dynamic Auth Factory from the Vault connection
    ``auth_method`` (bearer_token, api_key, basic, none).

    Extract flow (async):
      1. POST /extracts  →  { extractId }
      2. Poll GET /extracts/{extractId} until status = "completed"
      3. dataUrls is a dict { tableId: csv_url } — download each URL
      4. Parse CSV → list[dict]
    """

    def __init__(self, security_context: str | None = None, conn_id: str | None = None) -> None:
        connection = get_replicon_connection(security_context=security_context, conn_id=conn_id)
        self.base_url = str(connection.get("base_url") or "").rstrip("/")
        self._auth_connection = connection
        self._conn_id = (conn_id or "").strip()
        self._auth_method = str(connection.get("auth_method") or "").strip().lower()

        if not self.base_url:
            raise EnvironmentError("Replicon base_url is required (set env or Vault connection)")

    def _is_seeded_gold_connection(self) -> bool:
        return self._auth_method == "seeded_gold" or self.base_url.startswith("seeded://")

    # ------------------------------------------------------------------
    # Auth header
    # ------------------------------------------------------------------

    @property
    def _auth_headers(self) -> dict[str, str]:
        headers, _, _ = build_auth_headers(
            self._auth_connection,
            default_method="bearer_token",
            default_api_key_header="X-API-Key",
            base_headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        return headers

    def _log_auth(self, status_code: int | None = None) -> None:
        _, method, header_names = build_auth_headers(
            self._auth_connection,
            default_method="bearer_token",
            default_api_key_header="X-API-Key",
            base_headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        suffix = f" -> Respuesta del servidor {status_code}" if status_code is not None else ""
        logger.warning("%s%s", auth_trace(method, header_names), suffix)

    # ------------------------------------------------------------------
    # HTTP helpers with retry / backoff
    # ------------------------------------------------------------------

    def _get(self, path: str, timeout: int = 60) -> requests.Response:
        CartridgeCircuitBreaker.before_request()
        url = f"{self.base_url}{path}"
        delay = _RETRY_BASE_DELAY
        last_exc: Exception | None = None

        for _ in range(_RETRY_ATTEMPTS):
            try:
                logger.warning("Replicon outbound GET %s", url)
                resp = requests.get(url, headers=self._auth_headers, timeout=timeout)
                self._log_auth(resp.status_code)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                time.sleep(delay); delay *= 2
                continue

            if resp.status_code in _RETRYABLE_STATUSES:
                time.sleep(float(resp.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 60)
                last_exc = requests.exceptions.HTTPError(response=resp)
                continue

            if resp.status_code in {401, 403}:
                CartridgeCircuitBreaker.record_success()
                resp.raise_for_status()
            if resp.status_code >= 400:
                if resp.status_code >= 500:
                    CartridgeCircuitBreaker.record_failure()
                else:
                    CartridgeCircuitBreaker.record_success()
                resp.raise_for_status()
            CartridgeCircuitBreaker.record_success()
            resp.raise_for_status()
            return resp

        CartridgeCircuitBreaker.record_failure()
        raise last_exc or RuntimeError(f"GET {url} failed after {_RETRY_ATTEMPTS} attempts")

    def _post(self, path: str, body: dict, timeout: int = 60) -> requests.Response:
        CartridgeCircuitBreaker.before_request()
        url = f"{self.base_url}{path}"
        delay = _RETRY_BASE_DELAY
        last_exc: Exception | None = None

        for _ in range(_RETRY_ATTEMPTS):
            try:
                logger.warning("Replicon outbound POST %s", url)
                resp = requests.post(url, headers=self._auth_headers, json=body, timeout=timeout)
                self._log_auth(resp.status_code)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                time.sleep(delay); delay *= 2
                continue

            if resp.status_code in _RETRYABLE_STATUSES:
                time.sleep(float(resp.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 60)
                last_exc = requests.exceptions.HTTPError(response=resp)
                continue

            if resp.status_code in {401, 403}:
                CartridgeCircuitBreaker.record_success()
                resp.raise_for_status()
            if resp.status_code >= 400:
                if resp.status_code >= 500:
                    CartridgeCircuitBreaker.record_failure()
                else:
                    CartridgeCircuitBreaker.record_success()
                resp.raise_for_status()
            CartridgeCircuitBreaker.record_success()
            resp.raise_for_status()
            return resp

        CartridgeCircuitBreaker.record_failure()
        raise last_exc or RuntimeError(f"POST {url} failed after {_RETRY_ATTEMPTS} attempts")

    # ------------------------------------------------------------------
    # Tables discovery
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        return self._get("/tables").json()

    def get_table_schema(self, table_id: str) -> dict[str, Any]:
        return self._get(f"/tables/{table_id}").json()

    # ------------------------------------------------------------------
    # Async extract → CSV download
    # ------------------------------------------------------------------

    def _create_extract(self, table_ids: list[str]) -> str:
        body = {
            "target": {"type": "download", "format": "csv"},
            "tables": [{"tableId": tid} for tid in table_ids],
        }
        resp = self._post("/extracts", body)
        extract_id = resp.json().get("extractId")
        if not extract_id:
            raise RuntimeError(f"No extractId returned: {resp.text[:200]}")
        return extract_id

    def _poll_extract(self, extract_id: str) -> dict[str, Any]:
        """Poll until completed or failed. dataUrls is a dict {tableId: url}."""
        deadline = time.monotonic() + settings.replicon_poll_timeout
        while time.monotonic() < deadline:
            data = self._get(f"/extracts/{extract_id}").json()
            status = data.get("status")
            if status == "completed":
                return data
            if status == "failed":
                raise RuntimeError(f"Extract {extract_id} failed: {data}")
            time.sleep(settings.replicon_poll_interval)

        raise TimeoutError(
            f"Extract {extract_id} did not complete within {settings.replicon_poll_timeout}s"
        )

    def _download_csv(self, url: str) -> pd.DataFrame:
        """Download a pre-signed S3 CSV URL (no auth header needed)."""
        delay = _RETRY_BASE_DELAY
        last_exc: Exception | None = None

        for _ in range(_RETRY_ATTEMPTS):
            try:
                # S3 pre-signed URLs must NOT include the Authorization header
                logger.warning("Replicon outbound CSV download %s", url.split("?", 1)[0])
                resp = requests.get(url, timeout=120)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                time.sleep(delay); delay *= 2
                continue

            if resp.status_code in _RETRYABLE_STATUSES:
                time.sleep(float(resp.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 60)
                last_exc = requests.exceptions.HTTPError(response=resp)
                continue

            resp.raise_for_status()
            return pd.read_csv(io.StringIO(resp.text), low_memory=False)

        raise last_exc or RuntimeError(f"CSV download failed after {_RETRY_ATTEMPTS} attempts")

    # ------------------------------------------------------------------
    # Main extraction entry point
    # ------------------------------------------------------------------

    def extract_table(self, table_id: str) -> list[dict[str, Any]]:
        """
        Full extract of one Replicon table. Returns list[dict].

        dataUrls in the completed extract is a dict keyed by tableId:
          { "Project": "https://s3.amazonaws.com/..." }
        """
        extract_id = self._create_extract([table_id])
        result = self._poll_extract(extract_id)

        # dataUrls is {tableId: url}, not a list
        data_urls: dict[str, str] = result.get("dataUrls") or {}
        if not data_urls:
            return []

        frames: list[pd.DataFrame] = []
        for url in data_urls.values():
            df = self._download_csv(url)
            frames.append(df)

        if not frames:
            return []

        combined = pd.concat(frames, ignore_index=True)
        combined.columns = [
            c.strip().lower().replace(" ", "_").replace("-", "_")
            for c in combined.columns
        ]
        return combined.to_dict(orient="records")

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        if self._is_seeded_gold_connection():
            return {
                "status": "ok",
                "reachable": True,
                "data_only": True,
                "message": "Conexión de datos semilla activa; no requiere llamada a Replicon.",
                "base_url": self.base_url,
                **({"conn_id": self._conn_id} if self._conn_id else {}),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        try:
            tables = self.list_tables()
            return {
                "status": "ok",
                "reachable": True,
                "tables": len(tables),
                "base_url": self.base_url,
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except CircuitBreakerOpen as exc:
            return {
                "status": "unhealthy",
                "reachable": False,
                "error": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {401, 403}:
                return {
                    "status": "auth_error",
                    "reachable": True,
                    "http_status": status_code,
                    "base_url": self.base_url,
                    "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
                }
            return {
                "status": "error",
                "reachable": False,
                "http_status": status_code,
                "error": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }
        except requests.RequestException as exc:
            return {
                "status": "error",
                "reachable": False,
                "error": _request_error_message(exc, self.base_url),
                "base_url_host": _safe_host(self.base_url),
                **({"conn_id": self._conn_id} if self._conn_id else {}),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
            }


def _safe_host(url: str) -> str | None:
    host = urlsplit(str(url or "")).netloc
    return host or None


def _request_error_message(exc: requests.RequestException, base_url: str) -> str:
    text = str(exc)
    lowered = text.lower()
    if "name resolution" in lowered or "nodename nor servname provided" in lowered:
        host = _safe_host(base_url) or "configured host"
        return f"Replicon host could not be resolved by DNS: {host}"
    return text
