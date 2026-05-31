from __future__ import annotations

from .http_writeback import HttpWriteBackAdapter


class RepliconAdapter(HttpWriteBackAdapter):
    cartridge_id = "replicon"
