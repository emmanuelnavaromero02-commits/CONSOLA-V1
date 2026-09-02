from __future__ import annotations

from importlib import import_module

import pytest

from app.services import reconcile_packaged_app_grants as startup_grants
from app.services import marketplace_service


class _Context:
    def __init__(self, value, *, events=None, label=""):
        self.value = value
        self.events = events
        self.label = label

    async def __aenter__(self):
        if self.events is not None:
            self.events.append(f"enter:{self.label}")
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        if self.events is not None:
            suffix = type(exc).__name__ if exc is not None else "ok"
            self.events.append(f"exit:{self.label}:{suffix}")
        return False


class _Connection:
    def __init__(self):
        self.events: list[str] = []
        self.scope: tuple[str, str] | None = None

    def transaction(self):
        return _Context(self, events=self.events, label="transaction")

    async def execute(self, sql: str, *args):
        if "pg_advisory_xact_lock" in sql:
            self.events.append(f"lock:{args[0]}")
        elif "set_config('app.tenant_id'" in sql:
            self.scope = (str(args[0]), str(args[1]))
            self.events.append(f"scope:{args[0]}:{args[1]}")
        else:  # pragma: no cover - makes unexpected writes fail loudly
            raise AssertionError(sql)

    async def fetch(self, sql: str, *args):
        if "FROM public.workspaces" in sql:
            return [
                {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "workspace_id": "aaaaaaaa-0000-0000-0000-000000000001",
                },
                {
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "workspace_id": "bbbbbbbb-0000-0000-0000-000000000002",
                },
            ]
        if "FROM public.cartridge_installations" in sql:
            assert self.scope == (str(args[0]), str(args[1]))
            assert "ci.status = 'ready'" in sql
            return (
                [{"cartridge_id": "replicon"}]
                if str(args[1]).endswith("1")
                else [{"cartridge_id": "sap_successfactors"}]
            )
        raise AssertionError(sql)


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Context(self.connection)


@pytest.mark.asyncio
async def test_startup_reconcile_scopes_and_serializes_each_workspace(monkeypatch):
    connection = _Connection()
    calls: list[tuple[tuple[str, str] | None, str]] = []

    async def reconcile(conn, *, cartridge_id):
        calls.append((conn.scope, cartridge_id))
        return {"granted": 1, "revoked": 0}

    monkeypatch.setattr(startup_grants, "reconcile_workspace", reconcile)

    await startup_grants.reconcile_packaged_app_grants(_Pool(connection))

    assert calls == [
        (
            (
                "11111111-1111-1111-1111-111111111111",
                "aaaaaaaa-0000-0000-0000-000000000001",
            ),
            "replicon",
        ),
        (
            (
                "22222222-2222-2222-2222-222222222222",
                "bbbbbbbb-0000-0000-0000-000000000002",
            ),
            "sap_successfactors",
        ),
    ]
    assert sum(event.startswith("lock:") for event in connection.events) == 2
    assert connection.events.count("enter:transaction") == 2
    assert connection.events.count("exit:transaction:ok") == 2


@pytest.mark.asyncio
async def test_startup_reconcile_propagates_failure_for_readiness(monkeypatch):
    connection = _Connection()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("grant reconciliation failed")

    monkeypatch.setattr(startup_grants, "reconcile_workspace", fail)

    with pytest.raises(RuntimeError, match="grant reconciliation failed"):
        await startup_grants.reconcile_packaged_app_grants(_Pool(connection))

    assert "exit:transaction:RuntimeError" in connection.events


@pytest.mark.asyncio
async def test_marketplace_transition_uses_the_same_workspace_lock(monkeypatch):
    events: list[str] = []
    runtime_grants = import_module("app.domains.apps.grants")

    class Connection:
        async def execute(self, sql, *args):
            if "pg_advisory_xact_lock" in sql:
                events.append(f"lock:{args[0]}")
            elif "set_config('app.tenant_id'" in sql:
                events.append(f"scope:{args[0]}:{args[1]}")
            else:  # pragma: no cover - makes unexpected writes fail loudly
                raise AssertionError(sql)

    async def reconcile(_conn, *, cartridge_id):
        events.append(f"reconcile:{cartridge_id}")
        return {"granted": 1, "revoked": 0}

    monkeypatch.setattr(runtime_grants, "reconcile_workspace", reconcile)

    await marketplace_service._reconcile_packaged_app_grants(
        Connection(),
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="aaaaaaaa-0000-0000-0000-000000000001",
        cartridge_id="replicon",
        installation_status="ready",
    )

    assert events[0].startswith("lock:omega:app-grants:")
    assert events[1].startswith("scope:")
    assert events[2] == "reconcile:replicon"
