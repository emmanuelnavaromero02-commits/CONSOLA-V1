"""Error redaction contracts for rejected Bronze Query SQL."""

from __future__ import annotations

import logging
import traceback

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


def test_malformed_sql_does_not_leak_through_exception_context(caplog):
    secret_path = "/tmp/synthetic-malformed-secret-canary.csv"
    sql = f"SELECT * FROM read_parquet('{secret_path}'"

    with caplog.at_level(logging.ERROR, logger="bronze-query-redaction-test"):
        try:
            validate_table_function_query(sql)
        except TableFunctionPolicyError as exc:
            logging.getLogger("bronze-query-redaction-test").exception("blocked")
            rendered = "".join(traceback.format_exception(exc))
        else:
            raise AssertionError("malformed SQL was accepted")

    assert secret_path not in rendered
    assert secret_path not in caplog.text
    assert sql not in rendered
    assert sql not in caplog.text
