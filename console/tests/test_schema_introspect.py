from __future__ import annotations

import pytest

from app.services.schema_introspect import normalize_field, parse_odata_entity_sets, parse_odata_metadata, parse_openapi_fields


def test_parse_openapi_fields_extracts_types_and_primary_key():
    spec = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "Invoice": {
                    "required": ["id", "total"],
                    "properties": {
                        "id": {"type": "integer", "x-primary-key": True},
                        "total": {"type": "number"},
                        "posted_on": {"type": "string", "format": "date"},
                        "metadata": {"type": "object"},
                    },
                }
            }
        },
        "paths": {
            "/customers": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["uuid"],
                                            "properties": {
                                                "uuid": {"type": "string"},
                                                "active": {"type": "boolean"},
                                            },
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    fields = parse_openapi_fields(spec)

    invoice = {field["name"]: field for field in fields["Invoice"]}
    assert invoice["id"]["type"] == "int"
    assert invoice["id"]["primary_key"] is True
    assert invoice["id"]["nullable"] is False
    assert invoice["total"]["type"] == "float"
    assert invoice["posted_on"]["type"] == "date"
    assert invoice["metadata"]["type"] == "json"

    customers = {field["name"]: field for field in fields["customers"]}
    assert customers["uuid"]["primary_key"] is True
    assert customers["uuid"]["nullable"] is False
    assert customers["active"]["type"] == "bool"


def test_parse_openapi_fields_merges_allof_and_sibling_properties():
    spec = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "BaseInvoice": {
                    "required": ["id"],
                    "properties": {"id": {"type": "integer", "x-primary-key": True}},
                },
                "Invoice": {
                    "allOf": [{"$ref": "#/components/schemas/BaseInvoice"}],
                    "required": ["total"],
                    "properties": {"total": {"type": "number"}},
                },
            }
        },
    }

    invoice = {field["name"]: field for field in parse_openapi_fields(spec)["Invoice"]}

    assert invoice["id"]["primary_key"] is True
    assert invoice["id"]["nullable"] is False
    assert invoice["total"]["type"] == "float"
    assert invoice["total"]["nullable"] is False


def test_parse_odata_metadata_extracts_entity_sets_keys_and_edm_types():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="Demo" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="BusinessPartner">
        <Key><PropertyRef Name="BusinessPartnerID"/></Key>
        <Property Name="BusinessPartnerID" Type="Edm.String" Nullable="false"/>
        <Property Name="CreatedAt" Type="Edm.DateTimeOffset"/>
        <Property Name="Revenue" Type="Edm.Decimal" Nullable="true"/>
        <Property Name="Active" Type="Edm.Boolean" Nullable="false"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="A_BusinessPartner" EntityType="Demo.BusinessPartner"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

    fields = parse_odata_metadata(xml)

    assert "BusinessPartner" in fields
    assert "A_BusinessPartner" in fields
    assert parse_odata_entity_sets(xml) == {"A_BusinessPartner": "BusinessPartner"}
    by_name = {field["name"]: field for field in fields["A_BusinessPartner"]}
    assert by_name["BusinessPartnerID"]["primary_key"] is True
    assert by_name["BusinessPartnerID"]["nullable"] is False
    assert by_name["CreatedAt"]["type"] == "timestamp"
    assert by_name["Revenue"]["type"] == "float"
    assert by_name["Active"]["type"] == "bool"


def test_normalize_field_maps_sql_information_schema_types():
    assert normalize_field({"name": "updated_at", "source_type": "timestamp without time zone"})["type"] == "timestamp"
    assert normalize_field({"name": "amount", "source_type": "numeric"})["type"] == "float"
    assert normalize_field({"name": "payload", "source_type": "jsonb"})["type"] == "json"


def test_normalize_field_parses_boolean_strings_and_rejects_invalid_names():
    field = normalize_field({
        "name": "id",
        "type": "integer",
        "nullable": "false",
        "primary_key": "false",
    })

    assert field["nullable"] is False
    assert field["primary_key"] is False

    with pytest.raises(ValueError, match="field.name"):
        normalize_field({"name": "foo-bar", "type": "string"})
