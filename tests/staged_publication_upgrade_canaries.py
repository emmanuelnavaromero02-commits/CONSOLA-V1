from __future__ import annotations

import psycopg2
import pytest

from tests.staged_publication_canaries import (
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
)


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
        "37_staged_publication_roles.sql",
        "38_staged_publication_schema.sql",
        "39_staged_publication_functions.sql",
        "40_staged_publication_cas.sql",
    )
    for migration in (*migrations, *migrations):
        stack.rerun_gold_migration(migration)
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
