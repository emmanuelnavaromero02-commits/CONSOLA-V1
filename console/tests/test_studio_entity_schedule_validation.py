from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.studio.ops_invoke import invoke_studio_ops_tool
from app.services import cartridge_service
from app.services.studio_entities import _entity_config_fields

from app.domains.studio.entity_mutations import (
    ENTITY_UPDATE_FIELDS,
    update_studio_entity_payload,
    validate_entity_schedule_patch,
)


class FakeCartridgeService:
    def __init__(self):
        self.upserted: list[tuple[str, str, dict]] = []

    async def upsert_entity(self, cartridge_id: str, entity: str, **updates):
        self.upserted.append((cartridge_id, entity, updates))


def test_cron_timezone_is_an_updatable_entity_field():
    assert "cron_timezone" in ENTITY_UPDATE_FIELDS


@pytest.mark.parametrize(
    "patch",
    [
        {"cron_expression": "0 8 * * *", "trigger_type": "scheduled"},
        {"cron_expression": "0 19 * * 1-5", "cron_timezone": "America/Mexico_City"},
        {"cron_timezone": "UTC"},
        {"cron_timezone": "America/Argentina/Buenos_Aires"},
        {"cron_timezone": "Etc/GMT+5"},
        {"cron_expression": "@daily"},
        {"cron_expression": None, "trigger_type": "manual"},
        {"cron_expression": "", "trigger_type": "manual"},
        {"trigger_type": "scheduled"},
        {"display_name": "Facturas"},
    ],
)
def test_valid_schedule_patches_pass(patch):
    validate_entity_schedule_patch(patch)


@pytest.mark.parametrize(
    "timezone_value",
    [
        "Mars/Olympus_Mons",
        "../../etc/passwd",
        "/usr/share/zoneinfo/UTC",
        "America/Mexico_City\n",
        "America/Mexico_City; DROP TABLE entity_config",
        "",
        " ",
        None,
        123,
        "A" * 65,
    ],
)
def test_unknown_or_malformed_timezone_is_rejected_in_spanish(timezone_value):
    with pytest.raises(HTTPException) as exc:
        validate_entity_schedule_patch({"cron_timezone": timezone_value})
    assert exc.value.status_code == 400
    assert "Zona horaria no reconocida" in exc.value.detail


@pytest.mark.parametrize(
    "cron",
    ["not a cron", "0 25 * * *", "0 8 * *", "0 8 * * * *", "@sometimes", 42],
)
def test_invalid_cron_is_rejected_in_spanish(cron):
    with pytest.raises(HTTPException) as exc:
        validate_entity_schedule_patch({"cron_expression": cron, "trigger_type": "scheduled"})
    assert exc.value.status_code == 400
    assert exc.value.detail == "La programación personalizada no es válida."


def test_scheduled_trigger_with_empty_cron_is_rejected():
    with pytest.raises(HTTPException) as exc:
        validate_entity_schedule_patch({"cron_expression": "  ", "trigger_type": "scheduled"})
    assert exc.value.status_code == 400
    assert exc.value.detail == "Una frecuencia programada necesita una programación."


@pytest.mark.parametrize("cron", ["", "   "])
def test_bare_empty_cron_without_trigger_is_rejected(cron):
    with pytest.raises(HTTPException) as exc:
        validate_entity_schedule_patch({"cron_expression": cron})
    assert exc.value.status_code == 400
    assert exc.value.detail == "Una frecuencia programada necesita una programación."


def test_null_cron_alone_is_a_valid_clear():
    validate_entity_schedule_patch({"cron_expression": None})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"cron_expression": "", "trigger_type": "manual"},
        {"cron_expression": None, "trigger_type": "manual"},
        {"cron_expression": None},
    ],
)
async def test_cleared_cron_is_stored_as_null_never_empty_string(body):
    service = FakeCartridgeService()
    result = await update_studio_entity_payload(
        cartridge_id="sap_successfactors",
        entity="User",
        body=body,
        cartridge_service=service,
    )
    assert result["cron_expression"] is None
    stored = service.upserted[0][2]
    assert stored["cron_expression"] is None


@pytest.mark.asyncio
async def test_bare_empty_cron_patch_is_rejected_before_writing():
    service = FakeCartridgeService()
    with pytest.raises(HTTPException) as exc:
        await update_studio_entity_payload(
            cartridge_id="sap_successfactors",
            entity="User",
            body={"cron_expression": ""},
            cartridge_service=service,
        )
    assert exc.value.status_code == 400
    assert service.upserted == []


@pytest.mark.asyncio
async def test_update_persists_a_valid_schedule_with_its_timezone():
    service = FakeCartridgeService()
    result = await update_studio_entity_payload(
        cartridge_id="sap_successfactors",
        entity="User",
        body={
            "cron_expression": "0 8 * * *",
            "trigger_type": "scheduled",
            "cron_timezone": "America/Mexico_City",
        },
        cartridge_service=service,
    )
    assert result["cron_timezone"] == "America/Mexico_City"
    assert service.upserted == [
        (
            "sap_successfactors",
            "User",
            {
                "cron_expression": "0 8 * * *",
                "trigger_type": "scheduled",
                "cron_timezone": "America/Mexico_City",
            },
        )
    ]


@pytest.mark.asyncio
async def test_update_rejects_invalid_schedule_before_writing():
    service = FakeCartridgeService()
    with pytest.raises(HTTPException) as exc:
        await update_studio_entity_payload(
            cartridge_id="sap_successfactors",
            entity="User",
            body={"cron_expression": "0 8 * * *", "cron_timezone": "Nowhere/Land"},
            cartridge_service=service,
        )
    assert exc.value.status_code == 400
    assert service.upserted == []


class FakePg:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    async def fetchval(self, *args):
        return 1

    async def execute(self, sql, *params):
        self.executed.append((sql, params))

    async def close(self):
        return None


def _ops_kwargs(**overrides):
    base = dict(
        user={"id": 1},
        cartridge_service=cartridge_service,
        get_db_pool=None,
        pipeline_runs_scope_predicate=None,
        pipeline_runs_read_conn=None,
        mcp_registry=None,
        airflow_log_attempt=None,
        airflow_log_task_ids=None,
        uuid_factory=None,
        logger_debug=lambda *a, **k: None,
        logger_exception=lambda *a, **k: None,
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_ops_update_entity_tool_rejects_blank_cron(monkeypatch):
    fake = FakePg()

    async def _fake_pg():
        return fake

    monkeypatch.setattr(cartridge_service, "_pg", _fake_pg)
    with pytest.raises(HTTPException) as exc:
        await invoke_studio_ops_tool(
            tool="update_entity",
            args={"cartridge_id": "acme", "entity": "Invoice", "cron_expression": ""},
            **_ops_kwargs(),
        )
    assert exc.value.status_code == 400
    assert fake.executed == []


@pytest.mark.asyncio
async def test_ops_update_entity_tool_rejects_invalid_cron_and_timezone(monkeypatch):
    fake = FakePg()

    async def _fake_pg():
        return fake

    monkeypatch.setattr(cartridge_service, "_pg", _fake_pg)
    for args in (
        {"cartridge_id": "acme", "entity": "Invoice", "cron_expression": "not a cron"},
        {"cartridge_id": "acme", "entity": "Invoice", "cron_timezone": "Nowhere/Land"},
    ):
        with pytest.raises(HTTPException) as exc:
            await invoke_studio_ops_tool(tool="update_entity", args=dict(args), **_ops_kwargs())
        assert exc.value.status_code == 400
    assert fake.executed == []


@pytest.mark.asyncio
async def test_upsert_entity_stores_null_for_cleared_cron(monkeypatch):
    fake = FakePg()

    async def _fake_pg():
        return fake

    monkeypatch.setattr(cartridge_service, "_pg", _fake_pg)
    await cartridge_service.upsert_entity(
        "acme", "Invoice", cron_expression="", trigger_type="manual"
    )
    sql, params = fake.executed[0]
    assert "cron_expression" in sql
    assert None in params
    assert "" not in params


def test_entity_spec_sync_coerces_missing_cron_to_null():
    fields = _entity_config_fields({"name": "Invoice", "fields": []})
    assert fields["cron_expression"] is None
    assert _entity_config_fields({"name": "Invoice", "fields": [], "cron_expression": ""})[
        "cron_expression"
    ] is None


def test_full_cartridge_manifest_seeds_null_for_missing_cron():
    manifest, seed_sql = cartridge_service._normalize_full_cartridge_manifest(
        {
            "id": "canary",
            "name": "Canary",
            "entities": [{"entity": "Invoice", "fields": []}],
        }
    )
    assert manifest["entities"][0]["cron_expression"] is None
    row = next(line for line in seed_sql.splitlines() if "'Invoice'" in line)
    assert "NULL" in row
