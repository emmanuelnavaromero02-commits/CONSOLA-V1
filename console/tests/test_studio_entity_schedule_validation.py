from __future__ import annotations

import pytest
from fastapi import HTTPException

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
