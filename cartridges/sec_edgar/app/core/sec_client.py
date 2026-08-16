from __future__ import annotations

import random
import time
from typing import Any

import requests

from app.core.egress_guard import guarded_session
from app.core.rate_limit import WindowRateLimiter
from app.core.source_security import BASE_URL, sanitize_source_url, validate_url
from app.core.vault_client import resolve_user_agent


class SECClientError(RuntimeError):
    pass


class MetadataDriftError(SECClientError):
    pass


class SECClient:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        conn_id: str | None = None,
        security_context: str | None = None,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self._user_agent = user_agent or resolve_user_agent(conn_id=conn_id, security_context=security_context)
        self._session = session or guarded_session()
        self._sleep = sleep
        self._cache: dict[str, dict[str, Any]] = {}
        self._limiter = WindowRateLimiter(max_calls=8, window_seconds=1)

    def get_metadata(self, ciks: list[str]) -> dict[str, Any]:
        companies = []
        for cik in _ciks(ciks):
            payload = self._request(_submissions_path(cik))
            companies.append(
                {
                    "cik": _cik10(str(payload.get("cik") or cik)),
                    "name": str(payload.get("name") or ""),
                    "tickers": payload.get("tickers") or [],
                    "exchanges": payload.get("exchanges") or [],
                    "entity_type": str(payload.get("entityType") or ""),
                    "sic": str(payload.get("sic") or ""),
                    "sic_description": str(payload.get("sicDescription") or ""),
                    "fiscal_year_end": str(payload.get("fiscalYearEnd") or ""),
                    "raw_submission": payload,
                }
            )
        return {"sec_edgar": {"metadata": companies}}

    def get_company_facts(self, ciks: list[str]) -> dict[str, Any]:
        companies = []
        for cik in _ciks(ciks):
            payload = self._request(_companyfacts_path(cik))
            companies.append({"cik": _cik10(cik), "raw_facts": payload})
        return {"sec_edgar": {"companies": companies}}

    def source_url(self, cik: str, *, kind: str) -> str:
        path = _companyfacts_path(cik) if kind == "companyfacts" else _submissions_path(cik)
        return sanitize_source_url(f"{BASE_URL}{path}")

    def _request(self, path: str) -> dict[str, Any]:
        if path in self._cache:
            return self._cache[path]
        url = validate_url(f"{BASE_URL}{path}")
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": self._user_agent,
        }
        last_error: Exception | None = None
        for attempt in range(3):
            self._limiter.wait()
            try:
                response = self._session.get(url, headers=headers, timeout=(5, 30), allow_redirects=False)
                payload = self._handle_response(response)
                self._cache[path] = payload
                return payload
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                self._sleep(_backoff(attempt))
            except requests.HTTPError as exc:
                last_error = exc
                if not _retryable_status(exc.response.status_code):
                    break
                self._sleep(_retry_after(exc.response) or _backoff(attempt))
        raise SECClientError(_safe_error(last_error)) from last_error

    def _handle_response(self, response: requests.Response) -> dict[str, Any]:
        if 300 <= response.status_code < 400:
            raise SECClientError("SEC EDGAR redirects are not allowed")
        if response.status_code >= 400:
            raise requests.HTTPError(f"SEC EDGAR HTTP {response.status_code}", response=response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise SECClientError("SEC EDGAR response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SECClientError("SEC EDGAR response schema is incompatible")
        return payload


def _ciks(ciks: list[str]) -> list[str]:
    clean = [_cik10(item) for item in ciks if str(item).strip()]
    if not clean or len(clean) > 10:
        raise ValueError("SEC EDGAR supports 1 to 10 CIKs per request")
    return clean


def _cik10(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits or len(digits) > 10:
        raise ValueError("invalid SEC CIK")
    return digits.zfill(10)


def _submissions_path(cik: str) -> str:
    return f"/submissions/CIK{_cik10(cik)}.json"


def _companyfacts_path(cik: str) -> str:
    return f"/api/xbrl/companyfacts/CIK{_cik10(cik)}.json"


def _retryable_status(status_code: int) -> bool:
    return status_code in {429, 500, 502, 503, 504}


def _retry_after(response: requests.Response) -> float | None:
    header = response.headers.get("Retry-After")
    return float(header) if header and header.isdigit() else None


def _backoff(attempt: int) -> float:
    return min([1, 2, 4][attempt] + random.random() * 0.25, 5)


def _safe_error(exc: Exception | None) -> str:
    return type(exc).__name__ if exc else "SEC EDGAR request failed"
