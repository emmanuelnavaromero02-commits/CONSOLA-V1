from __future__ import annotations

import importlib
import json
import platform
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg2
import pytest

from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.staged_publication_live import (
    PASSWORD,
    PUBLISHER_PASSWORD,
    READER_PASSWORD,
    VERIFIER_PASSWORD,
    LiveStack,
    _docker,
    _port,
    _wait,
    valid_lineage,
)
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import staged_publication_live_stack

SCOPE = (TENANT_A, WORKSPACE_A)
BENCHMARK = "sap_successfactors_talent_benchmark_internal"
MIGRATION = "45_legacy_text_compatible_publication.sql"
SEARCH_PATH_MIGRATION = "46_publication_search_path_pg_temp_last.sql"
TEXT_COLUMNS = [
    {"name": "tenant_id", "type": "TEXT"},
    {"name": "workspace_id", "type": "TEXT"},
    {"name": "value", "type": "INTEGER"},
]


def _admin(stack: LiveStack, query: str, params=(), fetch: bool = True):
    return stack.sql(stack.admin_dsn, SCOPE, query, params, fetch=fetch)


def _compat_name(stack: LiveStack, dataset: str) -> str:
    return _admin(
        stack,
        "SELECT omega_publication.gold_compatibility_relation(%s::uuid,%s::uuid,%s)",
        (*SCOPE, dataset),
    )[0][0]


def _legacy_head(
    stack: LiveStack, dataset: str, definition: str, row: tuple
) -> tuple[uuid.UUID, str]:
    legacy = f"gold_{dataset}"
    run = uuid.uuid4()
    marks = ",".join(["%s"] * len(row))
    _admin(stack, f'CREATE TABLE public."{legacy}" ({definition})', fetch=False)
    _admin(stack, f'INSERT INTO public."{legacy}" VALUES ({marks})', row, fetch=False)
    _admin(
        stack,
        "INSERT INTO omega_publication.materialization_runs ("
        "materialization_run_id,tenant_id,workspace_id,dataset,layer,input_digest,"
        "contract_digest,gold_table,row_count,status) VALUES "
        "(%s,%s,%s,%s,'gold',repeat('0',64),repeat('0',64),%s,1,'legacy_unverified')",
        (str(run), *SCOPE, dataset, legacy),
        fetch=False,
    )
    _admin(
        stack,
        "INSERT INTO omega_publication.dataset_publication_heads "
        "(tenant_id,workspace_id,dataset,layer,materialization_run_id,generation) "
        "VALUES (%s,%s,%s,'gold',%s,1)",
        (*SCOPE, dataset, str(run)),
        fetch=False,
    )
    compat = _compat_name(stack, dataset)
    _admin(
        stack,
        f'CREATE TABLE public."{compat}" (LIKE public."{legacy}" INCLUDING DEFAULTS)',
        fetch=False,
    )
    _admin(
        stack, f'INSERT INTO public."{compat}" SELECT * FROM public."{legacy}"', fetch=False
    )
    _admin(stack, f'ALTER TABLE public."{compat}" OWNER TO omega_gold_owner', fetch=False)
    _admin(
        stack,
        "INSERT INTO omega_publication.dataset_gold_relations "
        "(tenant_id,workspace_id,dataset,relation_name) VALUES (%s,%s,%s,%s)",
        (*SCOPE, dataset, compat),
        fetch=False,
    )
    return run, compat


def _prepared_run(
    stack: LiveStack,
    dataset: str,
    columns: list[dict[str, str]],
    row: tuple,
    *,
    expected: uuid.UUID | None,
    digest: str = "a" * 64,
) -> tuple[uuid.UUID, str]:
    run = uuid.uuid4()
    stack.sql(
        stack.publisher_dsn,
        SCOPE,
        "SELECT * FROM omega_publication.reserve_materialization("
        "%s,%s,%s,%s,'gold',%s,%s,%s)",
        (str(run), *SCOPE, dataset, digest, "b" * 64, str(expected) if expected else None),
    )
    stage = stack.sql(
        stack.publisher_dsn,
        SCOPE,
        "SELECT omega_publication.create_gold_stage(%s,%s::jsonb)",
        (str(run), json.dumps(columns)),
    )[0][0]
    marks = ",".join(["%s"] * len(row))
    stack.sql(
        stack.publisher_dsn,
        SCOPE,
        f'INSERT INTO omega_publication_stage."{stage}" VALUES ({marks})',
        row,
        fetch=False,
    )
    uri, checksum = stack.write_object(dataset, run, 1)
    lineage = stack.bound_lineage(run, valid_lineage())
    catalog = json.dumps(columns)
    stack.attest(
        run,
        uri=uri,
        checksum=checksum,
        row_count=1,
        lineage=lineage,
        catalog=catalog,
    )
    stack.sql(
        stack.publisher_dsn,
        SCOPE,
        "SELECT * FROM omega_publication.mark_prepared("
        "%s,%s,%s,%s,1,%s,%s,%s::jsonb,%s::jsonb)",
        (
            str(run),
            uri,
            stack.object_version(uri),
            checksum,
            stage,
            stage,
            lineage,
            catalog,
        ),
    )
    return run, stage


_SEARCH_PATHS = (
    "SELECT p.proname, c FROM pg_proc p CROSS JOIN LATERAL unnest(p.proconfig) c "
    "WHERE p.pronamespace='omega_publication'::regnamespace "
    "AND c LIKE 'search_path=%' ORDER BY 1"
)
_PG_TEMP_LAST = {
    "search_path=pg_catalog, pg_temp",
    "search_path=pg_catalog, omega_publication, pg_temp",
    "search_path=pg_catalog, public, pg_temp",
}


def _column_types(stack: LiveStack, relation: str) -> dict[str, str]:
    return dict(
        _admin(
            stack,
            "SELECT attname,format_type(atttypid,atttypmod) FROM pg_attribute "
            "WHERE attrelid=format('public.%%I',%s::text)::regclass "
            "AND attnum>0 AND NOT attisdropped",
            (relation,),
        )
    )


def _run_state(stack: LiveStack, run: uuid.UUID) -> tuple:
    return _admin(
        stack,
        "SELECT status,recovery_reason,to_regclass("
        "'omega_publication_stage.run_' || replace(%s::text,'-','')) IS NULL "
        "FROM omega_publication.materialization_runs WHERE materialization_run_id=%s",
        (str(run), str(run)),
    )[0]


def _head(stack: LiveStack, dataset: str) -> tuple[str, int] | None:
    head = stack.head(dataset)
    return None if head is None else (str(head[0]), int(head[1]))


def _reject(stack: LiveStack, run: uuid.UUID, expected: uuid.UUID | None) -> str:
    with pytest.raises(psycopg2.Error) as raised:
        stack.publish(run, expected)
    return str(raised.value.pgcode)


def test_unbounded_varchar_compat_relation_publishes_a_text_stage(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    dataset = "legacy_varchar_probe"
    legacy_run, compat = _legacy_head(
        stack,
        dataset,
        "tenant_id character varying, workspace_id character varying, value integer",
        (*SCOPE, 7),
    )
    before = _column_types(stack, compat)
    run, _ = _prepared_run(stack, dataset, TEXT_COLUMNS, (*SCOPE, 1), expected=legacy_run)

    receipt = stack.publish(run, legacy_run)

    assert receipt[1:] == (2, False)
    assert _head(stack, dataset) == (str(run), 2)
    assert _run_state(stack, run)[0] == "published"
    assert before["tenant_id"] == "character varying"
    assert _column_types(stack, compat) == before
    assert stack.compatibility_rows(dataset) == [(1,)]
    assert stack.rows(dataset) == [(1,)]
    security = _admin(
        stack,
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE oid=format('public.%%I',%s::text)::regclass",
        (compat,),
    )[0]
    policies = _admin(
        stack,
        "SELECT policyname FROM pg_policies WHERE schemaname='public' "
        "AND tablename=%s ORDER BY policyname",
        (compat,),
    )
    assert security == (True, True)
    assert policies == [("publication_owner",), ("tenant_workspace_rls",)]


@pytest.mark.parametrize(
    "dataset, definition, stage_type, value",
    [
        (
            "legacy_bounded_varchar_probe",
            "tenant_id character varying(64), workspace_id text, value integer",
            "INTEGER",
            1,
        ),
        (
            "legacy_integer_text_probe",
            "tenant_id text, workspace_id text, value integer",
            "TEXT",
            "1",
        ),
    ],
)
def test_real_type_conflicts_fail_with_datatype_mismatch(
    staged_publication_live_stack: LiveStack,
    dataset: str,
    definition: str,
    stage_type: str,
    value: object,
) -> None:
    stack = staged_publication_live_stack
    legacy_run, compat = _legacy_head(stack, dataset, definition, (*SCOPE, 7))
    before = _column_types(stack, compat)
    columns = [*TEXT_COLUMNS[:2], {"name": "value", "type": stage_type}]
    run, _ = _prepared_run(stack, dataset, columns, (*SCOPE, value), expected=legacy_run)

    assert _reject(stack, run, legacy_run) == "42804"
    assert _head(stack, dataset) == (str(legacy_run), 1)
    assert _run_state(stack, run)[0] == "prepared"
    assert _column_types(stack, compat) == before
    assert _admin(stack, f'SELECT value FROM public."{compat}"') == [(7,)]


def test_benchmark_compat_relation_publishes_under_its_approval_check(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    definition = (
        "tenant_id character varying, workspace_id character varying, "
        "user_id character varying, approved boolean, approved_by integer, "
        "approved_at integer, approval_source text, benchmark_version text, "
        "blockers text, approval_actor_source text, "
        "approval_recorded_by_server boolean NOT NULL DEFAULT false, "
        "approval_evidence_ref text, approval_authorization_ref text, "
        "approval_authorization_verified boolean NOT NULL DEFAULT false, "
        "approval_status text NOT NULL DEFAULT 'unreviewed'"
    )

    def row(user: str, approved: bool) -> tuple:
        return (
            *SCOPE,
            user,
            approved,
            None,
            None,
            "system_default",
            "talent_benchmark_internal.v1.unreviewed",
            '["benchmark_internal_unreviewed"]',
            None,
            False,
            None,
            None,
            False,
            "unreviewed",
        )

    legacy_run, compat = _legacy_head(stack, BENCHMARK, definition, row("legacy", False))
    columns = [
        {"name": "tenant_id", "type": "TEXT"},
        {"name": "workspace_id", "type": "TEXT"},
        {"name": "user_id", "type": "TEXT"},
        {"name": "approved", "type": "BOOLEAN"},
        {"name": "approved_by", "type": "INTEGER"},
        {"name": "approved_at", "type": "INTEGER"},
        {"name": "approval_source", "type": "TEXT"},
        {"name": "benchmark_version", "type": "TEXT"},
        {"name": "blockers", "type": "TEXT"},
        {"name": "approval_actor_source", "type": "TEXT"},
        {"name": "approval_recorded_by_server", "type": "BOOLEAN"},
        {"name": "approval_evidence_ref", "type": "TEXT"},
        {"name": "approval_authorization_ref", "type": "TEXT"},
        {"name": "approval_authorization_verified", "type": "BOOLEAN"},
        {"name": "approval_status", "type": "TEXT"},
    ]
    constraint = (
        "SELECT count(*) FROM pg_constraint WHERE conname="
        "'talent_benchmark_approval_authority_check' "
        "AND conrelid=format('public.%%I',%s::text)::regclass"
    )
    assert _admin(stack, constraint, (compat,)) == [(1,)]

    forged, _ = _prepared_run(
        stack, BENCHMARK, columns, row("forged", True), expected=legacy_run
    )
    assert _reject(stack, forged, legacy_run) == "23514"
    assert _head(stack, BENCHMARK) == (str(legacy_run), 1)

    run, _ = _prepared_run(
        stack,
        BENCHMARK,
        columns,
        row("current", False),
        expected=legacy_run,
        digest="c" * 64,
    )
    stack.publish(run, legacy_run)

    assert _head(stack, BENCHMARK) == (str(run), 2)
    assert _admin(stack, constraint, (compat,)) == [(1,)]
    assert stack.sql(
        stack.reader_dsn,
        SCOPE,
        f'SELECT user_id,approval_status FROM public."{compat}"',
    ) == [("current", "unreviewed")]


def test_missing_prepared_gold_stage_is_object_not_in_prerequisite_state(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    run, stage = _prepared_run(
        stack, "missing_stage_sqlstate_probe", TEXT_COLUMNS, (*SCOPE, 1), expected=None
    )
    _admin(stack, f'DROP TABLE omega_publication_stage."{stage}"', fetch=False)

    assert _reject(stack, run, None) == "55000"
    assert _head(stack, "missing_stage_sqlstate_probe") is None


def test_temporary_catalog_shadow_cannot_bypass_the_type_gate(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    dataset = "temp_catalog_shadow_probe"
    legacy_run, compat = _legacy_head(
        stack,
        dataset,
        "tenant_id character varying, workspace_id character varying, value integer",
        (*SCOPE, 7),
    )
    columns = [*TEXT_COLUMNS[:2], {"name": "value", "type": "DECIMAL(10,2)"}]
    run, stage = _prepared_run(stack, dataset, columns, (*SCOPE, "2.60"), expected=legacy_run)

    with pytest.raises(psycopg2.Error) as raised:
        with psycopg2.connect(stack.publisher_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,true)", (SCOPE[0],))
            cur.execute("SELECT set_config('app.workspace_id',%s,true)", (SCOPE[1],))
            cur.execute(
                "CREATE TEMP TABLE pg_attribute AS "
                "SELECT attrelid,attname,atttypid,atttypmod,attnum,attisdropped "
                "FROM pg_catalog.pg_attribute WHERE attrelid IN ("
                "format('omega_publication_stage.%%I',%s::text)::regclass,"
                "format('public.%%I',%s::text)::regclass)",
                (stage, compat),
            )
            cur.execute(
                "UPDATE pg_temp.pg_attribute SET atttypid='numeric'::regtype,"
                "atttypmod=(SELECT atttypmod FROM pg_catalog.pg_attribute WHERE attrelid="
                "format('omega_publication_stage.%%I',%s::text)::regclass "
                "AND attname='value') WHERE attname='value'",
                (stage,),
            )
            cur.execute("GRANT SELECT ON pg_temp.pg_attribute TO PUBLIC")
            cur.execute(
                "SELECT * FROM omega_publication.publish_materialization(%s,%s)",
                (str(run), str(legacy_run)),
            )

    assert raised.value.pgcode == "42804"
    assert _head(stack, dataset) == (str(legacy_run), 1)
    assert _run_state(stack, run)[0] == "prepared"
    assert _column_types(stack, compat)["value"] == "integer"
    assert _admin(stack, f'SELECT value FROM public."{compat}"') == [(7,)]


def test_every_pinned_publication_function_searches_pg_temp_last(
    staged_publication_live_stack: LiveStack,
) -> None:
    paths = _admin(staged_publication_live_stack, _SEARCH_PATHS.replace("%", "%%"))

    assert len(paths) == 17
    assert {row[1] for row in paths} == _PG_TEMP_LAST


def test_database_at_44_upgrades_to_45_and_records_it(
    staged_publication_live_stack: LiveStack,
) -> None:
    stack = staged_publication_live_stack
    container = f"omega-gold-45-upgrade-{uuid.uuid4().hex[:12]}"
    shared_parent = stack.repo_root.parent if platform.system() == "Darwin" else None
    options = (
        f"-c app.omega_refinement_gold_password={READER_PASSWORD} "
        f"-c app.omega_gold_publisher_password={PUBLISHER_PASSWORD} "
        f"-c app.omega_gold_verifier_password={VERIFIER_PASSWORD}"
    )
    with tempfile.TemporaryDirectory(prefix="gold-45-", dir=shared_parent) as temp:
        at_44 = Path(temp) / "init_gold"
        shutil.copytree(
            stack.repo_root / "infra/init_gold",
            at_44,
            ignore=shutil.ignore_patterns(MIGRATION, SEARCH_PATH_MIGRATION),
        )
        _docker(
            "run",
            "--pull=never",
            "-d",
            "--rm",
            "--name",
            container,
            "-e",
            "POSTGRES_DB=modecissions_gold",
            "-e",
            "POSTGRES_USER=postgres",
            "-e",
            f"POSTGRES_PASSWORD={PASSWORD}",
            "-e",
            f"PGOPTIONS={options}",
            "-v",
            f"{at_44}:/docker-entrypoint-initdb.d:ro",
            "-v",
            f"{stack.repo_root}/infra/init_gold:/current-gold:ro",
            "-P",
            "postgres:15.18",
        )
        try:
            dsn = (
                f"postgresql://postgres:{PASSWORD}@127.0.0.1:"
                f"{_port(container, '5432/tcp')}/modecissions_gold"
            )
            _wait(dsn)
            recorded = (
                "SELECT count(*) FILTER (WHERE filename="
                "'gold/44_talent_benchmark_approval_publication_authority.sql'),"
                "count(*) FILTER (WHERE filename='gold/" + MIGRATION + "'),"
                "count(*) FILTER (WHERE filename='gold/" + SEARCH_PATH_MIGRATION + "') "
                "FROM schema_migrations"
            )
            definition = (
                "SELECT pg_get_functiondef("
                "'omega_publication.publish_materialization(uuid,uuid)'::regprocedure)"
            )
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(recorded)
                assert cur.fetchone() == (1, 0, 0)
                cur.execute(definition)
                assert "42804" not in cur.fetchone()[0]
            for filename in (MIGRATION, SEARCH_PATH_MIGRATION) * 2:
                _docker(
                    "exec",
                    "-e",
                    f"PGPASSWORD={PASSWORD}",
                    "-e",
                    f"PGOPTIONS={options}",
                    container,
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "postgres",
                    "-d",
                    "modecissions_gold",
                    "-f",
                    f"/current-gold/{filename}",
                )
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(recorded)
                assert cur.fetchone() == (1, 1, 1)
                cur.execute(definition)
                function = cur.fetchone()[0]
                assert "ERRCODE='42804'" in function
                assert "ERRCODE='55000'" in function
                assert "existing_type = 'character varying'" in function
                cur.execute(_SEARCH_PATHS)
                paths = cur.fetchall()
                assert len(paths) == 17
                assert {row[1] for row in paths} == _PG_TEMP_LAST
                cur.execute(
                    "SELECT role, has_function_privilege(role,"
                    "'omega_publication.publish_materialization(uuid,uuid)','EXECUTE') "
                    "FROM unnest(ARRAY['omega_gold_publisher','omega_refinement_gold',"
                    "'omega_gold_verifier']) role ORDER BY role"
                )
                assert cur.fetchall() == [
                    ("omega_gold_publisher", True),
                    ("omega_gold_verifier", False),
                    ("omega_refinement_gold", False),
                ]
                cur.execute(
                    "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid="
                    "'omega_publication.publish_materialization(uuid,uuid)'::regprocedure"
                )
                assert cur.fetchone() == ("omega_gold_owner",)
        finally:
            _docker("rm", "-f", "-v", container, check=False)


def _refinement_main(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "INTERNAL_API_KEY", "legacy-compat-live-internal-api-key-over-32-chars"
    )
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "refinement")
    )
    return importlib.import_module("app.main")


def test_type_conflict_is_a_typed_rejection_without_fallback(
    staged_publication_live_stack: LiveStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = staged_publication_live_stack
    dataset = {
        "name": "typed_rejection_probe",
        "layer": "gold",
        "cartridge": "acceptance",
        "sql_def": "SELECT 'unit'::VARCHAR AS label",
        "sources": [],
    }
    compat = _compat_name(stack, dataset["name"])
    _admin(
        stack,
        f'CREATE TABLE public."{compat}" '
        "(tenant_id text, workspace_id text, label integer)",
        fetch=False,
    )
    _admin(stack, f'ALTER TABLE public."{compat}" OWNER TO omega_gold_owner', fetch=False)
    _engine(stack, monkeypatch)
    main = _refinement_main(monkeypatch)
    fallbacks: list[str] = []

    def no_fallback(*_args, **_kwargs):
        fallbacks.append("fallback")
        return None

    monkeypatch.setattr(main, "fallback_dataset_for_successfactors", no_fallback)
    monkeypatch.setattr(main, "_materialize_readfree_empty", no_fallback)
    try:
        with pytest.raises(main.PublicationRejected) as rejected:
            main._materialize_with_operational_fallback(dataset, _scope())
    finally:
        if main.engine._con is not None:
            main.engine._con.close()

    status, detail = main._friendly_duckdb_error(rejected.value, dataset["name"])
    assert (status, detail["code"]) == (409, "publication_rejected")
    assert rejected.value.__cause__.pgcode == "42804"
    assert fallbacks == []
    run = _admin(
        stack,
        "SELECT materialization_run_id FROM omega_publication.materialization_runs "
        "WHERE dataset=%s",
        (dataset["name"],),
    )
    assert len(run) == 1
    assert _run_state(stack, run[0][0]) == (
        "recoverable_failed",
        "legacy_type_conflict",
        True,
    )
    assert _admin(
        stack,
        "SELECT reason FROM omega_publication.materialization_recovery_events "
        "WHERE materialization_run_id=%s ORDER BY created_at",
        (str(run[0][0]),),
    ) == [("legacy_type_conflict",), ("legacy_type_conflict",)]
    assert _head(stack, dataset["name"]) is None
    assert _column_types(stack, compat)["label"] == "integer"
