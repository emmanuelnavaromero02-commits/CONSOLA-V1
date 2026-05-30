from __future__ import annotations

from .http_writeback import HttpWriteBackAdapter


class SapSuccessFactorsAdapter(HttpWriteBackAdapter):
    cartridge_id = "sap_successfactors"
