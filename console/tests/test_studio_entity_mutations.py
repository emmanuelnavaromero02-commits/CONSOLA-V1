from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.studio.entity_mutations import (
    manifest_entity_names,
    rename_studio_entity_payload,
    update_studio_entity_payload,
)


class FakeCartridgeService:
    def __init__(self, manifest: dict | None = None):
        self.manifest = manifest
        self.renamed: list[tuple[str, str, str]] = []
        self.upserted: list[tuple[str, str, dict]] = []

    async def get_cartridge(self, cartridge_id: str):
        return self.manifest

    async def rename_entity(self, cartridge_id: str, old_name: str, new_name: str):
        self.renamed.append((cartridge_id, old_name, new_name))

    async def upsert_entity(self, cartridge_id: str, entity: str, **updates):
        self.upserted.append((cartridge_id, entity, updates))


def test_manifest_entity_names_supports_entity_and_id_keys():
    assert manifest_entity_names(
        {"entities": [{"entity": "User"}, {"id": "EmpJob"}, {"entity": ""}]}
    ) == ["User", "EmpJob", None]


@pytest.mark.asyncio
async def test_rename_studio_entity_rejects_empty_name():
    service = FakeCartridgeService({"entities": [{"entity": "User"}]})

    with pytest.raises(HTTPException) as exc:
        await rename_studio_entity_payload(
            cartridge_id="sap_successfactors",
            entity="User",
            body={"new_name": " "},
            cartridge_service=service,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "new_name is required"
    assert service.renamed == []


@pytest.mark.asyncio
async def test_rename_studio_entity_returns_same_name_without_mutation():
    service = FakeCartridgeService({"entities": [{"entity": "User"}]})

    result = await rename_studio_entity_payload(
        cartridge_id="sap_successfactors",
        entity="User",
        body={"new_name": "User"},
        cartridge_service=service,
    )

    assert result == {"renamed": False, "reason": "same name"}
    assert service.renamed == []


@pytest.mark.asyncio
async def test_rename_studio_entity_verifies_manifest_and_duplicates():
    service = FakeCartridgeService({"entities": [{"entity": "User"}, {"id": "EmpJob"}]})

    with pytest.raises(HTTPException) as missing:
        await rename_studio_entity_payload(
            cartridge_id="sap_successfactors",
            entity="Candidate",
            body={"new_name": "NewCandidate"},
            cartridge_service=service,
        )
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as duplicate:
        await rename_studio_entity_payload(
            cartridge_id="sap_successfactors",
            entity="User",
            body={"new_name": "EmpJob"},
            cartridge_service=service,
        )
    assert duplicate.value.status_code == 409
    assert duplicate.value.detail == "Entity 'EmpJob' already exists"
    assert service.renamed == []


@pytest.mark.asyncio
async def test_rename_studio_entity_mutates_valid_request():
    service = FakeCartridgeService({"entities": [{"entity": "User"}]})

    result = await rename_studio_entity_payload(
        cartridge_id="sap_successfactors",
        entity="User",
        body={"new_name": "Employee"},
        cartridge_service=service,
    )

    assert result == {"renamed": True, "old_name": "User", "new_name": "Employee"}
    assert service.renamed == [("sap_successfactors", "User", "Employee")]


@pytest.mark.asyncio
async def test_update_studio_entity_filters_allowed_fields():
    service = FakeCartridgeService()

    result = await update_studio_entity_payload(
        cartridge_id="sap_successfactors",
        entity="User",
        body={
            "display_name": "Usuarios",
            "enabled": True,
            "unexpected": "ignored",
        },
        cartridge_service=service,
    )

    assert result == {
        "updated": True,
        "entity": "User",
        "display_name": "Usuarios",
        "enabled": True,
    }
    assert service.upserted == [
        (
            "sap_successfactors",
            "User",
            {"display_name": "Usuarios", "enabled": True},
        )
    ]


@pytest.mark.asyncio
async def test_update_studio_entity_rejects_empty_update():
    service = FakeCartridgeService()

    with pytest.raises(HTTPException) as exc:
        await update_studio_entity_payload(
            cartridge_id="sap_successfactors",
            entity="User",
            body={"unexpected": "ignored"},
            cartridge_service=service,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "No valid fields to update"
    assert service.upserted == []
