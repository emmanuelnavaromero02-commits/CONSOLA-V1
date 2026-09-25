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


def _console_context() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": "admin",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222",
        "allowed_cartridges": ["replicon"],
        "_signature": "stale",
        "_signed_at": 1,
    }


def test_reveal_carries_a_freshly_signed_console_context(monkeypatch):
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
    base_url, _connection, conn_id = module._get_connection("default", _console_context())
    assert base_url == "https://replicon.example.test" and conn_id == "default"
    signed = json.loads(seen[0]["x-security-context"])
    assert signed["source"] == "console" and signed["tenant_id"].startswith("1111")
    assert signed["_signature"] != "stale"
    from runtime_security_context import verify_runtime_signature

    verify_runtime_signature(signed)


@pytest.mark.parametrize(
    "context",
    [None, {}, {**_console_context(), "trusted": False}, {**_console_context(), "source": "airflow"},
     {**_console_context(), "allowed_cartridges": ["hubspot"]}],
)
def test_reveal_without_a_console_context_fails_before_any_request(monkeypatch, context):
    module = _load(monkeypatch)
    monkeypatch.setitem(
        sys.modules, "requests", types.SimpleNamespace(get=lambda *a, **k: pytest.fail("no request expected"))
    )
    with pytest.raises(RuntimeError):
        module._get_connection("default", context)
