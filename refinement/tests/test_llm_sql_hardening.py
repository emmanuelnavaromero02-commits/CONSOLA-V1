from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.llm_sql import GeneratedSQLValidationError, generate_sql, validate_generated_sql


def test_validate_generated_sql_rejects_mutations():
    with pytest.raises(GeneratedSQLValidationError):
        validate_generated_sql("DROP TABLE datasets")


def test_validate_generated_sql_requires_latest_partition():
    with pytest.raises(GeneratedSQLValidationError):
        validate_generated_sql("SELECT * FROM read_parquet('s3://lakehouse/raw/x/**/*.parquet')")


def test_validate_generated_sql_allows_gold_registered_sources_without_silver_rules():
    sql = validate_generated_sql(
        "WITH totals AS (SELECT customer_id, SUM(amount) AS revenue FROM pggold.gold_invoices GROUP BY customer_id) "
        "SELECT * FROM totals",
        layer="gold",
    )

    assert "pggold.gold_invoices" in sql


def test_validate_generated_sql_keeps_common_safety_for_gold():
    with pytest.raises(GeneratedSQLValidationError):
        validate_generated_sql("SELECT * FROM pggold.gold_invoices; DROP TABLE users", layer="gold")


def test_validate_generated_sql_rejects_unsupported_master_layer():
    with pytest.raises(GeneratedSQLValidationError, match="silver, gold"):
        validate_generated_sql("SELECT * FROM pggold.gold_invoices", layer="master")


@pytest.mark.anyio
async def test_generate_sql_rejects_non_json(monkeypatch):
    class Messages:
        async def create(self, **_kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text="SELECT * FROM raw_text")])

    monkeypatch.setitem(generate_sql.__globals__, "_client", SimpleNamespace(messages=Messages()))

    with pytest.raises(GeneratedSQLValidationError, match="not valid JSON"):
        await generate_sql("make dataset", {"raw/x": {"fields": [{"name": "id", "type": "VARCHAR"}]}})


@pytest.mark.anyio
async def test_generate_sql_returns_only_valid_json_sql(monkeypatch):
    class Messages:
        async def create(self, **_kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                "sql": "SELECT * FROM read_parquet('s3://lakehouse/raw/x/**/*.parquet') "
                       "WHERE load_date = '{latest_date}'",
                "explanation": "ok",
            }))])

    monkeypatch.setitem(generate_sql.__globals__, "_client", SimpleNamespace(messages=Messages()))

    sql, explanation = await generate_sql("make dataset", {"raw/x": {"fields": [{"name": "id", "type": "VARCHAR"}]}})

    assert sql.startswith("SELECT")
    assert explanation == "ok"


@pytest.mark.anyio
async def test_generate_sql_uses_layer_specific_gold_prompt(monkeypatch):
    captured = {}

    class Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                "sql": "SELECT customer_id, SUM(amount) AS revenue FROM pggold.gold_timeentry_clean GROUP BY customer_id",
                "explanation": "gold ok",
            }))])

    monkeypatch.setitem(generate_sql.__globals__, "_client", SimpleNamespace(messages=Messages()))

    sql, explanation = await generate_sql(
        "revenue by customer",
        {"timeentry_clean": {"fields": [{"name": "customer_id", "type": "VARCHAR"}, {"name": "amount", "type": "DOUBLE"}]}},
        layer="gold",
    )

    assert sql.startswith("SELECT")
    assert explanation == "gold ok"
    assert "Postgres Gold" in captured["system"]
    assert "pggold.gold_<dataset>" in captured["system"]
    assert "{latest_date}" not in captured["messages"][0]["content"]
