from __future__ import annotations

import hashlib
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
from app.services.reconcile_packaged_app_grants import (  # noqa: E402
    reconcile_packaged_app_grants,
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


async def test_temp_tables_cannot_steer_the_definer_function(app_conn):
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
        assert not any("public" in item for item in config), row["proname"]


async def test_the_owner_role_cannot_log_in_or_escalate(conn):
    row = await conn.fetchrow(
        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
        "FROM pg_roles WHERE rolname = 'omega_app_grants_owner'"
    )
    assert row is not None
    assert not any(row.values())


async def test_the_owner_has_only_scoped_source_read_policies(conn):
    rows = await conn.fetch(
        """SELECT tablename, policyname, cmd, roles, qual
             FROM pg_policies
            WHERE schemaname = 'public'
              AND policyname LIKE '%app_grants_owner_read'
            ORDER BY tablename"""
    )
    assert {row["tablename"] for row in rows} == {
        "analytic_apps",
        "cartridge_installations",
        "datasets",
    }
    for row in rows:
        assert row["cmd"] == "SELECT"
        assert row["roles"] == ["omega_app_grants_owner"]
        assert "omega_rls_workspace_matches" in row["qual"] or (
            row["tablename"] == "analytic_apps"
            and "analytic_app_manifests" in row["qual"]
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE public.analytic_apps SET created_by_id = 1 WHERE name = $1",
        "UPDATE public.analytic_apps SET cartridge_id = 'replicon' "
        "WHERE name = $1",
        "UPDATE public.analytic_apps SET scope_status = 'scoped' "
        "WHERE name = $1",
        f"UPDATE public.analytic_apps SET tenant_id = '{TENANT_B}'::uuid "
        "WHERE name = $1",
        f"UPDATE public.analytic_apps SET workspace_id = '{WS_B2}'::uuid "
        "WHERE name = $1",
        f"UPDATE public.analytic_apps SET created_by_id = 1, "
        f"tenant_id = '{TENANT_B}'::uuid, "
        f"workspace_id = '{WS_B2}'::uuid, scope_status = 'scoped' "
        "WHERE name = $1",
    ),
    ids=(
        "owned",
        "wrong_cartridge",
        "wrong_scope_status",
        "tenant_bound",
        "workspace_bound",
        "other_scope_collision",
    ),
)
async def test_every_packaged_app_identity_mismatch_is_rejected(app_conn, mutation):
    await app_conn.execute("RESET ROLE")
    transaction = app_conn.transaction()
    await transaction.start()
    try:
        await app_conn.execute(mutation, APP)
        await app_conn.execute("SET LOCAL ROLE omega_console")
        await _scoped(app_conn, TENANT_A, WS_A1)
        with pytest.raises(asyncpg.PostgresError, match="app is not packaged"):
            await _reconcile(app_conn)
    finally:
        await transaction.rollback()

    assert await app_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants"
    ) == 0
    await app_conn.execute("SET ROLE omega_console")


async def test_missing_packaged_app_row_is_distinguished_and_rejected(app_conn):
    await app_conn.execute("RESET ROLE")
    transaction = app_conn.transaction()
    await transaction.start()
    try:
        await app_conn.execute(
            "DELETE FROM public.analytic_apps WHERE name = $1",
            APP,
        )
        await app_conn.execute("SET LOCAL ROLE omega_console")
        await _scoped(app_conn, TENANT_A, WS_A1)
        with pytest.raises(
            asyncpg.PostgresError,
            match="packaged app is not registered",
        ):
            await _reconcile(app_conn)
    finally:
        await transaction.rollback()

    assert await app_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants"
    ) == 0
    await app_conn.execute("SET ROLE omega_console")


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


async def test_html_mention_does_not_authorise(conn):
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
    await _scoped(conn, TENANT_A, WS_A1)
    granted = [r["dataset_name"] for r in await _reconcile(conn)]
    assert OTHER_CARTRIDGE not in granted


async def test_legacy_unscoped_dataset_is_never_granted(conn):
    await conn.execute(
        """UPDATE public.datasets
              SET scope_status = 'legacy_unscoped'
            WHERE tenant_id = $1::uuid
              AND workspace_id = $2::uuid
              AND name = $3""",
        TENANT_A,
        WS_A1,
        APPROVED,
    )
    try:
        await _scoped(conn, TENANT_A, WS_A1)
        granted = [row["dataset_name"] for row in await _reconcile(conn)]
        assert APPROVED not in granted
    finally:
        await conn.execute(
            """UPDATE public.datasets
                  SET scope_status = 'scoped'
                WHERE tenant_id = $1::uuid
                  AND workspace_id = $2::uuid
                  AND name = $3""",
            TENANT_A,
            WS_A1,
            APPROVED,
        )


async def test_a_custom_app_gets_nothing(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    assert await _reconcile(conn, app=CUSTOM_APP) == []
    assert served_digest(CUSTOM_APP, "<html></html>") is None


async def test_a_stale_expected_digest_aborts(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    with pytest.raises(asyncpg.PostgresError, match="stale"):
        await _reconcile(conn, expected="f" * 64)


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


async def test_startup_reconciles_ready_installations_idempotently(conn):
    assert await conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants"
    ) == 0
    supplemental_app = await conn.fetchval(
        """INSERT INTO public.analytic_apps
                  (name, title, html, cartridge_id, created_by_id, visibility,
                   tenant_id, workspace_id, scope_status)
           VALUES ('sap_successfactors_workforce_overview', 'Workforce',
                   '<html></html>', 'sap_successfactors', NULL, 'shared',
                   NULL, NULL, 'platform_template')
           ON CONFLICT (name) DO NOTHING
           RETURNING name"""
    )

    async def initialize(connection):
        await connection.execute("SET ROLE omega_console")

    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1, setup=initialize)
    try:
        await reconcile_packaged_app_grants(pool)
        first_grants = await conn.fetchval(
            "SELECT count(*) FROM public.analytic_app_dataset_grants "
            "WHERE revoked_at IS NULL"
        )
        first_events = await conn.fetchval(
            "SELECT count(*) FROM public.analytic_app_dataset_grant_events "
            "WHERE event = 'granted'"
        )
        await reconcile_packaged_app_grants(pool)
        second_grants = await conn.fetchval(
            "SELECT count(*) FROM public.analytic_app_dataset_grants "
            "WHERE revoked_at IS NULL"
        )
        second_events = await conn.fetchval(
            "SELECT count(*) FROM public.analytic_app_dataset_grant_events "
            "WHERE event = 'granted'"
        )
    finally:
        await pool.close()
        if supplemental_app:
            await conn.execute(
                "DELETE FROM public.analytic_apps WHERE name = $1",
                supplemental_app,
            )

    assert first_grants > 0
    assert second_grants == first_grants
    assert second_events == first_events


async def test_leaving_ready_revokes_immediately(conn):
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
    assert await conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants WHERE revoked_at IS NOT NULL"
    ) == before


async def test_scope_isolation(conn):
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


async def test_the_registry_matches_the_packaged_manifests(conn):
    from scripts.generate_app_manifest_registry import EXPECTED_APPS

    manifests = load_packaged_manifests()
    assert len(manifests) == EXPECTED_APPS
    rows = await conn.fetch(
        "SELECT app_name, cartridge_id, manifest_digest FROM public.analytic_app_manifests "
        "WHERE revision = 'active' AND source = 'packaged_manifest' ORDER BY app_name"
    )
    assert len(rows) == EXPECTED_APPS
    for row in rows:
        packaged = manifests[row["app_name"]]
        assert row["cartridge_id"] == packaged["cartridge_id"]
        assert row["manifest_digest"] == packaged["packaged_digest"]


async def test_active_manifests_carry_exactly_the_packaged_datasets(conn):
    manifests = load_packaged_manifests()
    rows = await conn.fetch(
        "SELECT d.app_name, d.dataset_name FROM public.analytic_app_manifest_datasets d "
        "JOIN public.analytic_app_manifests m ON m.app_name = d.app_name "
        "AND m.manifest_digest = d.manifest_digest WHERE m.revision = 'active' "
        "AND m.source = 'packaged_manifest'"
    )
    active: dict[str, set[str]] = {}
    for row in rows:
        active.setdefault(row["app_name"], set()).add(row["dataset_name"])
    assert active == {
        name: set(manifest["datasets"])
        for name, manifest in manifests.items()
        if manifest["datasets"]
    }


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


CUSTOM_HTML = "<html></html>"
CUSTOM_SHA = hashlib.sha256(CUSTOM_HTML.encode("utf-8")).hexdigest()


def _ws_digest(datasets: list[str], html: str = CUSTOM_HTML, app: str = CUSTOM_APP) -> str:
    return manifest_digest(
        app_name=app, cartridge_id="workspace", datasets=sorted(datasets), html=html
    )


async def _register_ws(conn, datasets: list[str], *, app=CUSTOM_APP,
                       sha=CUSTOM_SHA, digest: str | None = None):
    digest = digest or _ws_digest(datasets, app=app)
    return await conn.fetchval(
        "SELECT public.register_workspace_app_manifest($1, $2, $3::text[], $4)",
        app, sha, sorted(datasets), digest,
    )


async def _reconcile_ws(conn, app=CUSTOM_APP):
    return await conn.fetch(
        "SELECT * FROM public.reconcile_workspace_app_dataset_grants($1)", app,
    )


@pytest_asyncio.fixture
async def ws_conn():
    connection = await asyncpg.connect(DSN)
    try:
        await connection.execute("DELETE FROM public.analytic_app_dataset_grants")
        await connection.execute(
            "UPDATE public.cartridge_installations SET status = 'ready'"
        )
        await connection.execute(
            "DELETE FROM public.analytic_app_manifest_datasets WHERE app_name = $1",
            CUSTOM_APP,
        )
        await connection.execute(
            "DELETE FROM public.analytic_app_manifests WHERE app_name = $1",
            CUSTOM_APP,
        )
        await connection.execute("SET ROLE omega_console")
        yield connection
    finally:
        await connection.close()


async def test_workspace_registration_happy_path(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    digest = _ws_digest([APPROVED])
    assert await _register_ws(ws_conn, [APPROVED]) == digest
    row = await ws_conn.fetchrow(
        "SELECT cartridge_id, source, revision, tenant_id, workspace_id, "
        "created_by_user_id, html_sha256 FROM public.analytic_app_manifests "
        "WHERE app_name = $1", CUSTOM_APP,
    )
    assert dict(row) == {
        "cartridge_id": "workspace",
        "source": "workspace_publication",
        "revision": "active",
        "tenant_id": TENANT_A,
        "workspace_id": WS_A1,
        "created_by_user_id": "1",
        "html_sha256": CUSTOM_SHA,
    }
    children = await ws_conn.fetch(
        "SELECT dataset_name FROM public.analytic_app_manifest_datasets "
        "WHERE app_name = $1 AND manifest_digest = $2", CUSTOM_APP, digest,
    )
    assert [r["dataset_name"] for r in children] == [APPROVED]


async def test_workspace_reconcile_grants_and_reads_back(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    digest = await _register_ws(ws_conn, [APPROVED, SENSITIVE])
    actions = await _reconcile_ws(ws_conn)
    assert sorted((r["dataset_name"], r["action"]) for r in actions) == [
        (APPROVED, "granted"), (SENSITIVE, "granted"),
    ]
    rows = await ws_conn.fetch(
        "SELECT cartridge_id, grant_source, granted_by FROM "
        "public.analytic_app_dataset_grants WHERE app_name = $1 "
        "AND revoked_at IS NULL", CUSTOM_APP,
    )
    assert len(rows) == 2
    for row in rows:
        assert row["cartridge_id"] == "workspace"
        assert row["grant_source"] == "workspace_publication"
        assert row["granted_by"] == "server:workspace_publication"
    allowed = await granted_datasets(
        ws_conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=CUSTOM_APP, manifest_digest=digest,
    )
    assert allowed == sorted([APPROVED, SENSITIVE])
    assert await granted_datasets(
        ws_conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=CUSTOM_APP, manifest_digest="f" * 64,
    ) == []


async def test_workspace_republication_revokes_first_with_events(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    await _register_ws(ws_conn, [APPROVED, SENSITIVE])
    await _reconcile_ws(ws_conn)
    new_digest = await _register_ws(ws_conn, [APPROVED])
    actions = await _reconcile_ws(ws_conn)
    assert sorted((r["dataset_name"], r["action"]) for r in actions) == [
        (APPROVED, "granted"), (APPROVED, "revoked"), (SENSITIVE, "revoked"),
    ]
    assert await granted_datasets(
        ws_conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=CUSTOM_APP, manifest_digest=new_digest,
    ) == [APPROVED]
    events = await ws_conn.fetch(
        "SELECT e.event, count(*) AS n FROM public.analytic_app_dataset_grant_events e "
        "JOIN public.analytic_app_dataset_grants g ON g.id = e.grant_id "
        "WHERE g.app_name = $1 GROUP BY e.event", CUSTOM_APP,
    )
    assert {r["event"]: r["n"] for r in events} == {"granted": 3, "revoked": 2}


async def test_workspace_registration_rejects_wrong_scope(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A2)
    with pytest.raises(asyncpg.PostgresError, match="workspace app is not registered"):
        await _register_ws(ws_conn, [APPROVED])
    await _scoped(ws_conn, TENANT_B, WS_B2)
    with pytest.raises(asyncpg.PostgresError, match="workspace app is not registered"):
        await _register_ws(ws_conn, [APPROVED])


async def test_workspace_registration_cannot_shadow_a_packaged_app(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    with pytest.raises(asyncpg.PostgresError, match="belongs to a packaged app"):
        await _register_ws(
            ws_conn, [APPROVED], app=APP,
            digest=_ws_digest([APPROVED], app=APP),
        )
    with pytest.raises(asyncpg.PostgresError, match="belongs to a packaged app"):
        await _reconcile_ws(ws_conn, app=APP)


async def test_packaged_reconciler_still_rejects_the_workspace_app(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    await _register_ws(ws_conn, [APPROVED])
    assert await _reconcile(ws_conn, app=CUSTOM_APP) == []


async def test_workspace_registration_rejects_foreign_or_missing_datasets(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    for datasets in (["missing_dataset"], [APPROVED, "missing_dataset"]):
        with pytest.raises(asyncpg.PostgresError, match="not available in this workspace"):
            await _register_ws(ws_conn, datasets)


async def test_workspace_registration_rejects_html_drift(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    drifted = hashlib.sha256(b"<html>tampered</html>").hexdigest()
    with pytest.raises(asyncpg.PostgresError, match="does not match"):
        await _register_ws(ws_conn, [APPROVED], sha=drifted,
                           digest=_ws_digest([APPROVED], html="<html>tampered</html>"))


async def test_workspace_rows_are_invisible_to_foreign_workspaces(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    digest = await _register_ws(ws_conn, [APPROVED])
    await _reconcile_ws(ws_conn)
    await _scoped(ws_conn, TENANT_B, WS_B2)
    assert await ws_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_manifests WHERE app_name = $1",
        CUSTOM_APP,
    ) == 0
    assert await ws_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grants WHERE app_name = $1",
        CUSTOM_APP,
    ) == 0
    assert await granted_datasets(
        ws_conn, tenant_id=TENANT_B, workspace_id=WS_B2,
        app_name=CUSTOM_APP, manifest_digest=digest,
    ) == []


async def test_workspace_definer_functions_follow_the_owner_idiom(conn):
    rows = await conn.fetch("""
        SELECT p.proname, pg_get_userbyid(p.proowner) AS owner,
               r.rolsuper, r.rolbypassrls, p.proconfig
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_roles r ON r.oid = p.proowner
         WHERE n.nspname = 'public'
           AND p.proname IN ('register_workspace_app_manifest',
                             'reconcile_workspace_app_dataset_grants')
    """)
    assert {row["proname"] for row in rows} == {
        "register_workspace_app_manifest",
        "reconcile_workspace_app_dataset_grants",
    }
    for row in rows:
        assert row["owner"] == "omega_app_grants_owner", row["proname"]
        assert row["rolsuper"] is False and row["rolbypassrls"] is False
        config = list(row["proconfig"] or [])
        assert "search_path=pg_catalog, pg_temp" in config, row["proname"]


async def test_console_cannot_write_the_workspace_manifest_directly(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await ws_conn.execute(
            "INSERT INTO public.analytic_app_manifests "
            "(app_name, cartridge_id, manifest_digest, html_sha256, revision, "
            "source, tenant_id, workspace_id, created_by_user_id) "
            f"VALUES ('{CUSTOM_APP}', 'workspace', repeat('a',64), repeat('b',64), "
            f"'active', 'workspace_publication', '{TENANT_A}', '{WS_A1}', '1')"
        )


async def _retire_ws(conn, app=CUSTOM_APP):
    return await conn.fetchval(
        "SELECT public.retire_workspace_app_manifest($1)", app,
    )


async def test_workspace_registration_enforces_dataset_count_bounds(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    with pytest.raises(asyncpg.PostgresError, match="between 1 and 50"):
        await _register_ws(ws_conn, [], digest=_ws_digest([]))
    too_many = [f"ds_{i:02d}" for i in range(51)]
    with pytest.raises(asyncpg.PostgresError, match="between 1 and 50"):
        await _register_ws(ws_conn, too_many, digest=_ws_digest(too_many))


async def test_ledger_check_pairs_source_and_actor(conn):
    names = {
        r["conname"]: r["def"]
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'public.analytic_app_dataset_grants'::regclass "
            "AND contype = 'c'"
        )
    }
    assert "analytic_app_dataset_grants_source_actor_check" in names
    assert "analytic_app_dataset_grants_source_check" not in names
    assert "analytic_app_dataset_grants_actor_check" not in names
    await _scoped(conn, TENANT_A, WS_A1)
    await _reconcile(conn)
    digest = await _active_digest(conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO public.analytic_app_dataset_grants "
            "(tenant_id, workspace_id, app_name, cartridge_id, dataset_name, "
            " manifest_digest, grant_source, granted_by) "
            "VALUES ($1::uuid, $2::uuid, $3, 'sap_successfactors', $4, $5, "
            "'packaged_manifest', 'server:workspace_publication')",
            TENANT_A, WS_A1, APP, SENSITIVE, digest,
        )


async def test_paired_check_swap_is_reapply_safe_with_live_rows_of_both_kinds(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    await _reconcile(ws_conn)
    await _register_ws(ws_conn, [APPROVED])
    await _reconcile_ws(ws_conn)
    await ws_conn.execute("RESET ROLE")
    kinds = {
        r["grant_source"]
        for r in await ws_conn.fetch(
            "SELECT DISTINCT grant_source FROM public.analytic_app_dataset_grants"
        )
    }
    assert kinds == {"packaged_manifest", "workspace_publication"}
    for statement in (
        "ALTER TABLE public.analytic_app_dataset_grants "
        "DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_source_check",
        "ALTER TABLE public.analytic_app_dataset_grants "
        "DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_actor_check",
        "ALTER TABLE public.analytic_app_dataset_grants "
        "DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_source_actor_check",
        "ALTER TABLE public.analytic_app_dataset_grants "
        "ADD CONSTRAINT analytic_app_dataset_grants_source_actor_check CHECK ("
        "(grant_source = 'packaged_manifest' "
        " AND granted_by = 'server:packaged_manifest') OR "
        "(grant_source = 'workspace_publication' "
        " AND granted_by = 'server:workspace_publication'))",
    ):
        await ws_conn.execute(statement)
    await ws_conn.execute("SET ROLE omega_console")


async def test_retirement_revokes_first_and_frees_the_name_for_reuse(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    digest = await _register_ws(ws_conn, [APPROVED, SENSITIVE])
    await _reconcile_ws(ws_conn)

    revoked = await _retire_ws(ws_conn)
    assert revoked == 2
    assert await granted_datasets(
        ws_conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=CUSTOM_APP, manifest_digest=digest,
    ) == []
    retire_events = await ws_conn.fetchval(
        "SELECT count(*) FROM public.analytic_app_dataset_grant_events e "
        "JOIN public.analytic_app_dataset_grants g ON g.id = e.grant_id "
        "WHERE g.app_name = $1 AND e.event = 'revoked' "
        "AND e.detail = 'publication_retired'", CUSTOM_APP,
    )
    assert retire_events == 2
    # Grant history still references the dataset rows, so the manifest is
    # deactivated rather than deleted.
    assert await ws_conn.fetchval(
        "SELECT revision FROM public.analytic_app_manifests WHERE app_name = $1",
        CUSTOM_APP,
    ) == "superseded"
    # The same workspace can republish the name immediately.
    assert await _register_ws(ws_conn, [APPROVED]) == _ws_digest([APPROVED])
    assert await ws_conn.fetchval(
        "SELECT revision FROM public.analytic_app_manifests WHERE app_name = $1",
        CUSTOM_APP,
    ) == "active"
    assert await _retire_ws(ws_conn) == 0


async def test_delete_flow_frees_the_name_for_another_workspace(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    await _register_ws(ws_conn, [APPROVED])
    await _reconcile_ws(ws_conn)

    # Refinement's delete_app removes the analytic_apps row; grants cascade.
    await ws_conn.execute("RESET ROLE")
    await ws_conn.execute(
        "DELETE FROM public.analytic_apps WHERE name = $1", CUSTOM_APP,
    )
    await ws_conn.execute("SET ROLE omega_console")
    try:
        await _scoped(ws_conn, TENANT_A, WS_A1)
        assert await _retire_ws(ws_conn) == 0
        assert await ws_conn.fetchval(
            "SELECT count(*) FROM public.analytic_app_manifests WHERE app_name = $1",
            CUSTOM_APP,
        ) == 0

        await ws_conn.execute("RESET ROLE")
        await ws_conn.execute(
            "INSERT INTO public.analytic_apps "
            "(name, title, html, cartridge_id, created_by_id, tenant_id, "
            " workspace_id, scope_status) "
            "VALUES ($1, 'CU-B', $2, NULL, 1, $3::uuid, $4::uuid, 'scoped')",
            CUSTOM_APP, CUSTOM_HTML, TENANT_B, WS_B2,
        )
        await ws_conn.execute("SET ROLE omega_console")
        await _scoped(ws_conn, TENANT_B, WS_B2)
        digest_b = await _register_ws(ws_conn, [APPROVED])
        assert digest_b == _ws_digest([APPROVED])
    finally:
        await ws_conn.execute("RESET ROLE")
        await ws_conn.execute(
            "DELETE FROM public.analytic_app_manifest_datasets WHERE app_name = $1",
            CUSTOM_APP,
        )
        await ws_conn.execute(
            "DELETE FROM public.analytic_app_manifests WHERE app_name = $1",
            CUSTOM_APP,
        )
        await ws_conn.execute(
            "DELETE FROM public.analytic_apps WHERE name = $1", CUSTOM_APP,
        )
        await ws_conn.execute(
            "INSERT INTO public.analytic_apps "
            "(name, title, html, cartridge_id, created_by_id, tenant_id, "
            " workspace_id, scope_status) "
            "VALUES ($1, 'CU', $2, 'sap_successfactors', 1, $3::uuid, "
            "$4::uuid, 'scoped')",
            CUSTOM_APP, CUSTOM_HTML, TENANT_A, WS_A1,
        )
        await ws_conn.execute("SET ROLE omega_console")


async def test_name_collision_raises_an_honest_error_not_raw_23505(ws_conn):
    await _scoped(ws_conn, TENANT_A, WS_A1)
    await ws_conn.execute("RESET ROLE")
    # A manifest row from a foreign scope holds the name and is RLS-invisible.
    await ws_conn.execute(
        "INSERT INTO public.analytic_app_manifests "
        "(app_name, cartridge_id, manifest_digest, html_sha256, revision, "
        " source, tenant_id, workspace_id, created_by_user_id) "
        "VALUES ($1, 'workspace', repeat('e', 64), repeat('f', 64), 'active', "
        "'workspace_publication', $2, $3, '1')",
        CUSTOM_APP, TENANT_B, WS_B2,
    )
    await ws_conn.execute("SET ROLE omega_console")
    try:
        await _scoped(ws_conn, TENANT_A, WS_A1)
        with pytest.raises(
            asyncpg.PostgresError, match="nombre reservado por otra publicación"
        ):
            await _register_ws(ws_conn, [APPROVED])
    finally:
        await ws_conn.execute("RESET ROLE")
        await ws_conn.execute(
            "DELETE FROM public.analytic_app_manifests WHERE app_name = $1",
            CUSTOM_APP,
        )
        await ws_conn.execute("SET ROLE omega_console")


async def test_shared_visibility_never_leaks_the_app_row_across_workspaces(ws_conn):
    await ws_conn.execute("RESET ROLE")
    await ws_conn.execute(
        "UPDATE public.analytic_apps SET visibility = 'shared' WHERE name = $1",
        CUSTOM_APP,
    )
    await ws_conn.execute("SET ROLE omega_console")
    await _scoped(ws_conn, TENANT_A, WS_A1)
    assert await ws_conn.fetchval(
        "SELECT count(*) FROM public.analytic_apps WHERE name = $1", CUSTOM_APP,
    ) == 1
    await _scoped(ws_conn, TENANT_B, WS_B2)
    assert await ws_conn.fetchval(
        "SELECT count(*) FROM public.analytic_apps WHERE name = $1", CUSTOM_APP,
    ) == 0


async def test_retirement_function_follows_the_owner_idiom(conn):
    row = await conn.fetchrow("""
        SELECT pg_get_userbyid(p.proowner) AS owner, r.rolsuper,
               r.rolbypassrls, p.proconfig
          FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
         WHERE p.proname = 'retire_workspace_app_manifest'
    """)
    assert row is not None
    assert row["owner"] == "omega_app_grants_owner"
    assert row["rolsuper"] is False and row["rolbypassrls"] is False
    assert "search_path=pg_catalog, pg_temp" in list(row["proconfig"] or [])
