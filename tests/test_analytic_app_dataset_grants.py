"""The grant ledger is the only thing that authorises a published app's read.

Run against a real PostgreSQL with the real migrations applied. Three families
of check live here:

* **self-authorisation** — C1's canaries: the app's HTML, its stored
  ``datasets_used`` and a cross-cartridge claim must all fail to grant;
* **pg_temp shadowing** — a caller that creates temp tables named like the
  tables a ``SECURITY DEFINER`` function consults must not be able to steer it.
  This was reproducible: with ``search_path = pg_catalog, public`` PostgreSQL
  searches the temporary schema *first* for relations, and the function was
  owned by a superuser, so shadowing it granted an unapproved dataset;
* **lifecycle** — revocation, scope isolation, and grants dying with their
  installation.

``OMEGA_TEST_GRANTS_DSN`` selects the database. CI sets it and these tests must
not skip there; locally they skip when it is absent.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

asyncpg = pytest.importorskip("asyncpg")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from app.domains.apps.grants import granted_datasets, has_grant  # noqa: E402
from app.domains.apps.manifests import (  # noqa: E402
    drift_report,
    load_packaged_manifests,
    manifest_digest,
    served_digest,
)

DSN = os.environ.get("OMEGA_TEST_GRANTS_DSN", "")
pytestmark = [
    pytest.mark.skipif(not DSN, reason="OMEGA_TEST_GRANTS_DSN not configured"),
    pytest.mark.asyncio,
]

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
WS_A1 = "aaaaaaaa-0000-0000-0000-000000000001"
WS_A2 = "aaaaaaaa-0000-0000-0000-000000000002"
WS_B2 = "bbbbbbbb-0000-0000-0000-000000000002"

APP = "sap_successfactors_talent_health"
CUSTOM_APP = "custom_user_app"
APPROVED = "sap_successfactors_employees_anomalies"
SENSITIVE = "sensitive_same_workspace"
OTHER_CARTRIDGE = "consultor_mensual"


async def _scoped(conn, tenant: str, workspace: str) -> None:
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, false), "
        "set_config('app.workspace_id', $2, false)",
        tenant, workspace,
    )


@pytest_asyncio.fixture
async def conn():
    connection = await asyncpg.connect(DSN)
    try:
        await connection.execute("DELETE FROM public.analytic_app_dataset_grants")
        await connection.execute(
            "UPDATE public.cartridge_installations SET status = 'ready'"
        )
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def app_conn():
    """A connection acting as omega_console — what the service really is."""
    connection = await asyncpg.connect(DSN)
    try:
        await connection.execute("DELETE FROM public.analytic_app_dataset_grants")
        await connection.execute("SET ROLE omega_console")
        yield connection
    finally:
        await connection.close()


async def _reconcile(conn, app=APP, expected=None):
    return await conn.fetch(
        "SELECT * FROM public.reconcile_analytic_app_dataset_grants($1, $2)",
        app, expected,
    )


async def _active_digest(conn, app=APP):
    return await conn.fetchval(
        "SELECT manifest_digest FROM public.analytic_app_manifests "
        "WHERE app_name = $1 AND revision = 'active'", app,
    )


# --- pg_temp shadowing ------------------------------------------------------


async def test_temp_tables_cannot_steer_the_definer_function(app_conn):
    """The reproduced P0, kept as a canary.

    Five temp tables shadow every relation the function consults, including a
    forged manifest registry that "approves" a dataset nobody reviewed. The
    function must resolve public.* regardless and grant only what the real
    registry holds.
    """
    await _scoped(app_conn, TENANT_A, WS_A1)
    await app_conn.execute("""
        CREATE TEMP TABLE datasets (name TEXT, layer TEXT, cartridge TEXT,
            workspace_id UUID, tenant_id UUID, scope_status TEXT);
        CREATE TEMP TABLE analytic_apps (name TEXT, title TEXT, html TEXT,
            cartridge_id TEXT, created_by_id BIGINT);
        CREATE TEMP TABLE cartridge_installations (id TEXT, tenant_id UUID,
            workspace_id UUID, cartridge_id TEXT, status TEXT);
        CREATE TEMP TABLE analytic_app_manifests (app_name TEXT,
            cartridge_id TEXT, manifest_digest CHAR(64), html_sha256 CHAR(64),
            revision TEXT, source TEXT);
        CREATE TEMP TABLE analytic_app_manifest_datasets (app_name TEXT,
            manifest_digest CHAR(64), dataset_name TEXT);
    """)
    await app_conn.execute(
        "INSERT INTO pg_temp.analytic_app_manifests VALUES "
        "($1,'sap_successfactors',repeat('a',64),repeat('a',64),'active','packaged_manifest')",
        APP,
    )
    await app_conn.execute(
        "INSERT INTO pg_temp.analytic_app_manifest_datasets VALUES "
        "($1,repeat('a',64),$2)", APP, SENSITIVE,
    )
    await app_conn.execute(
        "INSERT INTO pg_temp.datasets VALUES ($1,'gold','sap_successfactors',$2::uuid,$3::uuid,'scoped')",
        SENSITIVE, WS_A1, TENANT_A,
    )

    granted = [r["dataset_name"] for r in await _reconcile(app_conn)]
    assert SENSITIVE not in granted
    assert await app_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants "
        "WHERE dataset_name = $1 AND revoked_at IS NULL", SENSITIVE,
    ) == 0


async def test_the_old_permissive_signature_is_gone(app_conn):
    """A caller that can pass datasets, cartridge or actor grants itself
    anything. That overload must not exist at all."""
    with pytest.raises(asyncpg.PostgresError):
        await app_conn.fetch(
            "SELECT * FROM public.reconcile_analytic_app_dataset_grants"
            "($1,$2,$3::text[],$4,$5)",
            APP, "sap_successfactors", [SENSITIVE], "a" * 64, "attacker",
        )


async def test_definer_functions_are_not_owned_by_a_superuser(conn):
    rows = await conn.fetch("""
        SELECT p.proname, pg_get_userbyid(p.proowner) AS owner,
               r.rolsuper, r.rolbypassrls, p.proconfig
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_roles r ON r.oid = p.proowner
         WHERE n.nspname = 'public' AND p.proname LIKE '%analytic_app%'
    """)
    assert rows, "the grant functions must exist"
    for row in rows:
        assert row["owner"] == "omega_app_grants_owner", row["proname"]
        assert row["rolsuper"] is False, row["proname"]
        assert row["rolbypassrls"] is False, row["proname"]
        config = list(row["proconfig"] or [])
        assert "search_path=pg_catalog, pg_temp" in config, row["proname"]
        # public must not be on the path; every name is qualified instead.
        assert not any("public" in item for item in config), row["proname"]


async def test_the_owner_role_cannot_log_in_or_escalate(conn):
    row = await conn.fetchrow(
        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
        "FROM pg_roles WHERE rolname = 'omega_app_grants_owner'"
    )
    assert row is not None
    assert not any(row.values())


async def test_no_application_role_may_write_the_ledger_or_the_registry(app_conn):
    await _scoped(app_conn, TENANT_A, WS_A1)
    for statement in (
        "INSERT INTO public.analytic_app_dataset_grants(tenant_id,workspace_id,"
        "app_name,cartridge_id,dataset_name,manifest_digest,grant_source,granted_by)"
        f" VALUES('{TENANT_A}','{WS_A1}','{APP}','sap_successfactors','{SENSITIVE}',"
        "repeat('a',64),'packaged_manifest','server:packaged_manifest')",
        "UPDATE public.analytic_app_dataset_grants SET revoked_at = NULL",
        "DELETE FROM public.analytic_app_dataset_grants",
        f"INSERT INTO public.analytic_app_manifest_datasets VALUES('{APP}',repeat('a',64),'{SENSITIVE}')",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(statement)


# --- self-authorisation canaries -------------------------------------------


async def test_html_mention_does_not_authorise(conn):
    """The app's own markup is not an approval, however plainly it asks."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _reconcile(conn)
    digest = await _active_digest(conn)
    allowed = await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=digest,
    )
    assert SENSITIVE not in allowed
    assert APPROVED in allowed


async def test_runtime_metadata_does_not_authorise(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    await conn.execute(
        "UPDATE public.analytic_apps SET datasets_used = $1::text[] WHERE name = $2",
        [APPROVED, SENSITIVE], APP,
    )
    await _reconcile(conn)
    digest = await _active_digest(conn)
    allowed = await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=digest,
    )
    assert SENSITIVE not in allowed


async def test_cross_cartridge_dataset_is_never_granted(conn):
    """The registry has no such row for this app, so there is nothing to grant
    — and the manifest FK would refuse the row even if something tried."""
    await _scoped(conn, TENANT_A, WS_A1)
    granted = [r["dataset_name"] for r in await _reconcile(conn)]
    assert OTHER_CARTRIDGE not in granted


async def test_a_custom_app_gets_nothing(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    assert await _reconcile(conn, app=CUSTOM_APP) == []
    assert served_digest(CUSTOM_APP, "<html></html>") is None


async def test_a_stale_expected_digest_aborts(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    with pytest.raises(asyncpg.PostgresError, match="stale"):
        await _reconcile(conn, expected="f" * 64)


# --- the positive read ------------------------------------------------------


async def test_a_reviewed_manifest_authorises_exactly_its_datasets(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    actions = await _reconcile(conn)
    assert all(r["action"] == "granted" for r in actions)
    digest = await _active_digest(conn)
    allowed = await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=digest,
    )
    registry = [
        r["dataset_name"] for r in await conn.fetch(
            "SELECT dataset_name FROM public.analytic_app_manifest_datasets "
            "WHERE app_name = $1 AND manifest_digest = $2 ORDER BY dataset_name",
            APP, digest,
        )
    ]
    # Only the registry datasets that exist in this workspace.
    assert set(allowed) <= set(registry)
    assert APPROVED in allowed
    assert await has_grant(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1, app_name=APP,
        manifest_digest=digest, dataset=APPROVED,
    )


async def test_reconciliation_is_idempotent(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    first = len(await _reconcile(conn))
    assert first > 0
    assert await _reconcile(conn) == []
    assert await _reconcile(conn) == []


# --- lifecycle --------------------------------------------------------------


async def test_leaving_ready_revokes_immediately(conn):
    """A grant that outlives its installation is authority the operator
    believes they withdrew."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _reconcile(conn)
    digest = await _active_digest(conn)
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=digest,
    )
    revoked = await conn.fetchval(
        "SELECT public.revoke_analytic_app_cartridge_grants($1, $2)",
        "sap_successfactors", "installation_paused",
    )
    assert revoked > 0
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=digest,
    ) == []


async def test_a_not_ready_installation_blocks_the_read_without_revoking(conn):
    """The authoritative read joins the installation, so there is no window
    between the grant lookup and the state check."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _reconcile(conn)
    digest = await _active_digest(conn)
    await conn.execute(
        "UPDATE public.cartridge_installations SET status = 'paused' "
        "WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid", TENANT_A, WS_A1,
    )
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=digest,
    ) == []


async def test_returning_to_ready_reconciles_without_resurrecting(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    await _reconcile(conn)
    before = await conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants WHERE revoked_at IS NULL"
    )
    await conn.fetchval(
        "SELECT public.revoke_analytic_app_cartridge_grants($1, $2)",
        "sap_successfactors", "installation_paused",
    )
    await _reconcile(conn)
    after = await conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants WHERE revoked_at IS NULL"
    )
    assert after == before
    # The revoked rows stay revoked; new rows were issued instead.
    assert await conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants WHERE revoked_at IS NOT NULL"
    ) == before


async def test_scope_isolation(conn):
    """The same dataset name exists in all three scopes, so this really tests
    scope and not name matching."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _reconcile(conn)
    digest = await _active_digest(conn)
    for tenant, workspace in ((TENANT_A, WS_A2), (TENANT_B, WS_B2)):
        await _scoped(conn, tenant, workspace)
        assert await granted_datasets(
            conn, tenant_id=tenant, workspace_id=workspace,
            app_name=APP, manifest_digest=digest,
        ) == []


async def test_rls_hides_other_scopes_from_the_application_role(app_conn):
    await _scoped(app_conn, TENANT_A, WS_A1)
    await _reconcile(app_conn)
    mine = await app_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants"
    )
    assert mine > 0
    await _scoped(app_conn, TENANT_B, WS_B2)
    assert await app_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants"
    ) == 0


# --- registry integrity -----------------------------------------------------


async def test_the_registry_matches_the_packaged_manifests(conn):
    manifests = load_packaged_manifests()
    assert len(manifests) == 18
    rows = await conn.fetch(
        "SELECT app_name, cartridge_id, manifest_digest FROM public.analytic_app_manifests "
        "WHERE revision = 'active' ORDER BY app_name"
    )
    assert len(rows) == 18
    for row in rows:
        packaged = manifests[row["app_name"]]
        assert row["cartridge_id"] == packaged["cartridge_id"]
        assert row["manifest_digest"] == packaged["packaged_digest"]


async def test_drift_report_describes_but_never_widens():
    report = drift_report(
        granted=[APPROVED], referenced=[APPROVED, SENSITIVE],
        manifest_datasets=[APPROVED], served="a" * 64, packaged="b" * 64,
    )
    assert report["requested_but_not_granted"] == [SENSITIVE]
    assert report["manifest_drift"] is True
    assert "allowed" not in report


async def test_packaged_digest_covers_the_html():
    manifests = load_packaged_manifests()
    one = manifests[APP]
    same = manifest_digest(
        app_name=one["app_name"], cartridge_id=one["cartridge_id"],
        datasets=one["datasets"], html=one["packaged_html"],
    )
    edited = manifest_digest(
        app_name=one["app_name"], cartridge_id=one["cartridge_id"],
        datasets=one["datasets"], html=one["packaged_html"] + "<!-- x -->",
    )
    assert same == one["packaged_digest"]
    assert edited != one["packaged_digest"]
