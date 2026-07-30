from __future__ import annotations

import pytest

from airflow.dags.dataset_refresh_outcome import (
    require_saved_pipeline_response,
    require_successful_intelligence_response,
    require_successful_materialization_response,
)


class _Response:
    def __init__(
        self, status: int, payload=None, *, json_error: Exception | None = None
    ):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def test_intelligence_response_requires_typed_complete_success():
    body = require_successful_intelligence_response(
        _Response(
            200,
            {
                "ok": True,
                "status": "completed",
                "run_ref": "gold-refresh:e2e",
                "intelligence_run_id": "run-1",
                "signals": 1,
            },
        )
    )

    assert body["ok"] is True
    assert body["signals"] == 1


@pytest.mark.parametrize(
    "response",
    [
        _Response(200, {"ok": False, "status": "failed"}),
        _Response(200, {"ok": True, "status": "completed", "signals": 0}),
        _Response(200, {"ok": True, "signals": 1}),
        _Response(200, {"status": "completed", "signals": 1}),
        _Response(200, ["partial"]),
        _Response(500, {"ok": True, "status": "completed", "signals": 1}),
        _Response(200, json_error=ValueError("invalid json")),
    ],
)
def test_intelligence_response_fails_closed(response):
    with pytest.raises(RuntimeError, match="intelligence outcome unavailable"):
        require_successful_intelligence_response(response)


def test_materialization_response_requires_durable_receipt():
    body = require_successful_materialization_response(
        _Response(
            200,
            {
                "name": "pnl_mensual",
                "layer": "gold",
                "row_count": 3,
                "storage_uri": "s3://lakehouse/gold/pnl.parquet",
            },
        ),
        expected_name="pnl_mensual",
    )
    assert body["row_count"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False},
        {"error": "failed"},
        {"name": "other", "layer": "gold", "row_count": 1, "storage_uri": "s3://x"},
        {
            "name": "pnl_mensual",
            "layer": "unknown",
            "row_count": 1,
            "storage_uri": "s3://x",
        },
        {
            "name": "pnl_mensual",
            "layer": "gold",
            "row_count": True,
            "storage_uri": "s3://x",
        },
        {"name": "pnl_mensual", "layer": "gold", "row_count": 1},
    ],
)
def test_materialization_response_fails_closed(payload):
    with pytest.raises(RuntimeError, match="materialization outcome unavailable"):
        require_successful_materialization_response(
            _Response(200, payload), expected_name="pnl_mensual"
        )


def test_pipeline_registry_requires_exact_saved_receipt():
    body = require_saved_pipeline_response(
        _Response(
            200,
            {
                "result": {
                    "saved": True,
                    "run_id": "run-1",
                    "status": "running",
                }
            },
        ),
        expected_run_id="run-1",
        expected_status="running",
    )
    assert body["saved"] is True

    for response in (
        _Response(200, {"ok": False}),
        _Response(200, {"saved": True, "run_id": "run-1", "status": "running"}),
        _Response(
            200,
            {"result": {"saved": True, "run_id": "other", "status": "running"}},
        ),
        _Response(
            200,
            {"result": {"saved": True, "run_id": "run-1", "status": "partial"}},
        ),
        _Response(
            500,
            {"result": {"saved": True, "run_id": "run-1", "status": "running"}},
        ),
        _Response(200, json_error=ValueError("private response")),
    ):
        with pytest.raises(RuntimeError, match="pipeline registry unavailable"):
            require_saved_pipeline_response(
                response, expected_run_id="run-1", expected_status="running"
            )
