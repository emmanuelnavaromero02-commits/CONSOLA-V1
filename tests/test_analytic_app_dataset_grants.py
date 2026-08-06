"""The grant ledger is the only thing that authorises a published app's read.

These are C1's three self-authorisation canaries, run against a real
PostgreSQL with the real migration applied:

A. the app's HTML mentions a dataset its reviewed manifest does not list;
B. runtime metadata (``analytic_apps.datasets_used``) declares one;
C. a SuccessFactors app claims a dataset belonging to another cartridge.

None of them may reach the ledger, the wrapper's allowlist, or the backend.
Alongside them: the positive read, immediate revocation, digest drift,
cross-scope isolation and a user-created app with no approval.

Set ``OMEGA_TEST_GRANTS_DSN`` to run. Skipped otherwise — this asserts real
database behaviour (RLS, FORCE RLS, triggers, SECURITY DEFINER), none of which
a mock can stand in for.
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
DIGEST = "c" * 64
STALE = "d" * 64


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
        await connection.execute("DELETE FROM analytic_app_dataset_grants")
        yield connection
    finally:
        await connection.close()


async def _grant(conn, datasets, *, digest=DIGEST, app=APP, cartridge="sap_successfactors"):
    return await conn.fetch(
        "SELECT * FROM reconcile_analytic_app_dataset_grants($1,$2,$3::text[],$4,$5)",
        app, cartridge, list(datasets), digest, "server:test",
    )


# --- canary A: the HTML says so -------------------------------------------


async def test_html_mention_does_not_authorise(conn):
    """The app's own markup is not an approval, however plainly it asks."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _grant(conn, [APPROVED])

    html = f'<script>fetch("/api/data/{SENSITIVE}")</script>'
    assert SENSITIVE in html  # the app really is asking

    allowed = await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=DIGEST,
    )
    assert SENSITIVE not in allowed
    assert allowed == [APPROVED]
    assert not await has_grant(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1, app_name=APP,
        manifest_digest=DIGEST, dataset=SENSITIVE,
    )


# --- canary B: runtime metadata says so ------------------------------------


async def test_runtime_metadata_does_not_authorise(conn):
    """``analytic_apps.datasets_used`` is stored beside the app and moves with
    it, so it is the app talking. It informs nothing about permission."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _grant(conn, [APPROVED])
    await conn.execute(
        "UPDATE analytic_apps SET datasets_used = $1::text[] WHERE name = $2",
        [APPROVED, SENSITIVE], APP,
    )
    allowed = await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=DIGEST,
    )
    assert allowed == [APPROVED]


# --- canary C: another cartridge's dataset ---------------------------------


async def test_cross_cartridge_grant_is_refused_by_the_database(conn):
    """Refused in the schema, not only in the service: a bug in the caller
    cannot produce a row that crosses cartridges."""
    await _scoped(conn, TENANT_A, WS_A1)
    with pytest.raises(asyncpg.PostgresError, match="cartridge"):
        await _grant(conn, [OTHER_CARTRIDGE])
    assert await conn.fetchval(
        "SELECT count(*) FROM analytic_app_dataset_grants "
        "WHERE dataset_name = $1", OTHER_CARTRIDGE,
    ) == 0


# --- the positive read ------------------------------------------------------


async def test_a_reviewed_grant_authorises_exactly_its_dataset(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    actions = await _grant(conn, [APPROVED])
    assert [(r["dataset_name"], r["action"]) for r in actions] == [(APPROVED, "granted")]
    assert await has_grant(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1, app_name=APP,
        manifest_digest=DIGEST, dataset=APPROVED,
    )


# --- revocation -------------------------------------------------------------


async def test_revocation_takes_effect_on_the_next_read(conn):
    """No cache, no grace: the next lookup is already refused."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _grant(conn, [APPROVED])
    await conn.fetchval(
        "SELECT revoke_analytic_app_dataset_grant($1,$2,$3,$4)",
        APP, APPROVED, "server:test", "explicit_revocation",
    )
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=DIGEST,
    ) == []


# --- drift ------------------------------------------------------------------


async def test_a_new_digest_strands_every_earlier_grant(conn):
    """Editing the app moves the digest, and the old grants stop serving.
    There is no path by which changing an app re-approves it."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _grant(conn, [APPROVED], digest=DIGEST)
    await _grant(conn, [APPROVED], digest=STALE)
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=DIGEST,
    ) == []
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=STALE,
    ) == [APPROVED]


async def test_an_unknown_digest_authorises_nothing(conn):
    await _scoped(conn, TENANT_A, WS_A1)
    await _grant(conn, [APPROVED])
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest="f" * 64,
    ) == []
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=APP, manifest_digest=None,
    ) == []


# --- scope ------------------------------------------------------------------


async def test_a_grant_in_one_workspace_does_not_serve_another(conn):
    """The same dataset name exists in all three scopes, so this is really
    testing scope and not name matching."""
    await _scoped(conn, TENANT_A, WS_A1)
    await _grant(conn, [APPROVED])

    for tenant, workspace in ((TENANT_A, WS_A2), (TENANT_B, WS_B2)):
        await _scoped(conn, tenant, workspace)
        assert await granted_datasets(
            conn, tenant_id=tenant, workspace_id=workspace,
            app_name=APP, manifest_digest=DIGEST,
        ) == []


async def test_a_custom_app_gets_nothing_automatically(conn):
    """A user-created app has no reviewed manifest, so it has no digest, so it
    has no grants — and fails closed rather than falling back."""
    await _scoped(conn, TENANT_A, WS_A1)
    assert served_digest(CUSTOM_APP, "<html></html>") is None
    assert await granted_datasets(
        conn, tenant_id=TENANT_A, workspace_id=WS_A1,
        app_name=CUSTOM_APP, manifest_digest=None,
    ) == []


# --- diagnostics stay diagnostics ------------------------------------------


async def test_drift_report_describes_but_never_widens():
    report = drift_report(
        granted=[APPROVED],
        referenced=[APPROVED, SENSITIVE],
        manifest_datasets=[APPROVED],
        served="a" * 64,
        packaged="b" * 64,
    )
    assert report["requested_but_not_granted"] == [SENSITIVE]
    assert report["manifest_drift"] is True
    # It reports. It returns no allowlist of its own.
    assert "allowed" not in report


async def test_packaged_digest_is_deterministic_and_covers_the_html():
    manifests = load_packaged_manifests()
    assert len(manifests) == 18
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
