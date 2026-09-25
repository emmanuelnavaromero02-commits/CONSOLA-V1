from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import operations_service


@pytest.mark.asyncio
async def test_list_migrations_returns_rows_as_dicts():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [
        {"filename": "00_schema.sql", "applied_at": datetime(2026, 1, 1), "checksum": None},
        {"filename": "21_system_settings.sql", "applied_at": datetime(2026, 5, 1), "checksum": None},
    ]
    with patch.object(operations_service.auth, "pool", return_value=mock_pool):
        result = await operations_service.list_migrations()
    assert len(result) == 2
    assert result[0]["filename"] == "00_schema.sql"
    sql = mock_pool.fetch.call_args[0][0]
    assert "schema_migrations" in sql
    assert "ORDER BY filename" in sql


@pytest.mark.asyncio
async def test_list_migrations_empty_table_returns_empty_list():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    with patch.object(operations_service.auth, "pool", return_value=mock_pool):
        result = await operations_service.list_migrations()
    assert result == []


@pytest.mark.asyncio
async def test_get_system_version_reads_repo_root_version_file():
    version = await operations_service.get_system_version()
    from pathlib import Path
    expected = Path("VERSION").read_text().strip()
    assert version == expected


@pytest.mark.asyncio
async def test_get_system_version_returns_unknown_when_no_file(monkeypatch):
    class StubPath:
        def __init__(self, *_a, **_kw): pass
        def exists(self): return False
        def resolve(self): return self
        @property
        def parent(self): return self
        def __truediv__(self, _other): return self
        def read_text(self):
            raise FileNotFoundError()
    monkeypatch.setattr(operations_service, "Path", StubPath)
    result = await operations_service.get_system_version()
    assert result == "unknown"


@pytest.mark.asyncio
async def test_probe_services_returns_one_entry_per_service():
    async def fake_probe(name, url):
        return {"name": name, "status": "down", "error": "ConnectError"}

    with patch.object(operations_service, "_probe_one", side_effect=fake_probe):
        result = await operations_service.probe_services()
    assert len(result) == len(operations_service.SERVICE_PROBES)
    names = {s["name"] for s in result}
    assert names == set(operations_service.SERVICE_PROBES.keys())


@pytest.mark.asyncio
async def test_probe_services_filters_cartridge_services_to_active_connections():
    async def fake_probe(name, url):
        return {"name": name, "status": "down", "error": "ConnectError"}

    with patch.object(operations_service, "_probe_one", side_effect=fake_probe):
        result = await operations_service.probe_services(active_cartridges={"sap_successfactors"})

    names = {s["name"] for s in result}
    assert "sap-successfactors" in names
    assert "replicon" not in names
    assert "hubspot" not in names
    assert "sap-hcm" not in names
    assert "sap-s4hana" not in names
    assert "sap-b1" not in names
    assert "console" in names
    assert "refinement" in names


@pytest.mark.asyncio
async def test_probe_one_marks_console_up_on_401():
    mock_resp = type("R", (), {"status_code": 401})()
    async def fake_get(self, url): return mock_resp
    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await operations_service._probe_one(
            "console", "http://console:8000/api/system/info"
        )
    assert result["status"] == "up"
    assert result["code"] == 401


@pytest.mark.asyncio
async def test_probe_one_marks_other_service_up_on_200():
    mock_resp = type("R", (), {"status_code": 200})()
    async def fake_get(self, url): return mock_resp
    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await operations_service._probe_one("workspace", "http://workspace:8001/healthz")
    assert result["status"] == "up"


@pytest.mark.asyncio
async def test_probe_one_marks_service_down_on_500():
    mock_resp = type("R", (), {"status_code": 500})()
    async def fake_get(self, url): return mock_resp
    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await operations_service._probe_one("vault", "http://vault:8002/healthz")
    assert result["status"] == "down"
    assert result["code"] == 500


@pytest.mark.asyncio
async def test_probe_one_handles_connection_error():
    async def fake_get(self, url): raise ConnectionError("refused")
    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await operations_service._probe_one("airflow", "http://airflow:8080/health")
    assert result["status"] == "down"
    assert result["error"] == "ConnectionError"
