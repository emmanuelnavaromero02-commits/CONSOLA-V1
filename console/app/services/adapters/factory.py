from __future__ import annotations

import inspect
from typing import Any

from .base import BaseAdapter
from app.services.adapter_idempotency import adapter_guarantees_idempotency
from .replicon_adapter import RepliconAdapter
from .sap_hcm_adapter import SapHcmAdapter


class SapHcmIt0008Adapter(BaseAdapter):
    cartridge_id = "sap_hcm"
    supports_idempotency = True

    async def execute(self, action_data: dict[str, Any], credentials: dict[str, Any]):
        payload = {
            **action_data,
            "template_type": action_data.get("template_type") or "sap_hcm_it0008",
        }
        result = SapHcmAdapter().execute(payload, credentials, dry_run=False)
        if inspect.isawaitable(result):
            result = await result
        return result


class RepliconWriteBackAdapter(BaseAdapter):
    cartridge_id = "replicon"
    supports_idempotency = True

    async def execute(self, action_data: dict[str, Any], credentials: dict[str, Any]):
        result = RepliconAdapter().execute(action_data, credentials, dry_run=False)
        if inspect.isawaitable(result):
            result = await result
        return result


class WriteBackAdapterFactory:
    _MAPPING: dict[str, type[BaseAdapter]] = {
        "prepare_hcm_access_review": SapHcmIt0008Adapter,
        "sap_hcm_it0008": SapHcmIt0008Adapter,
        "prepare_billing_review": RepliconWriteBackAdapter,
        "prepare_replicon_adjustment": RepliconWriteBackAdapter,
    }

    @classmethod
    def supports(cls, template_type: str) -> bool:
        return str(template_type or "") in cls._MAPPING

    @classmethod
    def has_adapter(cls, template_type: str) -> bool:
        return cls.supports(template_type)

    @classmethod
    def supports_idempotency(cls, template_type: str) -> bool:
        adapter_cls = cls._MAPPING.get(str(template_type or ""))
        return bool(
            adapter_cls and adapter_guarantees_idempotency(template_type, adapter_cls)
        )

    @classmethod
    def create(cls, template_type: str) -> BaseAdapter:
        adapter_cls = cls._MAPPING.get(str(template_type or ""))
        if not adapter_cls:
            raise NotImplementedError(
                f"No production write-back adapter registered for template {template_type!r}"
            )
        return adapter_cls()

    @classmethod
    def get_adapter(cls, template_type: str) -> BaseAdapter:
        return cls.create(template_type)
