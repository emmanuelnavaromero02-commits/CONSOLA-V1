from __future__ import annotations

from app.services import cartridge_introspect_router as r


def test_detect_kind_openapi():
    assert r.detect_source_kind({"spec": {"openapi": "3.0.0"}}) == "openapi"


def test_detect_kind_odata_by_url():
    assert r.detect_source_kind({"url": "https://x/odata/$metadata"}) == "odata"


def test_detect_kind_graphql_by_sample():
    s = {"data": {"__schema": {"types": []}}}
    assert r.detect_source_kind({"sample": s}) == "graphql"


def test_detect_kind_sql_and_file_and_rest():
    assert r.detect_source_kind({"columns": [{"table_name": "t", "column_name": "c"}]}) == "sql"
    assert r.detect_source_kind({"file_path": "data/x.csv"}) == "file_csv"
    assert r.detect_source_kind({"sample": {"id": 1}}) == "rest_sample"


def test_csv_header_parsing_with_types():
    csv_text = "id,amount,created_at,name\n42,19.99,2026-01-01,Acme"
    out = r.parse_csv_header(csv_text)
    assert "records" in out
    by = {f["name"]: f for f in out["records"]}
    assert by["amount"]["type"] == "float"
    assert by["id"]["type"] == "int"
    assert by["created_at"]["type"] == "date"
    assert by["name"]["type"] == "string"


def test_sql_information_schema_grouping():
    rows = [
        {"table_name": "deals", "column_name": "id", "data_type": "integer", "is_nullable": "NO", "is_primary_key": True},
        {"table_name": "deals", "column_name": "amount", "data_type": "numeric", "is_nullable": "YES"},
        {"table_name": "contacts", "column_name": "email", "data_type": "varchar", "is_nullable": "YES"},
    ]
    out = r.parse_sql_information_schema(rows)
    assert set(out) == {"deals", "contacts"}
    deals = {f["name"]: f for f in out["deals"]}
    assert deals["id"]["type"] == "int"
    assert deals["id"]["primary_key"] is True
    assert deals["id"]["nullable"] is False
    assert deals["amount"]["type"] == "float"


def test_json_sample_inference():
    sample = {"data": [{"deal_id": "d1", "amount": 100.5, "won": True, "stage": "open"}]}
    out = r.parse_json_sample(sample, "deals")
    by = {f["name"]: f for f in out["deals"]}
    assert by["amount"]["type"] == "float"
    assert by["won"]["type"] == "bool"
    assert by["deal_id"]["primary_key"] is True


def test_graphql_introspection_parsing():
    sample = {"data": {"__schema": {"types": [
        {"kind": "OBJECT", "name": "Deal", "fields": [
            {"name": "id", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "ID"}}},
            {"name": "amount", "type": {"kind": "SCALAR", "name": "Float"}},
        ]},
        {"kind": "OBJECT", "name": "Query", "fields": []},
        {"kind": "OBJECT", "name": "__Type", "fields": []},
    ]}}}
    out = r.parse_graphql_introspection(sample)
    assert "Deal" in out and "Query" not in out and "__Type" not in out
    by = {f["name"]: f for f in out["Deal"]}
    assert by["amount"]["type"] == "float"
    assert by["id"]["type"] == "string"


def test_wsdl_complextype_parsing():
    wsdl = """<?xml version="1.0"?>
    <definitions xmlns:xsd="http://www.w3.org/2001/XMLSchema">
      <xsd:complexType name="Customer">
        <xsd:sequence>
          <xsd:element name="CustomerId" type="xsd:int"/>
          <xsd:element name="Balance" type="xsd:decimal" minOccurs="0"/>
        </xsd:sequence>
      </xsd:complexType>
    </definitions>"""
    out = r.parse_wsdl_elements(wsdl)
    assert "Customer" in out
    by = {f["name"]: f for f in out["Customer"]}
    assert by["CustomerId"]["type"] == "int"
    assert by["Balance"]["type"] == "float"
    assert by["Balance"]["nullable"] is True


def test_detect_pattern_incremental_and_paginated():
    fields = [
        {"name": "id", "type": "string"},
        {"name": "last_modified", "type": "timestamp"},
    ]
    desc = {"sample": {"results": [{"id": "1"}], "next": "cursor123"}}
    pat = r.detect_source_pattern(desc, fields)
    assert pat["paginated"] is True
    assert pat["incremental"] is True
    assert pat["incremental_field"] == "last_modified"
    assert pat["extraction_mode"] == "incremental"


def test_extract_entities_end_to_end_csv():
    entities, kind = r.extract_entities({"file_path": "x.csv", "csv": "id,amount\n1,9.9"})
    assert kind == "file_csv"
    assert entities and entities[0]["name"] == "records"


def test_extract_entities_empty_safe():
    entities, kind = r.extract_entities({"kind": "soap", "wsdl": "<not-xml"})
    assert entities == []


def test_dos_cap_on_huge_schema():
    cols = [{"table_name": "t", "column_name": f"c{i}", "data_type": "int"} for i in range(3000)]
    ents, _ = r.extract_entities({"columns": cols})
    assert len(ents[0]["fields"]) <= 1000


def test_detect_source_kind_non_dict_never_raises():
    assert r.detect_source_kind(None) == "rest_sample"
    assert r.detect_source_kind("odata") == "rest_sample"
    assert r.detect_source_kind(42) == "rest_sample"


def test_detect_source_pattern_non_dict_never_raises():
    result = r.detect_source_pattern(None)
    assert isinstance(result, dict)
    assert "paginated" in result


def test_extract_entities_non_dict_returns_empty():
    ents, kind = r.extract_entities(None)
    assert ents == []
    assert kind == "rest_sample"
    ents2, _ = r.extract_entities("string")
    assert ents2 == []


def test_parse_csv_header_non_string_returns_empty():
    assert r.parse_csv_header(None) == {}
    assert r.parse_csv_header(123) == {}


def test_parse_csv_header_pk_uses_looks_like_pk():
    out = r.parse_csv_header("name,id,value\nfoo,1,99")
    by = {f["name"]: f for f in out["records"]}
    assert by["id"]["primary_key"] is True
    assert by["name"]["primary_key"] is False
    assert by["value"]["primary_key"] is False


def test_parse_sql_information_schema_non_list_rows():
    assert r.parse_sql_information_schema(None) == {}
    assert r.parse_sql_information_schema("bad") == {}


def test_detect_source_pattern_non_dict_field_skipped():
    fields = [None, {"name": "updated_at", "type": "timestamp"}, "garbage"]
    desc = {"sample": {"next": "cursor"}}
    pat = r.detect_source_pattern(desc, fields)
    assert pat["incremental"] is True
    assert pat["incremental_field"] == "updated_at"


def test_graphql_introspection_entity_cap():
    types = [
        {"kind": "OBJECT", "name": f"T{i}", "fields": [{"name": "id", "type": {"kind": "SCALAR", "name": "ID"}}]}
        for i in range(600)
    ]
    sample = {"data": {"__schema": {"types": types}}}
    out = r.parse_graphql_introspection(sample)
    assert len(out) <= r._MAX_ENTITIES


def test_first_record_wrapper_with_none_value_returns_none():
    from app.services.cartridge_introspect_router import _first_record
    assert _first_record({"results": None}) is None
    assert _first_record({"data": None, "items": None}) is None
    assert _first_record({"id": 1, "name": "foo"}) == {"id": 1, "name": "foo"}


def test_first_record_empty_list_returns_none():
    from app.services.cartridge_introspect_router import _first_record
    assert _first_record({"results": []}) is None
    assert _first_record({"data": []}) is None


def test_parse_json_sample_none_results_produces_no_fields():
    out = r.parse_json_sample({"results": None}, "records")
    assert out == {}


def test_safe_field_slugifies_non_ascii_name():
    out = r.parse_json_sample({"nombre": "Ana", "monto_crédit": 42.5}, "records")
    assert out
    names = {f["name"] for f in out["records"]}
    assert any("nombre" in n or "nombre" == n for n in names)


def test_safe_field_cjk_name_slugified():
    out = r.parse_json_sample({"名前": "Tanaka", "金額": 50000}, "records")
    assert out


def test_detect_source_pattern_nan_sample_does_not_raise():
    import math
    desc = {"sample": {"amount": float("nan"), "next": "cursor123"}}
    pat = r.detect_source_pattern(desc)
    assert pat["paginated"] is True


def test_infer_scalar_type_bool_vs_int():
    from app.services.cartridge_introspect_router import _infer_scalar_type
    assert _infer_scalar_type(True) == "bool"
    assert _infer_scalar_type(False) == "bool"
    assert _infer_scalar_type(1) == "int"
    assert _infer_scalar_type(0) == "int"


def test_parse_json_sample_native_int_and_bool():
    sample = {"data": [{"id": "x", "count": 5, "active": True}]}
    out = r.parse_json_sample(sample, "things")
    by = {f["name"]: f for f in out["things"]}
    assert by["count"]["type"] == "int"
    assert by["active"]["type"] == "bool"


def test_wsdl_nested_complextype_does_not_bleed_fields():
    wsdl = """<?xml version="1.0"?>
    <definitions xmlns:xsd="http://www.w3.org/2001/XMLSchema">
      <xsd:complexType name="Order">
        <xsd:sequence>
          <xsd:element name="OrderId" type="xsd:int"/>
          <xsd:complexType name="Address">
            <xsd:sequence>
              <xsd:element name="Street" type="xsd:string"/>
            </xsd:sequence>
          </xsd:complexType>
        </xsd:sequence>
      </xsd:complexType>
    </definitions>"""
    out = r.parse_wsdl_elements(wsdl)
    assert "Order" in out
    order_names = [f["name"] for f in out["Order"]]
    assert "OrderId" in order_names
    assert "Street" not in order_names


def test_extract_entities_soap_valid_wsdl():
    wsdl = """<?xml version="1.0"?>
    <definitions xmlns:xsd="http://www.w3.org/2001/XMLSchema">
      <xsd:complexType name="Invoice">
        <xsd:sequence>
          <xsd:element name="InvoiceId" type="xsd:int"/>
          <xsd:element name="Amount" type="xsd:decimal" minOccurs="0"/>
        </xsd:sequence>
      </xsd:complexType>
    </definitions>"""
    entities, kind = r.extract_entities({"kind": "soap", "wsdl": wsdl})
    assert kind == "soap"
    assert len(entities) == 1
    assert entities[0]["name"] == "Invoice"
    by = {f["name"]: f for f in entities[0]["fields"]}
    assert by["InvoiceId"]["type"] == "int"
    assert by["Amount"]["nullable"] is True
