from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import psycopg2
import pytest

from refinement.app.publication_recovery import PublicationRecoveryMixin
from refinement.app.publication_snapshot import (
    PUBLICATION_RESOLUTION_ERRORS,
    PublicationRecoveryPending,
)
from refinement.app.publication_store import PublicationStore
from tests.staged_publication_live import LiveStack
from tests.test_staged_publication_legacy_compat_live import (
    SCOPE,
    TEXT_COLUMNS,
    _admin,
    _head,
    _prepared_run,
    _run_state,
)
from tests.test_staged_publication_live import staged_publication_live_stack  # noqa: F401


def _identity(run: uuid.UUID, dataset: str) -> SimpleNamespace:
    return SimpleNamespace(
        materialization_run_id=run,
        scope=SimpleNamespace(
            tenant_id=SCOPE[0], workspace_id=SCOPE[1], dataset=dataset, layer="gold"
        ),
    )


class _Candidates:
    def __init__(self, stack: LiveStack):
        self.stack = stack

    def submit(self, identity, *, object_uri, object_version, object_checksum,
               row_count, lineage, catalog):
        return self.stack.sql(
            self.stack.publisher_dsn,
            SCOPE,
            "SELECT omega_publication.submit_verification_candidate("
            "%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
            (str(identity.materialization_run_id), object_uri, object_version,
             object_checksum, row_count, json.dumps(lineage, sort_keys=True),
             json.dumps(catalog)),
        )[0][0]


class _Verifier:
    def __init__(self, stack: LiveStack):
        self.stack = stack

    def verify(self, candidate_id):
        self.stack.sql(
            self.stack.verifier_dsn,
            SCOPE,
            "SELECT omega_publication.record_attestation(%s)",
            (candidate_id,),
        )


class _Engine(PublicationRecoveryMixin):
    def __init__(self, stack: LiveStack, store: PublicationStore):
        self._publication_store = store
        self._candidate_store = _Candidates(stack)
        self._publication_verifier = _Verifier(stack)

    def _verify_prepared_object(self, _values):
        return None


class _RefusedOnReopen(PublicationStore):
    def reopen_prepared(self, identity, reason):
        psycopg2.connect("postgresql://nobody:x@127.0.0.1:1/none", connect_timeout=1)
        raise AssertionError("unreachable")


class _LockTimeoutOnReopen(PublicationStore):
    def __init__(self, stack: LiveStack, run: uuid.UUID):
        super().__init__(stack.publisher_dsn, stack.reader_dsn)
        self.stack = stack
        self.run_id = run

    def reopen_prepared(self, identity, reason):
        locker = psycopg2.connect(self.stack.admin_dsn)
        try:
            with locker.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM omega_publication.materialization_runs "
                    "WHERE materialization_run_id=%s FOR UPDATE",
                    (str(self.run_id),),
                )
            timed = PublicationStore(
                self.stack.publisher_dsn + "?options=-c%20lock_timeout%3D200",
                self.stack.reader_dsn,
            )
            return timed.reopen_prepared(identity, reason)
        finally:
            locker.rollback()
            locker.close()


def _evidence_count(stack: LiveStack, run: uuid.UUID) -> int:
    return _admin(
        stack,
        "SELECT count(*) FROM omega_publication.materialization_evidence "
        "WHERE materialization_run_id=%s",
        (str(run),),
    )[0][0]


def _recovery_reasons(stack: LiveStack, run: uuid.UUID) -> list[tuple[str]]:
    return _admin(
        stack,
        "SELECT reason FROM omega_publication.materialization_recovery_events "
        "WHERE materialization_run_id=%s ORDER BY created_at",
        (str(run),),
    )


@pytest.mark.parametrize("kind", ["connection_refused", "lock_not_available"])
def test_transient_reopen_failure_keeps_the_run_publishable(
    staged_publication_live_stack: LiveStack, kind: str
) -> None:
    stack = staged_publication_live_stack
    dataset = f"recovery_transient_{kind}"
    run, _ = _prepared_run(stack, dataset, TEXT_COLUMNS, (*SCOPE, 1), expected=None)
    store = (
        _RefusedOnReopen(stack.publisher_dsn, stack.reader_dsn)
        if kind == "connection_refused"
        else _LockTimeoutOnReopen(stack, run)
    )
    lost = psycopg2.OperationalError("server closed the connection unexpectedly")

    with pytest.raises(PublicationRecoveryPending) as raised:
        _Engine(stack, store)._recover_prepared(_identity(run, dataset), lost)

    cause = raised.value.__cause__
    assert isinstance(cause, psycopg2.OperationalError)
    if kind == "lock_not_available":
        assert cause.pgcode == "55P03"
    assert _run_state(stack, run) == ("prepared", None, False)
    assert _evidence_count(stack, run) == 1
    assert _recovery_reasons(stack, run) == []
    stack.publish(run, None)
    assert _head(stack, dataset) == (str(run), 1)


def test_ambiguous_commit_is_recovered_as_the_existing_receipt(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    dataset = "recovery_ambiguous_commit"
    run, _ = _prepared_run(stack, dataset, TEXT_COLUMNS, (*SCOPE, 1), expected=None)
    published = stack.publish(run, None)
    store = PublicationStore(stack.publisher_dsn, stack.reader_dsn)
    lost = psycopg2.OperationalError("server closed the connection unexpectedly")

    receipt = _Engine(stack, store)._recover_prepared(_identity(run, dataset), lost)

    assert receipt == {
        "receipt_id": str(published[0]),
        "generation": 1,
        "replayed": False,
    }
    assert _head(stack, dataset) == (str(run), 1)
    assert _run_state(stack, run)[0] == "published"
    assert _recovery_reasons(stack, run) == []


def test_unrecoverable_reopen_is_typed_when_quarantine_is_refused(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    dataset = "recovery_quarantine_refused"
    run, _ = _prepared_run(stack, dataset, TEXT_COLUMNS, (*SCOPE, 1), expected=None)
    _admin(
        stack,
        "UPDATE omega_publication.materialization_runs SET status='reserved' "
        "WHERE materialization_run_id=%s",
        (str(run),),
        fetch=False,
    )
    store = PublicationStore(stack.publisher_dsn, stack.reader_dsn)

    with pytest.raises(PUBLICATION_RESOLUTION_ERRORS) as raised:
        _Engine(stack, store)._recover_prepared(
            _identity(run, dataset), psycopg2.OperationalError("x")
        )

    assert isinstance(raised.value, PublicationRecoveryPending)
    assert isinstance(raised.value.__cause__, psycopg2.Error)
    assert _head(stack, dataset) is None


def test_head_cas_loser_is_quarantined_with_a_recovery_event(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    dataset = "recovery_head_cas_loser"
    loser, _ = _prepared_run(stack, dataset, TEXT_COLUMNS, (*SCOPE, 1), expected=None)
    winner, _ = _prepared_run(
        stack, dataset, TEXT_COLUMNS, (*SCOPE, 2), expected=None, digest="c" * 64
    )
    stack.publish(winner, None)
    store = PublicationStore(stack.publisher_dsn, stack.reader_dsn)
    identity = _identity(loser, dataset)
    with pytest.raises(psycopg2.Error) as conflict:
        store.publish(identity, None)
    assert conflict.value.pgcode == "40001"

    with pytest.raises(PublicationRecoveryPending, match="head conflict"):
        _Engine(stack, store)._quarantine_head_cas_loser(identity, conflict.value)

    assert _run_state(stack, loser) == (
        "recoverable_failed",
        "publication_head_cas_lost",
        True,
    )
    assert _recovery_reasons(stack, loser) == [("publication_head_cas_lost",)]
    assert _head(stack, dataset) == (str(winner), 1)
