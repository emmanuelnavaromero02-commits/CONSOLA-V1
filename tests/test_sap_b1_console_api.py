from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.routers import sap_b1 as route
from app.services.control_room import sap_b1_learning

USER = {"id": 7, "email": "admin@example.com", "tenant_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222", "role": "admin"}


def test_learning_proposes_thresholds_only_with_evidence():
    rows = [
        {"source": "WB-B1-MARGEN", "alerts": 6, "decisions": 3, "false_positives": 4, "outcomes": 2,
         "achieved": 1, "not_achieved": 2},
        {"source": "WB-B1-ABASTO", "alerts": 2, "decisions": 1, "false_positives": 2, "outcomes": 1,
         "achieved": 1, "not_achieved": 0},
        {"source": "WB-B1-SEMAFORO", "alerts": 9, "decisions": 0, "false_positives": 9, "outcomes": 0,
         "achieved": 0, "not_achieved": 0},
    ]
    result = sap_b1_learning.learning_from_rows(rows)
    assert result.status == "ready" and [s["source"] for s in result.sources] == ["WB-B1-MARGEN", "WB-B1-ABASTO", "WB-B1-SEMAFORO"]
    assert [(s["source"], s["thresholds"]) for s in result.suggestions] == [
        ("WB-B1-MARGEN", ["margin_min_pct"]), ("WB-B1-MARGEN", ["margin_min_pct"])]
    assert "4 de 6 alertas de WB-B1-MARGEN" in result.suggestions[0]["reason"]
    assert result.breaches == [item["reason"] for item in result.suggestions]
    empty = sap_b1_learning.learning_from_rows([])
    assert empty.status == "degraded" and not empty.breaches and empty.notes


def test_learning_sql_is_scoped_to_the_workspace_and_sap_b1_alerts():
    sql = sap_b1_learning.LEARNING_SQL
    assert "i.workspace_id = $1::uuid" in sql and "i.cartridge_id = 'sap_b1'" in sql
    assert "LIKE 'WB-B1-%'" in sql and "make_interval(days => $2)" in sql


def test_parameters_text_is_read_from_the_connection_fields_only():
    assert route._parameters_text(None) == ""
    assert route._parameters_text({"fields": {"business_parameters": "threshold:*:*:margin_min_pct=25"},
                                   "password": "x"}) == "threshold:*:*:margin_min_pct=25"
    assert route._parameters_text({"business_parameters": "setting:*:*:safety_days=7"}) == "setting:*:*:safety_days=7"


def test_completeness_counts_missing_keys_without_a_default():
    catalog = [{"key": "margin_min_pct", "kind": "threshold", "default": None},
               {"key": "coverage_red_days", "kind": "setting", "default": "30"},
               {"key": "channel_days_max", "kind": "threshold", "default": None}]
    parsed = [{"kind": "threshold", "key": "margin_min_pct"}, {"kind": "branch", "key": "02"},
              {"kind": "account", "key": "revenue"}]
    assert route._completeness(catalog, parsed) == {
        "keys_total": 3, "keys_set": 1, "missing": ["channel_days_max"], "using_default": ["coverage_red_days"],
        "branches": 1, "accounts": 1}


@pytest.mark.parametrize(
    "env, expected",
    [({"EMAIL_PROVIDER": "smtp", "SMTP_HOST": "mailhog"}, "sin_configurar"),
     ({"EMAIL_PROVIDER": "smtp", "SMTP_HOST": '"mailhog"'}, "sin_configurar"),
     ({"EMAIL_PROVIDER": "ses"}, "ses"),
     ({"EMAIL_PROVIDER": "smtp", "SMTP_HOST": "smtp.example.com"}, "smtp")],
)
def test_email_transport_never_exposes_the_host(monkeypatch, env, expected):
    for name in ("EMAIL_PROVIDER", "SMTP_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert route._email_transport() == expected


def test_signed_context_requires_a_workspace(monkeypatch):
    monkeypatch.setattr(route, "build_security_context", lambda user: {"trusted": True, "tenant_id": "t", "workspace_id": ""})
    with pytest.raises(HTTPException) as exc:
        route._signed_context(USER)
    assert exc.value.status_code == 403


class _Response:
    def __init__(self, status: int, payload: Any):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _cartridge_fake(calls: list, validate_status: int = 200):
    async def fake(method, path, *, user=None, body=None, timeout=None):
        calls.append((method, path, user is not None, dict(body or {})))
        if path == "/business-parameters/validate":
            if validate_status == 422:
                return _Response(422, {"detail": "unknown parameter kind 'control'"})
            return _Response(200, {"valid": True, "count": 1, "parameters": []})
        if path == "/business-parameters/refresh":
            return _Response(200, {"status": "success"})
        if path == "/finance-runs":
            return _Response(200, {"rows": 3, "indicators": ["margen_bruto"], "companies": {"mx": ["2026-08"]},
                                   "storage_uri": "s3://secret-bucket/raw"})
        raise AssertionError(path)
    return fake


def test_put_parameters_validates_then_writes_only_that_field_and_refreshes(monkeypatch):
    calls: list = []
    stored: dict = {}
    monkeypatch.setattr(route, "_cartridge", _cartridge_fake(calls))

    async def connection(user):
        return {"conn_id": "default", "base_url": "hana://x", "fields": {"password": "keep", "business_parameters": "old"}}

    monkeypatch.setattr(route, "_connection", connection)
    monkeypatch.setattr(route, "_vault_headers", lambda user: {"x-security-context": "{}"})

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def put(self, url, headers=None, json=None):
            stored.update(url=url, body=json)
            return httpx.Response(200, json={"saved": True})

    audits: list = []

    async def record_event(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(route.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(route.audit_service, "record_event", record_event)
    result = asyncio.run(route.put_business_parameters({"text": "threshold:*:*:margin_min_pct=25"}, USER))
    assert result == {"saved": True, "count": 1, "refreshed": True, "refresh_error": None}
    assert stored["url"].endswith("/connections/sap_b1/default")
    assert stored["body"] == {"base_url": "hana://x", "fields": {"password": "keep",
                                                                "business_parameters": "threshold:*:*:margin_min_pct=25"}}
    assert [c[1] for c in calls] == ["/business-parameters/validate", "/business-parameters/refresh"]
    assert calls[1][2] is True, "the refresh carries the signed context"
    assert audits[0]["action"] == "sap_b1.business_parameters.update" and "threshold" not in str(audits[0])

    calls.clear()
    monkeypatch.setattr(route, "_cartridge", _cartridge_fake(calls, validate_status=422))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.put_business_parameters({"text": "control:mx:2026-08:cogs=1"}, USER))
    assert exc.value.status_code == 422 and "unknown parameter kind" in exc.value.detail
    assert [c[1] for c in calls] == ["/business-parameters/validate"], "nothing is written after a failed validation"


def test_finance_run_is_forwarded_signed_and_the_storage_uri_is_not_returned(monkeypatch):
    calls: list = []
    monkeypatch.setattr(route, "_cartridge", _cartridge_fake(calls))

    async def record_event(**kwargs):
        return None

    monkeypatch.setattr(route.audit_service, "record_event", record_event)
    csv = "indicador,empresa,mes,dimension,clave,valor\nmargen_bruto,mx,2026-08,total,,1\n"
    result = asyncio.run(route.post_finance_run({"csv": csv}, USER))
    assert result == {"rows": 3, "indicators": ["margen_bruto"], "companies": {"mx": ["2026-08"]}}
    assert calls == [("POST", "/finance-runs", True, {"csv": csv})]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.post_finance_run({"csv": "   "}, USER))
    assert exc.value.status_code == 422


def test_recipients_are_written_inside_the_workspace_scope(monkeypatch):
    seen: list = []

    class _Conn:
        async def fetchval(self, sql, tenant, workspace):
            seen.append(("count", tenant, workspace))
            return 0

        async def fetchrow(self, sql, *args):
            seen.append(("write", *args[:3]))
            return {"email": args[2]}

    @asynccontextmanager
    async def scoped(pool, user):
        yield _Conn(), user["tenant_id"], user["workspace_id"]

    async def fake_pool():
        return object()

    async def record_event(**kwargs):
        return None

    monkeypatch.setattr(route, "scoped_db_for_user", scoped)
    monkeypatch.setattr(route.auth, "pool", fake_pool)
    monkeypatch.setattr(route.audit_service, "record_event", record_event)
    result = asyncio.run(route.add_recipient({"email": " Compras@Example.com "}, USER))
    assert result == {"added": True, "email": "compras@example.com"}
    assert seen[-1] == ("write", USER["tenant_id"], USER["workspace_id"], "compras@example.com")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.add_recipient({"email": "no-es-correo"}, USER))
    assert exc.value.status_code == 422


def test_every_write_route_needs_csrf_and_control_room_write():
    for api_route in route.router.routes:
        names = {getattr(dep.dependency, "__name__", "") for dep in api_route.dependencies}
        if api_route.methods & {"PUT", "POST", "DELETE"}:
            assert "require_csrf" in names, api_route.path
        assert api_route.path.startswith("/api/sap-b1/")
