from __future__ import annotations

import io
import platform
import subprocess
import tarfile
import tempfile
import uuid

import psycopg2
import pytest

from tests.staged_publication_canaries import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)


def _historical_gold_upgrade(stack, migrations, old_names) -> None:
    from tests.staged_publication_live import (
        PASSWORD,
        PUBLISHER_PASSWORD,
        READER_PASSWORD,
        VERIFIER_PASSWORD,
        _docker,
        _port,
        _wait,
    )

    archive = subprocess.run(
        [
            "git",
            "archive",
            "c680f0ddbd91f114797e70d0d26c5245d3cdc2db",
            "infra/init_gold",
        ],
        cwd=stack.repo_root,
        check=True,
        capture_output=True,
    ).stdout
    container = f"pr555-historical-gold-{uuid.uuid4().hex[:12]}"
    shared_parent = stack.repo_root.parent if platform.system() == "Darwin" else None
    with tempfile.TemporaryDirectory(
        prefix="pr555-gold-upgrade-", dir=shared_parent
    ) as temp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(temp, filter="data")
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
            f"PGOPTIONS=-c app.omega_refinement_gold_password={READER_PASSWORD} "
            f"-c app.omega_gold_publisher_password={PUBLISHER_PASSWORD} "
            f"-c app.omega_gold_verifier_password={VERIFIER_PASSWORD}",
            "-v",
            f"{temp}/infra/init_gold:/docker-entrypoint-initdb.d:ro",
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
            options = (
                f"-c app.omega_refinement_gold_password={READER_PASSWORD} "
                f"-c app.omega_gold_publisher_password={PUBLISHER_PASSWORD} "
                f"-c app.omega_gold_verifier_password={VERIFIER_PASSWORD}"
            )
            with psycopg2.connect(dsn, options=options) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM schema_migrations WHERE filename=ANY(%s)",
                    ([old_names[-1].removeprefix("gold/")],),
                )
                assert cur.fetchone()[0] == 1
                cur.execute(
                    "SELECT to_regclass('omega_publication.materialization_runs'),"
                    "to_regprocedure('omega_publication.publish_materialization(uuid,uuid)')"
                )
                assert all(cur.fetchone())
                for migration in (*migrations, *migrations):
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
                        f"/current-gold/{migration}",
                    )
                cur.execute(
                    "SELECT count(*) FROM schema_migrations WHERE filename=ANY(%s)",
                    ([f"gold/{name}" for name in migrations],),
                )
                assert cur.fetchone()[0] == len(migrations)
        finally:
            _docker("rm", "-f", "-v", container, check=False)


def C11_legacy_drop_negative_control(stack):
    stack.baseline("canary_c11")
    source = (stack.repo_root / "refinement/app/main.py").read_text(encoding="utf-8")
    guard = source.index("published datasets require a staged retirement operation")
    assert guard < source.index("DROP TABLE IF EXISTS", guard)
    assert stack.rows("canary_c11") == [(1,)]


def C12_upgrade_and_rerun(stack):
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "CREATE TABLE public.gold_legacy_probe "
        "(tenant_id text, workspace_id text, value integer)",
        fetch=False,
    )
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "INSERT INTO public.gold_legacy_probe VALUES (%s,%s,7);"
        "ALTER TABLE public.gold_legacy_probe ENABLE ROW LEVEL SECURITY;"
        "CREATE POLICY permissive_legacy_leak ON public.gold_legacy_probe USING (true);"
        "CREATE VIEW public.gold_leak_view AS SELECT * FROM public.gold_legacy_probe;"
        "GRANT SELECT ON public.gold_leak_view TO omega_refinement_gold",
        (TENANT_A, WORKSPACE_A),
        fetch=False,
    )
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "CREATE TABLE public.gold_unscoped_probe(value integer);"
        "INSERT INTO public.gold_unscoped_probe VALUES (8)",
        fetch=False,
    )
    migrations = (
        "39_staged_publication_roles.sql",
        "40_staged_publication_schema.sql",
        "41_staged_publication_functions.sql",
        "42_staged_publication_cas.sql",
        "43_staged_publication_authority.sql",
    )
    old_names = tuple(
        f"gold/{value}_staged_publication_{suffix}.sql"
        for value, suffix in (
            (37, "roles"),
            (38, "schema"),
            (39, "functions"),
            (40, "cas"),
            (41, "authority"),
        )
    )
    new_names = tuple(f"gold/{name}" for name in migrations)
    _historical_gold_upgrade(stack, migrations, old_names)
    stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "DELETE FROM schema_migrations WHERE filename=ANY(%s);"
        "INSERT INTO schema_migrations(filename,applied_at) "
        "SELECT value,clock_timestamp() FROM unnest(%s::text[]) value "
        "ON CONFLICT DO NOTHING",
        (list(new_names), list(old_names)),
        fetch=False,
    )
    for migration in (*migrations, *migrations):
        stack.rerun_gold_migration(migration)
    assert stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT count(*) FROM schema_migrations WHERE filename=ANY(%s)",
        (list(old_names + new_names),),
    )[0][0] == len(old_names) + len(new_names)
    head = stack.head("legacy_probe")
    assert head and head[1] == 1 and stack.rows("legacy_probe") == [(7,)]
    assert stack.head("legacy_probe", (TENANT_B, WORKSPACE_B)) is None
    rls = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE oid='public.gold_legacy_probe'::regclass",
    )[0]
    assert rls == (True, True)
    policies = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT policyname FROM pg_policies WHERE schemaname='public' "
        "AND tablename='gold_legacy_probe'",
    )
    assert sorted(policies) == [
        ("gold_legacy_probe_publication_owner",),
        ("gold_legacy_probe_tenant_workspace_rls",),
    ]
    with pytest.raises(psycopg2.Error):
        stack.sql(
            stack.reader_dsn,
            (TENANT_A, WORKSPACE_A),
            "SELECT * FROM public.gold_leak_view",
        )
    quarantine = stack.sql(
        stack.admin_dsn,
        (TENANT_A, WORKSPACE_A),
        "SELECT to_regclass('public.gold_unscoped_probe'),"
        "to_regclass('omega_publication_legacy.gold_unscoped_probe'),"
        "(SELECT value FROM omega_publication_legacy.gold_unscoped_probe)",
    )[0]
    assert quarantine == (None, "omega_publication_legacy.gold_unscoped_probe", 8)
