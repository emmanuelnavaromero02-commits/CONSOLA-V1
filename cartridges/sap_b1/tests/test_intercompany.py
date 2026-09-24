"""The configurable intercompany partner mapping and its Bronze snapshot."""
from __future__ import annotations

import pytest

from app.core.b1_source import B1ConfigurationError
from app.services import intercompany as ic


def test_mapping_parses_company_card_counterparty_triples():
    partners = ic.parse_intercompany("mx_mfg:C-IC-DIST-A=mx_dist_a, mx_mfg:C-IC-DIST-B=mx_dist_b;mx_dist_a:V-IC-MFG=mx_mfg")
    assert [(p.company, p.card_code, p.counterparty) for p in partners] == [
        ("mx_mfg", "C-IC-DIST-A", "mx_dist_a"),
        ("mx_mfg", "C-IC-DIST-B", "mx_dist_b"),
        ("mx_dist_a", "V-IC-MFG", "mx_mfg"),
    ]
    assert ic.parse_intercompany("") == []
    for bad in ("mx_mfg:C1", "mx_mfg=mx_dist_a", "MX:C1=mx_dist_a", "mx_mfg:C1=mx_mfg", "mx_mfg:C1=a,mx_mfg:C1=b", "mx_mfg:C 1=mx_dist_a", 'mx_mfg:C"1=mx_dist_a'):
        with pytest.raises(B1ConfigurationError):
            ic.parse_intercompany(bad)


def test_mapping_must_name_configured_companies(monkeypatch):
    monkeypatch.setenv("SAP_B1_DIALECT", "postgres")
    monkeypatch.setenv("SAP_B1_HOST", "db.invalid")
    monkeypatch.setenv("SAP_B1_USER", "reader")
    monkeypatch.setenv("SAP_B1_PASSWORD", "x")
    monkeypatch.setenv("SAP_B1_DATABASE", "b1")
    monkeypatch.setenv("SAP_B1_COMPANIES", "mx_mfg=S1,mx_dist_a=S2")
    monkeypatch.setenv("SAP_B1_INTERCOMPANY", "mx_mfg:C-IC-DIST-A=mx_dist_a,mx_dist_a:V-IC-MFG=mx_mfg")
    assert len(ic.resolve_intercompany()) == 2
    monkeypatch.setenv("SAP_B1_INTERCOMPANY", "mx_mfg:C-IC-DIST-B=mx_dist_b")
    with pytest.raises(B1ConfigurationError, match="not in SAP_B1_COMPANIES"):
        ic.resolve_intercompany()


def test_refresh_writes_the_mapping_as_a_typed_bronze_snapshot(monkeypatch):
    monkeypatch.setenv("SAP_B1_DIALECT", "postgres")
    monkeypatch.setenv("SAP_B1_HOST", "db.invalid")
    monkeypatch.setenv("SAP_B1_USER", "reader")
    monkeypatch.setenv("SAP_B1_PASSWORD", "x")
    monkeypatch.setenv("SAP_B1_DATABASE", "b1")
    monkeypatch.setenv("SAP_B1_COMPANIES", "mx_mfg=S1,mx_dist_a=S2,mx_dist_b=S3")
    monkeypatch.setenv("SAP_B1_INTERCOMPANY", "mx_dist_b:V-IC-MFG=mx_mfg,mx_mfg:C-IC-DIST-A=mx_dist_a")
    uploads: list[dict] = []
    finished: list[dict] = []
    monkeypatch.setattr(ic, "create_run", lambda **kw: "run-ic")
    monkeypatch.setattr(ic, "finish_run", lambda **kw: finished.append(kw))
    monkeypatch.setattr(ic, "fail_run", lambda **kw: pytest.fail(f"unexpected failure: {kw}"))
    monkeypatch.setattr(ic, "write_parquet_and_upload", lambda **kw: (uploads.append(kw), "s3://lakehouse/raw/sap_b1/IntercompanyPartners/x.parquet")[1])

    result = ic.refresh_intercompany_partners()
    assert result["entity"] == "IntercompanyPartners" and result["record_count"] == 2
    assert result["companies"] == ["mx_dist_b", "mx_mfg"]
    rows = uploads[0]["rows"]
    assert rows == [
        {"CardCode": "V-IC-MFG", "CounterpartyCompany": "mx_mfg", "MappingSource": "config", "_company": "mx_dist_b", "_source_updated_at": None},
        {"CardCode": "C-IC-DIST-A", "CounterpartyCompany": "mx_dist_a", "MappingSource": "config", "_company": "mx_mfg", "_source_updated_at": None},
    ]
    schema = uploads[0]["arrow_schema"]
    assert schema.names[:3] == ["CardCode", "CounterpartyCompany", "MappingSource"]
    assert finished[0]["records_extracted"] == 2

    monkeypatch.setenv("SAP_B1_INTERCOMPANY", "")
    uploads.clear()
    assert ic.refresh_intercompany_partners()["record_count"] == 0
    assert uploads[0]["rows"] == [], "an empty mapping still leaves a typed zero-row artifact"
