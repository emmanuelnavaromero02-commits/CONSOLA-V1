from __future__ import annotations

import pytest

from app.core.sap_client import ODataRequestError, SAPClientError, SapSfClient


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"d": {"results": []}}


class _ForbiddenResponse:
    status_code = 403
    text = '{"error":"Not authorized for Candidate","access_token":"secret-token"}'


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
    client._conn_id = "tenant_sf"
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
    import requests

    captured: dict = {}
    response = requests.Response()
    response.status_code = 400
    response.url = (
        "https://api68sales.successfactors.com/odata/v2/EmpEmploymentTermination"
        "?$filter=personIdExternal%20eq%20SENTINEL-PII"
    )
    response._content = (
        b'{"error":{"code":"COE_PROPERTY_NOT_FOUND","message":{"value":'
        b'"Invalid property names for SENTINEL-PII"}},"access_token":"secret-token"}'
    )
    response.request = requests.Request(
        "GET",
        response.url,
        headers={"Authorization": "Bearer secret-token"},
    ).prepare()
    client = SapSfClient.__new__(SapSfClient)
    client.base_url = "https://api68sales.successfactors.com/odata/v2"
    client._conn_id = "tenant_sf"
    client._session = type(
        "Session",
        (),
        {
            "get": lambda _self, url, **kwargs: captured.update({"url": url, **kwargs}) or response,
        },
    )()
    monkeypatch.setattr(client, "_require_configured", lambda: None)
    monkeypatch.setattr(client, "_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(client, "_log_auth", lambda _status_code: None)

    with pytest.raises(ODataRequestError) as exc_info:
        client.fetch_entity(
            "EmpEmploymentTermination",
            select=["userId", "endDate", "eventReasonExternalCode", "lastModifiedDateTime"],
            filter_expr="personIdExternal eq 'SENTINEL-PII'",
        )

    error = exc_info.value
    message = str(error)
    assert error.status_code == 400
    assert error.filter_applied is True
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "response")
    assert not hasattr(error, "request")
    assert "SuccessFactors rechazo solicitud OData (HTTP 400)" in message
    assert "entity=EmpEmploymentTermination" in message
    assert "conn_id=" not in message
    assert "select_fields=" not in message
    assert "api68sales" not in message
    assert "COE_PROPERTY_NOT_FOUND" not in message
    assert "Invalid property names" not in message
    assert "secret-token" not in message
    assert "SENTINEL-PII" not in repr(error)
    assert "api68sales" not in repr(vars(error))
    assert "secret-token" not in repr(vars(error))

    unsafe_entity = ODataRequestError(
        entity="PerPerson/SENTINEL-PII",
        status_code=400,
        filter_applied=True,
    )
    assert "entity=unknown" in str(unsafe_entity)
    assert "SENTINEL-PII" not in str(unsafe_entity)
