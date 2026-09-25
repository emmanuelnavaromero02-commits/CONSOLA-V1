from __future__ import annotations

import logging
import pathlib

import asyncpg


logger = logging.getLogger(__name__)


_DAG_SEARCH_PATHS = [
    pathlib.Path("/registry/cartridges"),
    pathlib.Path("/app/cartridges"),
    pathlib.Path("/opt/airflow/dags"),
]


def _find_dag_source(cartridge_id: str, file_name: str) -> str | None:
    if not file_name:
        return None

    for root in _DAG_SEARCH_PATHS:
        for candidate in (
            root / cartridge_id / "dags" / file_name,
            root / cartridge_id / file_name,
            root / file_name,
        ):
            if candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(
                        "[seed_dag_sources] failed reading %s: %s",
                        candidate, e,
                    )
                    continue
    return None


async def seed_missing_dag_sources(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT cartridge_id, dag_id, file FROM cartridge_dags "
            "WHERE source_code IS NULL AND file IS NOT NULL"
        )
        if not rows:
            logger.info("[seed_dag_sources] no rows need backfill")
            return

        updated = 0
        skipped = 0
        for r in rows:
            cid = r["cartridge_id"]
            fname = r["file"]
            did = r["dag_id"]
            src = _find_dag_source(cid, fname)
            if src is None:
                logger.info(
                    "[seed_dag_sources] no on-disk source for %s/%s (skipping)",
                    cid, fname,
                )
                skipped += 1
                continue
            await conn.execute(
                "UPDATE cartridge_dags SET source_code = $1, updated_at = NOW() "
                "WHERE cartridge_id = $2 AND dag_id = $3 AND source_code IS NULL",
                src, cid, did,
            )
            updated += 1
            logger.info(
                "[seed_dag_sources] populated %s/%s (%d bytes)",
                cid, did, len(src),
            )

        logger.info(
            "[seed_dag_sources] complete: %d updated, %d skipped",
            updated, skipped,
        )
