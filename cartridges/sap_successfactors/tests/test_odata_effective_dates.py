from __future__ import annotations

import pytest

from app.core.sap_client import SAPClientError, SapSfClient


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"d": {"results": []}}


class _ForbiddenResponse:
    status_code = 403
    text = '{"error":"Not authorized for Candidate","access_token":"secret-token"}'


class _BadRequestResponse:
    status_code = 400
    text = (
        '{"error":{"code":"COE_PROPERTY_NOT_FOUND","message":{"value":'
        '"Invalid property names: EmpEmploymentTermination/eventReasonExternalCode"}},'
        '"access_token":"secret-token"}'
    )

    def raise_for_status(self) -> None:
        import requests

        raise requests.HTTPError("400 Client Error", response=self)


def test_fetch_entity_includes_effective_dated_from_to_params(monkeypatch):
    captured: dict = {}
    client = SapSfClient.__new__(SapSfClient)
    client.base_url = "https://example.successfactors.test/odata/v2"
    client._session = type(
        "Session",
        (),
        {
            "get": lambda _self, url, **kwargs: captured.update({"url": url, **kwargs}) or _Response(),
        },
    )()
    monkeypatch.setattr(client, "_require_configured", lambda: None)
    monkeypatch.setattr(client, "_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(client, "_log_auth", lambda _status_code: None)

    rows = client.fetch_entity(
        "EmpJob",
        select=["userId", "startDate"],
        page_size=200,
        skip=0,
        filter_expr="lastModifiedDateTime gt '2026-01-01T00:00:00Z'",
        from_date="1900-01-01",
        to_date="9999-12-31",
    )

    assert rows == []
    assert captured["url"] == "https://example.successfactors.test/odata/v2/EmpJob"
    assert captured["params"] == {
        "$format": "json",
        "$top": 200,
        "$skip": 0,
        "$select": "userId,startDate",
        "$filter": "lastModifiedDateTime gt '2026-01-01T00:00:00Z'",
        "fromDate": "1900-01-01",
        "toDate": "9999-12-31",
    }


def test_fetch_entity_403_uses_safe_permission_error(monkeypatch):
    captured: dict = {}
    client = SapSfClient.__new__(SapSfClient)
    client.base_url = "https://api68sales.successfactors.com/odata/v2"
    client._conn_id = "femsa_sf"
    client._session = type(
        "Session",
        (),
        {
            "get": lambda _self, url, **kwargs: captured.update({"url": url, **kwargs}) or _ForbiddenResponse(),
        },
    )()
    monkeypatch.setattr(client, "_require_configured", lambda: None)
    monkeypatch.setattr(client, "_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(client, "_log_auth", lambda _status_code: None)

    with pytest.raises(SAPClientError) as exc_info:
        client.fetch_entity(
            "Candidate",
            select=["candidateId", "firstName", "lastName", "lastModifiedDateTime"],
        )

    message = str(exc_info.value)
    assert "SuccessFactors rechazo acceso OData (HTTP 403)" in message
    assert "entity=Candidate" in message
    assert "conn_id=" not in message
    assert "select_fields=" not in message
    assert "api68sales" not in message
    assert "secret-token" not in message


def test_fetch_entity_400_does_not_report_sap_body_query_or_url(monkeypatch):
    captured: dict = {}
    client = SapSfClient.__new__(SapSfClient)
    client.base_url = "https://api68sales.successfactors.com/odata/v2"
    client._conn_id = "femsa_sf"
    client._session = type(
        "Session",
        (),
        {
            "get": lambda _self, url, **kwargs: captured.update({"url": url, **kwargs}) or _BadRequestResponse(),
        },
    )()
    monkeypatch.setattr(client, "_require_configured", lambda: None)
    monkeypatch.setattr(client, "_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(client, "_log_auth", lambda _status_code: None)

    with pytest.raises(SAPClientError) as exc_info:
        client.fetch_entity(
            "EmpEmploymentTermination",
            select=["userId", "endDate", "eventReasonExternalCode", "lastModifiedDateTime"],
        )

    message = str(exc_info.value)
    assert "SuccessFactors rechazo solicitud OData (HTTP 400)" in message
    assert "entity=EmpEmploymentTermination" in message
    assert "conn_id=" not in message
    assert "select_fields=" not in message
    assert "api68sales" not in message
    assert "COE_PROPERTY_NOT_FOUND" not in message
    assert "Invalid property names" not in message
    assert "secret-token" not in message
