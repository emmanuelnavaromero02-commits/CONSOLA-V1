from __future__ import annotations

from .base import BaseAdapter
from .replicon_adapter import RepliconAdapter
from .sap_hcm_adapter import SapHcmAdapter
from .sap_s4hana_adapter import SapS4hanaAdapter
from .sap_successfactors_adapter import SapSuccessFactorsAdapter


class WriteBackAdapterFactory:
    _MAPPING: dict[str, type[BaseAdapter]] = {
        "prepare_replicon_adjustment": RepliconAdapter,
        "prepare_billing_review": RepliconAdapter,
        "prepare_hcm_access_review": SapHcmAdapter,
        "prepare_hcm_org_review": SapHcmAdapter,
        "prepare_sap_review": SapS4hanaAdapter,
        "prepare_s4_revenue_review": SapS4hanaAdapter,
        "prepare_s4_business_partner_review": SapS4hanaAdapter,
        "prepare_s4_procurement_review": SapS4hanaAdapter,
        "prepare_successfactors_review": SapSuccessFactorsAdapter,
        "prepare_successfactors_recruiting_review": SapSuccessFactorsAdapter,
    }

    @classmethod
    def supports(cls, template_type: str) -> bool:
        return str(template_type or "") in cls._MAPPING

    @classmethod
    def create(cls, template_type: str) -> BaseAdapter:
        adapter_cls = cls._MAPPING.get(str(template_type or ""))
        if not adapter_cls:
            raise NotImplementedError(f"No production write-back adapter registered for template {template_type!r}")
        return adapter_cls()
