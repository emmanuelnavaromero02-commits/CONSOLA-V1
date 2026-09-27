from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from omega_lakehouse.storage_scope import (
    SCOPE_INDEX_BY_ROOT,
    ReaderUriError,
    canonical_storage_key,
    has_exact_storage_scope,
    parse_required_scope,
    require_scoped_reader_uri,
    scoped_storage_allowed,
    scoped_storage_key,
)

REPO = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
SCOPE = f"tenant_id={TENANT}/workspace_id={WORKSPACE}"
FOREIGN = f"tenant_id={OTHER}/workspace_id={OTHER}"

# Keys exactly as the writers build them (see the referenced writer for each).
WRITER_LAYOUTS = [
    # cartridges/*/app/services/parquet_service.py and sap_b1 bronze_object_name
    f"raw/sap_b1/OINV/{SCOPE}/load_date=2026-09-26/batch_id=r1/OINV.parquet",
    f"raw/sap_successfactors/EmpEmployment/{SCOPE}/load_date=2026-09-26/batch_id=r1/EmpEmployment.parquet",
    # sap_b1 heartbeat_object_name
    f"raw/sap_b1/_agent/{SCOPE}/heartbeat.json",
    # market cartridges extraction_service final prefix
    f"raw/banxico/SF43718/{SCOPE}/load_date=2026-09-26/batch_id=r1/part-0.parquet",
    # replicon shared raw roots after _scope_kb_sql
    f"raw/fx_rates/mxn_usd/{SCOPE}/fx_rates.parquet",
    # refinement duckdb_engine silver/gold dataset objects
    f"silver/sap_successfactors/employees/{SCOPE}/data.parquet",
    f"gold/sap_b1/sales_by_month/{SCOPE}/data.parquet",
    # cartridge write_kb_parquet with a configured output_path
    f"knowledge_bits/hubspot/open_pipeline_by_owner/{SCOPE}/load_date=2026-09-26/batch_id=r1/kb.parquet",
    f"silver/sap_b1/kb_stock_on_hand/{SCOPE}/load_date=2026-09-26/batch_id=r1/kb.parquet",
    # airflow/dags/file_ingest.py uploads prefix
    f"uploads/replicon/{SCOPE}/in/report.xlsx",
    # listing prefixes carry a trailing slash
    f"raw/sap_b1/OINV/{SCOPE}/",
    f"uploads/replicon/{SCOPE}/",
]


def test_scope_index_matches_the_writer_layouts():
    assert SCOPE_INDEX_BY_ROOT == {
        "raw": 3,
        "silver": 3,
        "gold": 3,
        "knowledge_bits": 3,
        "uploads": 2,
    }


@pytest.mark.parametrize("key", WRITER_LAYOUTS)
def test_real_writer_layouts_keep_their_exact_scope(key):
    assert has_exact_storage_scope(key, TENANT, WORKSPACE)
    assert not has_exact_storage_scope(key, OTHER, WORKSPACE)
    assert not has_exact_storage_scope(key, TENANT, OTHER)


@pytest.mark.parametrize(
    "key",
    [
        f"raw/sap_b1/OINV/{SCOPE}/load_date=*/batch_id=*/*.parquet",
        f"raw/sap_b1/OINV/{SCOPE}/**/*.parquet",
        f"silver/sap_b1/kb_sales/{SCOPE}/*.parquet",
        f"uploads/replicon/{SCOPE}/in/*.csv",
    ],
)
def test_globs_after_the_scope_are_allowed(key):
    assert has_exact_storage_scope(key, TENANT, WORKSPACE)


@pytest.mark.parametrize(
    "key",
    [
        # misplaced markers (scope must sit at the root's index)
        f"raw/sap_b1/{SCOPE}/OINV/x.parquet",
        f"raw/sap_b1/OINV/sub/{SCOPE}/x.parquet",
        f"uploads/{SCOPE}/x.csv",
        f"uploads/replicon/in/{SCOPE}/x.csv",
        f"raw/banxico/_staging/SF43718/{SCOPE}/x.parquet",
        # a second pair anywhere: first-match and last-match readers disagree
        f"raw/sap_b1/OINV/{SCOPE}/{FOREIGN}/x.parquet",
        f"raw/sap_b1/OINV/{FOREIGN}/{SCOPE}/x.parquet",
        f"raw/sap_b1/OINV/{SCOPE}/tenant_id={TENANT}/x.parquet",
        f"raw/sap_b1/OINV/{SCOPE}/workspace_id={WORKSPACE}/x.parquet",
        # swapped or partial pair
        f"raw/sap_b1/OINV/workspace_id={WORKSPACE}/tenant_id={TENANT}/x.parquet",
        f"raw/sap_b1/OINV/tenant_id={TENANT}",
        f"raw/sap_b1/OINV/tenant_id={TENANT}/x.parquet",
        # globs before the scope could reach other tenants' directories
        f"raw/sap_b1/*/{SCOPE}/x.parquet",
        f"raw/*/OINV/{SCOPE}/x.parquet",
        f"raw/sap_b1/OIN?/{SCOPE}/x.parquet",
        f"raw/sap_b1/OINV/tenant_id=*/workspace_id=*/x.parquet",
        f"raw/sap_b1/OINV/tenant_id={TENANT}/workspace_id=*/x.parquet",
        # value prefixes are not the value
        f"raw/sap_b1/OINV/tenant_id={TENANT}x/workspace_id={WORKSPACE}/x.parquet",
        f"raw/sap_b1/OINV/tenant_id={TENANT}/workspace_id={WORKSPACE}0/x.parquet",
        # non-canonical spellings are never decoded
        f"raw/sap_b1/OINV/{SCOPE}/%2e%2e/x.parquet",
        f"raw/sap_b1/OINV/tenant_id%3D{TENANT}/workspace_id={WORKSPACE}/x.parquet",
        f"raw\\sap_b1\\OINV\\{SCOPE}\\x.parquet",
        f"raw/sap_b1//OINV/{SCOPE}/x.parquet",
        f"raw/sap_b1/./OINV/{SCOPE}/x.parquet",
        f"raw/sap_b1/OINV/{SCOPE}/../x.parquet",
        f"/raw/sap_b1/OINV/{SCOPE}/x.parquet",
        f"raw/sap_b1/ＯINV/{SCOPE}/x.parquet",
        f"raw/sap_b1/OINV/{SCOPE}/x\x00.parquet",
        f"raw/sap_b1/OINV/{SCOPE}/x .parquet",
        # unknown roots have no anchored scope
        f"cartridges/sap_b1/OINV/{SCOPE}/x.parquet",
        f"inbound/sap_b1/OINV/{SCOPE}/x.parquet",
        f"bronze/sap_b1/OINV/{SCOPE}/x.parquet",
        "",
    ],
)
def test_hostile_or_misplaced_scopes_are_rejected(key):
    assert not has_exact_storage_scope(key, TENANT, WORKSPACE)


@pytest.mark.parametrize("tenant,workspace", [("", WORKSPACE), (TENANT, ""), (None, None)])
def test_missing_expected_scope_never_matches(tenant, workspace):
    assert not has_exact_storage_scope(WRITER_LAYOUTS[0], tenant, workspace)


@pytest.mark.parametrize(
    "value",
    [
        "raw/x/%2e%2e/y",
        "raw/x/a%20b",
        "raw\\x\\y",
        "raw//x",
        "/raw/x",
        "raw/./x",
        "raw/../x",
        "raw/x/ｙ",
        "raw/x/y\x7f",
        "raw/x/y\tz",
        "raw/x/y z",
        "raw/x/*",
        "raw/x/y?",
        "",
        "/",
    ],
)
def test_canonical_storage_key_rejects_without_decoding(value):
    with pytest.raises(ValueError):
        canonical_storage_key(value)


def test_canonical_storage_key_accepts_plain_keys_and_scoped_globs():
    assert canonical_storage_key(" raw/x/y.parquet ") == "raw/x/y.parquet"
    assert canonical_storage_key("raw/x/y/") == "raw/x/y"
    assert canonical_storage_key("raw/x/**/*.parquet", allow_glob=True) == "raw/x/**/*.parquet"
    with pytest.raises(ValueError):
        canonical_storage_key("raw/x/**/*.parquet")


def test_scoped_storage_key_checks_scope_cartridge_and_bucket():
    ctx = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_cartridges": ["sap_b1"],
        "allowed_buckets": ["lakehouse"],
    }
    key = WRITER_LAYOUTS[0]
    assert scoped_storage_key(key, ctx, bucket="lakehouse") == key
    assert scoped_storage_allowed(key, ctx, bucket="lakehouse")
    with pytest.raises(PermissionError):
        scoped_storage_key(key, ctx, bucket="other")
    with pytest.raises(PermissionError):
        scoped_storage_key(key.replace("raw/sap_b1", "raw/hubspot"), ctx)
    with pytest.raises(PermissionError):
        scoped_storage_key(f"raw/sap_b1/OINV/{FOREIGN}/x.parquet", ctx)
    with pytest.raises(PermissionError):
        scoped_storage_key(f"raw/sap_b1/{SCOPE}/OINV/x.parquet", ctx)
    with pytest.raises(PermissionError):
        scoped_storage_key(key, {"allowed_cartridges": ["*"]})
    with pytest.raises(ValueError):
        scoped_storage_key(f"knowledge_bits/sap_b1/kb/{SCOPE}/x.parquet", ctx)
    with pytest.raises(ValueError):
        scoped_storage_key(key.replace("OINV", "%4FINV"), ctx)
    assert not scoped_storage_allowed(f"raw/sap_b1/OINV/{SCOPE}/{FOREIGN}/x", ctx)
    glob = f"raw/sap_b1/OINV/{SCOPE}/**/*.parquet"
    assert scoped_storage_key(glob, ctx, allow_glob=True) == glob
    with pytest.raises(PermissionError):
        scoped_storage_key(f"raw/sap_b1/*/{SCOPE}/x.parquet", ctx, allow_glob=True)


def test_parse_required_scope_accepts_only_the_exact_marker():
    assert parse_required_scope(f"{SCOPE}/") == (TENANT, WORKSPACE)
    assert parse_required_scope(SCOPE) == (TENANT, WORKSPACE)
    for bad in (
        "",
        None,
        f"/{SCOPE}/",
        f"tenant_id={TENANT}/",
        f"workspace_id={WORKSPACE}/tenant_id={TENANT}/",
        f"{SCOPE}/extra/",
        f"tenant_id={TENANT}/workspace_id=a/b/",
        f"tenant_id=/workspace_id={WORKSPACE}/",
        f"tenant_id=a%2fb/workspace_id={WORKSPACE}/",
    ):
        with pytest.raises(ValueError):
            parse_required_scope(bad)


PREFIXES = ("s3://lakehouse/raw/sap_b1/", "s3://lakehouse/silver/sap_b1/")


@pytest.mark.parametrize(
    "uri,reason",
    [
        ("s3://lakehouse/raw/sap_b1/OINV/x.parquet?s3_region=x", "query"),
        ("s3://lakehouse/raw/sap_b1/OINV/x.parquet#frag", "query"),
        ("file:///etc/passwd", "scheme"),
        ("/etc/passwd", "scheme"),
        ("../x.parquet", "scheme"),
        ("~/x.parquet", "scheme"),
        ("https://example.com/x.parquet", "scheme"),
        ("gs://lakehouse/raw/sap_b1/OINV/x.parquet", "prefix"),
        ("S3://lakehouse/raw/sap_b1/OINV/x.parquet", "prefix"),
        ("s3://other/raw/sap_b1/OINV/x.parquet", "prefix"),
        ("s3://lakehouse/raw/hubspot/deals/x.parquet", "prefix"),
        ("s3://lakehouse/raw/sap_b1/../hubspot/x.parquet", "traversal"),
        ("s3://lakehouse/raw/sap_b1/%2e%2e/hubspot/x.parquet", "canonical"),
        ("s3://lakehouse/raw/sap_b1/OINV\\..\\x.parquet", "canonical"),
        ("s3://lakehouse/raw/sap_b1//OINV/x.parquet", "canonical"),
        ("s3://lakehouse/raw/sap_b1/./OINV/x.parquet", "canonical"),
        ("s3://lakehouse/raw/sap_b1/ＯINV/x.parquet", "canonical"),
        ("s3://lakehouse/raw/sap_b1/OINV/x\x01.parquet", "canonical"),
        (" s3://lakehouse/raw/sap_b1/OINV/x.parquet", "canonical"),
        ("s3://lakehouse/raw/sap_b1/OINV/", "canonical"),
        ("s3://lakehouse", "canonical"),
    ],
)
def test_reader_uri_reports_the_failed_check(uri, reason):
    with pytest.raises(ReaderUriError) as caught:
        require_scoped_reader_uri(uri, prefixes=PREFIXES, tenant=TENANT, workspace=WORKSPACE)
    assert caught.value.reason == reason


def test_reader_uri_scope_is_exact_and_template_mode_skips_it():
    scoped = f"s3://lakehouse/raw/sap_b1/OINV/{SCOPE}/load_date=*/batch_id=*/*.parquet"
    unscoped = "s3://lakehouse/raw/sap_b1/OINV/**/*.parquet"
    assert require_scoped_reader_uri(scoped, prefixes=PREFIXES, tenant=TENANT, workspace=WORKSPACE) == scoped
    assert require_scoped_reader_uri(unscoped, prefixes=PREFIXES, tenant=None, workspace=None) == unscoped
    for uri, tenant, workspace in (
        (unscoped, TENANT, WORKSPACE),
        (scoped, OTHER, WORKSPACE),
        (scoped, "", ""),
        (scoped, TENANT, None),
        (f"s3://lakehouse/raw/sap_b1/{SCOPE}/OINV/x.parquet", TENANT, WORKSPACE),
        (f"s3://lakehouse/raw/sap_b1/OINV/{FOREIGN}/{SCOPE}/x.parquet", TENANT, WORKSPACE),
        (f"s3://lakehouse/raw/sap_b1/*/{SCOPE}/x.parquet", TENANT, WORKSPACE),
    ):
        with pytest.raises(ReaderUriError) as caught:
            require_scoped_reader_uri(uri, prefixes=PREFIXES, tenant=tenant, workspace=workspace)
        assert caught.value.reason == "scope"


def test_old_private_scope_helpers_are_gone_and_callers_use_the_shared_module():
    assert not (REPO / "mcp-infra/app/storage_scope.py").exists()
    assert not (REPO / "refinement/app/storage_scope_policy.py").exists()
    mcp_main = (REPO / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    policy = (REPO / "mcp-infra/app/sql_reader_policy.py").read_text(encoding="utf-8")
    heads = (REPO / "mcp-infra/app/publication_heads.py").read_text(encoding="utf-8")
    refinement_main = (REPO / "refinement/app/main.py").read_text(encoding="utf-8")
    engine = (REPO / "refinement/app/duckdb_engine.py").read_text(encoding="utf-8")
    assert "def _storage_scope_markers" not in mcp_main
    assert "def _has_foreign_storage_scope" not in mcp_main
    assert "return not has_exact_storage_scope(key, tenant_id, workspace_id)" in mcp_main
    assert "_SCOPE_RE" not in policy
    assert "has_exact_storage_scope(key, tenant_id, workspace_id)" in policy
    for source in (mcp_main, policy, heads, refinement_main, engine):
        assert "from omega_lakehouse.storage_scope import" in source
    for cartridge in ("hubspot", "replicon", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors"):
        service = (REPO / f"cartridges/{cartridge}/app/services/duckdb_service.py").read_text(encoding="utf-8")
        assert "def _path_has_scope" not in service
        assert "has_exact_storage_scope(output_path, tenant, workspace)" in service


@pytest.fixture
def reader_policy():
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    sys.path.insert(0, str(REPO / "mcp-infra"))
    try:
        yield importlib.import_module("app.sql_reader_policy")
    finally:
        sys.path.remove(str(REPO / "mcp-infra"))


def _policy_sql(path: str) -> str:
    return f"SELECT * FROM read_parquet('{path}')"


@pytest.mark.parametrize(
    "path",
    [
        f"s3://lakehouse/raw/replicon/TimeEntry/{SCOPE}/**/*.parquet",
        f"s3://lakehouse/silver/replicon/timeentry_latest/{SCOPE}/data.parquet",
        f"s3://lakehouse/raw/fx_rates/mxn_usd/{SCOPE}/fx_rates.parquet",
        "s3://{bucket}/raw/replicon/TimeEntry/**/*.parquet",
    ],
)
def test_mcp_reader_policy_accepts_anchored_and_server_resolved_reads(reader_policy, path):
    reads = reader_policy.validate_cartridge_reader_query(
        _policy_sql(path),
        cartridge_id="replicon",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        expected_bucket="lakehouse",
        shared_roots=reader_policy.SHARED_RAW_ROOTS_BY_CARTRIDGE["replicon"],
        allow_server_resolution=True,
    )
    assert [read.path for read in reads] == [path]


@pytest.mark.parametrize(
    "path",
    [
        f"s3://lakehouse/raw/replicon/{SCOPE}/TimeEntry/x.parquet",
        f"s3://lakehouse/raw/replicon/TimeEntry/{FOREIGN}/{SCOPE}/x.parquet",
        f"s3://lakehouse/raw/replicon/TimeEntry/{SCOPE}/{FOREIGN}/x.parquet",
        f"s3://lakehouse/raw/replicon/*/{SCOPE}/x.parquet",
        f"s3://lakehouse/raw/replicon/TimeEntry/workspace_id={WORKSPACE}/x.parquet",
        f"s3://lakehouse/raw/replicon/TimeEntry/{FOREIGN}/x.parquet",
    ],
)
def test_mcp_reader_policy_denies_misplaced_or_foreign_markers(reader_policy, path):
    with pytest.raises(reader_policy.ReaderPolicyError):
        reader_policy.validate_cartridge_reader_query(
            _policy_sql(path),
            cartridge_id="replicon",
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            expected_bucket="lakehouse",
            shared_roots=reader_policy.SHARED_RAW_ROOTS_BY_CARTRIDGE["replicon"],
            allow_server_resolution=True,
        )
