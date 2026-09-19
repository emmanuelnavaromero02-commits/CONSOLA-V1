"""Mission 5: deferred narration job — claim, budget, CAS, attestation, no platform key."""

from __future__ import annotations

import ast
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services import llm_client
from app.services.db_scope import SET_SCOPE_SQL
from app.services.intelligence import narrative_job, narrative_service
from app.services.intelligence.alert_narrative import (
    NARRATIVE_ATTESTATION_KEY,
    attest_narrative,
    narrate_alert,
    published_narrative,
    verified_stored_narrative,
)
from app.services.security_context import build_security_context

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER_TENANT = "55555555-5555-4555-8555-555555555555"
OTHER_WORKSPACE = "44444444-4444-4444-8444-444444444444"
PROSE = "Finanzas muestra senales agregadas que conviene revisar con el equipo."
PLANTED = "Finanzas muestra senales agregadas que conviene revisar."
NOW = datetime(2026, 9, 15, 12, 30, tzinfo=UTC)
DAY_START = datetime(2026, 9, 15, tzinfo=UTC)


def _metadata(**metrics: object) -> dict:
    return {
        "source": "agent",
        "agent_id": "33333333-3333-4333-8333-333333333333",
        "alert_type": "wisdombit_monitor",
        "analysis_evidence": {
            "analysis_type": "wb-finanzas_monitor",
            "engine": "wisdom_bit",
            "engine_run_id": "agent:a:run:1:wisdombit:WB-FINANZAS",
            "confidence": 0.8,
            "quantiles": {"p10": None, "p50": None, "p90": None},
            "metrics": {
                "status": "degraded",
                "signal_count": 3,
                "blocker_count": 0,
                "scheduled_fire_at": "2026-09-15T08:00:00+00:00",
                **metrics,
            },
            "blockers": [],
        },
    }


async def _fresh(metadata: dict, prose: str | None = PROSE) -> dict:
    return await narrate_alert(
        domain="Finanzas",
        severity="medium",
        metadata=metadata,
        llm_caller=None if prose is None else (lambda _prompt: prose),
    )


def _sign(
    narrative: dict,
    *,
    item_id: str = "alert-1",
    tenant_id: str = TENANT,
    workspace_id: str = WORKSPACE,
) -> dict:
    signed = attest_narrative(
        narrative, item_id=item_id, tenant_id=tenant_id, workspace_id=workspace_id
    )
    # The test keyring from conftest must be usable, or every test below would
    # silently exercise the unsigned path.
    assert NARRATIVE_ATTESTATION_KEY in signed
    return signed


def _verifies(stored: Any, item_id: str = "alert-1") -> bool:
    return (
        verified_stored_narrative(
            stored, item_id=item_id, tenant_id=TENANT, workspace_id=WORKSPACE
        )
        is not None
    )


# ── Fake asyncpg pool ────────────────────────────────────────────────────────


class _FakeDB:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.llm_events: dict[str, int] = {}
        self.reserved: list[tuple[str, str, dict]] = []
        self.statements: list[tuple[str, tuple]] = []
        self.open_transactions = 0
        self.epoch = 1_000_000.0
        self.fail_candidates_for: set[str] = set()
        self.fail_claim_for: set[str] = set()
        self.on_claim: Any = None
        self.scopes = [{"tenant_id": TENANT, "workspace_id": WORKSPACE}]

    def add_alert(
        self,
        item_id: str,
        *,
        tenant_id: str = TENANT,
        workspace_id: str = WORKSPACE,
        metadata: dict | None = None,
        status: str = "open",
    ) -> None:
        self.rows[(workspace_id, item_id)] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "item_id": item_id,
            "domain": "Finanzas",
            "severity": "medium",
            "status": status,
            "item_kind": "agent_alert",
            "metadata": metadata if metadata is not None else _metadata(),
        }

    def metadata(self, item_id: str, workspace_id: str = WORKSPACE) -> dict:
        return self.rows[(workspace_id, item_id)]["metadata"]

    def executed(self, sql: str) -> list[tuple]:
        return [args for statement, args in self.statements if statement == sql]


class _FakeConn:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db
        self.scope: tuple[str, str] | None = None

    @asynccontextmanager
    async def transaction(self):
        self.db.open_transactions += 1
        try:
            yield
        finally:
            self.db.open_transactions -= 1

    def _in_scope(self, tenant_id: str, workspace_id: str) -> None:
        # Every statement filters by the same tenant/workspace the RLS GUCs set.
        assert self.db.open_transactions == 1
        assert self.scope == (tenant_id, workspace_id)

    async def execute(self, sql: str, *args: Any) -> str:
        self.db.statements.append((sql, args))
        if sql == SET_SCOPE_SQL:
            self.scope = (args[0], args[1])
            return "SELECT 1"
        if sql == narrative_job.BUDGET_LOCK_SQL:
            assert self.scope is not None
            assert args == (
                f"control_room_narrative_budget:{self.scope[0]}:{self.scope[1]}",
            )
            return "SELECT 1"
        if sql == narrative_job.BUDGET_RESERVE_SQL:
            tenant_id, workspace_id, item_id, metadata = args
            self._in_scope(tenant_id, workspace_id)
            self.db.llm_events[workspace_id] = (
                self.db.llm_events.get(workspace_id, 0) + 1
            )
            self.db.reserved.append((workspace_id, item_id, json.loads(metadata)))
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {sql}")

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        self.db.statements.append((sql, args))
        assert sql == narrative_job.CANDIDATES_SQL
        tenant_id, workspace_id, statuses, limit = args
        self._in_scope(tenant_id, workspace_id)
        if workspace_id in self.db.fail_candidates_for:
            raise RuntimeError("relation exploded: secret-driver-detail")
        rows = [
            row
            for (row_workspace, _), row in self.db.rows.items()
            if row_workspace == workspace_id
            and row["tenant_id"] == tenant_id
            and row["item_kind"] == "agent_alert"
            and row["status"] in statuses
            and "analysis_evidence" in row["metadata"]
        ]
        # asyncpg without a JSONB codec returns metadata as text.
        return [
            {
                "item_id": row["item_id"],
                "domain": row["domain"],
                "severity": row["severity"],
                "metadata": json.dumps(row["metadata"]),
            }
            for row in rows[:limit]
        ]

    async def fetchval(self, sql: str, *args: Any) -> int:
        self.db.statements.append((sql, args))
        assert sql == narrative_job.BUDGET_COUNT_SQL
        tenant_id, workspace_id, start, end = args
        self._in_scope(tenant_id, workspace_id)
        assert start == DAY_START
        assert end == DAY_START + timedelta(days=1)
        return self.db.llm_events.get(workspace_id, 0)

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        self.db.statements.append((sql, args))
        if sql == narrative_job.CLAIM_SQL:
            tenant_id, workspace_id, item_id, token, ttl = args
            self._in_scope(tenant_id, workspace_id)
            if item_id in self.db.fail_claim_for:
                raise ConnectionError("claim exploded: secret-driver-detail")
            if self.db.on_claim is not None:
                self.db.on_claim(item_id)
            row = self.db.rows.get((workspace_id, item_id))
            if row is None or row["tenant_id"] != tenant_id:
                return None
            metadata = row["metadata"]
            claim = metadata.get("narrative_claim")
            if (
                isinstance(claim, dict)
                and isinstance(claim.get("claimed_epoch"), (int, float))
                and claim["claimed_epoch"] > self.db.epoch - ttl
            ):
                return None
            metadata["narrative_claim"] = {
                "token": token,
                "claimed_epoch": self.db.epoch,
            }
            return {
                "item_id": item_id,
                "domain": row["domain"],
                "severity": row["severity"],
                "metadata": json.dumps(metadata),
            }
        if sql == narrative_job.WRITE_SQL:
            tenant_id, workspace_id, item_id, narrative, token = args
            self._in_scope(tenant_id, workspace_id)
            row = self.db.rows.get((workspace_id, item_id))
            if row is None or row["tenant_id"] != tenant_id:
                return None
            claim = row["metadata"].get("narrative_claim")
            if not isinstance(claim, dict) or claim.get("token") != token:
                return None
            row["metadata"].pop("narrative_claim")
            row["metadata"]["narrative"] = json.loads(narrative)
            return {"item_id": item_id}
        raise AssertionError(f"unexpected fetchrow: {sql}")


class _Acquire:
    def __init__(self, db: _FakeDB) -> None:
        self.conn = _FakeConn(db)

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db

    def acquire(self) -> _Acquire:
        return _Acquire(self.db)


class _Caller:
    def __init__(
        self,
        db: _FakeDB,
        reply: object = PROSE,
        *,
        raises: BaseException | None = None,
        sleep: float = 0.0,
        during: Any = None,
    ) -> None:
        self.db = db
        self.reply = reply
        self.raises = raises
        self.sleep = sleep
        self.during = during
        self.prompts: list[str] = []
        self.scopes: list[tuple[str, str]] = []

    def factory(self, tenant_id: str, workspace_id: str):
        self.scopes.append((tenant_id, workspace_id))

        async def call(prompt: str) -> object:
            self.prompts.append(prompt)
            # The claim is committed: no transaction, so no row lock, is held.
            assert self.db.open_transactions == 0
            if self.during is not None:
                self.during()
            if self.sleep:
                await asyncio.sleep(self.sleep)
            if self.raises is not None:
                raise self.raises
            return self.reply

        return call


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    fake = _FakeDB()

    async def active_scopes(_pool: Any) -> list[dict[str, str]]:
        return [dict(scope) for scope in fake.scopes]

    monkeypatch.setattr(
        narrative_job.scheduled_runtime, "_active_workspace_scopes", active_scopes
    )
    return fake


async def _run(db: _FakeDB, factory: Any = None) -> dict:
    return await narrative_job.narrate_pending_alerts(
        _FakePool(db), llm_caller_factory=factory, now=NOW
    )


def test_prose_fixture_still_passes_the_strict_validator():
    for sentence in (PROSE, PLANTED):
        prose, reason = narrative_service._validate_prose(
            sentence,
            basis_code=narrative_service.basis_code_for({}),
            denylist=frozenset(),
        )
        assert (prose, reason) == (sentence, "ok")


# ── Happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ready_prose_is_signed_and_stored_through_claim_reservation_and_cas(
    db,
):
    db.add_alert("alert-1")
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["narrated_ready"] == 1
    assert result["candidates"] == 1
    assert result["status"] == "ready"
    assert result["failures"] == []
    assert len(caller.prompts) == 1
    assert caller.scopes == [(TENANT, WORKSPACE)]

    metadata = db.metadata("alert-1")
    stored = metadata["narrative"]
    assert "narrative_claim" not in metadata
    assert stored["status"] == "ready"
    assert stored["explanation"] == PROSE
    assert _verifies(stored)
    published = published_narrative(
        domain="Finanzas",
        severity="medium",
        metadata=metadata,
        item_id="alert-1",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    )
    assert published["status"] == "ready"
    assert published["explanation"] == PROSE
    assert stored["source_fingerprint"] == published["source_fingerprint"]
    assert db.llm_events[WORKSPACE] == 1
    assert db.reserved == [
        (
            WORKSPACE,
            "alert-1",
            {"narrator_version": narrative_service.NARRATOR_VERSION},
        )
    ]

    order = [statement for statement, _ in db.statements if statement != SET_SCOPE_SQL]
    assert order == [
        narrative_job.CANDIDATES_SQL,
        narrative_job.CLAIM_SQL,
        narrative_job.BUDGET_LOCK_SQL,
        narrative_job.BUDGET_COUNT_SQL,
        narrative_job.BUDGET_RESERVE_SQL,
        narrative_job.WRITE_SQL,
    ]
    (candidate_args,) = db.executed(narrative_job.CANDIDATES_SQL)
    assert candidate_args == (
        TENANT,
        WORKSPACE,
        ["open", "in_review", "decision_created", "approved"],
        narrative_job.MAX_ALERTS_PER_TICK,
    )
    (claim_args,) = db.executed(narrative_job.CLAIM_SQL)
    assert len(claim_args) == 5
    assert claim_args[:3] == (TENANT, WORKSPACE, "alert-1")
    assert claim_args[4] == float(narrative_job.CLAIM_TTL_SECONDS) == 300.0


@pytest.mark.asyncio
async def test_stored_attestation_is_bound_to_item_and_scope(db):
    db.add_alert("alert-1")

    await _run(db, _Caller(db).factory)

    stored = db.metadata("alert-1")["narrative"]
    assert set(stored[NARRATIVE_ATTESTATION_KEY]) == {"key_id", "signature"}
    assert _verifies(stored)
    assert not _verifies(stored, item_id="alert-2")
    assert (
        verified_stored_narrative(
            stored, item_id="alert-1", tenant_id=TENANT, workspace_id=OTHER_WORKSPACE
        )
        is None
    )
    tampered = {**stored, "explanation": PLANTED}
    assert not _verifies(tampered)


@pytest.mark.asyncio
async def test_at_most_twenty_alerts_per_workspace_per_tick(db):
    for index in range(25):
        db.add_alert(f"alert-{index:02d}", metadata=_metadata(signal_count=index + 2))
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert narrative_job.MAX_ALERTS_PER_TICK == 20
    assert result["candidates"] == 20
    assert len(caller.prompts) == 20
    assert result["narrated_ready"] == 20


# ── Skip / trust ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("prose", [None, PROSE], ids=("template", "ready"))
async def test_skips_alert_whose_signed_narrative_is_current(db, prose):
    metadata = _metadata()
    metadata["narrative"] = _sign(await _fresh(metadata, prose))
    db.add_alert("alert-1", metadata=metadata)
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["skipped_current"] == 1
    assert result["narrated_ready"] == result["narrated_template"] == 0
    assert caller.prompts == []
    assert db.executed(narrative_job.CLAIM_SQL) == []
    assert db.llm_events == {}


@pytest.mark.asyncio
async def test_signed_narrative_from_a_previous_occurrence_is_renarrated(db):
    metadata = _metadata(signal_count=5)
    metadata["narrative"] = _sign(await _fresh(_metadata(), PLANTED))
    db.add_alert("alert-1", metadata=metadata)
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["narrated_ready"] == 1
    assert db.metadata("alert-1")["narrative"]["explanation"] == PROSE


@pytest.mark.asyncio
@pytest.mark.parametrize("forgery", ["unsigned", "forged_signature", "bad_key_id"])
async def test_planted_ready_narrative_with_matching_fingerprint_is_renarrated(
    db, forgery
):
    metadata = _metadata()
    planted = await _fresh(metadata, PLANTED)
    if forgery == "forged_signature":
        signed = _sign(planted)
        planted = {
            **planted,
            NARRATIVE_ATTESTATION_KEY: {
                "key_id": signed[NARRATIVE_ATTESTATION_KEY]["key_id"],
                "signature": "0" * 64,
            },
        }
    elif forgery == "bad_key_id":
        signed = _sign(planted)
        planted = {
            **planted,
            NARRATIVE_ATTESTATION_KEY: {
                "key_id": "attacker-key",
                "signature": signed[NARRATIVE_ATTESTATION_KEY]["signature"],
            },
        }
    metadata["narrative"] = planted
    db.add_alert("alert-1", metadata=metadata)
    # The fingerprint matches the alert as it is now: only the signature is wrong.
    assert planted["source_fingerprint"] == (await _fresh(_metadata()))["source_fingerprint"]
    assert planted["status"] == "ready"
    assert not _verifies(planted)
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["skipped_current"] == 0
    assert result["narrated_ready"] == 1
    assert len(caller.prompts) == 1
    stored = db.metadata("alert-1")["narrative"]
    assert stored["explanation"] == PROSE
    assert _verifies(stored)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign",
    [
        {"item_id": "alert-2"},
        {"workspace_id": OTHER_WORKSPACE},
        {"tenant_id": OTHER_TENANT},
    ],
    ids=("other-item", "other-workspace", "other-tenant"),
)
async def test_narrative_signed_for_another_item_or_scope_is_not_trusted(db, foreign):
    metadata = _metadata()
    metadata["narrative"] = _sign(await _fresh(metadata, PLANTED), **foreign)
    db.add_alert("alert-1", metadata=metadata)
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["skipped_current"] == 0
    assert result["narrated_ready"] == 1
    assert len(caller.prompts) == 1
    stored = db.metadata("alert-1")["narrative"]
    assert stored["explanation"] == PROSE
    assert _verifies(stored)


@pytest.mark.asyncio
async def test_post_claim_current_check_releases_claim_without_budget(db):
    db.add_alert("alert-1")
    other_runner_narrative = _sign(await _fresh(_metadata(), PLANTED))

    def other_runner_finishes(item_id: str) -> None:
        # Between candidate selection and our claim, another runner stored a
        # verified narrative for this very occurrence.
        db.metadata(item_id)["narrative"] = dict(other_runner_narrative)

    db.on_claim = other_runner_finishes
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["skipped_current"] == 1
    assert result["lost_claim"] == 0
    assert result["narrated_ready"] == result["narrated_template"] == 0
    assert caller.prompts == []
    assert db.llm_events == {}
    assert db.executed(narrative_job.BUDGET_LOCK_SQL) == []
    assert db.executed(narrative_job.BUDGET_COUNT_SQL) == []
    assert db.executed(narrative_job.BUDGET_RESERVE_SQL) == []

    (claim_args,) = db.executed(narrative_job.CLAIM_SQL)
    (write_args,) = db.executed(narrative_job.WRITE_SQL)
    assert write_args[4] == claim_args[3]  # released under our own token
    metadata = db.metadata("alert-1")
    assert "narrative_claim" not in metadata
    assert metadata["narrative"] == other_runner_narrative
    assert _verifies(metadata["narrative"])


@pytest.mark.asyncio
async def test_post_claim_unsigned_narrative_does_not_release_the_claim(db):
    db.add_alert("alert-1")
    planted = await _fresh(_metadata(), PLANTED)
    assert planted["status"] == "ready"

    def planter(item_id: str) -> None:
        db.metadata(item_id)["narrative"] = dict(planted)

    db.on_claim = planter
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["skipped_current"] == 0
    assert result["narrated_ready"] == 1
    assert len(caller.prompts) == 1
    assert db.llm_events[WORKSPACE] == 1
    assert _verifies(db.metadata("alert-1")["narrative"])


# ── Claim / CAS ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_claim_held_elsewhere_means_no_llm_call(db):
    metadata = _metadata()
    metadata["narrative_claim"] = {"token": "other-runner", "claimed_epoch": db.epoch - 10}
    db.add_alert("alert-1", metadata=metadata)
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["lost_claim"] == 1
    assert caller.prompts == []
    assert db.llm_events == {}
    assert db.executed(narrative_job.BUDGET_RESERVE_SQL) == []
    assert db.executed(narrative_job.WRITE_SQL) == []
    assert db.metadata("alert-1")["narrative_claim"]["token"] == "other-runner"
    assert "narrative" not in db.metadata("alert-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim",
    [
        {"token": "crashed-runner", "claimed_epoch": 1_000_000.0 - 301},
        {"token": "crashed-runner", "claimed_epoch": "yesterday"},
    ],
    ids=("expired", "malformed"),
)
async def test_expired_or_malformed_claim_is_reclaimed(db, claim):
    metadata = _metadata()
    metadata["narrative_claim"] = claim
    db.add_alert("alert-1", metadata=metadata)
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["narrated_ready"] == 1
    assert "narrative_claim" not in db.metadata("alert-1")


@pytest.mark.asyncio
async def test_cas_token_mismatch_reports_lost_claim_and_writes_nothing(db):
    db.add_alert("alert-1")

    def steal() -> None:
        db.metadata("alert-1")["narrative_claim"]["token"] = "faster-runner"

    caller = _Caller(db, during=steal)

    result = await _run(db, caller.factory)

    assert len(caller.prompts) == 1
    assert result["lost_claim"] == 1
    assert result["narrated_ready"] == result["narrated_template"] == 0
    assert "narrative" not in db.metadata("alert-1")
    assert db.metadata("alert-1")["narrative_claim"]["token"] == "faster-runner"


# ── Budget ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_exhausted_at_fifty_stores_template_without_llm_call(db):
    db.add_alert("alert-1")
    db.llm_events[WORKSPACE] = narrative_job.MAX_LLM_CALLS_PER_DAY
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert narrative_job.MAX_LLM_CALLS_PER_DAY == 50
    assert result["budget_exhausted"] == 1
    assert result["narrated_template"] == 1
    assert caller.prompts == []
    assert db.llm_events[WORKSPACE] == 50
    assert db.executed(narrative_job.BUDGET_RESERVE_SQL) == []
    stored = db.metadata("alert-1")["narrative"]
    assert stored["status"] == "template"
    assert stored["reason"] == "no_llm"
    assert _verifies(stored)
    assert "narrative_claim" not in db.metadata("alert-1")


@pytest.mark.asyncio
async def test_budget_is_shared_across_alerts_of_the_workspace(db):
    db.add_alert("alert-1", metadata=_metadata(signal_count=2))
    db.add_alert("alert-2", metadata=_metadata(signal_count=4))
    db.llm_events[WORKSPACE] = 49
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert len(caller.prompts) == 1
    assert result["narrated_ready"] == 1
    assert result["budget_exhausted"] == 1
    assert result["narrated_template"] == 1
    assert db.llm_events[WORKSPACE] == 50


@pytest.mark.asyncio
async def test_reservation_counts_even_when_the_call_fails(db):
    db.add_alert("alert-1")
    caller = _Caller(db, raises=RuntimeError("provider down"))

    await _run(db, caller.factory)

    assert db.llm_events[WORKSPACE] == 1


# ── Provider failures ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_timeout_stores_template_with_closed_reason(db, monkeypatch):
    monkeypatch.setattr(narrative_service, "LLM_TIMEOUT_SECONDS", 0.01)
    db.add_alert("alert-1")
    caller = _Caller(db, sleep=1.0)

    result = await _run(db, caller.factory)

    assert result["narrated_template"] == 1
    stored = db.metadata("alert-1")["narrative"]
    assert stored["status"] == "template"
    assert stored["reason"] == "llm_timeout"
    assert stored["reason"] in narrative_service.NARRATIVE_REASONS


@pytest.mark.asyncio
async def test_llm_exception_stores_template_without_exception_text(db):
    db.add_alert("alert-1")
    caller = _Caller(db, raises=RuntimeError("sk-ant-secret provider-detail"))

    result = await _run(db, caller.factory)

    assert result["narrated_template"] == 1
    stored = db.metadata("alert-1")["narrative"]
    assert stored["status"] == "template"
    assert stored["reason"] == "llm_error"
    assert "sk-ant" not in json.dumps(stored)
    assert "provider-detail" not in json.dumps(result)


# ── No platform-key fallback ─────────────────────────────────────────────────


def test_narrator_context_is_scoped_non_admin_and_signed():
    ctx = narrative_job.narrator_user_context(TENANT, WORKSPACE)

    assert ctx["role"] not in {"owner", "super_admin", "admin"}
    assert not llm_client._is_platform_admin_context(ctx)
    assert llm_client._tenant_scope_parts(ctx) == (TENANT, WORKSPACE)

    signed = build_security_context(ctx)
    # What vault's _require_secret_scope needs for a workspace scope: a trusted
    # signed context with tenant and workspace, not an unscoped admin.
    assert signed["trusted"] is True
    assert signed["_signature"]
    assert signed["tenant_id"] == TENANT
    assert signed["workspace_id"] == WORKSPACE
    assert signed["role"] == narrative_job.NARRATOR_ROLE
    assert signed["role"] not in {"owner", "super_admin", "admin"}
    assert signed["allowed_cartridges"] == []
    assert not any(
        permission.startswith(("vault.", "llm.", "iam.", "control_room."))
        for permission in signed["permissions"]
    )


@pytest.mark.asyncio
async def test_resolve_key_never_reads_platform_env_for_narrator(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-platform-must-not-be-used")
    seen: list[dict] = []

    async def no_workspace_key(scope, key, user_context=None):
        seen.append({"scope": scope, "key": key, "ctx": dict(user_context or {})})
        return None

    monkeypatch.setattr(llm_client, "_vault_secret", no_workspace_key)

    with pytest.raises(llm_client.LLMConfigurationError):
        await llm_client._resolve_anthropic_api_key(
            narrative_job.narrator_user_context(TENANT, WORKSPACE)
        )
    assert [(item["scope"], item["key"]) for item in seen] == [
        ("llm", "anthropic_api_key")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("vault", ["missing_key", "vault_down"])
async def test_default_factory_without_workspace_key_stores_template(
    db, monkeypatch, vault
):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-platform-must-not-be-used")
    vault_contexts: list[dict] = []
    provider_keys: list[object] = []

    async def fake_vault(scope, key, user_context=None):
        vault_contexts.append(dict(user_context or {}))
        if vault == "vault_down":
            raise llm_client.LLMConfigurationError(
                "workspace LLM credential lookup failed"
            )
        return None

    async def fake_anthropic_chat(*_args, **kwargs):
        provider_keys.append(kwargs.get("api_key"))
        return PROSE, [], []

    monkeypatch.setattr(llm_client, "_vault_secret", fake_vault)
    monkeypatch.setattr(llm_client, "_anthropic_chat", fake_anthropic_chat)
    db.add_alert("alert-1")

    result = await _run(db, None)

    assert provider_keys == []
    assert result["narrated_template"] == 1
    stored = db.metadata("alert-1")["narrative"]
    assert stored["status"] == "template"
    assert stored["reason"] == "llm_error"
    assert len(vault_contexts) == 1
    assert vault_contexts[0]["role"] == narrative_job.NARRATOR_ROLE
    assert vault_contexts[0]["tenant_id"] == TENANT
    assert vault_contexts[0]["workspace_id"] == WORKSPACE

    with pytest.raises(llm_client.LLMConfigurationError):
        await narrative_job.default_llm_caller_factory(TENANT, WORKSPACE)("prompt")
    assert provider_keys == []


@pytest.mark.asyncio
async def test_default_caller_is_toolless_bounded_and_workspace_scoped(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return PROSE, [], []

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    reply = await narrative_job.default_llm_caller_factory(TENANT, WORKSPACE)(
        "prompt text"
    )

    assert reply == PROSE
    assert captured["messages"] == [{"role": "user", "content": "prompt text"}]
    assert captured["tools"] == []
    assert captured["tool_server_map"] == {}
    assert captured["max_tokens"] <= 300
    assert captured["temperature"] <= 0.3
    assert captured["user_context"] == narrative_job.narrator_user_context(
        TENANT, WORKSPACE
    )
    assert await captured["invoke_tool"]("server", "tool", {}) == {
        "error": "tools are disabled for the narrator"
    }


# ── SQL hygiene ──────────────────────────────────────────────────────────────


def test_sql_is_static_positional_and_filters_tenant_and_workspace():
    for sql in narrative_job.SQL_STATEMENTS:
        assert "{" not in sql and "}" not in sql
        assert "%" not in sql
    scoped = [
        sql
        for sql in narrative_job.SQL_STATEMENTS
        if sql != narrative_job.BUDGET_LOCK_SQL
    ]
    for sql in scoped:
        assert "tenant_id = $1::uuid" in sql
        assert "workspace_id = $2::uuid" in sql

    assert "item_kind = 'agent_alert'" in narrative_job.CANDIDATES_SQL
    assert "metadata ? 'analysis_evidence'" in narrative_job.CANDIDATES_SQL
    assert "ORDER BY last_seen_at DESC" in narrative_job.CANDIDATES_SQL
    assert "item_kind = 'agent_alert'" in narrative_job.CLAIM_SQL
    assert "clock_timestamp()" in narrative_job.CLAIM_SQL
    assert "RETURNING" in narrative_job.CLAIM_SQL
    # A stored narrative is attacker-writable: it must never gate the claim.
    assert "source_fingerprint" not in narrative_job.CLAIM_SQL
    assert "'narrative'" not in narrative_job.CLAIM_SQL
    assert "$5::double precision" in narrative_job.CLAIM_SQL
    assert "$6" not in narrative_job.CLAIM_SQL
    assert (
        "metadata->'narrative_claim'->>'token' = $5::text" in narrative_job.WRITE_SQL
    )
    assert "- 'narrative_claim'" in narrative_job.WRITE_SQL
    event = narrative_job.LLM_CALL_EVENT_TYPE
    assert f"'{event}'" in narrative_job.BUDGET_COUNT_SQL
    assert f"'{event}'" in narrative_job.BUDGET_RESERVE_SQL
    assert "FOR UPDATE" not in "".join(narrative_job.SQL_STATEMENTS)


def test_every_database_call_passes_a_module_level_sql_constant():
    source = Path(narrative_job.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    constants = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.endswith("_SQL")
    }
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.endswith("_SQL")
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            assert isinstance(node.value.value, str)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"fetch", "fetchrow", "fetchval", "execute"}
    ]
    assert calls
    for call in calls:
        first = call.args[0]
        assert isinstance(first, ast.Name), ast.unparse(call)
        assert first.id in constants, ast.unparse(call)


def test_job_signs_what_it_stores_and_trusts_only_verified_narratives():
    source = Path(narrative_job.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert {"attest_narrative", "verified_stored_narrative"} <= names
    # The fingerprint is never read straight out of unverified metadata.
    assert "metadata.get(NARRATIVE_METADATA_KEY).get" not in source
    assert '["narrative"]["source_fingerprint"]' not in source


# ── Isolation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_workspace_failing_does_not_stop_the_others(db):
    db.scopes = [
        {"tenant_id": OTHER_TENANT, "workspace_id": OTHER_WORKSPACE},
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
    ]
    db.fail_candidates_for = {OTHER_WORKSPACE}
    db.add_alert("alert-1")
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["workspaces"] == 2
    assert result["narrated_ready"] == 1
    assert result["status"] == "partial"
    assert result["failures"] == [
        {"workspace_id": OTHER_WORKSPACE, "error_code": "RuntimeError"}
    ]
    assert "secret-driver-detail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_one_alert_failing_does_not_stop_the_next(db):
    db.add_alert("alert-bad", metadata=_metadata(signal_count=2))
    db.add_alert("alert-good", metadata=_metadata(signal_count=4))
    db.fail_claim_for = {"alert-bad"}
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["narrated_ready"] == 1
    assert result["failures"] == [
        {"workspace_id": WORKSPACE, "error_code": "ConnectionError"}
    ]
    assert _verifies(db.metadata("alert-good")["narrative"], item_id="alert-good")
    assert "secret-driver-detail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_scope_enumeration_failure_never_raises(db, monkeypatch):
    async def broken(_pool):
        raise ConnectionError("db host secret-driver-detail")

    monkeypatch.setattr(narrative_job.scheduled_runtime, "_active_workspace_scopes", broken)

    result = await _run(db, _Caller(db).factory)

    assert result["status"] == "unavailable"
    assert result["failures"] == [{"workspace_id": "", "error_code": "ConnectionError"}]
    assert "secret-driver-detail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_tick_budget_defers_remaining_alerts_to_a_later_tick(db, monkeypatch):
    monkeypatch.setattr(narrative_job, "TICK_BUDGET_SECONDS", -1.0)
    db.add_alert("alert-1")
    caller = _Caller(db)

    result = await _run(db, caller.factory)

    assert result["deferred"] == 1
    assert caller.prompts == []
    assert db.executed(narrative_job.CLAIM_SQL) == []
