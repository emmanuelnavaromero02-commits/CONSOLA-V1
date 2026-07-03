from __future__ import annotations

from pathlib import Path

from app.domains.studio.static_introspection import (
    fields_from_static_entity,
    load_static_entity_specs,
    schema_entities_from_fields,
    static_introspection_payload,
)


def test_load_static_entity_specs_reads_repo_fallback(tmp_path: Path):
    config_dir = tmp_path / "cartridges" / "sap_successfactors" / "app" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "entities.yaml").write_text(
        """
entities:
  - entity: User
    display_name: Users
  - not-a-dict
""",
        encoding="utf-8",
    )

    specs = load_static_entity_specs(
        "sap_successfactors",
        registry_root=tmp_path / "missing_registry",
        repo_root=tmp_path,
    )

    assert specs == [{"entity": "User", "display_name": "Users"}]


def test_fields_from_static_entity_merges_static_sources():
    fields = fields_from_static_entity(
        {
            "fields": [
                {
                    "name": "id",
                    "type": "integer",
                    "nullable": False,
                    "primary_key": True,
                }
            ],
            "select_fields": ["id", "lastModifiedDateTime"],
            "properties": ["status", "updated_at"],
            "primary_key": "id",
            "watermark_field": "lastModifiedDateTime",
        }
    )
    by_name = {field["name"]: field for field in fields}

    assert by_name["id"]["type"] == "int"
    assert by_name["id"]["primary_key"] is True
    assert by_name["lastModifiedDateTime"]["type"] == "timestamp"
    assert by_name["lastModifiedDateTime"]["source_type"] == "static_select_field"
    assert by_name["status"]["type"] == "string"
    assert by_name["status"]["source_type"] == "static_property"
    assert by_name["updated_at"]["type"] == "timestamp"


def test_fields_from_static_entity_adds_minimum_primary_key_and_watermark():
    fields = fields_from_static_entity(
        {"primary_key": "externalCode", "watermark_field": "lastModifiedDateTime"}
    )

    assert fields == [
        {
            "name": "externalCode",
            "type": "string",
            "nullable": False,
            "primary_key": True,
            "source_type": "static_primary_key",
        },
        {
            "name": "lastModifiedDateTime",
            "type": "timestamp",
            "nullable": True,
            "primary_key": False,
            "source_type": "static_watermark_field",
        },
    ]


def test_schema_entities_from_fields_skips_invalid_entity_names():
    entities = schema_entities_from_fields(
        {
            "GoodEntity": [
                {
                    "name": "id",
                    "type": "string",
                    "nullable": False,
                    "primary_key": True,
                }
            ],
            "bad-name": [
                {
                    "name": "id",
                    "type": "string",
                    "nullable": False,
                    "primary_key": True,
                }
            ],
        }
    )

    assert [entity["name"] for entity in entities] == ["GoodEntity"]
    assert entities[0]["primary_key"] == "id"


def test_static_introspection_payload_uses_safe_static_contract():
    payload = static_introspection_payload(
        "sap_successfactors",
        {"connection": {"auth_method": "oauth2"}},
        reason="metadata unavailable",
        entity_specs=[
            {
                "entity": "User",
                "display_name": "Users",
                "description": "Masked user directory",
                "mode": "incremental",
                "select_fields": ["userId", "lastModifiedDateTime"],
                "primary_key": "userId",
                "watermark_field": "lastModifiedDateTime",
                "odata_entity": "User",
            },
            {"entity": "bad-name", "primary_key": "id"},
        ],
    )

    assert payload["source"] == "static"
    assert payload["reason"] == "metadata unavailable"
    assert payload["endpoint"] == "/api/cartridges/sap_successfactors/connector_schema"
    assert [entity["name"] for entity in payload["entities"]] == ["User"]
    entity = payload["entities"][0]
    assert entity["display_name"] == "Users"
    assert entity["description"] == "Masked user directory"
    assert entity["mode"] == "incremental"
    assert entity["primary_key"] == "userId"
    assert entity["watermark_field"] == "lastModifiedDateTime"
    assert entity["odata_entity"] == "User"
    assert [field["name"] for field in entity["fields"]] == [
        "userId",
        "lastModifiedDateTime",
    ]
