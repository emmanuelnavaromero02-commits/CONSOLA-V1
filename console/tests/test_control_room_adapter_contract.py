from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest


def test_control_room_service_is_modular_core_alias():
    from app.services import control_room_service
    from app.services.control_room import core

    assert control_room_service is core
    assert hasattr(control_room_service, "execute_item")
    assert hasattr(control_room_service, "BaseAdapter")
    assert hasattr(control_room_service, "ControlRoomService")


def test_adapter_package_exports_runtime_contract():
    from app.services.adapters import (
        AdapterCircuitOpenError,
        AdapterExecutionError,
        BaseAdapter,
        WriteBackAdapterFactory,
    )

    assert issubclass(AdapterCircuitOpenError, AdapterExecutionError)
    assert hasattr(BaseAdapter, "execute")
    assert WriteBackAdapterFactory.supports("prepare_hcm_access_review") is True
    assert WriteBackAdapterFactory.has_adapter("prepare_hcm_access_review") is True


def test_adapter_factory_advertises_only_proven_hcm_and_replicon_writebacks():
    from app.services.adapters import WriteBackAdapterFactory

    for template_id in (
        "prepare_hcm_access_review",
        "sap_hcm_it0008",
        "prepare_billing_review",
        "prepare_replicon_adjustment",
    ):
        assert WriteBackAdapterFactory.supports(template_id) is True

    for template_id in (
        "prepare_s4_revenue_review",
        "prepare_s4_business_partner_review",
        "prepare_s4_procurement_review",
        "prepare_successfactors_review",
        "prepare_successfactors_recruiting_review",
    ):
        assert WriteBackAdapterFactory.supports(template_id) is False
        with pytest.raises(NotImplementedError):
            WriteBackAdapterFactory.create(template_id)


def test_active_control_room_claims_billing_review_only_when_external_flag_enabled(
    monkeypatch,
):
    from app.services import control_room_service

    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    template = control_room_service.ACTION_TEMPLATES["prepare_billing_review"]
    capability = control_room_service._writeback_capability(template)  # noqa: SLF001 - registry wiring test

    assert capability["mode"] == "external_writeback"
    assert capability["adapter_available"] is True
    assert capability["supported"] is False
    assert capability["status"] == "external_writeback_disabled"
    assert capability["reason"] == "Write-back ERP externo no habilitado en Control Room V1; usa ejecucion supervisada."

    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    capability = control_room_service._writeback_capability(template)  # noqa: SLF001 - registry wiring test
    assert capability["adapter_available"] is True
    assert capability["supported"] is True
    assert capability["status"] == "supported"
    assert capability["reason"] is None


def test_modular_control_room_matches_monolith_writeback_capability(monkeypatch):
    from app.services import control_room as modular_control_room
    from app.services import control_room_service

    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")

    for template_id in ("prepare_billing_review", "prepare_hcm_access_review"):
        template = control_room_service.ACTION_TEMPLATES[template_id]
        modular = modular_control_room._writeback_capability(template)  # noqa: SLF001 - drift guard
        monolith = control_room_service._writeback_capability(template)  # noqa: SLF001 - drift guard

        for key in (
            "supported",
            "mode",
            "target",
            "external",
            "adapter_available",
            "status",
            "reason",
        ):
            assert modular.get(key) == monolith.get(key)


@pytest.mark.asyncio
async def test_sap_hcm_factory_bridge_executes_it0008_with_live_semantics(monkeypatch):
    from app.services.adapters import factory

    calls: list[dict] = []

    class FakeSapHcmAdapter:
        def execute(self, action_data, credentials, dry_run=True):
            calls.append(
                {
                    "action_data": action_data,
                    "credentials": credentials,
                    "dry_run": dry_run,
                }
            )
            return SimpleNamespace(
                ok=True,
                message="SAP HCM ok",
                to_dict=lambda: {"ok": True, "message": "SAP HCM ok"},
            )

    monkeypatch.setattr(factory, "SapHcmAdapter", FakeSapHcmAdapter)

    adapter = factory.WriteBackAdapterFactory.create("prepare_hcm_access_review")
    assert (
        factory.WriteBackAdapterFactory.get_adapter(
            "prepare_hcm_access_review"
        ).__class__
        is adapter.__class__
    )
    result = await adapter.execute(
        {"item_id": "item-1"},
        {"base_url": "https://sap.example", "token": "secret"},
    )

    assert result.message == "SAP HCM ok"
    assert calls == [
        {
            "action_data": {"item_id": "item-1", "template_type": "sap_hcm_it0008"},
            "credentials": {"base_url": "https://sap.example", "token": "secret"},
            "dry_run": False,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template_id,target",
    [("prepare_hcm_access_review", "SapHcmAdapter"), ("prepare_billing_review", "RepliconAdapter")],
)
async def test_blocking_writeback_adapters_do_not_stall_the_event_loop(monkeypatch, template_id, target):
    from app.services.adapters import factory

    threads: list[int] = []

    class BlockingAdapter:
        def execute(self, action_data, credentials, dry_run=True):
            threads.append(threading.get_ident())
            time.sleep(0.5)
            return {"ok": True, "dry_run": dry_run}

    monkeypatch.setattr(factory, target, BlockingAdapter)
    adapter = factory.WriteBackAdapterFactory.create(template_id)
    gaps: list[float] = []

    async def ticker() -> None:
        last = time.monotonic()
        while len(gaps) < 200:
            await asyncio.sleep(0.02)
            gaps.append(time.monotonic() - last)
            last = time.monotonic()

    ticking = asyncio.create_task(ticker())
    result = await adapter.execute({"item_id": "item-1"}, {"base_url": "https://sap.example"})
    ticking.cancel()

    assert result == {"ok": True, "dry_run": False}
    assert threads and threading.get_ident() not in threads
    assert gaps and max(gaps) < 0.3


@pytest.mark.asyncio
async def test_run_adapter_awaits_async_adapters_on_the_loop():
    from app.services.adapters.base import run_adapter

    threads: list[int] = []

    class AsyncAdapter:
        async def execute(self, action_data, credentials, dry_run=True):
            threads.append(threading.get_ident())
            return {"async": True, "dry_run": dry_run}

    class BridgedAdapter:
        def execute(self, action_data, credentials, dry_run=True):
            return AsyncAdapter().execute(action_data, credentials, dry_run=dry_run)

    assert await run_adapter(AsyncAdapter().execute, {}, {}, dry_run=False) == {"async": True, "dry_run": False}
    assert await run_adapter(BridgedAdapter().execute, {}, {}, dry_run=False) == {"async": True, "dry_run": False}
    assert threads == [threading.get_ident(), threading.get_ident()]
