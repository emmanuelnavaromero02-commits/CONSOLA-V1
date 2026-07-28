from __future__ import annotations

import base64
import unicodedata
from urllib.parse import quote

import pytest

from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)
from procedural_language_corpus import (
    PROCEDURAL_LANGUAGE_BUSINESS_COPY,
    PROCEDURAL_LANGUAGE_LAYOUT_VARIANTS,
    PROCEDURAL_LANGUAGE_STATEMENTS,
)


@pytest.mark.parametrize("statement", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_runtime_blocks_procedural_language_statement(statement: str) -> None:
    assert contains_runtime_sql(statement)


@pytest.mark.parametrize("statement", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_public_sql_blocks_procedural_language_statement(statement: str) -> None:
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_business_label_blocks_procedural_language_statement(statement: str) -> None:
    assert public_business_label(statement) is None


@pytest.mark.parametrize("statement", PROCEDURAL_LANGUAGE_LAYOUT_VARIANTS)
def test_layout_variants_are_blocked_by_every_policy(statement: str) -> None:
    assert contains_runtime_sql(statement)
    assert contains_public_sql(statement)
    assert contains_public_technical_copy(statement)
    assert public_business_label(statement) is None


@pytest.mark.parametrize("statement", PROCEDURAL_LANGUAGE_STATEMENTS)
@pytest.mark.parametrize(
    "template",
    (
        "Business note. {statement}",
        "{statement} approved by the business.",
    ),
)
def test_procedural_language_is_blocked_at_every_stream_position(
    statement: str,
    template: str,
) -> None:
    raw = template.format(statement=statement)

    assert contains_runtime_sql(raw)
    assert contains_public_sql(raw)
    assert public_business_label(raw) is None


@pytest.mark.parametrize("statement", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_encoded_procedural_language_statement_is_blocked(statement: str) -> None:
    percent_encoded = quote(statement, safe="")
    base64_encoded = base64.b64encode(statement.encode("utf-8")).decode("ascii")

    for encoded in (percent_encoded, base64_encoded):
        assert contains_public_technical_copy(encoded)
        assert public_business_label(encoded) is None
    assert contains_public_sql(base64_encoded)


def test_nfkc_procedural_language_statement_is_blocked() -> None:
    statement = "ＤＲＯＰ ＰＲＯＣＥＤＵＲＡＬ ＬＡＮＧＵＡＧＥ Comercio"
    normalized = unicodedata.normalize("NFKC", statement)

    assert normalized.startswith("DROP PROCEDURAL LANGUAGE")
    assert contains_public_sql(normalized)
    assert contains_public_technical_copy(statement)
    assert public_business_label(statement) is None


@pytest.mark.parametrize("copy", PROCEDURAL_LANGUAGE_BUSINESS_COPY)
def test_procedural_language_adjacent_business_copy_is_byte_identical(
    copy: str,
) -> None:
    assert not contains_runtime_sql(copy)
    assert not contains_public_sql(copy)
    assert not contains_public_technical_copy(copy)
    assert public_business_label(copy) == copy
