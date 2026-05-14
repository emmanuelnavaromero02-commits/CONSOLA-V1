"""Idempotent seed of cartridge_dags.source_code from on-disk .py files.

Runs once at console startup. For each row in cartridge_dags with NULL
source_code, looks for the matching .py file under known cartridge paths
and uploads its content.

This enables Studio (step 2 DAGs) to render the task graph for SAP
cartridges — same as Replicon — without operators having to run the
"infra__airflow_create_dag" tool by hand. Replicon already has
source_code populated by historical mechanisms; SAP cartridges shipped
with the .py files on disk but the column NULL, hence this seed.

Idempotency: only updates rows where source_code IS NULL. A subsequent
startup is a no-op once every row is populated.
"""
from __future__ import annotations

import logging
import pathlib

import asyncpg


logger = logging.getLogger(__name__)


# Search order for .py files. First hit wins.
# `/registry/cartridges` is the canonical location for cartridge code
# inside the console container — mounted as a volume from the repo's
# `cartridges/` directory in infra/docker-compose.yml.
_DAG_SEARCH_PATHS = [
    pathlib.Path("/registry/cartridges"),
    pathlib.Path("/app/cartridges"),       # alternate mount layout used by some deploys
    pathlib.Path("/opt/airflow/dags"),     # airflow's view of the same files
]


def _find_dag_source(cartridge_id: str, file_name: str) -> str | None:
    """Locate the .py for ``(cartridge_id, file_name)`` on disk and read it.

    Returns ``None`` if the file isn't found at any known path; callers
    are expected to log + skip rather than raise. A read error is also
    treated as "not found" so a permission issue on one cartridge can't
    block the rest of the seed.
    """
    if not file_name:
        return None

    # Two layouts we expect to find:
    #   <root>/<cartridge_id>/dags/<file_name>   ← cartridge source tree
    #   <root>/<cartridge_id>/<file_name>        ← airflow's mount layout
    for root in _DAG_SEARCH_PATHS:
        for candidate in (
            root / cartridge_id / "dags" / file_name,
            root / cartridge_id / file_name,
        ):
            if candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(
                        "[seed_dag_sources] failed reading %s: %s",
                        candidate, e,
                    )
                    # Try the next candidate — don't give up on this DAG
                    # just because one of its mirror paths is unreadable.
                    continue
    return None


async def seed_missing_dag_sources(pool: asyncpg.Pool) -> None:
    """For each ``cartridge_dags`` row with NULL ``source_code``, fill it
    from disk if a matching .py file exists. Logs counts but never raises
    — the caller wraps in try/except so console startup is never blocked
    by a seeding hiccup."""
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
