from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest

from tests.staged_publication_canaries import (
    CANARIES,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)
from tests.staged_publication_live import valid_catalog, valid_lineage
from tests.staged_publication_upgrade_canaries import (
    C11_legacy_drop_negative_control,
    C12_upgrade_and_rerun,
)


def _assert_old(stack, dataset: str, before: dict) -> None:
    assert stack.published_state(dataset) == before


def _baseline(stack, dataset: str) -> dict:
    stack.baseline(dataset)
    return stack.published_state(dataset)


def C1_object_pending(stack):
    before = _baseline(stack, dataset := "canary_c1")
    new = uuid.uuid4()
    stack.reserve(dataset, new)
    stack.write_object(dataset, new, 2)
    _assert_old(stack, dataset, before)


def C2_gold_prepared_then_external_failure(stack):
    before = _baseline(stack, dataset := "canary_c2")
    new = uuid.uuid4()
    stack.reserve(dataset, new)
    stack.stage_gold(dataset, new, 2)
    with pytest.raises(Exception):
        stack.s3.put_object(
            Bucket="missing-publication-bucket",
            Key=f"failure/{new.hex}",
            Body=b"minio-failure-after-private-gold",
        )
    _assert_old(stack, dataset, before)
    prepared = uuid.uuid4()
    stack.reserve(dataset, prepared)
    stack.prepare(dataset, prepared, 3)
    status = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT status FROM omega_publication.materialization_runs "
        "WHERE materialization_run_id=%s",
        (str(prepared),),
    )[0][0]
    assert status == "prepared"
    _assert_old(stack, dataset, before)


def C3_lineage_failure(stack):
    before = _baseline(stack, dataset := "canary_c3")
    new = uuid.uuid4()
    stack.reserve(dataset, new)
    stage, uri, checksum = stack.stage(dataset, new, 2)
    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.publisher_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT * FROM omega_publication.mark_prepared(%s,%s,%s,1,%s,%s,%s::jsonb,%s::jsonb)",
            (str(new), uri, checksum, stage, stage, "{}", valid_catalog()),
        )
    _assert_old(stack, dataset, before)


def C4_catalog_failure(stack):
    before = _baseline(stack, dataset := "canary_c4")
    new = uuid.uuid4()
    stack.reserve(dataset, new)
    stage, uri, checksum = stack.stage(dataset, new, 2)
    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.publisher_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT * FROM omega_publication.mark_prepared(%s,%s,%s,1,%s,%s,%s::jsonb,%s::jsonb)",
            (
                str(new),
                uri,
                checksum,
                stage,
                stage,
                valid_lineage(),
                '[{"name":"not_value","type":"INTEGER"}]',
            ),
        )
    _assert_old(stack, dataset, before)


def C5_cas_failure(stack):
    old = stack.baseline(dataset := "canary_c5")
    before = stack.published_state(dataset)
    new = uuid.uuid4()
    stack.reserve(dataset, new)
    stack.prepare(dataset, new, 2)
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        """CREATE FUNCTION public.fail_receipt_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$;
        CREATE TRIGGER fail_receipt BEFORE INSERT ON
        omega_publication.materialization_receipts FOR EACH ROW
        EXECUTE FUNCTION public.fail_receipt_insert()""",
        fetch=False,
    )
    try:
        with pytest.raises(psycopg2.Error):
            stack.publish(new, old)
    finally:
        stack.sql(
            stack.admin_dsn,
            (TENANT_A, WORKSPACE_A),
            "DROP TRIGGER fail_receipt ON omega_publication.materialization_receipts;"
            "DROP FUNCTION public.fail_receipt_insert()",
            fetch=False,
        )
    _assert_old(stack, dataset, before)


def C6_materialization_replay(stack):
    run = uuid.uuid4()
    stack.reserve("canary_c6", run)
    stack.prepare("canary_c6", run, 1)
    first = stack.publish(run, None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replays = list(pool.map(lambda _: stack.publish(run, None), range(2)))
    assert all(item[:2] == first[:2] and item[2] is True for item in replays)
    count = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT (SELECT count(*) FROM omega_publication.materialization_receipts "
        "WHERE materialization_run_id=%s),(SELECT count(*) FROM "
        "omega_publication.materialization_evidence WHERE materialization_run_id=%s)",
        (str(run), str(run)),
    )[0]
    assert count == (1, 1) and stack.rows("canary_c6") == [(1,)]


def C7_concurrent_distinct_runs(stack):
    dataset = "canary_c7"
    old = stack.baseline(dataset)
    runs = [uuid.uuid4(), uuid.uuid4()]
    for index, run in enumerate(runs, 2):
        stack.reserve(dataset, run)
        stack.prepare(dataset, run, index)

    def attempt(run):
        try:
            return stack.publish(run, old)
        except psycopg2.Error:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, runs))
    assert sum(item is not None for item in results) == 1


def C8_run_digest_mismatch(stack):
    run = uuid.uuid4()
    stack.reserve("canary_c8", run)
    with pytest.raises(psycopg2.Error):
        stack.reserve("canary_c8", run, digest="e" * 64)


def C9_head_versioned_cache(stack):
    dataset = "canary_c9"
    old = stack.baseline(dataset)
    old_key = stack.head(dataset)
    new = uuid.uuid4()
    stack.reserve(dataset, new)
    stack.prepare(dataset, new, 2)
    stack.publish(new, old)
    new_key = stack.head(dataset)
    assert old_key != new_key and new_key[1] == old_key[1] + 1


def C10_scope_and_reader_dml(stack):
    stack.baseline("canary_c10")
    assert stack.head("canary_c10", (TENANT_B, WORKSPACE_B)) is None
    run = uuid.uuid4()
    stack.reserve("canary_c10_stage", run)
    stage = stack.stage_gold("canary_c10_stage", run, 5)
    assert (
        stack.sql(
            stack.publisher_dsn,
            (TENANT_B, WORKSPACE_B),
            f'SELECT value FROM omega_publication_stage."{stage}"',
        )
        == []
    )
    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.publisher_dsn,
            (TENANT_B, WORKSPACE_B),
            f'INSERT INTO omega_publication_stage."{stage}" VALUES (%s,%s,6)',
            (TENANT_A, WORKSPACE_A),
            fetch=False,
        )
    for dsn, statement in (
        (stack.reader_dsn, 'DELETE FROM public."gold_canary_c10"'),
        (stack.publisher_dsn, 'DELETE FROM public."gold_canary_c10"'),
        (stack.publisher_dsn, "CREATE TABLE public.publisher_bypass(value integer)"),
    ):
        with pytest.raises(psycopg2.Error):
            stack.sql(dsn, (TENANT_A, WORKSPACE_A), statement, fetch=False)
    roles = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT rolname,rolcanlogin,rolbypassrls FROM pg_roles WHERE rolname IN "
        "('omega_gold_owner','omega_gold_publisher','omega_refinement_gold')",
    )
    assert sorted(roles) == [
        ("omega_gold_owner", False, False),
        ("omega_gold_publisher", True, False),
        ("omega_refinement_gold", True, False),
    ]


CANARY_IMPLEMENTATIONS = {name: globals()[name] for name in CANARIES}
