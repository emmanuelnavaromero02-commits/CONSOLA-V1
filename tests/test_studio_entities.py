from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def studio_entities(monkeypatch):
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    os.environ.setdefault("FIELD_ENCRYPTION_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")

    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from app.services import studio_entities as service

    return service


class FakePool:
    def __init__(self, rows=None, insert_row=None, duplicate=False):
        self.rows = rows or []
        self.insert_row = insert_row
        self.duplicate = duplicate
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if self.duplicate:
            import asyncpg

            raise asyncpg.UniqueViolationError("duplicate")
        return self.insert_row or {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": args[0],
            "cartridge": args[1],
            "spec": args[2],
            "created_by": args[3],
            "created_at": None,
            "updated_at": None,
        }


def _manifest(entity="TimeEntry"):
    return {
        "id": "replicon",
        "name": "Replicon",
        "entities": [{
            "entity": entity,
            "display_name": entity,
            "mode": "full",
            "primary_key": "id",
            "dag_id": "replicon_timeentry_full",
        }],
    }


def _pool_factory(pool):
    async def fake_pool():
        return pool

    return fake_pool


@pytest.mark.asyncio
async def test_list_entities_empty(studio_entities, monkeypatch):
    pool = FakePool(rows=[])
    async def fake_get_cartridge(cartridge):
        return _manifest()

    monkeypatch.setattr(studio_entities.auth, "pool", _pool_factory(pool))
    monkeypatch.setattr(studio_entities.cartridge_service, "get_cartridge", fake_get_cartridge)

    entities = await studio_entities.list_entities("replicon")

    assert entities[0]["entity"] == "TimeEntry"
    assert entities[0]["source"] == "entity_config"


@pytest.mark.asyncio
async def test_list_entities_filtered_by_cartridge(studio_entities, monkeypatch):
    pool = FakePool(rows=[{
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Invoice",
        "cartridge": "replicon",
        "spec": {"name": "Invoice", "cartridge": "replicon", "fields": [{"name": "id"}]},
        "created_by": 1,
        "created_at": None,
        "updated_at": None,
    }])
    async def fake_get_cartridge(cartridge):
        return _manifest()

    monkeypatch.setattr(studio_entities.auth, "pool", _pool_factory(pool))
    monkeypatch.setattr(studio_entities.cartridge_service, "get_cartridge", fake_get_cartridge)

    entities = await studio_entities.list_entities("replicon")

    assert any(e["entity"] == "Invoice" for e in entities)
    assert pool.fetch_calls[0][1] == ("replicon",)


@pytest.mark.asyncio
async def test_create_entity_persists_and_audits(studio_entities, monkeypatch):
    pool = FakePool()
    upserts = []
    audits = []

    async def fake_get_cartridge(cartridge):
        return _manifest()

    async def fake_upsert(cartridge, entity, **fields):
        upserts.append((cartridge, entity, fields))

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(studio_entities.auth, "pool", _pool_factory(pool))
    monkeypatch.setattr(studio_entities.cartridge_service, "get_cartridge", fake_get_cartridge)
    monkeypatch.setattr(studio_entities.cartridge_service, "upsert_entity", fake_upsert)
    monkeypatch.setattr(studio_entities.audit_service, "record_event", fake_audit)

    row = await studio_entities.create_entity(
        "Invoice",
        "replicon",
        {"fields": [{"name": "id", "primary_key": True}], "mode": "incremental"},
        {"id": 7, "email": "admin@local.ai"},
    )

    assert row["name"] == "Invoice"
    assert upserts[0][0:2] == ("replicon", "Invoice")
    assert upserts[0][2]["mode"] == "incremental"
    assert audits[0]["action"] == "studio.entity.create"


@pytest.mark.asyncio
async def test_create_entity_rejects_duplicate_name_cartridge(studio_entities, monkeypatch):
    pool = FakePool(duplicate=True)

    async def fake_get_cartridge(cartridge):
        return _manifest()

    monkeypatch.setattr(studio_entities.auth, "pool", _pool_factory(pool))
    monkeypatch.setattr(studio_entities.cartridge_service, "get_cartridge", fake_get_cartridge)

    with pytest.raises(studio_entities.DuplicateEntityError):
        await studio_entities.create_entity(
            "Invoice",
            "replicon",
            {"fields": [{"name": "id"}]},
            {"id": 7, "email": "admin@local.ai"},
        )


@pytest.mark.asyncio
async def test_create_entity_rejects_unknown_cartridge(studio_entities, monkeypatch):
    async def fake_get_cartridge(cartridge):
        return None

    monkeypatch.setattr(studio_entities.cartridge_service, "get_cartridge", fake_get_cartridge)

    with pytest.raises(studio_entities.UnknownCartridgeError):
        await studio_entities.create_entity("Invoice", "missing", {"fields": [{"name": "id"}]}, {"id": 7})


@pytest.mark.asyncio
async def test_upload_spec_yaml_creates_entities(studio_entities, monkeypatch):
    created = []

    async def fake_create(name, cartridge, spec, user):
        created.append((name, cartridge, spec, user))
        return {"name": name, "cartridge": cartridge}

    monkeypatch.setattr(studio_entities, "create_entity", fake_create)
    result = await studio_entities.upload_spec(
        "entities:\n  - name: Invoice\n    cartridge: replicon\n    fields:\n      - name: id\n",
        {"id": 1},
    )

    assert result["errors"] == []
    assert result["created"][0]["name"] == "Invoice"
    assert created[0][1] == "replicon"


@pytest.mark.asyncio
async def test_upload_spec_json_creates_entities(studio_entities, monkeypatch):
    async def fake_create(name, cartridge, spec, user):
        return {"name": name, "cartridge": cartridge}

    monkeypatch.setattr(studio_entities, "create_entity", fake_create)
    result = await studio_entities.upload_spec(
        '{"entities":[{"name":"User","cartridge":"replicon","fields":[{"name":"id"}]}]}',
        {"id": 1},
    )

    assert result["created"][0]["name"] == "User"


@pytest.mark.asyncio
async def test_upload_spec_validates_required_fields(studio_entities, monkeypatch):
    async def fake_create(name, cartridge, spec, user):
        raise ValueError("spec.fields must be a non-empty list")

    monkeypatch.setattr(studio_entities, "create_entity", fake_create)
    result = await studio_entities.upload_spec(
        "entities:\n  - name: Broken\n    cartridge: replicon\n",
        {"id": 1},
    )

    assert result["created"] == []
    assert "fields" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_upload_spec_partial_failure_per_entity(studio_entities, monkeypatch):
    async def fake_create(name, cartridge, spec, user):
        if name == "Broken":
            raise ValueError("bad entity")
        return {"name": name, "cartridge": cartridge}

    monkeypatch.setattr(studio_entities, "create_entity", fake_create)
    result = await studio_entities.upload_spec(
        """
entities:
  - name: Good
    cartridge: replicon
    fields: [{name: id}]
  - name: Broken
    cartridge: replicon
    fields: [{name: id}]
""",
        {"id": 1},
    )

    assert [e["name"] for e in result["created"]] == ["Good"]
    assert result["errors"][0]["entity"] == "Broken"


def test_entities_require_studio_write_permission():
    source = (REPO / "console/app/routers/studio.py").read_text(encoding="utf-8")
    assert 'require_permission("studio.write")' in source
    assert '@router.post("/entity", dependencies=[Depends(require_csrf), Depends(require_studio_write)])' in source
    assert '@router.post("/entities/upload", dependencies=[Depends(require_csrf), Depends(require_studio_write)])' in source
