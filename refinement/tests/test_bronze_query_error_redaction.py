"""Error redaction contracts for rejected Bronze Query SQL."""

from __future__ import annotations

import logging

import pytest

from app.sql_table_function_policy import (
    TableFunctionPolicyError,
    validate_table_function_query,
)


def test_unsupported_command_does_not_log_sql_or_path(caplog):
    secret_path = "/tmp/synthetic-bronze-secret-canary.csv"
    sql = f"CALL read_csv_auto('{secret_path}')"

    with caplog.at_level(logging.WARNING, logger="sqlglot"):
        with pytest.raises(TableFunctionPolicyError, match="SQL blocked by safety policy"):
            validate_table_function_query(sql)

    assert secret_path not in caplog.text
    assert sql not in caplog.text
