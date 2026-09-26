from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = "replicon-reveal-signing-key-with-enough-entropy-000000"


def _load(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", "airflow-to-console")
    monkeypatch.syspath_prepend(str(ROOT / "airflow" / "dags"))
    decorators = types.ModuleType("airflow.decorators")
    decorators.dag = lambda *a, **k: (lambda fn: fn)
    def _inert(*_args, **_kwargs):
        return None

    decorators.task = lambda *a, **k: _inert if a and callable(a[0]) else (lambda _fn: _inert)
    decorators.get_current_context = lambda: {}
    models = types.ModuleType("airflow.models")
    models.Variable = types.SimpleNamespace(get=lambda *a, **k: None)
    operators = types.ModuleType("airflow.operators.python")
    operators.get_current_context = lambda: {}
    airflow = types.ModuleType("airflow")
    for name, module in {
        "airflow": airflow,
        "airflow.decorators": decorators,
        "airflow.models": models,
        "airflow.operators": types.ModuleType("airflow.operators"),
        "airflow.operators.python": operators,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(ROOT / "cartridges" / "replicon" / "dags"))
    monkeypatch.delitem(sys.modules, "replicon_extract", raising=False)
    return importlib.import_module("replicon_extract")


def _console_context(**overrides) -> dict:
    context = {
        "trusted": True,
        "source": "console",
        "role": "admin",
        "user_id": "user-1",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222",
        "permissions": ["pipelines.run"],
        "allowed_cartridges": ["replicon"],
    }
    context.update(overrides)
    return context


def _admit(context: dict):
    import cartridge_run_admission
    from runtime_security_context import sign_runtime_context

    return cartridge_run_admission.admit_run(
        {"security_context": sign_runtime_context(context)}, cartridge_id="replicon", dag_run=None
    )


def _no_requests(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "requests", types.SimpleNamespace(get=lambda *a, **k: pytest.fail("no request expected"))
    )


def test_reveal_carries_a_freshly_minted_context_for_the_admitted_run(monkeypatch):
    module = _load(monkeypatch)
    seen: list[dict] = []

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"base_url": "https://replicon.example.test", "auth_method": "bearer_token", "token": "t"}

    def fake_get(url, headers, timeout):
        seen.append(headers)
        return _Response()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    admitted = _admit(_console_context(allowed_cartridges=["*"], permissions=["pipelines.run", "users.manage"]))
    base_url, _connection, conn_id = module._get_connection("default", admitted)
    assert base_url == "https://replicon.example.test" and conn_id == "default"
    signed = json.loads(seen[0]["x-security-context"])
    assert signed["source"] == "airflow" and signed["tenant_id"].startswith("1111")
    assert signed["allowed_cartridges"] == ["replicon"]
    assert "users.manage" not in signed["permissions"]
    assert signed["on_behalf_of"] == "user-1"
    from runtime_security_context import verify_runtime_signature

    verify_runtime_signature(signed)


def test_a_stale_console_context_is_no_longer_re_signed(monkeypatch):
    _load(monkeypatch)
    import cartridge_run_admission

    stale = {**_console_context(), "_signature": "stale", "_signed_at": 1, "_signature_version": "hmac-sha256-v1"}
    with pytest.raises(ValueError):
        cartridge_run_admission.admit_run({"security_context": stale}, cartridge_id="replicon", dag_run=None)


@pytest.mark.parametrize(
    "context",
    [
        {**_console_context(), "trusted": False},
        {**_console_context(), "source": "agent_runner"},
        {**_console_context(), "allowed_cartridges": ["hubspot"]},
        {**_console_context(), "permissions": ["datasets.read"]},
    ],
)
def test_contexts_that_are_not_replicon_run_authority_are_refused(monkeypatch, context):
    _load(monkeypatch)
    _no_requests(monkeypatch)
    with pytest.raises(ValueError):
        _admit(context)


@pytest.mark.parametrize("admitted", [None, {}, _console_context(), "replicon"])
def test_reveal_without_an_admitted_run_fails_before_any_request(monkeypatch, admitted):
    module = _load(monkeypatch)
    _no_requests(monkeypatch)
    with pytest.raises(RuntimeError):
        module._get_connection("default", admitted)


def test_reveal_refuses_a_run_admitted_for_another_cartridge(monkeypatch):
    module = _load(monkeypatch)
    _no_requests(monkeypatch)
    import cartridge_run_admission

    other = cartridge_run_admission.service_run(
        "hubspot",
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        principal="airflow:test",
    )
    with pytest.raises(RuntimeError):
        module._get_connection("default", other)
