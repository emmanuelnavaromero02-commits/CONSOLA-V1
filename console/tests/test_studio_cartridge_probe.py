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


def _console_main():
    import importlib

    return importlib.import_module("app.main")


def _stub_manifest(monkeypatch, main):
    async def get_cartridge(cartridge_id):
        return {"id": cartridge_id, "name": cartridge_id}

    monkeypatch.setattr(main.cartridge_service, "get_cartridge", get_cartridge)


@pytest.mark.asyncio
async def test_status_probes_the_cartridge_service_from_the_operations_health_map(monkeypatch):
    main = _console_main()
    _stub_manifest(monkeypatch, main)
    monkeypatch.setenv("REPLICON_URL", "http://replicon-probe.test:8201")
    probed = []

    async def fake_probe(base_url, cartridge_id):
        probed.append((base_url, cartridge_id))
        return {"status": "offline", "reason": "/health HTTP 503"}

    monkeypatch.setattr(main, "_probe_microservice", fake_probe)

    result = await main.studio_cartridge_status("replicon", user=None)

    assert probed == [("http://replicon-probe.test:8201", "replicon")]
    assert result == {"cartridge_id": "replicon", "status": "offline", "reason": "/health HTTP 503"}


@pytest.mark.asyncio
async def test_status_reports_registered_when_the_cartridge_has_no_service(monkeypatch):
    main = _console_main()
    _stub_manifest(monkeypatch, main)

    async def forbidden_probe(*_args, **_kwargs):
        raise AssertionError("a cartridge without its own service must not be probed")

    monkeypatch.setattr(main, "_probe_microservice", forbidden_probe)

    result = await main.studio_cartridge_status("banxico", user=None)

    assert result == {
        "cartridge_id": "banxico",
        "status": "registered",
        "detail": "sin servicio propio que sondear",
    }
    assert result["status"] != "operational"
