from __future__ import annotations

import uuid

import psycopg2
import pytest

from tests.staged_publication_live import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
    LiveStack,
)
from tests.test_staged_publication_live import staged_publication_live_stack  # noqa: F401

DATASET = "sap_successfactors_talent_benchmark_internal"

_COLUMNS = [
    {"name": "tenant_id", "type": "TEXT"},
    {"name": "workspace_id", "type": "TEXT"},
    {"name": "approved", "type": "BOOLEAN"},
    {"name": "approved_by", "type": "TEXT"},
    {"name": "approved_at", "type": "TIMESTAMPTZ"},
    {"name": "approval_actor_source", "type": "TEXT"},
    {"name": "approval_recorded_by_server", "type": "BOOLEAN"},
    {"name": "approval_evidence_ref", "type": "TEXT"},
    {"name": "approval_authorization_ref", "type": "TEXT"},
    {"name": "approval_authorization_verified", "type": "BOOLEAN"},
    {"name": "approval_status", "type": "TEXT"},
]

UNREVIEWED = (
    False,
    None,
    None,
    None,
    False,
    None,
    None,
    False,
    "unreviewed",
)
FORGED_APPROVAL = (
    True,
    None,
    None,
    None,
    False,
    None,
    None,
    False,
    "unreviewed",
)
SERVER_APPROVAL = (
    True,
    "42",
    "2026-08-01T00:00:00Z",
    "server",
    True,
    "evidence:benchmark:v1",
    "authorization:benchmark:v1",
    True,
    "approved",
)


def _catalog(columns: list[dict[str, str]]) -> str:
    import json

    return json.dumps(columns)


def _lineage() -> str:
    import json

    return json.dumps(
        {
            "source_entity": "real",
            "source_load_date": "2026-08-01",
            "source_batch_id": "benchmark-authority",
            "sql_digest": "e" * 64,
            "column_mapping_digest": "f" * 64,
        }
    )


def _publish_benchmark(
    stack: LiveStack,
    run: uuid.UUID,
    row: tuple,
    *,
    scope: tuple[str, str],
    columns: list[dict[str, str]] | None = None,
    expected_head: uuid.UUID | None = None,
):
    columns = columns or _COLUMNS
    stack.reserve(DATASET, run, scope=scope)
    stage = stack.sql(
        stack.publisher_dsn,
        scope,
        "SELECT omega_publication.create_gold_stage(%s,%s::jsonb)",
        (str(run), _catalog(columns)),
    )[0][0]
    placeholders = ",".join(["%s"] * len(columns))
    stack.sql(
        stack.publisher_dsn,
        scope,
        f'INSERT INTO omega_publication_stage."{stage}" VALUES ({placeholders})',
        (*scope, *row),
        fetch=False,
    )
    uri, checksum = stack.write_object(DATASET, run, 1, scope)
    lineage = stack.bound_lineage(run, _lineage(), scope)
    stack.attest(
        run,
        uri=uri,
        checksum=checksum,
        row_count=1,
        lineage=lineage,
        catalog=_catalog(columns),
        scope=scope,
    )
    stack.sql(
        stack.publisher_dsn,
        scope,
        "SELECT * FROM omega_publication.mark_prepared(%s,%s,%s,%s,1,%s,%s,%s::jsonb,%s::jsonb)",
        (
            str(run),
            uri,
            stack.object_version(uri),
            checksum,
            stage,
            stage,
            lineage,
            _catalog(columns),
        ),
    )
    return stack.publish(run, expected_head, scope=scope)


def _relation_name(stack: LiveStack, scope: tuple[str, str]) -> str:
    return stack.sql(
        stack.reader_dsn,
        scope,
        "SELECT relation_name FROM omega_publication.dataset_gold_relations WHERE dataset=%s",
        (DATASET,),
    )[0][0]


def _admin_rows(stack: LiveStack, relation: str):
    with psycopg2.connect(stack.admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT tenant_id, approved, approval_status FROM public."{relation}"'
        )
        return cur.fetchall()


def test_benchmark_approval_survives_publication_and_republication(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    scope_a = (TENANT_A, WORKSPACE_A)

    first = uuid.uuid4()
    _publish_benchmark(stack, first, UNREVIEWED, scope=scope_a)
    head = stack.head(DATASET, scope_a)
    assert head is not None and head[1] == 1
    relation = _relation_name(stack, scope_a)
    with psycopg2.connect(stack.admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",
            (f'public."{relation}"',),
        )
        names = {row[0] for row in cur.fetchall()}
    assert "talent_benchmark_approval_authority_check" in names

    forged = uuid.uuid4()
    with pytest.raises(psycopg2.Error) as excinfo:
        _publish_benchmark(
            stack, forged, FORGED_APPROVAL, scope=scope_a, expected_head=head[0]
        )
    assert excinfo.value.pgcode in {"23514", "P0001"}
    after_forgery = stack.head(DATASET, scope_a)
    assert after_forgery is not None
    assert after_forgery[0] == head[0]
    assert after_forgery[1] == 1
    assert _admin_rows(stack, relation) == [(TENANT_A, False, "unreviewed")]

    extended = _COLUMNS + [{"name": "note", "type": "TEXT"}]
    self_attested = uuid.uuid4()
    with pytest.raises(psycopg2.Error) as self_attested_error:
        _publish_benchmark(
            stack,
            self_attested,
            SERVER_APPROVAL + ("forged",),
            scope=scope_a,
            columns=extended,
            expected_head=head[0],
        )
    assert self_attested_error.value.pgcode in {"23514", "P0001"}
    assert stack.head(DATASET, scope_a)[0] == head[0]

    republished = uuid.uuid4()
    _publish_benchmark(
        stack,
        republished,
        UNREVIEWED + ("republished",),
        scope=scope_a,
        columns=extended,
        expected_head=head[0],
    )
    advanced = stack.head(DATASET, scope_a)
    assert advanced is not None and advanced[1] == 2
    assert _admin_rows(stack, relation) == [(TENANT_A, False, "unreviewed")]

    scope_b = (TENANT_B, WORKSPACE_B)
    other = uuid.uuid4()
    _publish_benchmark(stack, other, UNREVIEWED, scope=scope_b)
    relation_b = _relation_name(stack, scope_b)
    assert relation_b != relation
    assert _admin_rows(stack, relation) == [(TENANT_A, False, "unreviewed")]
    assert _admin_rows(stack, relation_b) == [(TENANT_B, False, "unreviewed")]
