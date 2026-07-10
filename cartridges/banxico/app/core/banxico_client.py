from __future__ import annotations

import random
import time
from typing import Any
from urllib.parse import urlencode

import requests

from app.core.rate_limit import WindowRateLimiter
from app.core.source_security import BASE_URL, sanitize_source_url, validate_url
from app.core.vault_client import resolve_banxico_token


class BanxicoClientError(RuntimeError):
    pass


class BanxicoRateLimitError(BanxicoClientError):
    def __init__(self, seconds_to_reset: int | None = None) -> None:
        super().__init__("Banxico rate limit exceeded")
        self.seconds_to_reset = seconds_to_reset


class MetadataDriftError(BanxicoClientError):
    pass


class BanxicoClient:
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
        ids = _series_path(series_ids)
        return self._request("metadata", f"/series/{ids}", {"locale": "es"})

    def get_observations(self, series_ids: list[str], from_date: str, to_date: str) -> dict[str, Any]:
        ids = _series_path(series_ids)
        path = f"/series/{ids}/datos/{from_date}/{to_date}"
        return self._request("historical", path, {"locale": "es"})

    def source_url(self, path: str, params: dict[str, str] | None = None) -> str:
        query = urlencode(params or {})
        return sanitize_source_url(f"{BASE_URL}{path}" + (f"?{query}" if query else ""))

    def _request(self, kind: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        limiter = self._metadata_limiter if kind == "metadata" else self._historical_limiter
        key = (path, tuple(sorted(params.items())))
        if key in self._cache:
            return self._cache[key]
        url = validate_url(f"{BASE_URL}{path}")
        headers = {"Accept": "application/json", "Bmx-Token": self._token}
        last_error: Exception | None = None
        for attempt in range(3):
            limiter.wait()
            try:
                response = self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=(5, 30),
                    allow_redirects=False,
                )
                payload = self._handle_response(response)
                self._cache[key] = payload
                return payload
            except BanxicoRateLimitError as exc:
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
        raise BanxicoClientError(_safe_error(last_error)) from last_error

    def _handle_response(self, response: requests.Response) -> dict[str, Any]:
        if 300 <= response.status_code < 400:
            raise BanxicoClientError("Banxico redirects are not allowed")
        if response.status_code == 400:
            seconds = _seconds_to_reset(response)
            if seconds is not None:
                raise BanxicoRateLimitError(seconds)
        if response.status_code >= 400:
            response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise BanxicoClientError("Banxico response is not valid JSON") from exc
        if not isinstance(payload, dict) or "bmx" not in payload:
            raise BanxicoClientError("Banxico response schema is incompatible")
        return payload


def _series_path(series_ids: list[str]) -> str:
    if not series_ids or len(series_ids) > 20:
        raise ValueError("Banxico supports 1 to 20 series per request")
    return ",".join(series_ids)


def _resolved_token(*, conn_id: str | None, security_context: str | None) -> str:
    return resolve_banxico_token(conn_id=conn_id, security_context=security_context)


def _retryable_status(status_code: int) -> bool:
    return status_code in {500, 502, 503, 504}


def _backoff(attempt: int) -> float:
    return min([1, 2, 4][attempt] + random.random() * 0.25, 5)


def _safe_error(exc: Exception | None) -> str:
    if exc is None:
        return "Banxico request failed"
    return type(exc).__name__


def _seconds_to_reset(response: requests.Response) -> int | None:
    header = response.headers.get("Bmx-secondsToReset")
    if header and header.isdigit():
        return int(header)
    try:
        value = response.json().get("error", {}).get("secondsToReset")
    except ValueError:
        return None
    return int(value) if isinstance(value, int) or str(value).isdigit() else None
