from __future__ import annotations

import base64
from urllib.parse import quote

import pytest

from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)
from ddl_modifier_corpus import (
    BUSINESS_COPY_CONTROLS,
    DDL_LAYOUT_VARIANTS,
    DDL_STATEMENTS,
    TRUNCATED_DDL_COPY,
)


@pytest.mark.parametrize("statement", DDL_STATEMENTS + DDL_LAYOUT_VARIANTS)
def test_parser_derived_ddl_modifier_matrix_is_blocked(statement: str) -> None:
    assert contains_runtime_sql(statement)
    assert contains_public_sql(statement)
    assert contains_public_technical_copy(statement)
    assert public_business_label(statement) is None


@pytest.mark.parametrize("statement", DDL_STATEMENTS[:12])
@pytest.mark.parametrize(
    "template",
    (
        "Business note. {statement}",
        "{statement} approved by the business.",
        "Before review; {statement}; SELECT 1",
    ),
)
def test_ddl_modifier_statement_is_blocked_at_every_stream_position(
    statement: str,
    template: str,
) -> None:
    raw = template.format(statement=statement)

    assert contains_runtime_sql(raw)
    assert contains_public_sql(raw)
    assert public_business_label(raw) is None


@pytest.mark.parametrize("copy", BUSINESS_COPY_CONTROLS)
def test_non_statement_business_copy_remains_byte_identical(copy: str) -> None:
    assert not contains_runtime_sql(copy)
    assert not contains_public_sql(copy)
    assert not contains_public_technical_copy(copy)
    assert public_business_label(copy) == copy


@pytest.mark.parametrize("copy", TRUNCATED_DDL_COPY)
def test_modifier_words_without_an_object_production_remain_copy(copy: str) -> None:
    assert not contains_runtime_sql(copy)
    assert not contains_public_sql(copy)
    assert not contains_public_technical_copy(copy)
    assert public_business_label(copy) == copy


@pytest.mark.parametrize("statement", DDL_STATEMENTS[:4])
def test_encoded_ddl_modifier_statement_is_blocked(statement: str) -> None:
    encoded_forms = (
        quote(statement, safe=""),
        base64.b64encode(statement.encode("utf-8")).decode("ascii"),
    )

    for encoded in encoded_forms:
        assert contains_public_technical_copy(encoded)
        assert public_business_label(encoded) is None


def test_nfkc_and_homoglyph_ddl_modifier_forms_are_blocked() -> None:
    forms = (
        "ＣＲＥＡＴＥ ＯＲ ＲＥＰＬＡＣＥ ＳＣＨＥＭＡ Comercio",
        "CRЕATE OR REPLACE SCHEMA Comercio",  # Cyrillic IE in CREATE.
    )

    for value in forms:
        assert contains_public_technical_copy(value)
        assert public_business_label(value) is None


def test_complete_ddl_prefix_and_overflow_fail_closed() -> None:
    complete_prefix = "CREATE OR REPLACE SCHEMA"
    overflow = f"{complete_prefix} Comercio {'x' * 9000}"

    assert contains_runtime_sql(complete_prefix)
    assert contains_public_sql(complete_prefix)
    assert contains_runtime_sql(overflow)
    assert contains_public_technical_copy(overflow)
    assert public_business_label(overflow) is None
