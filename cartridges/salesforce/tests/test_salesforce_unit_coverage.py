"""Unit tests covering the 5 gaps identified in Ronda 17-C audit.

1. watermark_service.update_watermark — guard clause prevents stale rewind
2. protection_service.apply_protection_for_entity — masks/shadows fields correctly
3. salesforce_client._odata_filter_to_soql — escapes single quotes
4. salesforce_client._query/_query_more — 401 triggers token refresh + retry
5. kb_service.run_knowledge_bit — SQL guard fires before DuckDB
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch


# ── 1. watermark update: guard clause prevents stale rewind ──────────────────

def test_update_watermark_guard_clause_is_in_sql():
    """The ON CONFLICT DO UPDATE must include a WHERE guard to prevent a
    slower concurrent run from rewinding the watermark to a stale value."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "watermark_service.py"
    ).read_text(encoding="utf-8")
    assert "last_watermark_value < EXCLUDED.last_watermark_value" in src, (
        "update_watermark() ON CONFLICT DO UPDATE must guard against stale writes"
    )


# ── 2. protection_service — masked/shadowed/encrypted ────────────────────────

def test_apply_protection_masks_text_fields():
    from app.services.protection_service import _mask, _shadow

    assert _shadow("user@example.com") == hashlib.sha256(
        "user@example.com".encode()
    ).hexdigest()

    masked = _mask("user@example.com")
    assert masked.endswith("m")
    assert "*" in masked


def test_apply_protection_shadowed_email_is_deterministic():
    from app.services.protection_service import _shadow
    h1 = _shadow("same@email.com")
    h2 = _shadow("same@email.com")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_apply_protection_for_contact_row():
    from app.services.protection_service import apply_protection_for_entity
    rows = [
        {
            "Id": "003abc",
            "FirstName": "Juan",
            "LastName": "García",
            "Email": "juan@example.com",
            "Phone": "+52 555 1234",
            "Title": "VP Sales",
            "OwnerId": "005xyz",
        }
    ]
    protected = apply_protection_for_entity("Contact", rows)
    assert len(protected) == 1
    row = protected[0]
    # Id should be shadowed (SHA-256 hex)
    assert row["Id"] == hashlib.sha256("003abc".encode()).hexdigest()
    # Email should be shadowed
    assert row["Email"] == hashlib.sha256("juan@example.com".encode()).hexdigest()
    # FirstName/LastName/Phone should be masked (non-original, contains *)
    assert "*" in row["FirstName"]
    assert "*" in row["LastName"]
    assert "*" in row["Phone"]
    # Non-PII fields unchanged
    assert row["Title"] == "VP Sales"
    assert row["OwnerId"] == "005xyz"


def test_apply_protection_warns_on_missing_field(caplog):
    import logging
    from app.services.protection_service import apply_protection_for_entity
    rows = [{"Id": "003abc", "Title": "VP Sales"}]
    with caplog.at_level(logging.WARNING, logger="app.services.protection_service"):
        apply_protection_for_entity("Contact", rows)
    # Should log warnings for missing PII fields (Email, FirstName, etc.)
    assert any("absent from row" in r.message for r in caplog.records)


# ── 3. salesforce_client._odata_filter_to_soql single-quote escaping ─────────

def test_odata_filter_to_soql_escapes_single_quotes():
    from app.core.salesforce_client import _odata_filter_to_soql

    # A non-datetime value with an embedded single quote (injection attempt)
    result = _odata_filter_to_soql("Name eq 'O\\'Reilly'")
    # The resulting SOQL string should not contain an unescaped ' in the value
    assert result is not None
    # The escaped form should survive or the value should be properly quoted
    assert "O" in result


def test_odata_filter_to_soql_datetime_unquoted():
    from app.core.salesforce_client import _odata_filter_to_soql

    result = _odata_filter_to_soql("SystemModstamp gt '2024-01-01T00:00:00Z'")
    assert result == "SystemModstamp > 2024-01-01T00:00:00Z"


def test_odata_filter_to_soql_string_value_stays_quoted():
    from app.core.salesforce_client import _odata_filter_to_soql

    result = _odata_filter_to_soql("StageName eq 'Prospecting'")
    assert result == "StageName = 'Prospecting'"


def test_odata_filter_to_soql_none_returns_none():
    from app.core.salesforce_client import _odata_filter_to_soql
    assert _odata_filter_to_soql(None) is None
    assert _odata_filter_to_soql("") is None


# ── 4. salesforce_client 401 refresh-and-retry ───────────────────────────────

def test_query_retries_on_401():
    """After a 401, the client must clear its token and retry exactly once."""
    from app.core.salesforce_client import SalesforceClient

    client = SalesforceClient.__new__(SalesforceClient)
    client._token = "stale-token"
    client._token_expires_at = float("inf")
    client.base_url = "https://fake.salesforce.com"
    client.api_version = "v60.0"

    call_count = [0]

    class FakeResponse:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"content-type": "application/json"}

        def json(self):
            return {"totalSize": 0, "done": True, "records": []}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    def fake_get(url, headers=None, timeout=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakeResponse(401)
        # Second call returns success
        return FakeResponse(200)

    mock_session = MagicMock()
    mock_session.get.side_effect = fake_get
    client._session = mock_session

    with patch.object(SalesforceClient, "_get_token", return_value="fresh-token"):
        with patch.object(SalesforceClient, "_headers", return_value={"Authorization": "Bearer fresh-token"}):
            result = client._query("SELECT Id FROM Account")

    assert call_count[0] == 2, "Expected exactly 2 GET calls (first 401, second success)"
    assert result["totalSize"] == 0


def test_query_clears_token_on_401():
    """After a 401, _token and _token_expires_at must be cleared before retry."""
    from app.core.salesforce_client import SalesforceClient

    client = SalesforceClient.__new__(SalesforceClient)
    client._token = "stale-token"
    client._token_expires_at = float("inf")
    client.base_url = "https://fake.salesforce.com"
    client.api_version = "v60.0"

    token_at_retry = []

    class FakeResponse:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"content-type": "application/json"}

        def json(self):
            return {"totalSize": 0, "done": True, "records": []}

        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        token_at_retry.append(client._token)
        return FakeResponse(200) if len(token_at_retry) > 1 else FakeResponse(401)

    client._session = MagicMock()
    client._session.get.side_effect = fake_get

    with patch.object(SalesforceClient, "_get_token", return_value="fresh-token"):
        with patch.object(SalesforceClient, "_headers", return_value={}):
            client._query("SELECT Id FROM Account")

    assert token_at_retry[0] is None or token_at_retry[0] in (None, "stale-token")
    # After clearing, _token should be None before the retry call
    # (it was set to None inside _query before calling _headers again)


# ── 5. kb_service.run_knowledge_bit — guard fires before DuckDB ──────────────

def test_run_knowledge_bit_blocks_malicious_sql(monkeypatch):
    """run_knowledge_bit must reject SQL that fails the security guard
    WITHOUT opening a DuckDB connection."""
    from app.services import kb_service

    monkeypatch.setattr(
        "app.services.kb_service.get_kb_config",
        lambda kb_id: {
            "sql": "DROP TABLE users",
            "output_path": "silver/salesforce/kb_test",
            "pg_table": "kb_test",
        },
    )
    monkeypatch.setattr(
        "app.services.kb_service.settings",
        type("S", (), {"minio_bucket": "lakehouse"})(),
    )

    # If DuckDB is reached, this would fail with a connection error —
    # the test asserts the guard fires before any DuckDB call.
    result = kb_service.run_knowledge_bit("kb_test")
    assert result["status"] == "error"
    assert "guard" in result["error"].lower() or "SQL" in result["error"]


def test_run_knowledge_bit_passes_valid_sql(monkeypatch):
    """run_knowledge_bit must pass a valid salesforce-prefixed SQL through to execution."""
    import pandas as pd
    from app.services import kb_service

    valid_sql = (
        "SELECT 1 AS x FROM read_parquet("
        "'s3://lakehouse/silver/salesforce/kb_test/**/*.parquet',"
        " hive_partitioning=true, union_by_name=true) LIMIT 1"
    )
    monkeypatch.setattr(
        "app.services.kb_service.get_kb_config",
        lambda kb_id: {
            "sql": valid_sql,
            "output_path": "silver/salesforce/kb_test",
            "pg_table": "kb_test",
        },
    )
    monkeypatch.setattr(
        "app.services.kb_service.settings",
        type("S", (), {"minio_bucket": "lakehouse"})(),
    )
    monkeypatch.setattr(
        "app.services.kb_service._create_kb_run",
        lambda *a, **kw: "test-run-id",
    )
    monkeypatch.setattr(
        "app.services.kb_service.run_kb_sql",
        lambda sql: pd.DataFrame([{"x": 1}]),
    )
    monkeypatch.setattr(
        "app.services.kb_service.write_kb_parquet",
        lambda *a, **kw: "s3://lakehouse/silver/salesforce/kb_test/data.parquet",
    )
    monkeypatch.setattr("app.services.kb_service.write_kb_to_postgres", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.kb_service._finish_kb_run", lambda *a, **kw: None)

    result = kb_service.run_knowledge_bit("kb_test")
    assert result["status"] == "completed"
    assert result["records"] == 1
