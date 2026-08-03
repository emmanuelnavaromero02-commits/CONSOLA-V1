from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text

from cartridges.replicon.app.services import kb_config_reconciliation
from cartridges.replicon.app.services.kb_config_reconciliation import (
    CURRENT_PACKAGE_VERSIONS,
    reconcile_packaged_kbs,
)
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)
from tests.test_operational_truth_data_kb_config import _packaged


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "infra/init/99zp_replicon_wip_materialization_v3.sql"
)


def _wait_for_migration(engine) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with engine.connect() as conn:
                if conn.execute(
                    text("SELECT 1 FROM schema_migrations WHERE filename=:filename"),
                    {"filename": MIGRATION.name},
                ).scalar():
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError("Replicon v3 migration did not finish")


def _restore_config(engine, original: dict | None) -> None:
    with engine.begin() as conn:
        if original is None:
            conn.execute(
                text(
                    """DELETE FROM kb_config WHERE cartridge_id='replicon'
                         AND kb_id='kb_wip_mensual'"""
                )
            )
            return
        assignments = ", ".join(
            f"{key}=:{key}" for key in original if key not in {"cartridge_id", "kb_id"}
        )
        conn.execute(
            text(
                f"""UPDATE kb_config SET {assignments}
                     WHERE cartridge_id=:cartridge_id AND kb_id=:kb_id"""
            ),
            original,
        )


def test_real_postgres_concurrent_reconciliation_is_single_and_preserves_disabled(
    postgres_with_real_init_schema: str,
    monkeypatch,
) -> None:
    engine = create_engine(postgres_with_real_init_schema)
    _wait_for_migration(engine)
    packaged = _packaged()["kb_wip_mensual"]
    legacy_sql = "SELECT 'known-package-legacy-concurrent'"
    legacy_digest = hashlib.sha256(legacy_sql.encode()).hexdigest()
    monkeypatch.setitem(
        kb_config_reconciliation.LEGACY_PACKAGE_SQL_DIGESTS,
        "kb_wip_mensual",
        {legacy_digest},
    )
    with engine.begin() as conn:
        original_row = (
            conn.execute(
                text(
                    """SELECT * FROM kb_config WHERE cartridge_id='replicon'
                         AND kb_id='kb_wip_mensual'"""
                )
            )
            .mappings()
            .first()
        )
        original = dict(original_row) if original_row else None
        conn.execute(
            text(
                """INSERT INTO kb_config
                   (cartridge_id, kb_id, sql, name, description, pg_table,
                    output_path, enabled)
                   VALUES ('replicon', 'kb_wip_mensual', :sql,
                           'Customer concurrent', 'Preserve me',
                           'custom_table', 'custom/path', FALSE)
                   ON CONFLICT (cartridge_id, kb_id) DO UPDATE SET
                     sql=EXCLUDED.sql, name=EXCLUDED.name,
                     description=EXCLUDED.description, pg_table=EXCLUDED.pg_table,
                     output_path=EXCLUDED.output_path, enabled=EXCLUDED.enabled"""
            ),
            {"sql": legacy_sql},
        )

    barrier = Barrier(2)

    def worker() -> dict[str, int]:
        with engine.begin() as conn:
            barrier.wait(timeout=10)
            return reconcile_packaged_kbs(conn, [packaged], "replicon")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: worker(), range(2)))
        assert sorted(result["upgraded"] for result in results) == [0, 1]
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        """SELECT sql, name, description, pg_table, output_path, enabled,
                              package_version, package_sql_digest
                         FROM kb_config WHERE cartridge_id='replicon'
                          AND kb_id='kb_wip_mensual'"""
                    )
                )
                .mappings()
                .one()
            )
        assert row["sql"] == packaged["sql"]
        assert row["name"] == "Customer concurrent"
        assert row["description"] == "Preserve me"
        assert row["pg_table"] == "custom_table"
        assert row["output_path"] == "custom/path"
        assert row["enabled"] is False
        assert row["package_version"] == CURRENT_PACKAGE_VERSIONS["kb_wip_mensual"]
        assert (
            row["package_sql_digest"]
            == hashlib.sha256(packaged["sql"].encode()).hexdigest()
        )
    finally:
        _restore_config(engine, original)
        engine.dispose()


def test_real_postgres_reconciliation_failure_rolls_back_exact_row(
    postgres_with_real_init_schema: str,
    monkeypatch,
) -> None:
    engine = create_engine(postgres_with_real_init_schema)
    _wait_for_migration(engine)
    packaged = _packaged()["kb_wip_mensual"]
    legacy_sql = "SELECT 'rollback-known-package'"
    monkeypatch.setitem(
        kb_config_reconciliation.LEGACY_PACKAGE_SQL_DIGESTS,
        "kb_wip_mensual",
        {hashlib.sha256(legacy_sql.encode()).hexdigest()},
    )
    with engine.begin() as conn:
        original_row = (
            conn.execute(
                text(
                    """SELECT * FROM kb_config WHERE cartridge_id='replicon'
                     AND kb_id='kb_wip_mensual'"""
                )
            )
            .mappings()
            .first()
        )
        original = dict(original_row) if original_row else None
        conn.execute(
            text(
                """INSERT INTO kb_config
                   (cartridge_id,kb_id,sql,name,description,pg_table,output_path,enabled)
                   VALUES ('replicon','kb_wip_mensual',:sql,'Rollback customer',
                           'Byte stable','rollback_table','rollback/path',FALSE)
                   ON CONFLICT (cartridge_id,kb_id) DO UPDATE SET
                     sql=EXCLUDED.sql,name=EXCLUDED.name,description=EXCLUDED.description,
                     pg_table=EXCLUDED.pg_table,output_path=EXCLUDED.output_path,
                     enabled=EXCLUDED.enabled"""
            ),
            {"sql": legacy_sql},
        )
    try:
        with engine.connect() as conn:
            before = dict(
                conn.execute(
                    text(
                        """SELECT * FROM kb_config WHERE cartridge_id='replicon'
                             AND kb_id='kb_wip_mensual'"""
                    )
                )
                .mappings()
                .one()
            )
        with pytest.raises(RuntimeError, match="forced rollback"):
            with engine.begin() as conn:
                assert (
                    reconcile_packaged_kbs(conn, [packaged], "replicon")["upgraded"]
                    == 1
                )
                raise RuntimeError("forced rollback")
        with engine.connect() as conn:
            after = dict(
                conn.execute(
                    text(
                        """SELECT * FROM kb_config WHERE cartridge_id='replicon'
                             AND kb_id='kb_wip_mensual'"""
                    )
                )
                .mappings()
                .one()
            )
        assert after == before
    finally:
        _restore_config(engine, original)
        engine.dispose()
