from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from refinement.app import main as refinement_main

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"


def _context(*permissions: str, **extra) -> dict:
    context = {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "user_id": 7,
        "permissions": list(permissions),
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_prefixes": [],
    }
    context.update(extra)
    return context


def _body(tool: str, args: dict, *permissions: str, **extra) -> dict:
    return {
        "tool": tool,
        "args": args,
        "security_context": refinement_main._sign_security_context(
            _context(*permissions, **extra)
        ),
        "_verified_internal_service": "console",
    }


def _dataset(name: str, **extra) -> dict:
    return {
        "name": name,
        "layer": "silver",
        "cartridge": "sap_successfactors",
        "workspace_id": WORKSPACE,
        "created_by_id": None,
        "sources": [],
        **extra,
    }


class _Worker:
    def __init__(self) -> None:
        self.calls: list = []
        self.enqueued: list = []

    def catch_up(self, sec, **kwargs):
        self.calls.append((sec, kwargs))
        return SimpleNamespace(
            to_dict=lambda: {
                "status": "ready",
                "processed": 1,
                "pending": 0,
                "stale": 1,
                "annotation_epoch": "2026-09-26 12:00:00+00",
            }
        )

    def enqueue(self, sec, subject):
        self.enqueued.append((sec, subject))
        return True


@pytest.fixture()
def worker(monkeypatch):
    fake = _Worker()
    monkeypatch.setattr(refinement_main, "_catalog_copilot_worker", lambda: fake)
    return fake


def test_tool_catalogue_exposes_copilot_tools():
    import asyncio

    tools = {
        tool["name"]: tool
        for tool in asyncio.run(refinement_main.mcp_tools())["tools"]
    }
    assert set(tools["auto_catalog"]["input_schema"]["properties"]) == {
        "cartridge",
        "include_sources",
        "since",
    }
    assert tools["reject_relationship"]["input_schema"]["required"] == [
        "from_dataset",
        "from_column",
        "to_dataset",
        "to_column",
    ]
    register = tools["register_relationship"]["input_schema"]["properties"]
    assert register["join_hint"]["enum"] == ["INNER", "LEFT", "RIGHT", "FULL"]
    assert register["cardinality"]["enum"] == ["1:1", "1:N", "N:1", "N:N"]
    assert "schema_only" in tools["describe_source"]["input_schema"]["properties"]
    assert "include_sources" in tools["get_data_catalog"]["input_schema"]["properties"]


def test_auto_catalog_requires_read_permission(worker):
    with pytest.raises(HTTPException) as exc:
        refinement_main._mcp_invoke_sync(_body("auto_catalog", {}))
    assert exc.value.status_code == 403
    assert worker.calls == []


def test_auto_catalog_enqueues_with_the_verified_context(worker):
    result = refinement_main._mcp_invoke_sync(
        _body(
            "auto_catalog",
            {
                "cartridge": "sap_successfactors",
                "include_sources": True,
                "max_items": 99,
                "budget_ms": 60_000,
            },
            "datasets.read",
        )
    )
    assert result["status"] == "ready"
    sec, kwargs = worker.calls[0]
    assert sec["workspace_id"] == WORKSPACE and sec["trusted"] is True
    assert kwargs == {
        "cartridge": "sap_successfactors",
        "include_sources": True,
        "since": None,
    }


def test_auto_catalog_rejects_unsafe_cartridge_and_idles_without_workspace(worker):
    with pytest.raises(HTTPException) as exc:
        refinement_main._mcp_invoke_sync(
            _body("auto_catalog", {"cartridge": "x'; drop"}, "datasets.read")
        )
    assert exc.value.status_code == 400
    idle = refinement_main._mcp_invoke_sync(
        _body("auto_catalog", {}, "datasets.read", workspace_id="")
    )
    assert idle["status"] == "idle"
    assert worker.calls == []


def test_auto_catalog_failure_is_an_honest_503(monkeypatch):
    class Broken:
        def catch_up(self, sec, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(refinement_main, "_catalog_copilot_worker", lambda: Broken())
    with pytest.raises(HTTPException) as exc:
        refinement_main._mcp_invoke_sync(_body("auto_catalog", {}, "datasets.read"))
    assert exc.value.status_code == 503


class _Store:
    def __init__(self) -> None:
        self.rejected: list = []

    def reject_edge(self, sec, edge):
        self.rejected.append((sec["workspace_id"], edge))
        return True

    def load_annotations(self, sec, names, *, include_sources=False):
        raise RuntimeError("annotations unavailable")


def test_reject_relationship_requires_write_scope_on_both_datasets(monkeypatch):
    store = _Store()
    monkeypatch.setattr(refinement_main, "_catalog_copilot_store", lambda: store)
    datasets = {"employees": _dataset("employees"), "departments": _dataset("departments")}
    monkeypatch.setattr(
        refinement_main.store,
        "get_dataset",
        lambda name, **scope: datasets.get(name),
    )
    args = {
        "from_dataset": "employees",
        "from_column": "department_id",
        "to_dataset": "departments",
        "to_column": "department_id",
    }
    with pytest.raises(HTTPException) as exc:
        refinement_main._mcp_invoke_sync(_body("reject_relationship", args, "datasets.read"))
    assert exc.value.status_code == 403
    result = refinement_main._mcp_invoke_sync(
        _body("reject_relationship", args, "datasets.read", "datasets.write")
    )
    assert result == {
        "rejected": True,
        "relation": "employees.department_id → departments.department_id",
    }
    assert store.rejected == [(WORKSPACE, args)]
    with pytest.raises(HTTPException) as missing:
        refinement_main._mcp_invoke_sync(
            _body(
                "reject_relationship",
                {**args, "to_dataset": "ghost"},
                "datasets.write",
            )
        )
    assert missing.value.status_code == 404
    datasets["foreign"] = _dataset("foreign", workspace_id="33333333-3333-4333-8333-333333333333")
    with pytest.raises(HTTPException) as foreign:
        refinement_main._mcp_invoke_sync(
            _body("reject_relationship", {**args, "to_dataset": "foreign"}, "datasets.write")
        )
    assert foreign.value.status_code == 403
    with pytest.raises(HTTPException) as bad:
        refinement_main._mcp_invoke_sync(
            _body("reject_relationship", {**args, "from_column": 'a"b'}, "datasets.write")
        )
    assert bad.value.status_code == 400
    assert len(store.rejected) == 1


@pytest.mark.parametrize(
    "args,expected",
    [
        ({}, ("LEFT", None)),
        ({"join_hint": "many_to_one"}, ("LEFT", "N:1")),
        ({"join_hint": "one_to_one"}, ("LEFT", "1:1")),
        ({"join_hint": "inner", "cardinality": "n:1"}, ("INNER", "N:1")),
        ({"join_hint": "FULL", "cardinality": "1:N"}, ("FULL", "1:N")),
    ],
)
def test_relationship_shape_normalises_legacy_tokens(args, expected):
    assert refinement_main._relationship_shape(args) == expected


@pytest.mark.parametrize(
    "args", [{"join_hint": "COALESCE"}, {"cardinality": "many"}, {"join_hint": "LEFT; DROP"}]
)
def test_relationship_shape_rejects_unknown_tokens(args):
    with pytest.raises(HTTPException) as exc:
        refinement_main._relationship_shape(args)
    assert exc.value.status_code == 400


def test_register_relationship_writes_manual_active_rows(monkeypatch):
    captured = {}

    def pg_exec(query, params=None, fetch=False, security_context=None):
        captured["query"] = query
        captured["params"] = params
        return [{"id": 1}]

    monkeypatch.setattr(refinement_main, "_pg_exec", pg_exec)
    result = refinement_main._register_relationship(
        {
            "from_dataset": "employees",
            "from_column": "department_id",
            "to_dataset": "departments",
            "to_column": "department_id",
            "join_hint": "many_to_one",
        },
        _context("datasets.write"),
    )
    assert result["registered"] is True
    assert "'manual','active'" in captured["query"]
    assert "status      = 'active'" in captured["query"]
    assert captured["params"][4:6] == ("LEFT", "N:1")


def test_upsert_catalog_entries_rejects_unknown_origin():
    with pytest.raises(HTTPException) as exc:
        refinement_main._upsert_catalog_entries([], _context(), origin="packaged")
    assert exc.value.status_code == 400


def test_upsert_catalog_entries_guards_manual_rows_against_copilot_writes(monkeypatch):
    statements = []

    class Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, query, params=None):
            statements.append((query, params))

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: Conn())
    refinement_main._upsert_catalog_entries(
        [{"dataset": "employees", "column_name": "email", "description": "Plantilla"}],
        _context(),
        origin="copilot",
    )
    query, params = statements[-1]
    assert params["origin"] == "copilot"
    assert "data_catalog.description_origin IN ('manual', 'packaged')" in query
    assert "THEN data_catalog.description" in query


def test_describe_source_schema_only_skips_the_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(
        refinement_main.engine,
        "get_source_schema",
        lambda source, ctx: {"fields": [{"name": "a", "type": "VARCHAR"}]},
    )
    monkeypatch.setattr(
        refinement_main.engine,
        "preview_source",
        lambda *a, **k: calls.append(a) or {"data": [{"a": "secret"}]},
    )
    result = refinement_main._mcp_invoke_sync(
        _body(
            "describe_source",
            {"source": "raw/sap_successfactors/PerEmail", "schema_only": True},
            "datasets.read",
        )
    )
    assert result == {
        "source": "raw/sap_successfactors/PerEmail",
        "fields": [{"name": "a", "type": "VARCHAR"}],
        "sample": [],
        "error": None,
    }
    assert calls == []


def test_catalog_read_serves_snapshot_when_annotations_fail(monkeypatch):
    store = _Store()
    monkeypatch.setattr(refinement_main, "_catalog_copilot_store", lambda: store)
    monkeypatch.setattr(
        refinement_main.store, "list_datasets", lambda **scope: [_dataset("employees")]
    )
    captured = {}

    def published_catalog(metadata, sec, **kwargs):
        captured.update(kwargs)
        return {"datasets": {}, "relationships": []}

    monkeypatch.setattr(refinement_main, "published_catalog", published_catalog)
    monkeypatch.setattr(refinement_main, "_publication_snapshot_resolver", lambda: None)
    refinement_main._mcp_invoke_sync(_body("get_data_catalog", {}, "datasets.read"))
    annotations = captured["annotations"]
    assert annotations.degraded is True


def test_catalog_read_filters_bronze_subjects_to_the_caller_scope(monkeypatch):
    class Store:
        def load_annotations(self, sec, names, *, include_sources=False):
            assert include_sources is True
            return {
                "columns": {},
                "relationships": [],
                "subjects": {},
                "sources": [
                    {"subject": "raw/sap_successfactors/PerEmail"},
                    {"subject": "raw/hubspot/Deals"},
                ],
            }

    monkeypatch.setattr(refinement_main, "_catalog_copilot_store", lambda: Store())
    annotations = refinement_main._load_catalog_annotations(
        _context("datasets.read"), [_dataset("employees")], include_sources=True
    )
    assert [state["subject"] for state in annotations.sources] == [
        "raw/sap_successfactors/PerEmail"
    ]


def test_post_publish_enqueues_the_verified_context_and_still_reindexes(worker, monkeypatch):
    reindexed = []
    monkeypatch.setattr(
        refinement_main,
        "_reindex_dataset_best_effort",
        lambda name, body=None: reindexed.append(name),
    )
    body = _body("materialize", {"name": "employees"}, "datasets.write")
    refinement_main._post_publish_best_effort("employees", body)
    assert reindexed == ["employees"]
    sec, subject = worker.enqueued[0]
    assert subject == {"kind": "dataset", "name": "employees"}
    assert sec["workspace_id"] == WORKSPACE
    forged = {**body, "security_context": {**body["security_context"], "_signature": "0" * 64}}
    refinement_main._post_publish_best_effort("employees", forged)
    assert len(worker.enqueued) == 1
    assert reindexed == ["employees", "employees"]


def test_refresh_by_source_enqueues_the_bronze_profile_before_the_early_return(
    worker, monkeypatch
):
    monkeypatch.setattr(refinement_main.store, "list_datasets", lambda **scope: [])
    body = {
        "source": "raw/sap_successfactors/PerEmail",
        "security_context": refinement_main._sign_security_context(
            _context("datasets.read", "datasets.write")
        ),
    }
    result = refinement_main._refresh_by_source_sync(body, "console")
    assert result["status"] == "skipped"
    assert worker.enqueued[0][1] == {
        "kind": "bronze_source",
        "name": "raw/sap_successfactors/PerEmail",
    }


def test_source_allowance_is_prefix_scoped():
    sec = _context("datasets.read")
    assert refinement_main._copilot_source_allowed(sec, "raw/sap_successfactors/PerEmail")
    assert not refinement_main._copilot_source_allowed(sec, "raw/hubspot/Deals")
    assert not refinement_main._copilot_source_allowed(sec, "raw/sap_successfactors/../x")
    assert not refinement_main._copilot_source_allowed(
        sec, "raw/sap_successfactors/PerEmail/tenant_id=x"
    )


@pytest.mark.parametrize(
    "since,ok",
    [
        ("start", True),
        ("2026-09-26 12:00:00.123456+00", True),
        ("2026-09-26T12:00:00Z", True),
        ("yesterday", False),
        ("2026-09-26'; drop", False),
        (5, False),
    ],
)
def test_auto_catalog_validates_the_progress_mark(worker, since, ok):
    body = _body("auto_catalog", {"since": since}, "datasets.read")
    if ok:
        refinement_main._mcp_invoke_sync(body)
        assert worker.calls[-1][1]["since"] == since
    else:
        with pytest.raises(HTTPException) as exc:
            refinement_main._mcp_invoke_sync(body)
        assert exc.value.status_code == 400
