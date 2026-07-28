from __future__ import annotations

import pytest

from app.services.control_room.business_copy_hazards import is_diagnostic_copy
from app.services.public_identifier_sensitivity import (
    contains_public_identifier_copy,
    is_public_technical_structure_key,
)
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)


BUSINESS_TERMS = (
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


@pytest.mark.parametrize("value", BUSINESS_TERMS)
def test_contextual_business_terms_are_preserved_byte_identically(value: str) -> None:
    assert contains_public_technical_copy(value) is False
    assert public_business_label(value) == value


@pytest.mark.parametrize("value", TECHNICAL_COPY)
def test_structured_technical_terms_remain_blocked(value: str) -> None:
    assert contains_public_technical_copy(value) is True
    assert public_business_label(value) is None


@pytest.mark.parametrize(
    "key",
    (
        "source_status",
        "dataStatus",
        "receipt_id",
        "receiptId",
        "actionBindingsMap",
        "provenance_record",
    ),
)
def test_real_mapping_keys_remain_technical(key: str) -> None:
    assert is_public_technical_structure_key(key) is True


@pytest.mark.parametrize(
    "value", ("State: California", "Status: Won", "Data Status: Green")
)
def test_business_state_values_are_not_diagnostic_assignments(value: str) -> None:
    assert is_diagnostic_copy(value) is False


@pytest.mark.parametrize("value", ("status: ready", "source_status=missing"))
def test_runtime_state_values_remain_diagnostic_assignments(value: str) -> None:
    assert is_diagnostic_copy(value) is True


@pytest.mark.parametrize(
    "value",
    (
        "Sales Receipts",
        "Gross Receipts",
        "Binding Agreements",
        "Supply Chain Provenance",
    ),
)
def test_naked_business_vocabulary_is_not_a_technical_identifier(value: str) -> None:
    assert contains_public_identifier_copy(value) is False
