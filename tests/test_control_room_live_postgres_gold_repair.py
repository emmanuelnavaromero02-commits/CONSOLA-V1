from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAME = "37_sap_successfactors_talent_operational_truth_repair.sql"
MIGRATION = ROOT / "infra/init_gold" / MIGRATION_NAME
RUNNER = ROOT / "scripts/apply_db_migrations.sh"

LEGACY_FIXTURE = """
CREATE TABLE schema_migrations (
 id BIGSERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE,
 applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), checksum TEXT
);
INSERT INTO schema_migrations(filename) VALUES ('gold/36_legacy_fixture.sql');
CREATE TABLE gold_sap_successfactors_talent_benchmark_internal (
 approved BOOLEAN, approved_by TEXT, approved_at TIMESTAMPTZ,
 approval_source TEXT, benchmark_version TEXT, blockers TEXT
);
INSERT INTO gold_sap_successfactors_talent_benchmark_internal
VALUES (TRUE, 'legacy-bot', NOW(), 'legacy', 'approved', '[]');
CREATE TABLE gold_sap_successfactors_talent_readiness (
 source_mode TEXT, readiness_status TEXT, readiness_label TEXT,
 benchmark_raw_score DOUBLE PRECISION, benchmark_score DOUBLE PRECISION,
 readiness_score DOUBLE PRECISION, confidence DOUBLE PRECISION
);
INSERT INTO gold_sap_successfactors_talent_readiness
VALUES ('benchmark_internal', 'ready', 'Listo', 70, 70, 70, .7);
CREATE TABLE gold_sap_successfactors_talent_9box (
 source_mode TEXT, box_status TEXT,
 benchmark_performance_proxy DOUBLE PRECISION,
 benchmark_potential_proxy DOUBLE PRECISION,
 performance_proxy_score DOUBLE PRECISION,
 potential_proxy_score DOUBLE PRECISION,
 performance_band TEXT, potential_band TEXT
);
INSERT INTO gold_sap_successfactors_talent_9box
VALUES ('benchmark_internal', 'ready', 70, 70, 70, 70, 'high', 'high');
"""

COMPOSE = """
services:
  postgres:
    image: postgres:15.18
    environment:
      POSTGRES_DB: modecissions
      POSTGRES_HOST_AUTH_METHOD: trust
    volumes:
      - ./init:/docker-entrypoint-initdb.d
  postgres_gold:
    image: postgres:15.18
    command: ["postgres", "-p", "5433"]
    environment:
      POSTGRES_DB: modecissions_gold
      POSTGRES_HOST_AUTH_METHOD: trust
    volumes:
      - ./init_gold:/docker-entrypoint-initdb.d
"""


def _run(root: Path, *args: str, check: bool = True):
    result = subprocess.run(
        ["docker", "compose", "-f", str(root / "infra/docker-compose.yml"), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(f"compose failed: {result.stdout}\n{result.stderr}")
    return result


def _psql(root: Path, service: str, database: str, sql: str, *, check: bool = True):
    port = "5433" if service == "postgres_gold" else "5432"
    return _run(
        root,
        "exec",
        "-T",
        service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-p",
        port,
        "-d",
        database,
        "-Atc",
        sql,
        check=check,
    )


def _prepare(tmp_path: Path, *, include_repair: bool) -> Path:
    root = tmp_path / f"gold-{uuid.uuid4().hex[:8]}"
    for relative in ("infra/init", "infra/init_gold", "scripts"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "infra/docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (root / "infra/init/00_noop.sql").write_text("SELECT 1;", encoding="utf-8")
    (root / "infra/init_gold/00_noop.sql").write_text("SELECT 1;", encoding="utf-8")
    (root / "infra/init_gold/36_legacy_fixture.sql").write_text(
        LEGACY_FIXTURE, encoding="utf-8"
    )
    if include_repair:
        shutil.copy2(MIGRATION, root / "infra/init_gold" / MIGRATION_NAME)
    shutil.copy2(RUNNER, root / "scripts/apply_db_migrations.sh")
    return root


def _wait(root: Path, *, include_repair: bool) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        filename = (
            f"gold/{MIGRATION_NAME}" if include_repair else "gold/36_legacy_fixture.sql"
        )
        ready = _psql(
            root,
            "postgres_gold",
            "modecissions_gold",
            "SELECT current_database() FROM schema_migrations "
            f"WHERE filename='{filename}'",
            check=False,
        )
        if ready.returncode == 0 and ready.stdout.strip() == "modecissions_gold":
            return
        time.sleep(1)
    raise AssertionError("temporary Gold database did not become ready")


def _assert_repaired(root: Path) -> None:
    row = _psql(
        root,
        "postgres_gold",
        "modecissions_gold",
        """SELECT approved, approved_by IS NULL, approval_status,
                  (SELECT readiness_status FROM gold_sap_successfactors_talent_readiness),
                  (SELECT readiness_score IS NULL FROM gold_sap_successfactors_talent_readiness),
                  (SELECT box_status FROM gold_sap_successfactors_talent_9box),
                  (SELECT performance_proxy_score IS NULL FROM gold_sap_successfactors_talent_9box)
             FROM gold_sap_successfactors_talent_benchmark_internal""",
    ).stdout.strip()
    assert row == "f|t|unreviewed|insufficient_data|t|blocked|t"
    registered = _psql(
        root,
        "postgres_gold",
        "modecissions_gold",
        f"SELECT COUNT(*) FROM schema_migrations WHERE filename='gold/{MIGRATION_NAME}'",
    ).stdout.strip()
    assert registered == "1"
    main = _psql(
        root,
        "postgres",
        "modecissions",
        "SELECT current_database(), to_regclass('gold_sap_successfactors_talent_readiness')",
    ).stdout.strip()
    assert main == "modecissions|"


@pytest.mark.parametrize("mode", ["fresh", "upgrade"])
def test_real_main_and_gold_runner_repair_is_complete_and_idempotent(
    tmp_path: Path, mode: str
) -> None:
    assert subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    root = _prepare(tmp_path, include_repair=mode == "fresh")
    try:
        _run(root, "up", "-d", "postgres", "postgres_gold")
        _wait(root, include_repair=mode == "fresh")
        if mode == "upgrade":
            shutil.copy2(MIGRATION, root / "infra/init_gold" / MIGRATION_NAME)
            subprocess.run(
                ["bash", str(root / "scripts/apply_db_migrations.sh")],
                cwd=root,
                check=True,
            )
        _assert_repaired(root)
        subprocess.run(
            ["bash", str(root / "scripts/apply_db_migrations.sh")],
            cwd=root,
            check=True,
        )
        before = _psql(
            root,
            "postgres_gold",
            "modecissions_gold",
            "SELECT COUNT(*), MIN(applied_at)=MAX(applied_at) FROM schema_migrations",
        ).stdout.strip()
        subprocess.run(
            ["bash", str(root / "scripts/apply_db_migrations.sh")],
            cwd=root,
            check=True,
        )
        after = _psql(
            root,
            "postgres_gold",
            "modecissions_gold",
            "SELECT COUNT(*), MIN(applied_at)=MAX(applied_at) FROM schema_migrations",
        ).stdout.strip()
        assert after == before
        if mode == "upgrade":
            _psql(
                root,
                "postgres_gold",
                "modecissions_gold",
                f"""UPDATE gold_sap_successfactors_talent_benchmark_internal
                       SET approved=TRUE, approved_by='1', approved_at=NOW();
                    ALTER TABLE gold_sap_successfactors_talent_readiness
                       DROP COLUMN readiness_status;
                    DELETE FROM schema_migrations
                     WHERE filename='gold/{MIGRATION_NAME}';""",
            )
            failed = subprocess.run(
                ["bash", str(root / "scripts/apply_db_migrations.sh")],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            assert failed.returncode != 0
            rolled_back = _psql(
                root,
                "postgres_gold",
                "modecissions_gold",
                f"""SELECT approved,
                           (SELECT COUNT(*) FROM schema_migrations
                             WHERE filename='gold/{MIGRATION_NAME}')
                      FROM gold_sap_successfactors_talent_benchmark_internal""",
            ).stdout.strip()
            assert rolled_back == "t|0"
    finally:
        _run(root, "down", "-v", check=False)
