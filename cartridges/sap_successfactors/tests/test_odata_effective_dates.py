from __future__ import annotations

from app.core.sap_client import SapSfClient


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"d": {"results": []}}


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
