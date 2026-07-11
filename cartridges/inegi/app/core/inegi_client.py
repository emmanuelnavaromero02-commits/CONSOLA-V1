from __future__ import annotations

import random
import time
from typing import Any

import requests

from app.core.rate_limit import WindowRateLimiter
from app.core.source_security import BASE_URL, sanitize_source_url, validate_url
from app.core.vault_client import resolve_inegi_token


class INEGIClientError(RuntimeError):
    pass


class INEGIRateLimitError(INEGIClientError):
    def __init__(self, seconds_to_reset: int | None = None) -> None:
        super().__init__("INEGI rate limit exceeded")
        self.seconds_to_reset = seconds_to_reset


class MetadataDriftError(INEGIClientError):
    pass


class INEGIClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        conn_id: str | None = None,
        security_context: str | None = None,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self._token = token or _resolved_token(conn_id=conn_id, security_context=security_context)
        self._session = session or requests.Session()
        self._sleep = sleep
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
        self._metadata_limiter = WindowRateLimiter(max_calls=20, window_seconds=60)
        self._historical_limiter = WindowRateLimiter(max_calls=30, window_seconds=300)

    def get_metadata(self, series_ids: list[str]) -> dict[str, Any]:
        metadata = []
        for indicator_id in _series_ids(series_ids):
            catalog = self._request("metadata", _catalog_path("CL_INDICATOR", indicator_id, self._token))
            latest = self._indicator(indicator_id, recent=True)
            series = _first_series(latest)
            metadata.append(
                {
                    "id": indicator_id,
                    "title": _catalog_description(catalog),
                    "unit_code": str(series.get("UNIT") or ""),
                    "frequency_code": str(series.get("FREQ") or ""),
                    "last_update": str(series.get("LASTUPDATE") or ""),
                    "source": str(series.get("SOURCE") or ""),
                    "raw_catalog": catalog,
                    "raw_indicator": series,
                }
            )
        return {"inegi": {"metadata": metadata}}

    def get_observations(self, series_ids: list[str], from_date: str, to_date: str) -> dict[str, Any]:
        series = []
        for indicator_id in _series_ids(series_ids):
            payload = self._indicator(indicator_id, recent=False)
            item = _first_series(payload)
            item["INDICADOR"] = str(item.get("INDICADOR") or indicator_id)
            series.append(item)
        return {"inegi": {"series": series, "from_date": from_date, "to_date": to_date}}

    def source_url(self, indicator_id: str, *, recent: bool = False) -> str:
        return sanitize_source_url(f"{BASE_URL}{_indicator_path(indicator_id, recent=recent, token='__TOKEN__')}")

    def _indicator(self, indicator_id: str, *, recent: bool) -> dict[str, Any]:
        return self._request("historical", _indicator_path(indicator_id, recent=recent, token=self._token))

    def _request(self, kind: str, path: str) -> dict[str, Any]:
        limiter = self._metadata_limiter if kind == "metadata" else self._historical_limiter
        cache_path = path.replace(self._token, "__TOKEN__")
        key = (cache_path, ())
        if key in self._cache:
            return self._cache[key]
        url = validate_url(f"{BASE_URL}{path}")
        headers = {"Accept": "application/json"}
        last_error: Exception | None = None
        for attempt in range(3):
            limiter.wait()
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    timeout=(5, 30),
                    allow_redirects=False,
                )
                payload = self._handle_response(response)
                self._cache[key] = payload
                return payload
            except INEGIRateLimitError as exc:
                last_error = exc
                self._sleep(min(exc.seconds_to_reset or _backoff(attempt), 60))
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                self._sleep(_backoff(attempt))
            except requests.HTTPError as exc:
                last_error = exc
                if not _retryable_status(exc.response.status_code):
                    break
                self._sleep(_backoff(attempt))
        raise INEGIClientError(_safe_error(last_error)) from last_error

    def _handle_response(self, response: requests.Response) -> dict[str, Any]:
        if 300 <= response.status_code < 400:
            raise INEGIClientError("INEGI redirects are not allowed")
        if response.status_code == 400:
            seconds = _seconds_to_reset(response)
            if seconds is not None:
                raise INEGIRateLimitError(seconds)
        if response.status_code >= 400:
            raise INEGIClientError(f"INEGI HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise INEGIClientError("INEGI response is not valid JSON") from exc
        if not isinstance(payload, dict) or not ("Series" in payload or "CODE" in payload):
            raise INEGIClientError("INEGI response schema is incompatible")
        return payload


def _series_ids(series_ids: list[str]) -> list[str]:
    if not series_ids or len(series_ids) > 20:
        raise ValueError("INEGI supports 1 to 20 indicators per request")
    return [str(item).strip() for item in series_ids if str(item).strip()]


def _indicator_path(indicator_id: str, *, recent: bool, token: str) -> str:
    latest = "true" if recent else "false"
    return f"/INDICATOR/{indicator_id}/es/00/{latest}/BISE/2.0/{token}?type=json"


def _catalog_path(catalog: str, indicator_id: str, token: str) -> str:
    return f"/{catalog}/{indicator_id}/es/BISE/2.0/{token}?type=json"


def _resolved_token(*, conn_id: str | None, security_context: str | None) -> str:
    return resolve_inegi_token(conn_id=conn_id, security_context=security_context)


def _retryable_status(status_code: int) -> bool:
    return status_code in {500, 502, 503, 504}


def _backoff(attempt: int) -> float:
    return min([1, 2, 4][attempt] + random.random() * 0.25, 5)


def _safe_error(exc: Exception | None) -> str:
    if exc is None:
        return "INEGI request failed"
    return type(exc).__name__


def _seconds_to_reset(response: requests.Response) -> int | None:
    header = response.headers.get("Retry-After")
    if header and header.isdigit():
        return int(header)
    try:
        value = response.json().get("error", {}).get("secondsToReset")
    except ValueError:
        return None
    return int(value) if isinstance(value, int) or str(value).isdigit() else None


def _first_series(payload: dict[str, Any]) -> dict[str, Any]:
    series = payload.get("Series")
    if isinstance(series, list) and series and isinstance(series[0], dict):
        return dict(series[0])
    raise INEGIClientError("INEGI response has no Series item")


def _catalog_description(payload: dict[str, Any]) -> str:
    code = payload.get("CODE")
    if isinstance(code, list) and code and isinstance(code[0], dict):
        return str(code[0].get("Description") or code[0].get("description") or "")
    return ""
