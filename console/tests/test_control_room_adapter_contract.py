from __future__ import annotations

from types import SimpleNamespace

import pytest


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


def test_adapter_factory_does_not_advertise_unproven_erp_writebacks():
    from app.services.adapters import WriteBackAdapterFactory

    assert WriteBackAdapterFactory.supports("prepare_hcm_access_review") is True
    assert WriteBackAdapterFactory.supports("sap_hcm_it0008") is True

    for template_id in (
        "prepare_billing_review",
        "prepare_replicon_adjustment",
        "prepare_s4_revenue_review",
        "prepare_s4_business_partner_review",
        "prepare_s4_procurement_review",
        "prepare_successfactors_review",
        "prepare_successfactors_recruiting_review",
    ):
        assert WriteBackAdapterFactory.supports(template_id) is False
        with pytest.raises(NotImplementedError):
            WriteBackAdapterFactory.create(template_id)


def test_active_control_room_does_not_claim_billing_review_writeback_support(
    monkeypatch,
):
    from app.services import control_room_service

    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    template = control_room_service.ACTION_TEMPLATES["prepare_billing_review"]
    capability = control_room_service._writeback_capability(template)  # noqa: SLF001 - registry wiring test

    assert capability["mode"] == "external_writeback"
    assert capability["adapter_available"] is False
    assert capability["supported"] is False
    assert capability["status"] == "adapter_missing"
    assert capability["reason"] == "No hay adapter ERP aprobado para este template."


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
