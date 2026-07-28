from __future__ import annotations

from types import MappingProxyType
from urllib.parse import quote


P2_BUSINESS_COPY = (
    "Sales Receipts",
    "Gross Receipts",
    "State: California",
    "State: Texas",
    "Status: Won",
    "Data Status: Green",
    "Binding Agreements",
    "Supply Chain Provenance",
    "State pension review",
    "Status meeting today",
    "Receipt of annual leave request",
    "Binding employment agreement",
    "Provenance of organic coffee",
)

TECHNICAL_COPY = (
    "status: ready",
    "source_status",
    "dataStatus",
    "receipt=rcpt-1",
    "receipt_id",
    "receiptId",
    "actionBindingsMap",
    "provenance_record",
    "Update status set to ready",
)

CLASS_1_COPY = (
    "TABLE Rock",
    "SELECT Comercial",
    "TRUNCATE Labs",
    "Show Solutions",
    "Describe Digital",
)

CLASS_2_COPY = (
    "Call center roster",
    "Set of core values",
    "Grant Portfolio Review",
    "Copy of the signed contract",
)

SQL_AMBIGUOUS_COPY = (
    *CLASS_1_COPY,
    "Show me the Q4 report",
    "Describe the onboarding process",
    "Use of force policy",
    "Set goals to improve performance",
    "Set expectations to align teams",
    "Set goals to win",
    "Set priorities to high",
)


def _encoded_private_path(layers: int) -> str:
    value = "/srv/private/catalog"
    for _ in range(layers):
        value = quote(value, safe="")
    return value


HARD_SERVER_COPY = (
    "/srv/private/query.sql",
    "password=opaque",
    "tenant_id=tenant-private",
    '{"dataset":"private"}',
    "safe\u202etxt",
    "%252Fsrv%252Fprivate%252Fcatalog",
    _encoded_private_path(8),
    _encoded_private_path(9),
    _encoded_private_path(10),
    "status: ready",
)


def private_server_copy_registry(
    literal: str,
    *,
    endpoint: str,
    field: str,
) -> tuple[object, MappingProxyType]:
    copy_id = object()
    registry = MappingProxyType({(copy_id, endpoint, field): literal})
    return copy_id, registry


__all__ = (
    "CLASS_1_COPY",
    "CLASS_2_COPY",
    "HARD_SERVER_COPY",
    "P2_BUSINESS_COPY",
    "SQL_AMBIGUOUS_COPY",
    "TECHNICAL_COPY",
    "private_server_copy_registry",
)
