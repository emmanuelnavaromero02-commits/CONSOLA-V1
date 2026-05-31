from __future__ import annotations

from .sap_hcm_adapter import SapHcmAdapter


class SapS4hanaAdapter(SapHcmAdapter):
    cartridge_id = "sap_s4hana"
