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


@pytest.mark.anyio
async def test_generate_sql_rejects_non_json(monkeypatch):
    from app import llm_sql

    class Messages:
        async def create(self, **_kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text="SELECT * FROM raw_text")])

    monkeypatch.setattr(llm_sql, "_client", SimpleNamespace(messages=Messages()))

    with pytest.raises(GeneratedSQLValidationError, match="not valid JSON"):
        await generate_sql("make dataset", {"raw/x": {"fields": [{"name": "id", "type": "VARCHAR"}]}})


@pytest.mark.anyio
async def test_generate_sql_returns_only_valid_json_sql(monkeypatch):
    from app import llm_sql

    class Messages:
        async def create(self, **_kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                "sql": "SELECT * FROM read_parquet('s3://lakehouse/raw/x/**/*.parquet') "
                       "WHERE load_date = '{latest_date}'",
                "explanation": "ok",
            }))])

    monkeypatch.setattr(llm_sql, "_client", SimpleNamespace(messages=Messages()))

    sql, explanation = await generate_sql("make dataset", {"raw/x": {"fields": [{"name": "id", "type": "VARCHAR"}]}})

    assert sql.startswith("SELECT")
    assert explanation == "ok"
