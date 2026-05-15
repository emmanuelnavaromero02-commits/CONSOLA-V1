from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from app.core.config import settings

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY = 1.0  # doubles each attempt: 1, 2, 4, 8, 16 s


class RepliconClient:
    """
    Client for the Replicon Analytics BI API.

    Authentication: static Bearer token from REPLICON_TOKEN env var.

    Extract flow (async):
      1. POST /extracts  →  { extractId }
      2. Poll GET /extracts/{extractId} until status = "completed"
      3. dataUrls is a dict { tableId: csv_url } — download each URL
      4. Parse CSV → list[dict]
    """

    def __init__(self) -> None:
        self.base_url = settings.replicon_base_url.rstrip("/")
        self._token = settings.replicon_api_token

        if not settings.use_demo_data and not self._token:
            raise EnvironmentError("REPLICON_TOKEN is required (set in .env)")

    # ------------------------------------------------------------------
    # Auth header
    # ------------------------------------------------------------------

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # HTTP helpers with retry / backoff
    # ------------------------------------------------------------------

    def _get(self, path: str, timeout: int = 60) -> requests.Response:
        url = f"{self.base_url}{path}"
        delay = _RETRY_BASE_DELAY
        last_exc: Exception | None = None

        for _ in range(_RETRY_ATTEMPTS):
            try:
                resp = requests.get(url, headers=self._auth_headers, timeout=timeout)
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
            return resp

        raise last_exc or RuntimeError(f"GET {url} failed after {_RETRY_ATTEMPTS} attempts")

    def _post(self, path: str, body: dict, timeout: int = 60) -> requests.Response:
        url = f"{self.base_url}{path}"
        delay = _RETRY_BASE_DELAY
        last_exc: Exception | None = None

        for _ in range(_RETRY_ATTEMPTS):
            try:
                resp = requests.post(url, headers=self._auth_headers, json=body, timeout=timeout)
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
            return resp

        raise last_exc or RuntimeError(f"POST {url} failed after {_RETRY_ATTEMPTS} attempts")

    # ------------------------------------------------------------------
    # Tables discovery
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        if settings.use_demo_data:
            return [{"id": "Project", "name": "Project", "columns": []}]
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
        if settings.use_demo_data:
            return self._demo_rows(table_id)

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
    # Demo data
    # ------------------------------------------------------------------

    def _demo_rows(self, table_id: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        demo: dict[str, list[dict]] = {
            "User": [
                {"user_id": "U001", "login_name": "jsmith", "first_name": "John",
                 "last_name": "Smith", "department": "Engineering", "title": "Senior Engineer",
                 "cost_rate": 85.0, "billing_rate": 150.0, "is_active": True, "last_modified": now},
                {"user_id": "U002", "login_name": "mjones", "first_name": "Maria",
                 "last_name": "Jones", "department": "Consulting", "title": "Consultant",
                 "cost_rate": 70.0, "billing_rate": 130.0, "is_active": True, "last_modified": now},
            ],
            "Project": [
                {"project_id": "P001", "project_name": "ERP Implementation",
                 "client_id": "C001", "status": "InProgress",
                 "budget_amount": 500000.0, "budget_hours": 4000.0,
                 "start_date": "2024-01-01", "end_date": "2024-12-31", "last_modified": now},
                {"project_id": "P002", "project_name": "BI Dashboard",
                 "client_id": "C002", "status": "InProgress",
                 "budget_amount": 120000.0, "budget_hours": 800.0,
                 "start_date": "2024-03-01", "end_date": "2024-09-30", "last_modified": now},
            ],
            "TimeEntry": [
                {"entry_id": "T001", "user_id": "U001", "project_id": "P001",
                 "task_id": "TK001", "entry_date": today, "hours": 8.0,
                 "billable_status": "Billable", "approval_status": "Approved", "last_modified": now},
                {"entry_id": "T002", "user_id": "U002", "project_id": "P001",
                 "task_id": "TK001", "entry_date": today, "hours": 6.0,
                 "billable_status": "Billable", "approval_status": "Approved", "last_modified": now},
            ],
            "Task": [
                {"task_id": "TK001", "project_id": "P001", "task_name": "Analysis",
                 "status": "InProgress", "estimated_hours": 200.0,
                 "billable_type": "Billable", "last_modified": now},
            ],
            "Client": [
                {"client_id": "C001", "client_name": "Acme Corp",
                 "currency": "USD", "default_billing_rate": 150.0, "last_modified": now},
                {"client_id": "C002", "client_name": "Beta Industries",
                 "currency": "USD", "default_billing_rate": 130.0, "last_modified": now},
            ],
            "Invoice": [
                {"invoice_id": "INV001", "client_id": "C001", "project_id": "P001",
                 "invoice_date": "2024-03-01", "amount": 48000.0,
                 "status": "Unpaid", "due_date": "2024-03-31", "last_modified": now},
            ],
            "ResourceAssignment": [
                {"assignment_id": "A001", "project_id": "P001", "user_id": "U001",
                 "role": "Lead", "start_date": "2024-01-01", "end_date": "2024-12-31",
                 "allocated_hours": 2000.0, "last_modified": now},
            ],
            "ExpenseEntry": [
                {"expense_id": "E001", "user_id": "U001", "project_id": "P001",
                 "expense_date": today, "amount": 250.0, "category": "Travel",
                 "billable_status": "Billable", "last_modified": now},
            ],
        }
        return demo.get(table_id, [{"id": "demo-1", "last_modified": now}])

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        if settings.use_demo_data:
            return {"reachable": True, "tables": 1, "mode": "demo"}
        tables = self.list_tables()
        return {"reachable": True, "tables": len(tables), "base_url": self.base_url}
