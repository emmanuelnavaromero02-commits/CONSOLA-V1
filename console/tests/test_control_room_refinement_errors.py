from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests.test_control_room_service import USER


class _FakeDatasetResponse:
    status_code = 200

    def json(self):
        return {
            "code": "source_files_missing",
            "error": "No hay archivos Parquet para la fuente seleccionada.",
        }


class _FakeDatasetClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def post(self, *_args, **_kwargs):
        return _FakeDatasetResponse()


@pytest.mark.asyncio
async def test_query_dataset_rows_treats_refinement_error_payload_as_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(control_room_service.httpx, "AsyncClient", _FakeDatasetClient)

    with pytest.raises(HTTPException) as exc:
        await control_room_service.query_dataset_rows("pnl_mensual", USER)

    assert exc.value.status_code == 503
    assert "No hay archivos Parquet" in str(exc.value.detail)
