from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.domains.data_platform.catalog_copilot_requests import (
    AutoProfileMemo,
    AutoProfileResponse,
    RejectRelationshipResponse,
    auto_profile_payload,
    catalog_annotation_epoch,
    reject_relationship_payload,
)
from app.domains.data_platform.catalog_payloads import catalog_cache_key, catalog_query_args
from app.domains.data_platform.catalog_requests import catalog_get_payload

USER = {
    "id": 7,
    "role": "user",
    "active_tenant_id": "11111111-1111-4111-8111-111111111111",
    "active_workspace_id": "22222222-2222-4222-8222-222222222222",
    "allowed_cartridges": ["sap_successfactors"],
}
OTHER_USER = {**USER, "id": 8}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Refinement:
    def __init__(self, *payloads) -> None:
        self.payloads = list(payloads)
        self.calls: list = []

    async def __call__(self, tool, args, **kwargs):
        self.calls.append((tool, args, kwargs))
        return self.payloads.pop(0)


def _ready(processed=1, pending=0):
    return {
        "status": "ready" if not pending else "working",
        "processed": processed,
        "pending": pending,
        "stale": processed + pending,
        "annotation_epoch": "2026-09-26 12:00:00+00",
    }


@pytest.mark.asyncio
async def test_auto_profile_forwards_a_bounded_request_and_invalidates_on_progress():
    refinement = _Refinement(_ready())
    invalidated = []
    result = await auto_profile_payload(
        body={"cartridge": "sap_successfactors", "include_sources": True},
        user=USER,
        refinement_invoke=refinement,
        scoped_read_cache_invalidate=lambda scope, user: invalidated.append((scope, user)),
        memo=AutoProfileMemo(),
        clock=_Clock(),
    )
    assert result == {
        "status": "ready",
        "processed": 1,
        "pending": 0,
        "stale": 1,
        "annotation_epoch": "2026-09-26 12:00:00+00",
        "cached": False,
    }
    tool, args, kwargs = refinement.calls[0]
    assert tool == "auto_catalog"
    assert args == {"include_sources": True, "cartridge": "sap_successfactors"}
    assert kwargs == {"timeout": 10, "user": USER}
    assert invalidated == [("catalog", USER)]


@pytest.mark.asyncio
async def test_auto_profile_serves_the_fresh_answer_for_thirty_seconds():
    memo, clock = AutoProfileMemo(), _Clock()
    refinement = _Refinement(_ready(), _ready(processed=0))
    kwargs = dict(
        body={},
        user=USER,
        refinement_invoke=refinement,
        scoped_read_cache_invalidate=lambda *a: None,
        memo=memo,
        clock=clock,
    )
    await auto_profile_payload(**kwargs)
    clock.now += 29
    cached = await auto_profile_payload(**kwargs)
    assert cached["cached"] is True
    assert len(refinement.calls) == 1
    clock.now += 2
    fresh = await auto_profile_payload(**kwargs)
    assert fresh["cached"] is False
    assert len(refinement.calls) == 2


@pytest.mark.asyncio
async def test_pending_work_is_re_polled_but_not_faster_than_the_min_interval():
    memo, clock = AutoProfileMemo(), _Clock()
    refinement = _Refinement(_ready(processed=1, pending=3), _ready(processed=2))
    kwargs = dict(
        body={},
        user=USER,
        refinement_invoke=refinement,
        scoped_read_cache_invalidate=lambda *a: None,
        memo=memo,
        clock=clock,
    )
    first = await auto_profile_payload(**kwargs)
    assert first["status"] == "working"
    clock.now += 1.0
    throttled = await auto_profile_payload(**kwargs)
    assert throttled["cached"] is True and throttled["pending"] == 3
    clock.now += 1.0
    polled = await auto_profile_payload(**kwargs)
    assert polled["processed"] == 2 and polled["cached"] is False
    assert len(refinement.calls) == 2


@pytest.mark.asyncio
async def test_min_interval_is_per_identity_across_filters():
    memo, clock = AutoProfileMemo(), _Clock()
    refinement = _Refinement(_ready(), _ready(), _ready())
    base = dict(
        refinement_invoke=refinement,
        scoped_read_cache_invalidate=lambda *a: None,
        memo=memo,
        clock=clock,
    )
    await auto_profile_payload(body={}, user=USER, **base)
    with pytest.raises(HTTPException) as exc:
        await auto_profile_payload(body={"cartridge": "hubspot"}, user=USER, **base)
    assert exc.value.status_code == 429
    other = await auto_profile_payload(body={}, user=OTHER_USER, **base)
    assert other["cached"] is False


@pytest.mark.asyncio
async def test_auto_profile_rejects_unsafe_cartridges_and_sanitises_upstream():
    with pytest.raises(HTTPException) as exc:
        await auto_profile_payload(
            body={"cartridge": "../etc"},
            user=USER,
            refinement_invoke=_Refinement(),
            scoped_read_cache_invalidate=lambda *a: None,
            memo=AutoProfileMemo(),
        )
    assert exc.value.status_code == 400
    odd = await auto_profile_payload(
        body={},
        user=USER,
        refinement_invoke=_Refinement(
            {"status": "exploded", "processed": -4, "pending": True, "extra": {"a": 1}}
        ),
        scoped_read_cache_invalidate=lambda *a: None,
        memo=AutoProfileMemo(),
    )
    assert odd == {
        "status": "idle",
        "processed": 0,
        "pending": 0,
        "stale": 0,
        "annotation_epoch": None,
        "cached": False,
    }


def test_response_models_forbid_unknown_fields():
    with pytest.raises(Exception):
        AutoProfileResponse(status="idle", processed=0, pending=0, stale=0, secret=1)
    with pytest.raises(Exception):
        RejectRelationshipResponse(rejected=True, relation="a", extra="x")
    for model in (AutoProfileResponse, RejectRelationshipResponse):
        assert model.model_config["extra"] == "forbid"
        for field in model.model_fields.values():
            assert "dict" not in str(field.annotation).lower()
            assert "any" not in str(field.annotation).lower()


@pytest.mark.asyncio
async def test_reject_relationship_validates_and_invalidates():
    refinement = _Refinement({"rejected": True, "relation": "x"})
    invalidated = []
    body = {
        "from_dataset": "employees",
        "from_column": "department_id",
        "to_dataset": "departments",
        "to_column": "department_id",
    }
    result = await reject_relationship_payload(
        body=body,
        user=USER,
        refinement_invoke=refinement,
        scoped_read_cache_invalidate=lambda scope, user: invalidated.append(scope),
    )
    assert result == {
        "rejected": True,
        "relation": "employees.department_id → departments.department_id",
    }
    assert refinement.calls[0][0] == "reject_relationship"
    assert refinement.calls[0][1] == body
    assert invalidated == ["catalog"]
    for bad in (
        {**body, "from_dataset": "x;drop"},
        {**body, "to_column": 'a"b'},
        {**body, "to_column": ""},
    ):
        with pytest.raises(HTTPException) as exc:
            await reject_relationship_payload(
                body=bad,
                user=USER,
                refinement_invoke=refinement,
                scoped_read_cache_invalidate=lambda *a: None,
            )
        assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        await reject_relationship_payload(
            body=body,
            user=USER,
            refinement_invoke=_Refinement({"rejected": False}),
            scoped_read_cache_invalidate=lambda *a: None,
        )
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_annotation_epoch_reads_the_scoped_state_and_fails_soft():
    seen = {}

    class Conn:
        async def fetchval(self, sql, workspace_id):
            seen["sql"] = sql
            seen["workspace"] = workspace_id
            return "2026-09-26 12:00:00+00"

    @asynccontextmanager
    async def scoped(pool, user):
        yield Conn(), "tenant", "workspace-a"

    async def pool():
        return object()

    epoch = await catalog_annotation_epoch(USER, pool_factory=pool, scoped_db=scoped)
    assert epoch == "2026-09-26 12:00:00+00"
    assert "catalog_copilot_state" in seen["sql"]
    assert seen["workspace"] == "workspace-a"

    async def broken():
        raise RuntimeError("db down")

    assert await catalog_annotation_epoch(USER, pool_factory=broken, scoped_db=scoped) is None


@pytest.mark.asyncio
async def test_catalog_get_keys_the_cache_by_annotation_epoch_and_sources():
    captured = {}

    async def scope_arg(_user, cartridge):
        return "sap_successfactors"

    async def refinement(tool, args, **kwargs):
        captured["args"] = args
        return {"datasets": {}}

    async def cache(scope, user, key, loader):
        captured["key"] = key
        return await loader()

    await catalog_get_payload(
        layer="",
        cartridge="",
        tags="",
        datasets="",
        include_sources=True,
        annotation_epoch="2026-09-26 12:00:00+00",
        user=USER,
        scope_catalog_cartridge_arg=scope_arg,
        user_allowed_cartridges=lambda _u: ["sap_successfactors"],
        empty_catalog_payload=lambda: {},
        catalog_query_args=catalog_query_args,
        catalog_cache_key=catalog_cache_key,
        refinement_invoke=refinement,
        raise_for_refinement_payload_error=lambda *a: None,
        scoped_read_cache_get_or_set=cache,
    )
    assert captured["args"] == {"cartridge": "sap_successfactors", "include_sources": True}
    assert captured["key"] == (
        '{"cartridge": "sap_successfactors", "include_sources": true}',
        "2026-09-26 12:00:00+00",
    )


@pytest.mark.asyncio
async def test_semantic_enrichment_writes_with_the_copilot_origin():
    from app.domains.data_platform.semantic_requests import semantic_enrich_payload

    calls = []

    async def scope_arg(_user, cartridge):
        return "sap_successfactors"

    async def refinement(tool, args, **kwargs):
        calls.append((tool, args))
        if tool == "get_data_catalog":
            return {
                "datasets": {
                    "employees": {
                        "layer": "gold",
                        "columns": [{"name": "employee_id", "type": "VARCHAR"}],
                    }
                }
            }
        return {"updated": 1}

    await semantic_enrich_payload(
        body={},
        user=USER,
        scope_catalog_cartridge_arg=scope_arg,
        refinement_invoke=refinement,
        scoped_read_cache_invalidate=lambda *a: None,
    )
    upsert = [args for tool, args in calls if tool == "upsert_catalog_entries"]
    assert upsert and upsert[0]["origin"] == "copilot"
