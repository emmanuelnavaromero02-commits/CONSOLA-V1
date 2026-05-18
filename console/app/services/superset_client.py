"""Async Superset REST client used by Studio.

The MCP infra tools still expose Superset to the copilot. Studio needs a direct
backend client so the "Crear en Superset" button has a deterministic result and
can report configuration/auth failures without pretending success.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx


class SupersetConfigError(RuntimeError):
    pass


class SupersetRequestError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _raise_superset_error(response: httpx.Response, *, action: str) -> None:
    if response.status_code >= 400:
        raise SupersetRequestError(response.status_code, f"Superset {action} failed with HTTP {response.status_code}")


@dataclass
class SupersetClient:
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    timeout: float = 10.0
    transport: httpx.AsyncBaseTransport | None = None

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or os.environ.get("SUPERSET_URL") or "").rstrip("/")
        self.username = (
            self.username
            or os.environ.get("SUPERSET_SERVICE_USER")
            or os.environ.get("SUPERSET_ADMIN_USER")
            or os.environ.get("SUPERSET_USER")
        )
        self.password = (
            self.password
            or os.environ.get("SUPERSET_SERVICE_PASSWORD")
            or os.environ.get("SUPERSET_ADMIN_PASSWORD")
            or os.environ.get("SUPERSET_PASSWORD")
        )
        self._access_token: str | None = None
        self._csrf_token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    def require_configured(self) -> None:
        if not self.configured:
            raise SupersetConfigError(
                "Superset not configured: set SUPERSET_URL plus SUPERSET_SERVICE_USER/"
                "SUPERSET_SERVICE_PASSWORD or SUPERSET_ADMIN_USER/SUPERSET_ADMIN_PASSWORD"
            )

    async def login(self) -> dict[str, str]:
        self.require_configured()
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/security/login",
                json={
                    "username": self.username,
                    "password": self.password,
                    "provider": "db",
                    "refresh": True,
                },
            )
            _raise_superset_error(response, action="login")
            self._access_token = response.json()["access_token"]
            csrf = await client.get(
                f"{self.base_url}/api/v1/security/csrf_token/",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            _raise_superset_error(csrf, action="csrf")
            payload = csrf.json()
            self._csrf_token = (payload.get("result") or {}).get("csrf_token") or payload.get("csrf_token") or ""
        return {"access_token": self._access_token, "csrf_token": self._csrf_token or ""}

    async def _headers(self) -> dict[str, str]:
        if not self._access_token:
            await self.login()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-CSRFToken": self._csrf_token or "",
        }

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.require_configured()
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers=await self._headers(),
                        json=json,
                    )
                if response.status_code == 401 and attempt == 0:
                    self._access_token = None
                    self._csrf_token = None
                    continue
                if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return response
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt == 2:
                    raise SupersetRequestError(504, "Superset request timed out") from exc
                await asyncio.sleep(delay)
                delay *= 2
            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt == 2:
                    raise SupersetRequestError(503, "Superset connection failed") from exc
                await asyncio.sleep(delay)
                delay *= 2
        if last_exc:
            raise last_exc
        raise RuntimeError("Superset request failed without response")

    async def list_databases(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/v1/database/")
        _raise_superset_error(response, action="list databases")
        result = response.json().get("result", [])
        if isinstance(result, dict):
            result = result.get("data") or result.get("result") or []
        return [
            {
                "id": db.get("id"),
                "name": db.get("database_name") or db.get("name"),
                "backend": db.get("backend"),
            }
            for db in result if isinstance(db, dict)
        ]

    async def create_database(self, name: str, sqlalchemy_uri: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/v1/database/",
            json={"database_name": name, "sqlalchemy_uri": sqlalchemy_uri},
        )
        if response.status_code == 409:
            for db in await self.list_databases():
                if db.get("name") == name:
                    return {**db, "existing": True}
        _raise_superset_error(response, action="create database")
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else {}
        return {"id": payload.get("id") or (result or {}).get("id"), "name": name, "existing": False}

    async def list_datasets(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/v1/dataset/")
        _raise_superset_error(response, action="list datasets")
        result = response.json().get("result", [])
        if isinstance(result, dict):
            result = result.get("data") or result.get("result") or []
        return [
            {
                "id": ds.get("id"),
                "name": ds.get("table_name") or ds.get("name"),
                "schema": ds.get("schema"),
                "database_id": (ds.get("database") or {}).get("id") if isinstance(ds.get("database"), dict) else ds.get("database"),
            }
            for ds in result if isinstance(ds, dict)
        ]

    async def create_dataset(self, database_id: int, table_name: str, schema: str = "public") -> dict[str, Any]:
        payload = {"database": int(database_id), "schema": schema, "table_name": table_name}
        response = await self._request("POST", "/api/v1/dataset/", json=payload)
        if response.status_code == 409:
            for ds in await self.list_datasets():
                same_database = str(ds.get("database_id")) == str(database_id)
                if same_database and ds.get("name") == table_name and (ds.get("schema") or "public") == schema:
                    return {"dataset_id": ds.get("id"), "table": table_name, "schema": schema, "existing": True}
        _raise_superset_error(response, action="create dataset")
        data = response.json()
        result = data.get("result") if isinstance(data, dict) else {}
        return {
            "dataset_id": data.get("id") or (result or {}).get("id"),
            "table": table_name,
            "schema": schema,
            "existing": False,
        }


def client_from_env() -> SupersetClient:
    return SupersetClient()
