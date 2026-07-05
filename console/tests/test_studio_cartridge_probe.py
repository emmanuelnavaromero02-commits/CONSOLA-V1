from __future__ import annotations

import pytest

from app.domains.studio.cartridge_probe import probe_microservice


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict | None = None,
        *,
        text: str = "",
        content_type: str = "application/json",
    ):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {"content-type": content_type}

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._payload


class FakeClient:
    responses: list[FakeResponse] = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url: str) -> FakeResponse:
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_probe_microservice_marks_offline_when_liveness_fails():
    FakeClient.responses = [FakeResponse(status_code=503)]

    result = await probe_microservice(
        "http://sap-successfactors:8203",
        "sap_successfactors",
        http_client_factory=FakeClient,
    )

    assert result == {"status": "offline", "reason": "/health HTTP 503"}


@pytest.mark.asyncio
async def test_probe_microservice_marks_degraded_when_deep_check_fails():
    FakeClient.responses = [
        FakeResponse(status_code=200),
        FakeResponse(status_code=401, payload={"detail": "missing credentials"}),
    ]

    result = await probe_microservice(
        "http://sap-successfactors:8203",
        "sap_successfactors",
        http_client_factory=FakeClient,
        headers_factory=lambda: {"x-api-key": "test"},
    )

    assert result == {"status": "degraded", "reason": {"detail": "missing credentials"}}


@pytest.mark.asyncio
async def test_probe_microservice_marks_operational_with_deep_payload():
    FakeClient.responses = [
        FakeResponse(status_code=200),
        FakeResponse(status_code=200, payload={"credentials": "ok"}),
    ]

    result = await probe_microservice(
        "http://sap-successfactors:8203",
        "sap_successfactors",
        http_client_factory=FakeClient,
    )

    assert result == {"status": "operational", "credentials": "ok"}
