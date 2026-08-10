from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.main as console_main


USER = {
    "id": 7,
    "email": "emmanuelnavaromero02@gmail.com",
    "role": "super_admin",
    "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
    "active_tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
    "workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
    "active_workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
}


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payloads: list[dict] | dict, status_code: int = 200):
        self._payloads = list(payloads) if isinstance(payloads, list) else [payloads]
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def post(self, *_args, **_kwargs):
        payload = self._payloads.pop(0)
        return FakeResponse(payload, self._status_code)


class TimeoutClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def post(self, *_args, **_kwargs):
        raise console_main.httpx.ReadTimeout("slow dependency")


def _request() -> Request:
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/data/x", "headers": []}
    )
    request.state.user = USER
    return request


def _runtime_endpoint(path: str, method: str):
    return next(
        route.endpoint
        for route in console_main.app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    )


@pytest.mark.asyncio
async def test_api_data_preserves_gold_error_without_refinement_fallback(monkeypatch):
    from app.services.intelligence import gold_fetcher

    async def missing_gold(*_args, **_kwargs):
        raise HTTPException(404, "gold table missing")

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", missing_gold)

    def unexpected_refinement_client(**_kwargs):
        raise AssertionError(
            "the scoped Gold data API must not fall back to Refinement"
        )

    monkeypatch.setattr(
        console_main.httpx,
        "AsyncClient",
        unexpected_refinement_client,
    )

    with pytest.raises(HTTPException) as exc:
        await _runtime_endpoint("/api/data/{dataset}", "GET")(
            "sap_successfactors_employee_360",
            _request(),
            limit=20,
            user=USER,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "gold table missing"


@pytest.mark.asyncio
async def test_bronze_query_maps_refinement_error_payload_to_http(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "modecissions-lakehouse-783792")
    monkeypatch.setattr(
        console_main.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(
            {
                "code": "source_files_missing",
                "error": "No hay archivos Parquet para la fuente seleccionada.",
                "raw_error": "No files found that match the pattern s3://bucket/raw/sap_successfactors/PerPerson",
            }
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await _runtime_endpoint("/api/bronze/query", "POST")(
            {
                "sql": "select * from read_parquet('raw/sap_successfactors/PerPerson') limit 20"
            },
            user=USER,
        )

    assert exc.value.status_code == 404
    assert "Parquet" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_schema_maps_partition_error_payload_to_controlled_status(monkeypatch):
    monkeypatch.setenv("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "0")
    fake_client = FakeClient(
        [
            {
                "source": "raw/sap_successfactors/PerPerson",
                "error": "AccessDenied: not authorized",
            },
            {
                "source": "raw/sap_successfactors/PerPerson",
                "schema": [{"name": "userId", "type": "VARCHAR"}],
                "columns": [{"name": "userId", "type": "VARCHAR"}],
                "data": [],
            },
        ]
    )
    monkeypatch.setattr(
        console_main.httpx,
        "AsyncClient",
        lambda **_kwargs: fake_client,
    )

    payload = await _runtime_endpoint("/api/schema", "GET")(
        "raw/sap_successfactors/PerPerson", user=USER
    )

    assert payload["status"] == "partial"
    assert payload["message"] == "datos parciales"
    assert payload["errors"][0]["reason"] == "permission_denied"
    assert payload["partitions"]["status"] == "error"
    assert payload["preview"]["columns"][0]["name"] == "userId"
    assert payload["preview"]["data"] == []


@pytest.mark.asyncio
async def test_catalog_maps_refinement_error_payload_to_http(monkeypatch):
    async def fake_refinement(*_args, **_kwargs):
        return {"error": "Connection timeout while reading data_catalog"}

    monkeypatch.setattr(
        console_main,
        "_active_scoped_connection_cartridges",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(console_main, "_refinement_invoke", fake_refinement)

    with pytest.raises(HTTPException) as exc:
        await _runtime_endpoint("/api/catalog", "GET")(layer="gold", user=USER)

    assert exc.value.status_code == 503
    assert "timeout" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_refinement_transport_timeout_maps_to_503(monkeypatch):
    monkeypatch.setattr(
        console_main.httpx, "AsyncClient", lambda **_kwargs: TimeoutClient()
    )

    with pytest.raises(HTTPException) as exc:
        await console_main._refinement_invoke(
            "get_data_catalog", {"layer": "gold"}, user=USER
        )

    assert exc.value.status_code == 503
    assert "timed out" in str(exc.value.detail).lower()
    assert "get_data_catalog" in str(exc.value.detail)
