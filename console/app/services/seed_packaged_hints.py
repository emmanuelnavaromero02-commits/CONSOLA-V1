from __future__ import annotations

import logging
import pathlib

import asyncpg


logger = logging.getLogger(__name__)

_REGISTRY = pathlib.Path("/registry/cartridges")


async def seed_packaged_hints(pool: asyncpg.Pool) -> None:
    if not _REGISTRY.exists():
        logger.info("[seed_packaged_hints] no cartridge registry found")
        return

    async with pool.acquire() as conn:
        updated = 0
        for cartridge_dir in sorted(path for path in _REGISTRY.iterdir() if path.is_dir()):
            hints_path = cartridge_dir / "hints" / "assistant.md"
            if not hints_path.exists():
                continue
            try:
                hints = hints_path.read_text(encoding="utf-8").strip()
            except Exception as exc:
                logger.warning("[seed_packaged_hints] skip %s: %s", hints_path, exc)
                continue
            if not hints:
                continue
            result = await conn.execute(
                "UPDATE cartridges SET assistant_hints = $2, updated_at = NOW() WHERE id = $1",
                cartridge_dir.name,
                hints,
            )
            if result.endswith("1"):
                updated += 1
        logger.info("[seed_packaged_hints] updated %d cartridge hint sets", updated)
