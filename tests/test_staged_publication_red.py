from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    target = ROOT / path
    assert target.is_file(), f"missing staged-publication artifact: {path}"
    return target.read_text(encoding="utf-8")


def test_gold_evidence_migration_is_versioned_and_scoped() -> None:
    sql = _text("infra/init_gold/40_staged_publication_schema.sql")
    assert "materialization_run_id" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql


def test_gold_roles_are_separated_before_publication() -> None:
    sql = _text("infra/init_gold/39_staged_publication_roles.sql")
    assert "omega_gold_owner" in sql and "NOLOGIN" in sql
    assert "omega_gold_publisher" in sql
    assert "REVOKE CREATE ON SCHEMA public" in sql


def test_gold_store_has_one_scoped_head_and_receipt() -> None:
    sql = _text("infra/init_gold/40_staged_publication_schema.sql")
    assert "dataset_publication_heads" in sql
    assert "materialization_receipts" in sql
    assert "UNIQUE" in sql


def test_gold_publish_is_a_closed_cas_function() -> None:
    sql = _text("infra/init_gold/42_staged_publication_cas.sql")
    assert "publish_materialization" in sql
    assert "expected_head" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path" in sql


def test_runtime_uses_the_staged_engine() -> None:
    main = _text("refinement/app/main.py")
    assert "StagedPublicationEngine" in main
    assert "engine = StagedPublicationEngine()" in main


def test_production_materialization_scripts_use_the_staged_engine() -> None:
    for script in (
        "materialize_inegi_context.py",
        "materialize_banxico_context.py",
        "materialize_sec_edgar_context.py",
        "materialize_successfactors_foundation.py",
    ):
        source = _text(f"refinement/scripts/{script}")
        assert "StagedPublicationEngine" in source
        assert "DuckDBEngine()" not in source


def test_runtime_has_a_distinct_publisher_connection() -> None:
    engine = _text("refinement/app/staged_publication_engine.py")
    store = _text("refinement/app/publication_store.py")
    assert "GOLD_PUBLISHER_DATABASE_URL" in store
    assert "materialization_run_id" in engine


def test_latest_materialized_object_resolves_the_head() -> None:
    reader = _text("refinement/app/publication_reader.py")
    store = _text("refinement/app/publication_store.py")
    assert "dataset_publication_heads" in store
    assert "ORDER BY created_at DESC" not in reader


def test_lineage_and_catalog_resolve_exact_published_run() -> None:
    reader = _text("refinement/app/publication_evidence.py")
    assert "materialization_run_id" in reader
    assert "published" in reader


def test_console_gold_cache_identity_contains_head_generation() -> None:
    fetcher = _text("console/app/services/intelligence/gold_fetcher.py")
    assert "head_generation" in fetcher
    assert "materialization_run_id" in fetcher


def test_object_explorer_checks_published_object_reachability() -> None:
    router = _text("console/app/routers/v1/data.py")
    assert "published_object" in router


def test_mcp_minio_checks_published_object_reachability() -> None:
    tool = _text("mcp-infra/app/tools/minio.py")
    assert "published_object" in tool


def test_control_room_and_data_platform_caches_use_publication_epoch() -> None:
    control_room = _text("console/app/services/control_room/authorization_cache.py")
    data_platform = _text("console/app/domains/data_platform/scoped_reads.py")
    assert "publication_epoch" in control_room
    assert "publication_epoch" in data_platform
    heads = _text("console/app/services/publication_heads.py")
    assert "legacy-no-publication-heads" not in heads


def test_superset_result_cache_is_fail_closed() -> None:
    config = _text("infra/terraform/deploy/superset_config/superset_config.py")
    assert '"CACHE_TYPE": "NullCache"' in config
    assert "RESULTS_BACKEND = None" in config


def test_reader_role_has_no_direct_public_dml() -> None:
    roles = _text("infra/init_gold/39_staged_publication_roles.sql")
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in roles
    assert "omega_refinement_gold" in roles


def test_publication_files_respect_size_limit() -> None:
    paths = [
        "refinement/app/staged_publication_engine.py",
        "refinement/app/publication_reader.py",
        "refinement/app/publication_evidence.py",
        "refinement/app/publication_objects.py",
        "console/app/services/publication_heads.py",
        "mcp-infra/app/publication_heads.py",
    ]
    for path in paths:
        assert len(_text(path).splitlines()) <= 300, path
