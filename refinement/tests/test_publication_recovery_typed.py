from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from refinement.app import main as refinement_main

recovery = importlib.import_module("refinement.app.publication_recovery")
finalize = importlib.import_module("refinement.app.publication_finalize")
objects = importlib.import_module(recovery.PreparedObjectCorrupt.__module__)
snapshot = importlib.import_module(recovery.PublicationRejected.__module__)

TENANT = "tenant-a"
WORKSPACE = "workspace-a"
CONTEXT = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
PAIR_KEY = "typed-recovery-console-refinement-pair-key-over-32-chars"
TALENT_GOLD = "sap_successfactors_talent_employee_profile"


class _PgError(Exception):
    def __init__(self, pgcode: str | None, *, constraint: str | None = None):
        super().__init__("attestation expired; stage missing; checksum mismatch")
        self.pgcode = pgcode
        self.diag = SimpleNamespace(constraint_name=constraint)


@pytest.mark.parametrize(
    "error, reason",
    [
        (_PgError("42804"), "legacy_type_conflict"),
        (_PgError("55000"), "gold_stage_missing"),
        (_PgError("23514"), "publication_integrity_rejected"),
        (
            _PgError("23514", constraint="talent_benchmark_approval_authority_check"),
            "integrity_constraint_violation",
        ),
        (_PgError("23502"), "publication_rejected"),
        (_PgError("22P02"), "publication_rejected"),
        (_PgError("42501"), "publication_rejected"),
        (_PgError("P0001"), "evidence_mismatch"),
        (_PgError(None), "evidence_mismatch"),
        (objects.PreparedObjectUnavailable("x"), "object_unavailable"),
        (objects.PreparedObjectCorrupt("x"), "object_corrupt"),
        (objects.PreparedObjectIncomplete("x"), "evidence_mismatch"),
        (RuntimeError("attestation expired stage missing checksum"), "evidence_mismatch"),
    ],
)
def test_recovery_reason_is_decided_by_sqlstate_and_type_never_by_text(
    error, reason
) -> None:
    assert recovery._recovery_reason(error) == reason


class _Store:
    def __init__(
        self,
        *,
        publish_error=None,
        quarantine_error=None,
        reopen_error=None,
        run_error=None,
        abandon_error=None,
        status="prepared",
    ):
        self.publish_error = publish_error
        self.quarantine_error = quarantine_error
        self.reopen_error = reopen_error
        self.run_error = run_error
        self.abandon_error = abandon_error
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def reopen_prepared(self, _identity, reason):
        self.calls.append(("reopen", reason))
        if self.reopen_error:
            raise self.reopen_error
        self.status = "reserved"
        return {
            "object_uri": "s3://lakehouse/gold/x.parquet",
            "object_version": "v1",
            "object_checksum": "a" * 64,
            "row_count": 1,
            "staging_table": "run_" + "0" * 32,
            "gold_table": "run_" + "0" * 32,
            "lineage": {},
            "catalog": [],
            "expected_head_run_id": None,
        }

    def run(self, _identity):
        self.calls.append(("run", ""))
        if self.run_error:
            raise self.run_error
        return {"status": self.status, "expected_head_run_id": "head-1"}

    def mark_prepared(self, _identity, **_values):
        self.calls.append(("mark_prepared", ""))
        self.status = "prepared"

    def publish(self, _identity, expected):
        self.calls.append(("publish", str(expected or "")))
        if self.status == "published":
            return {"receipt_id": "r", "generation": 1, "replayed": True}
        if self.publish_error:
            raise self.publish_error
        self.status = "published"
        return {"receipt_id": "r", "generation": 1, "replayed": False}

    def quarantine_prepared(self, _identity, reason):
        self.calls.append(("quarantine", reason))
        if self.quarantine_error:
            raise self.quarantine_error
        self.status = "recoverable_failed"

    def abandon(self, _identity):
        self.calls.append(("abandon", ""))
        if self.abandon_error:
            raise self.abandon_error
        self.status = "recoverable_failed"


class _Engine(recovery.PublicationRecoveryMixin):
    def __init__(self, store: _Store, *, verify_error=None):
        self._publication_store = store
        self._candidate_store = SimpleNamespace(submit=lambda *_a, **_k: "candidate")
        self._publication_verifier = MagicMock()
        if verify_error:
            self._publication_verifier.verify.side_effect = verify_error

    def _verify_prepared_object(self, _values) -> None:
        return None


IDENTITY = SimpleNamespace(scope=SimpleNamespace(layer="gold"))


@pytest.mark.parametrize(
    "publish_error, reason",
    [
        (_PgError("42804"), "legacy_type_conflict"),
        (
            _PgError("23514", constraint="talent_benchmark_approval_authority_check"),
            "integrity_constraint_violation",
        ),
        (_PgError("23502"), "publication_rejected"),
        (_PgError("22P02"), "publication_rejected"),
    ],
)
def test_deterministic_publish_failure_is_quarantined_and_rejected(
    publish_error, reason
) -> None:
    store = _Store(publish_error=publish_error)

    with pytest.raises(snapshot.PublicationRejected) as raised:
        _Engine(store)._recover_prepared(IDENTITY, _PgError("42804"))

    assert raised.value.__cause__ is publish_error
    assert store.calls == [
        ("reopen", "legacy_type_conflict"),
        ("mark_prepared", ""),
        ("publish", ""),
        ("quarantine", reason),
    ]
    assert store.status == "recoverable_failed"
    assert isinstance(raised.value, snapshot.PublicationIntegrityError)


def test_head_cas_loss_after_recovery_is_quarantined_as_cas_lost() -> None:
    store = _Store(publish_error=_PgError("40001"))

    with pytest.raises(snapshot.PublicationRecoveryPending, match="head conflict"):
        _Engine(store)._recover_prepared(IDENTITY, _PgError("23514"))

    assert store.calls[-1] == ("quarantine", "publication_head_cas_lost")


@pytest.mark.parametrize(
    "publish_error",
    [_PgError("P0001"), _PgError("57014"), _PgError(None), ConnectionResetError("x")],
)
def test_transient_publish_failure_keeps_the_prepared_state(publish_error) -> None:
    store = _Store(publish_error=publish_error)

    with pytest.raises(snapshot.PublicationRecoveryPending, match="retry is required"):
        _Engine(store)._recover_prepared(IDENTITY, _PgError("P0001"))

    assert store.status == "prepared"
    assert [call[0] for call in store.calls] == ["reopen", "mark_prepared", "publish"]


def test_failed_quarantine_of_a_rejection_stays_pending() -> None:
    quarantine_error = _PgError("P0001")
    store = _Store(publish_error=_PgError("42804"), quarantine_error=quarantine_error)

    with pytest.raises(snapshot.PublicationRecoveryPending) as raised:
        _Engine(store)._recover_prepared(IDENTITY, _PgError("42804"))

    assert raised.value.__cause__ is quarantine_error


def test_failure_before_prepare_is_abandoned_and_pending() -> None:
    store = _Store()

    with pytest.raises(snapshot.PublicationRecoveryPending, match="retry is required"):
        _Engine(store, verify_error=RuntimeError("verifier down"))._recover_prepared(
            IDENTITY, _PgError("23514")
        )

    assert store.calls == [
        ("reopen", "publication_integrity_rejected"),
        ("run", ""),
        ("abandon", ""),
    ]


def test_unreopenable_run_is_quarantined_and_pending() -> None:
    store = _Store(reopen_error=_PgError("23514"))

    with pytest.raises(snapshot.PublicationRecoveryPending, match="unavailable"):
        _Engine(store)._recover_prepared(IDENTITY, _PgError("55000"))

    assert store.calls == [
        ("reopen", "gold_stage_missing"),
        ("run", ""),
        ("quarantine", "gold_stage_missing"),
    ]


class _SqlStateError(Exception):
    def __init__(self, pgcode: str | None):
        super().__init__("connection refused; lock not available")
        self.pgcode = pgcode


@pytest.mark.parametrize("pgcode", [None, "55P03", "53300", "57P01", "40P01", "08006"])
def test_transient_reopen_failure_never_quarantines(pgcode) -> None:
    reopen_error = _SqlStateError(pgcode)
    store = _Store(reopen_error=reopen_error)

    with pytest.raises(snapshot.PublicationRecoveryPending, match="unavailable") as raised:
        _Engine(store)._recover_prepared(IDENTITY, _PgError(None))

    assert raised.value.__cause__ is reopen_error
    assert store.calls == [("reopen", "evidence_mismatch")]
    assert store.status == "prepared"


def test_reopen_of_an_already_published_run_returns_its_receipt() -> None:
    store = _Store(reopen_error=_PgError("23514"), status="published")

    receipt = _Engine(store)._recover_prepared(IDENTITY, _PgError(None))

    assert receipt == {"receipt_id": "r", "generation": 1, "replayed": False}
    assert store.calls == [
        ("reopen", "evidence_mismatch"),
        ("run", ""),
        ("publish", "head-1"),
    ]


@pytest.mark.parametrize(
    "store",
    [
        lambda: _Store(reopen_error=_PgError("23514"), quarantine_error=_PgError("P0001")),
        lambda: _Store(reopen_error=_PgError("23514"), run_error=_SqlStateError(None)),
    ],
)
def test_failed_settlement_of_an_unreopenable_run_is_typed(store) -> None:
    store = store()

    with pytest.raises(snapshot.PublicationRecoveryPending, match="unavailable") as raised:
        _Engine(store)._recover_prepared(IDENTITY, _PgError(None))

    assert raised.value.__cause__ in (store.quarantine_error, store.run_error)


def test_failed_quarantine_of_a_head_cas_loser_is_typed() -> None:
    quarantine_error = _PgError("P0001")
    store = _Store(publish_error=_PgError("40001"), quarantine_error=quarantine_error)

    with pytest.raises(snapshot.PublicationRecoveryPending) as raised:
        _Engine(store)._recover_prepared(IDENTITY, _PgError("23514"))

    assert raised.value.__cause__ is quarantine_error


@pytest.mark.parametrize(
    "store",
    [
        lambda: _Store(run_error=_SqlStateError("57P01")),
        lambda: _Store(abandon_error=_PgError("P0001")),
    ],
)
def test_failed_cleanup_after_reconstruction_is_typed(store) -> None:
    store = store()

    with pytest.raises(snapshot.PublicationRecoveryPending, match="retry is required") as raised:
        _Engine(store, verify_error=RuntimeError("verifier down"))._recover_prepared(
            IDENTITY, _PgError("23514")
        )

    assert raised.value.__cause__ in (store.run_error, store.abandon_error)


class _CatalogBase:
    def _update_catalog(self, *_args, **_kwargs) -> None:
        raise AssertionError("catalog must not change without a receipt")


class _Finalizer(
    finalize.PublicationFinalizeMixin, recovery.PublicationRecoveryMixin, _CatalogBase
):
    pg_url = ""

    def __init__(self, store: _Store):
        self._publication_store = store
        self._evidence_store = SimpleNamespace(
            prepare=lambda *_a, **kw: (kw["lineage"], kw["schema_fields"])
        )
        self._candidate_store = SimpleNamespace(submit=lambda *_a, **_k: "candidate")
        self._publication_verifier = MagicMock()
        self.state = {
            "identity": SimpleNamespace(
                scope=SimpleNamespace(
                    layer="gold", tenant_id=TENANT, workspace_id=WORKSPACE
                )
            ),
            "expected_head": None,
            "dataset": {"name": "gold_x", "sources": []},
            "user_context": CONTEXT,
            "lineage": {"source_entity": "real"},
            "object_uri": "s3://lakehouse/gold/x.parquet",
            "object_checksum": "a" * 64,
            "object_version": "v1",
            "staging_table": "run_" + "0" * 32,
            "row_count": 1,
        }

    def _state(self):
        return self.state

    def _assert_inputs_unchanged(self, *_args) -> None:
        return None

    def _verify_parquet_evidence(self, **_kwargs):
        return 1, [{"name": "value", "type": "INTEGER"}]

    def _verify_prepared_object(self, _values) -> None:
        return None

    def _mark_publication_replayed(self, _replayed) -> None:
        raise AssertionError("a lost head CAS has no receipt")


def test_finalize_head_cas_loss_is_quarantined_like_the_engine() -> None:
    conflict = _PgError("40001")
    store = _Store(publish_error=conflict)

    with pytest.raises(snapshot.PublicationRecoveryPending, match="head conflict") as raised:
        _Finalizer(store)._update_catalog(
            "gold_x", "gold", "acceptance", [{"name": "value"}], {}
        )

    assert raised.value.__cause__ is conflict
    assert store.calls == [
        ("mark_prepared", ""),
        ("publish", ""),
        ("quarantine", "publication_head_cas_lost"),
    ]
    assert store.status == "recoverable_failed"


def test_successful_recovery_returns_the_receipt() -> None:
    store = _Store()

    receipt = _Engine(store)._recover_prepared(IDENTITY, _PgError("23514"))

    assert receipt["receipt_id"] == "r" and store.status == "published"


def test_typed_errors_are_publication_resolution_errors() -> None:
    assert issubclass(snapshot.PublicationRejected, snapshot.PublicationIntegrityError)
    assert issubclass(snapshot.PublicationRecoveryPending, RuntimeError)
    for kind in (snapshot.PublicationRejected, snapshot.PublicationRecoveryPending):
        assert issubclass(kind, snapshot.PUBLICATION_RESOLUTION_ERRORS)


@pytest.mark.parametrize(
    "error, status, code",
    [
        (
            lambda: refinement_main.PublicationRejected("rejected"),
            409,
            "publication_rejected",
        ),
        (
            lambda: refinement_main.PublicationRecoveryPending("retry is required"),
            503,
            "publication_recovery_retry",
        ),
    ],
)
def test_friendly_error_maps_typed_publication_failures(
    monkeypatch, error, status, code
) -> None:
    monkeypatch.setattr(
        refinement_main, "_log_internal_error", lambda *_args, **_kwargs: "req-1"
    )

    observed, detail = refinement_main._friendly_duckdb_error(error(), "gold_x")

    assert (observed, detail["code"]) == (status, code)
    assert "gold_x" in detail["message"]
    assert detail["detail"] == "Error interno"
    assert detail["request_id"] == "req-1"
    assert "rejected" not in detail["message"]
    assert "retry is required" not in detail["message"]


@pytest.mark.parametrize(
    "error",
    [
        lambda: refinement_main.PublicationRejected("rejected"),
        lambda: refinement_main.PublicationRecoveryPending("retry is required"),
    ],
)
def test_typed_publication_failures_never_publish_a_fallback(monkeypatch, error):
    calls: list[str] = []

    def materialize(ds, _context):
        calls.append(str(ds.get("sql_def") or ""))
        raise error()

    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)

    with pytest.raises(RuntimeError):
        refinement_main._materialize_with_operational_fallback(
            {"name": TALENT_GOLD, "sql_def": "SELECT real", "description": "d"},
            CONTEXT,
        )
    assert calls == ["SELECT real"]


def _security_context() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "user_id": 41,
        "permissions": ["datasets.read", "datasets.write"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_prefixes": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, status, code",
    [
        (
            lambda: refinement_main.PublicationRejected("rejected"),
            409,
            "publication_rejected",
        ),
        (
            lambda: refinement_main.PublicationRecoveryPending("retry is required"),
            503,
            "publication_recovery_retry",
        ),
    ],
)
async def test_materialize_tool_reports_typed_publication_failures(
    monkeypatch, error, status, code
):
    dataset = {
        "name": TALENT_GOLD,
        "sql_def": "SELECT 1 AS real_value",
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": ["gold/sap_successfactors/sap_successfactors_employee_360"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "created_by_id": 41,
    }
    calls: list[str] = []

    def materialize(ds, _context):
        calls.append(str(ds.get("sql_def") or ""))
        raise error()

    update_refresh = MagicMock()
    monkeypatch.setattr(
        refinement_main.store, "get_dataset", MagicMock(return_value=dataset)
    )
    monkeypatch.setattr(refinement_main.store, "update_refresh", update_refresh)
    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)
    transport = httpx.ASGITransport(app=refinement_main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://refinement.test"
    ) as client:
        response = await client.post(
            "/mcp/invoke",
            headers={"x-api-key": PAIR_KEY, "x-internal-service": "console"},
            json={
                "tool": "materialize",
                "args": {"name": TALENT_GOLD},
                "security_context": refinement_main._sign_security_context(
                    _security_context()
                ),
            },
        )

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert calls == ["SELECT 1 AS real_value"]
    update_refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, status, code",
    [
        (
            lambda: refinement_main.PublicationRejected("rejected"),
            409,
            "publication_rejected",
        ),
        (
            lambda: refinement_main.PublicationRecoveryPending("retry is required"),
            503,
            "publication_recovery_retry",
        ),
    ],
)
async def test_dataset_refresh_route_reports_typed_publication_failures(
    monkeypatch, error, status, code
):
    dataset = {
        "name": TALENT_GOLD,
        "sql_def": "SELECT 1 AS real_value",
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": ["gold/sap_successfactors/sap_successfactors_employee_360"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "created_by_id": 41,
    }
    calls: list[str] = []

    def materialize(ds, _context):
        calls.append(str(ds.get("sql_def") or ""))
        raise error()

    update_refresh = MagicMock()
    monkeypatch.setattr(
        refinement_main.store, "get_dataset", MagicMock(return_value=dataset)
    )
    monkeypatch.setattr(refinement_main.store, "update_refresh", update_refresh)
    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)
    transport = httpx.ASGITransport(app=refinement_main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://refinement.test"
    ) as client:
        response = await client.post(
            f"/datasets/{TALENT_GOLD}/refresh",
            headers={
                "x-api-key": PAIR_KEY,
                "x-internal-service": "console",
                "x-security-context": json.dumps(
                    refinement_main._sign_security_context(_security_context())
                ),
            },
        )

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert calls == ["SELECT 1 AS real_value"]
    update_refresh.assert_not_called()
