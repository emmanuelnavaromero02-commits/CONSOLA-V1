from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

import app.main as main  # noqa: E402
from app.domains.apps.embed import (  # noqa: E402
    APP_STALE_PUBLICATION_MESSAGE,
    app_embed_wrapper_html,
)


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-0000-0000-0000-000000000001"
HTML = "<html><body>ventas</body></html>"
DIGEST = "d" * 64


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Conn:
    async def execute(self, *_args):
        return None

    def transaction(self):
        return _AsyncContext(self)


class _Pool:
    def acquire(self):
        return _AsyncContext(_Conn())


def _wire(monkeypatch, *, served_html: str, registered_sha: str | None):
    async def app_html(*_args):
        return served_html, {"name": "ventas_semana"}

    async def scope(*_args):
        return TENANT, WORKSPACE

    async def pool():
        return _Pool()

    async def workspace_manifest(_conn, name):
        if registered_sha is None:
            return None
        return {
            "manifest_digest": DIGEST,
            "html_sha256": registered_sha,
            "cartridge_id": "workspace",
        }

    async def grants(_conn, **kwargs):
        assert kwargs["manifest_digest"] == DIGEST
        return ["ventas_diarias"]

    monkeypatch.setattr(main, "_refinement_app_html", app_html)
    monkeypatch.setattr(main, "_packaged_manifest", lambda _name: None)
    monkeypatch.setattr(main, "_served_manifest_digest", lambda *_a: None)
    monkeypatch.setattr(main, "_app_scope_for", scope)
    monkeypatch.setattr(main, "_get_db_pool", pool)
    monkeypatch.setattr(main, "_active_workspace_manifest", workspace_manifest)
    monkeypatch.setattr(main, "_granted_datasets", grants)


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"id": 7}))


@pytest.mark.asyncio
async def test_matching_workspace_publication_serves_with_grants(monkeypatch):
    sha = hashlib.sha256(HTML.encode("utf-8")).hexdigest()
    _wire(monkeypatch, served_html=HTML, registered_sha=sha)
    html_text, granted, digest, cartridge, stale = await main._app_grant_context(
        _request(), "ventas_semana", {"id": 7}
    )
    assert html_text == HTML
    assert granted == ["ventas_diarias"]
    assert digest == DIGEST
    assert cartridge == "workspace"
    assert stale is False


@pytest.mark.asyncio
async def test_html_drift_fails_closed(monkeypatch):
    _wire(monkeypatch, served_html=HTML + "<!-- drift -->",
          registered_sha=hashlib.sha256(HTML.encode("utf-8")).hexdigest())
    _html, granted, digest, _cartridge, stale = await main._app_grant_context(
        _request(), "ventas_semana", {"id": 7}
    )
    assert stale is True
    assert digest is None
    assert granted == []


@pytest.mark.asyncio
async def test_unregistered_workspace_app_gets_no_digest(monkeypatch):
    _wire(monkeypatch, served_html=HTML, registered_sha=None)
    _html, granted, digest, _cartridge, stale = await main._app_grant_context(
        _request(), "ventas_semana", {"id": 7}
    )
    assert stale is False
    assert digest is None
    assert granted == []


@pytest.mark.asyncio
async def test_stale_embed_renders_honest_state_without_capability(monkeypatch):
    _wire(monkeypatch, served_html=HTML + "x",
          registered_sha=hashlib.sha256(HTML.encode("utf-8")).hexdigest())

    def never_issue(**_kwargs):
        raise AssertionError("no capability may be issued for a stale publication")

    monkeypatch.setattr(main, "_issue_content_capability", never_issue)
    response = await main._build_app_embed_response(
        _request(), "ventas_semana", {"id": 7}
    )
    body = response.body.decode("utf-8")
    assert APP_STALE_PUBLICATION_MESSAGE in body
    assert 'role="alert"' in body
    assert "<iframe" not in body
    assert "cap=" not in body


@pytest.mark.asyncio
async def test_matching_embed_issues_a_capability(monkeypatch):
    sha = hashlib.sha256(HTML.encode("utf-8")).hexdigest()
    _wire(monkeypatch, served_html=HTML, registered_sha=sha)
    issued = []

    def issue(**kwargs):
        issued.append(kwargs)
        return "cap-token"

    monkeypatch.setattr(main, "_issue_content_capability", issue)
    response = await main._build_app_embed_response(
        _request(), "ventas_semana", {"id": 7}
    )
    body = response.body.decode("utf-8")
    assert "cap=cap-token" in body
    assert issued[0]["manifest_digest"] == DIGEST
    assert issued[0]["cartridge_id"] == "workspace"


@pytest.mark.asyncio
async def test_stale_publication_denies_scoped_data(monkeypatch):
    _wire(monkeypatch, served_html=HTML + "x",
          registered_sha=hashlib.sha256(HTML.encode("utf-8")).hexdigest())
    headers = {"sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
               "sec-fetch-site": "same-origin"}
    request = SimpleNamespace(
        state=SimpleNamespace(user={"id": 7}), headers=headers
    )
    with pytest.raises(main.HTTPException) as excinfo:
        await main._require_app_scoped_grant(
            request, "ventas_semana", "ventas_diarias", {"id": 7}
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_success_retires_the_workspace_publication(monkeypatch):
    retired = []

    class RetireConn(_Conn):
        async def execute(self, sql, *args):
            assert "set_config('app.tenant_id'" in sql
            retired.append(("scope", args))

    class RetirePool:
        def acquire(self):
            return _AsyncContext(RetireConn())

    async def pool():
        return RetirePool()

    async def scope(*_args):
        return TENANT, WORKSPACE

    async def fake_retire(_conn, *, app_name):
        retired.append(("retire", app_name))
        return 1

    monkeypatch.setattr(main, "_app_scope_for", scope)
    monkeypatch.setattr(main, "_get_db_pool", pool)
    monkeypatch.setattr(main, "_retire_workspace_app", fake_retire)
    await main._retire_workspace_app_publication("ventas_semana", {"id": 7})
    assert retired == [
        ("scope", (TENANT, WORKSPACE)),
        ("retire", "ventas_semana"),
    ]


@pytest.mark.asyncio
async def test_delete_retirement_skips_without_scope_and_never_raises(monkeypatch):
    async def scopeless(*_args):
        return "", ""

    async def broken_pool():
        raise AssertionError("no scope means no retirement query")

    monkeypatch.setattr(main, "_app_scope_for", scopeless)
    monkeypatch.setattr(main, "_get_db_pool", broken_pool)
    await main._retire_workspace_app_publication("ventas_semana", {"id": 7})

    async def scoped(*_args):
        return TENANT, WORKSPACE

    monkeypatch.setattr(main, "_app_scope_for", scoped)
    # A retirement failure is logged, never surfaced to the delete caller.
    await main._retire_workspace_app_publication("ventas_semana", {"id": 7})


def test_delete_route_calls_the_retirement_hook():
    import inspect

    source = inspect.getsource(main.api_apps_delete)
    assert "_retire_workspace_app_publication(name, user)" in source


def test_wrapper_stale_variant_is_fail_closed():
    html = app_embed_wrapper_html("ventas_semana", ["ventas_diarias"], "n" * 24,
                                  capability=None, stale=True)
    assert APP_STALE_PUBLICATION_MESSAGE in html
    assert "<iframe" not in html
    assert "/content" not in html
    normal = app_embed_wrapper_html("ventas_semana", ["ventas_diarias"], "n" * 24)
    assert "<iframe" in normal
    assert APP_STALE_PUBLICATION_MESSAGE not in normal
