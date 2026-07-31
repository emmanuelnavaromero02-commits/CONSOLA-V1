from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from console.app.services.intelligence import gold_fetcher
from console.app.services.publication_heads import visible_materialized_object
from console.app.services.superset_gold_scope import (
    resolve_scoped_database_id,
    scoped_gold_database_name,
    scoped_gold_sqlalchemy_uri,
)
from refinement.app import publication_public


SCOPE = {
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "workspace_id": "22222222-2222-2222-2222-222222222222",
}


def test_intelligence_never_falls_back_without_a_published_head(monkeypatch) -> None:
    async def unavailable(*_args, **_kwargs):
        raise HTTPException(404, "missing")

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", unavailable)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(gold_fetcher.query_intelligence_dataset_rows("pending", SCOPE))
    assert caught.value.status_code == 404


def test_object_listing_requires_exact_published_key_not_prefix() -> None:
    published = {"gold/x/abcdef.parquet"}
    assert visible_materialized_object("gold/x/abcdef.parquet", published)
    assert not visible_materialized_object("gold/x/abc", published)


def test_unpublished_metadata_is_omitted() -> None:
    original = publication_public.PublicationReader
    publication_public.PublicationReader = lambda: SimpleNamespace(
        published_head=lambda *_args: None
    )
    try:
        assert (
            publication_public.published_dataset_metadata(
                {"name": "pending", "sql_def": "SELECT secret", "sources": ["raw/x/y"]},
                SCOPE,
            )
            is None
        )
    finally:
        publication_public.PublicationReader = original


def test_published_metadata_and_catalog_are_explicit_projections(monkeypatch) -> None:
    head = {
        "materialization_run_id": "00000000-0000-0000-0000-000000000001",
        "row_count": 1,
        "published_at": datetime(2026, 7, 31, tzinfo=UTC),
        "status": "published",
    }
    evidence = {
        "catalog": [{"name": "value", "type": "INTEGER"}],
        "row_count": 1,
        "created_at": head["published_at"],
    }
    monkeypatch.setattr(
        publication_public,
        "PublicationReader",
        lambda: SimpleNamespace(published_head=lambda *_args: head),
    )
    monkeypatch.setattr(
        publication_public,
        "PublicationEvidenceStore",
        lambda: SimpleNamespace(read_exact=lambda *_args: evidence),
    )
    dataset = {
        "name": "safe",
        "layer": "gold",
        "cartridge": "probe",
        "sql_def": "SELECT secret FROM private",
        "sources": ["s3://private/path"],
        "metadata": {"receipt": "private"},
        "description": "/internal/path",
    }
    metadata = publication_public.published_dataset_metadata(dataset, SCOPE)
    catalog = publication_public.published_catalog([dataset], SCOPE)
    serialized = json.dumps({"metadata": metadata, "catalog": catalog})
    assert "SELECT" not in serialized and "s3://" not in serialized
    assert "/internal/path" not in serialized and "receipt" not in serialized


def test_superset_connection_is_server_scoped_and_opaque() -> None:
    raw = "postgresql+psycopg2://reader:secret@postgres_gold:5433/modecissions_gold"
    scoped = scoped_gold_sqlalchemy_uri(raw, SCOPE)
    options = parse_qs(urlsplit(scoped).query)["options"][0]
    assert f"app.tenant_id={SCOPE['tenant_id']}" in options
    assert f"app.workspace_id={SCOPE['workspace_id']}" in options
    name = scoped_gold_database_name(SCOPE)
    assert SCOPE["tenant_id"] not in name and SCOPE["workspace_id"] not in name


def test_superset_rejects_a_global_database_id() -> None:
    client = SimpleNamespace(
        list_databases=lambda: None,
    )

    async def list_databases():
        return [{"id": 7, "name": "modecissions_gold"}]

    client.list_databases = list_databases
    with pytest.raises(HTTPException) as caught:
        asyncio.run(resolve_scoped_database_id(client, SCOPE, 7))
    assert caught.value.status_code == 400


def test_superset_creates_only_the_workspace_scoped_database(monkeypatch) -> None:
    created: list[tuple[str, str]] = []

    class Client:
        async def list_databases(self):
            return []

        async def create_database(self, name, uri):
            created.append((name, uri))
            return {"id": 11, "name": name}

    monkeypatch.setenv(
        "SUPERSET_GOLD_SQLALCHEMY_URI",
        "postgresql+psycopg2://reader:secret@postgres_gold:5433/modecissions_gold",
    )
    assert asyncio.run(resolve_scoped_database_id(Client(), SCOPE, None)) == 11
    assert created[0][0] == scoped_gold_database_name(SCOPE)
    assert "app.workspace_id%3D" in created[0][1]
