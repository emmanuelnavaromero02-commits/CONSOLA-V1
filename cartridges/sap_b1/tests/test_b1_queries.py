from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.b1_dialects import get_dialect
from app.core.b1_source import (
    B1ConfigurationError,
    Connection,
    parse_companies,
    quote_ident,
)
from app.services import b1_queries as q


def _oinv() -> dict:
    return {
        "entity": "OINV",
        "select_fields": ["DocEntry", "DocNum", "CardCode", "DocDate", "DocTotal", "UpdateDate", "UpdateTS"],
        "primary_key": "DocEntry",
        "watermark_field": "UpdateDate",
        "watermark_ts_field": "UpdateTS",
        "watermark_format": "b1_update_ts",
        "date_field": "DocDate",
        "page_size": 2000,
        "mode": "incremental",
    }


def _inv1() -> dict:
    return {
        "entity": "INV1",
        "select_fields": ["DocEntry", "LineNum", "ItemCode", "Quantity", "LineTotal"],
        "primary_key": "DocEntry,LineNum",
        "watermark_field": "UpdateDate",
        "watermark_ts_field": "UpdateTS",
        "watermark_format": "b1_update_ts",
        "parent": "OINV",
        "parent_key": "DocEntry",
        "join_key": "DocEntry",
        "date_field": "DocDate",
        "page_size": 5000,
        "mode": "incremental",
    }


def _oinm() -> dict:
    return {
        "entity": "OINM",
        "select_fields": ["TransNum", "DocDate", "ItemCode", "Warehouse", "InQty", "OutQty"],
        "primary_key": "TransNum",
        "watermark_field": "TransNum",
        "watermark_format": "integer",
        "date_field": "DocDate",
        "page_size": 5000,
        "mode": "incremental",
    }


def test_identifiers_are_validated_before_they_reach_sql():
    assert quote_ident("DocEntry") == '"DocEntry"'
    assert quote_ident("Line_ID") == '"Line_ID"'
    for bad in ('X") UNION SELECT 1; --', "Doc Entry", "", "1abc", 'a"b', "a.b"):
        with pytest.raises(ValueError):
            quote_ident(bad)
    hana = get_dialect("hana")
    assert hana.company_prefix("SBO-DEMO_MX") == '"SBO-DEMO_MX"'
    assert hana.table_ref("SBODEMOMX", "OINV") == '"SBODEMOMX"."OINV"'
    for bad in ('a"b', "a b", "", 'x"; DROP SCHEMA public; --', "a/b"):
        with pytest.raises(ValueError):
            hana.company_prefix(bad)


def test_company_map_parses_and_rejects_malformed_entries():
    companies = parse_companies("mx_mfg=SBO_MFG, mx_dist_a=SBO-DIST-A;mx_dist_b=SBO_DIST_B")
    assert [(c.alias, c.schema) for c in companies] == [
        ("mx_mfg", "SBO_MFG"),
        ("mx_dist_a", "SBO-DIST-A"),
        ("mx_dist_b", "SBO_DIST_B"),
    ]
    assert parse_companies("") == []
    for bad in ("mx_mfg", "mx_mfg=", "=SBO", "MX=SBO_A", "mx=SBO_A,mx=SBO_B", 'mx=SBO"A'):
        with pytest.raises(B1ConfigurationError):
            parse_companies(bad)


def test_connection_renders_the_driver_placeholder():
    class _Raw:
        def close(self):
            pass

    assert Connection(_Raw(), "postgres", ()).render('SELECT ? FROM "T" WHERE "A" = ?') == 'SELECT %s FROM "T" WHERE "A" = %s'
    assert Connection(_Raw(), "hana", ()).render('SELECT ? FROM "T"') == 'SELECT ? FROM "T"'
    assert Connection(_Raw(), "mssql", ()).render('SELECT ? FROM "T"') == 'SELECT ? FROM "T"'


def test_update_stamp_watermark_round_trips_and_backs_off():
    mark = q.Watermark.parse("b1_update_ts", "2025-03-04T13:45:10")
    assert mark is not None
    assert mark.day == date(2025, 3, 4)
    assert mark.hhmmss == 134510
    assert mark.text() == "2025-03-04T13:45:10"
    assert mark.with_backoff(5).text() == "2025-03-04T13:40:10"
    assert mark.with_backoff(0) is mark
    assert q.Watermark.parse("b1_update_ts", "2025-03-04 13:45:10").text() == "2025-03-04T13:45:10"
    assert q.Watermark.parse("b1_update_ts", "garbage") is None
    assert q.Watermark.parse("b1_update_ts", "") is None
    assert q.Watermark.parse("b1_update_ts", None) is None
    assert q.Watermark.parse(None, "2025-03-04T13:45:10") is None


def test_integer_watermark_text_sorts_like_the_number():
    assert q.Watermark.from_number(999).text() < q.Watermark.from_number(1000).text()
    assert q.Watermark.parse("integer", "000000000000999").number == 999
    assert q.Watermark.parse("integer", "1000").number == 1000
    assert q.Watermark.parse("integer", "-1") is None
    assert q.Watermark.parse("integer", "x") is None
    assert q.Watermark.from_number(7).with_backoff(5).number == 7


def test_update_date_and_update_ts_combine_into_one_stamp():
    assert q.combine_update_stamp(date(2025, 3, 4), 134510) == datetime(2025, 3, 4, 13, 45, 10)
    assert q.combine_update_stamp(datetime(2025, 3, 4, 0, 0), 235959) == datetime(2025, 3, 4, 23, 59, 59)
    assert q.combine_update_stamp("2025-03-04 00:00:00", 5) == datetime(2025, 3, 4, 0, 0, 5)
    assert q.combine_update_stamp(date(2025, 3, 4), None) == datetime(2025, 3, 4, 0, 0, 0)
    assert q.combine_update_stamp(date(2025, 3, 4), 236000) is None
    assert q.combine_update_stamp(date(2025, 3, 4), 129960) is None
    assert q.combine_update_stamp(None, 1) is None


def test_watermark_keys_are_per_entity_and_company():
    assert q.watermark_key("OINV", "mx_mfg") == "OINV@mx_mfg"


def test_plan_rejects_incomplete_or_inconsistent_configs():
    with pytest.raises(ValueError, match="select_fields"):
        q.plan_from_config({"entity": "OINV", "select_fields": []})
    with pytest.raises(ValueError, match="primary key"):
        q.plan_from_config({**_oinv(), "primary_key": "DocNumX"})
    with pytest.raises(ValueError, match="watermark_format"):
        q.plan_from_config({**_oinv(), "watermark_format": "odata"})
    with pytest.raises(ValueError, match="parent_key"):
        q.plan_from_config({**_inv1(), "parent_key": None, "join_key": None})
    with pytest.raises(ValueError, match="join_key"):
        q.plan_from_config({**_inv1(), "join_key": "TrgetEntry"})
    with pytest.raises(ValueError, match="page_size"):
        q.plan_from_config({**_oinv(), "page_size": 0})
    with pytest.raises(ValueError, match="watermark_ts_field"):
        q.plan_from_config({**_oinv(), "select_fields": ["DocEntry", "UpdateDate"]})
    with pytest.raises(ValueError, match="Invalid identifier"):
        q.plan_from_config({**_oinv(), "select_fields": ["DocEntry", "x; DROP TABLE y"]})


def test_plan_accepts_json_and_csv_column_lists():
    plan = q.plan_from_config({**_oinv(), "select_fields": '["DocEntry", "UpdateDate", "UpdateTS"]', "primary_key": ["DocEntry"], "date_field": None})
    assert plan.columns == ("DocEntry", "UpdateDate", "UpdateTS")
    assert plan.primary_key == ("DocEntry",)
    assert plan.output_columns[-2:] == ("_company", "_source_updated_at")
    snapshot = q.plan_from_config({"entity": "OITW", "select_fields": "ItemCode, WhsCode, OnHand", "primary_key": "ItemCode,WhsCode"})
    assert snapshot.watermark_kind is None and not snapshot.incremental_capable


def test_header_incremental_sql_uses_the_stamp_pair_and_keyset_paging():
    plan = q.plan_from_config(_oinv())
    mark = q.Watermark.parse("b1_update_ts", "2025-03-04T13:45:10")
    sql, params = q.select_sql(plan, "SBO_X", dialect="hana", mode="incremental", watermark=mark)
    assert sql.startswith('SELECT t."DocEntry", t."DocNum", t."CardCode", t."DocDate", t."DocTotal", t."UpdateDate", t."UpdateTS", t."UpdateDate" AS "_wm_date", t."UpdateTS" AS "_wm_ts" FROM "SBO_X"."OINV" t')
    assert 'WHERE (t."UpdateDate" > ? OR (t."UpdateDate" = ? AND t."UpdateTS" >= ?))' in sql
    assert sql.endswith('ORDER BY t."DocEntry" LIMIT 2000')
    assert params == [datetime(2025, 3, 4), datetime(2025, 3, 4), 134510]
    assert "JOIN" not in sql and "OFFSET" not in sql

    sql2, params2 = q.select_sql(plan, "SBO_X", dialect="hana", mode="incremental", watermark=mark, after_key=(41,))
    assert 'AND t."DocEntry" > ?' in sql2
    assert params2 == [datetime(2025, 3, 4), datetime(2025, 3, 4), 134510, 41]


def test_line_tables_are_read_through_their_header():
    plan = q.plan_from_config(_inv1())
    mark = q.Watermark.parse("b1_update_ts", "2025-03-04T13:45:10")
    sql, params = q.select_sql(plan, "SBO_X", dialect="hana", mode="incremental", watermark=mark, after_key=(10, 3))
    assert 'FROM "SBO_X"."INV1" t JOIN "SBO_X"."OINV" h ON h."DocEntry" = t."DocEntry"' in sql
    assert 'h."UpdateDate" AS "_wm_date", h."UpdateTS" AS "_wm_ts"' in sql
    assert '(h."UpdateDate" > ? OR (h."UpdateDate" = ? AND h."UpdateTS" >= ?))' in sql
    assert '(t."DocEntry" > ? OR (t."DocEntry" = ? AND t."LineNum" > ?))' in sql
    assert sql.endswith('ORDER BY t."DocEntry", t."LineNum" LIMIT 5000')
    assert params == [datetime(2025, 3, 4), datetime(2025, 3, 4), 134510, 10, 10, 3]

    full_sql, full_params = q.select_sql(plan, "SBO_X", dialect="hana", mode="full")
    assert 'JOIN "SBO_X"."OINV" h' in full_sql and "_wm_date" in full_sql and "WHERE" not in full_sql
    assert full_params == []


def test_integer_watermark_is_a_strict_greater_than():
    plan = q.plan_from_config(_oinm())
    sql, params = q.select_sql(plan, "SBO_X", dialect="hana", mode="incremental", watermark=q.Watermark.from_number(500))
    assert 'WHERE t."TransNum" > ?' in sql
    assert "_wm_date" not in sql
    assert params == [500]
    with pytest.raises(ValueError, match="watermark kind"):
        q.select_sql(plan, "SBO_X", dialect="hana", mode="incremental", watermark=q.Watermark.parse("b1_update_ts", "2025-01-01T00:00:00"))


def test_historical_reads_use_the_document_date_inclusive_of_the_end_day():
    header = q.plan_from_config(_oinv())
    sql, params = q.select_sql(header, "SBO_X", dialect="hana", mode="historical", from_date="2025-01-01", to_date="2025-01-31")
    assert 'WHERE t."DocDate" >= ? AND t."DocDate" < ?' in sql
    assert params == [datetime(2025, 1, 1), datetime(2025, 2, 1)]
    line = q.plan_from_config(_inv1())
    sql2, params2 = q.select_sql(line, "SBO_X", dialect="hana", mode="historical", from_date="2025-01-01", to_date="2025-01-31")
    assert 'JOIN "SBO_X"."OINV" h' in sql2 and 'h."DocDate" >= ?' in sql2
    assert params2 == [datetime(2025, 1, 1), datetime(2025, 2, 1)]
    with pytest.raises(ValueError, match="ISO date"):
        q.select_sql(header, "SBO_X", dialect="hana", mode="historical", from_date="next tuesday")
    with pytest.raises(ValueError, match="before"):
        q.select_sql(header, "SBO_X", dialect="hana", mode="historical", from_date="2025-02-01", to_date="2025-01-01")
    with pytest.raises(ValueError, match="date_field"):
        q.select_sql(q.plan_from_config({**_oinv(), "date_field": None}), "SBO_X", dialect="hana", mode="historical", from_date="2025-01-01")


def test_column_types_become_an_explicit_parquet_schema():
    import pyarrow as pa

    config = {**_oinv(), "column_types": {"DocEntry": "int64", "DocTotal": "decimal(19,6)", "DocDate": "timestamp", "CardCode": "string"}}
    plan = q.plan_from_config(config)
    schema = q.arrow_schema(plan)
    assert schema.field("DocEntry").type == pa.int64()
    assert schema.field("DocTotal").type == pa.decimal128(19, 6)
    assert schema.field("DocDate").type == pa.timestamp("us")
    assert schema.field("CardCode").type == pa.string()
    assert schema.field("DocNum").type == pa.string(), "a column without a declared type is written as text"
    assert schema.names[-7:] == ["_company", "_source_updated_at", "_extracted_at", "_run_id", "_source_entity", "_load_type", "_watermark_value"]
    assert q.arrow_schema(q.plan_from_config(_oinv())) is None
    assert q.plan_from_config({**config, "column_types": '{"DocEntry": "int64"}'}).column_types == (("DocEntry", "int64"),)
    with pytest.raises(ValueError, match="column type"):
        q.plan_from_config({**_oinv(), "column_types": {"DocEntry": "bigint"}})
    with pytest.raises(ValueError, match="not in select_fields"):
        q.plan_from_config({**_oinv(), "column_types": {"Nope": "int64"}})
    assert isinstance(Decimal("1.5"), Decimal)


def test_full_mode_of_a_snapshot_table_has_no_predicate_or_paging_without_a_key():
    plan = q.plan_from_config({"entity": "CINF", "select_fields": ["Version", "CompnyName"], "page_size": 100})
    sql, params = q.select_sql(plan, "SBO_X", dialect="hana", mode="full")
    assert sql == 'SELECT t."Version", t."CompnyName" FROM "SBO_X"."CINF" t'
    assert params == []
    with pytest.raises(ValueError, match="mode"):
        q.select_sql(plan, "SBO_X", dialect="hana", mode="delta")


def test_rows_become_records_with_company_and_source_stamp():
    plan = q.plan_from_config(_inv1())
    columns = ["DocEntry", "LineNum", "ItemCode", "Quantity", "LineTotal", "_wm_date", "_wm_ts"]
    rows = [
        (10, 0, "A", 1, 5, datetime(2025, 3, 4), 134510),
        (10, 1, "B", 2, 7, datetime(2025, 3, 4), 134510),
        (11, 0, "C", 1, 9, datetime(2025, 3, 5), 91500),
    ]
    records = q.rows_to_records(plan, "mx_mfg", columns, rows)
    assert records[0] == {
        "DocEntry": 10, "LineNum": 0, "ItemCode": "A", "Quantity": 1, "LineTotal": 5,
        "_company": "mx_mfg", "_source_updated_at": "2025-03-04T13:45:10",
    }
    assert records[2]["_source_updated_at"] == "2025-03-05T09:15:00"
    assert q.keyset_cursor(plan, records[-1]) == (11, 0)
    assert q.next_watermark(plan, records).text() == "2025-03-05T09:15:00"
    assert q.next_watermark(plan, []) is None
    with pytest.raises(ValueError, match="do not match"):
        q.rows_to_records(plan, "mx_mfg", ["LineNum", "DocEntry"], [(0, 10)])


def test_integer_watermark_advances_to_the_largest_key_seen():
    plan = q.plan_from_config(_oinm())
    columns = list(plan.columns)
    rows = [(5, datetime(2025, 1, 1), "A", "W1", 1, 0), (9, datetime(2025, 1, 2), "A", "W1", 0, 1)]
    records = q.rows_to_records(plan, "mx_mfg", columns, rows)
    assert records[0]["_source_updated_at"] is None
    assert q.next_watermark(plan, records).text() == "000000000000009"
