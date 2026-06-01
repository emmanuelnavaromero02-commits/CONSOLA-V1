from __future__ import annotations

import inspect
from typing import Any

from .base import BaseAdapter
from .sap_hcm_adapter import SapHcmAdapter


class SapHcmIt0008Adapter(BaseAdapter):
    cartridge_id = "sap_hcm"

    async def execute(self, action_data: dict[str, Any], credentials: dict[str, Any]):
        payload = {
            **action_data,
            "template_type": action_data.get("template_type") or "sap_hcm_it0008",
        }
        result = SapHcmAdapter().execute(payload, credentials, dry_run=False)
        if inspect.isawaitable(result):
            result = await result
        return result


class WriteBackAdapterFactory:
    _MAPPING: dict[str, type[BaseAdapter]] = {
        "prepare_hcm_access_review": SapHcmIt0008Adapter,
        "sap_hcm_it0008": SapHcmIt0008Adapter,
    }

    @classmethod
    def supports(cls, template_type: str) -> bool:
        return str(template_type or "") in cls._MAPPING

    @classmethod
    def has_adapter(cls, template_type: str) -> bool:
        return cls.supports(template_type)

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
