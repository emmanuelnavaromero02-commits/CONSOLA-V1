from __future__ import annotations

import inspect

import pytest

from refinement.app.publication_store import PublicationStore
from refinement.app.publication_contract import PublicationScope
from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.staged_publication_live import LiveStack
from tests.test_staged_publication_live import staged_publication_live_stack


def _relation(stack: LiveStack, dataset: str) -> str:
    rows = stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        """SELECT relation_name FROM omega_publication.dataset_gold_relations
             WHERE dataset=%s""",
        (dataset,),
    )
    return str(rows[0][0])


def test_gold_physical_relations_do_not_collide_at_postgres_identifier_limit(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    prefix = "dataset_" + "a" * 64
    first = prefix + "_uno"
    second = prefix + "_dos"
    stack.baseline(first)
    stack.baseline(second)

    first_relation = _relation(stack, first)
    second_relation = _relation(stack, second)
    assert first_relation != second_relation
    assert len(first_relation.encode()) <= 63
    assert len(second_relation.encode()) <= 63
    assert stack.compatibility_rows(first) == [(1,)]
    assert stack.compatibility_rows(second) == [(1,)]


def test_prepared_retry_is_not_abandoned_or_recomputed() -> None:
    source = inspect.getsource(PublicationStore.run_lock)
    assert 'status == "prepared"' in source
    assert "self.abandon(identity)" not in source.split("except Exception:", 1)[1]


def test_unicode_dataset_name_fails_closed_before_physical_relation_mapping() -> None:
    with pytest.raises(ValueError, match="Invalid materialization dataset"):
        PublicationScope.from_dataset(
            {"name": "nómina_mensual", "layer": "gold"},
            {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
        )


def test_rerunning_publication_migrations_after_publish_preserves_state(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    stack.baseline("migration_rerun_after_publish")
    before = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT "
        "(SELECT count(*) FROM omega_publication.materialization_runs),"
        "(SELECT count(*) FROM omega_publication.materialization_receipts),"
        "(SELECT count(*) FROM omega_publication.dataset_publication_heads),"
        "(SELECT count(*) FROM omega_publication.dataset_gold_relations)",
    )[0]
    published = stack.published_state("migration_rerun_after_publish")

    for migration in (
        "38_staged_publication_schema.sql",
        "39_staged_publication_functions.sql",
        "40_staged_publication_cas.sql",
        "41_staged_publication_authority.sql",
    ):
        stack.rerun_gold_migration(migration)

    after = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT "
        "(SELECT count(*) FROM omega_publication.materialization_runs),"
        "(SELECT count(*) FROM omega_publication.materialization_receipts),"
        "(SELECT count(*) FROM omega_publication.dataset_publication_heads),"
        "(SELECT count(*) FROM omega_publication.dataset_gold_relations)",
    )[0]
    assert after == before
    assert stack.published_state("migration_rerun_after_publish") == published
