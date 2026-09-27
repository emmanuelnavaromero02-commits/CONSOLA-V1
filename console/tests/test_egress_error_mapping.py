from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import egress_guard, mcp_registry
from app.services.adapters import sap_hcm_adapter
from app.services.adapters.base import AdapterConfigurationError, AdapterExecutionError
from app.services.adapters.circuit_breaker import CartridgeCircuitBreaker


class _Pool:
    async def fetchrow(self, *_args):
        return {"url": "http://mcp-infra.invalid:8010", "category": "infra"}


@pytest.fixture
def mcp(monkeypatch):
    async def pool():
        return _Pool()

    monkeypatch.setattr(mcp_registry, "_get_pool", pool)
    monkeypatch.setattr(mcp_registry, "_validate_mcp_url", lambda url: None)
    monkeypatch.setattr(mcp_registry, "_headers_for", lambda *args: {})

    def respond_with(exc: BaseException):
        async def fake(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(mcp_registry.egress_guard, "pinned_request", fake)

    return respond_with


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc,status",
    [
        (egress_guard.EgressGuardError("response failed (ConnectionResetError)", request_dispatched=True), 502),
        (egress_guard.EgressGuardError("response exceeds size limit", request_dispatched=True), 502),
        (egress_guard.EgressResponseTimeout(), 504),
        (TimeoutError("egress deadline exceeded before sending"), 504),
        (egress_guard.EgressGuardError("mcp invoke URL resolved to a non-public address"), 403),
        (egress_guard.EgressGuardError("request header is not valid HTTP"), 403),
    ],
)
async def test_mcp_invoke_separates_policy_blocks_from_transport_failures(mcp, exc, status):
    mcp(exc)
    with pytest.raises(HTTPException) as error:
        await mcp_registry.invoke("infra", "some_tool", {}, security_context={})
    assert error.value.status_code == status
    if status != 403:
        assert "egress blocked" not in str(error.value.detail)


@pytest.fixture
def csrf(monkeypatch):
    CartridgeCircuitBreaker.reset("sap_hcm")
    yield
    CartridgeCircuitBreaker.reset("sap_hcm")


def _fetch_raises(monkeypatch, exc: BaseException) -> None:
    def fake(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(sap_hcm_adapter.egress_guard, "pinned_request_sync", fake)


def _execute() -> None:
    sap_hcm_adapter.SapHcmAdapter().execute(
        {"idempotency_key": "k"}, {"base_url": "https://sap.example", "token": "t"}, dry_run=False
    )


@pytest.mark.parametrize(
    "exc",
    [
        egress_guard.EgressGuardError("response failed (ConnectionResetError)", request_dispatched=True),
        egress_guard.EgressResponseTimeout(),
        ConnectionRefusedError("refused"),
        TimeoutError("egress deadline exceeded before sending"),
    ],
)
def test_csrf_transport_failures_trip_the_breaker(monkeypatch, csrf, exc):
    _fetch_raises(monkeypatch, exc)
    for _ in range(3):
        with pytest.raises(AdapterExecutionError) as error:
            _execute()
        assert error.value.status_code == 503
        assert error.value.outcome_ambiguous is False
    assert CartridgeCircuitBreaker.state("sap_hcm")["state"] == "UNHEALTHY"


def test_csrf_policy_blocks_stay_configuration_errors(monkeypatch, csrf):
    _fetch_raises(monkeypatch, egress_guard.EgressGuardError("SAP HCM CSRF URL host is not public"))
    for _ in range(4):
        with pytest.raises(AdapterConfigurationError):
            _execute()
    assert CartridgeCircuitBreaker.state("sap_hcm")["failures"] == 0
