from __future__ import annotations

import json
import logging
import pathlib
import re

import asyncpg


logger = logging.getLogger(__name__)

_REGISTRY = pathlib.Path("/registry/cartridges")
_DATA_API_RE = re.compile(r"/api/data/([A-Za-z_][A-Za-z0-9_]*)")
_DATA_BIND_RE = re.compile(r"\bdata-bind=[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']")


def _declared_datasets(meta: dict, html: str) -> list[str]:
    candidates: list[str] = []
    for key in ("datasets_used", "datasets", "dataset"):
        raw = meta.get(key)
        if isinstance(raw, list):
            candidates.extend(str(item).strip() for item in raw)
        elif isinstance(raw, str):
            candidates.append(raw.strip())
    candidates.extend(_DATA_API_RE.findall(html))
    candidates.extend(_DATA_BIND_RE.findall(html))
    return sorted({
        item
        for item in candidates
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item or "")
    })


def _app_files() -> dict[str, list[pathlib.Path]]:
    if not _REGISTRY.exists():
        return {}
    out: dict[str, list[pathlib.Path]] = {}
    for apps_dir in _REGISTRY.glob("*/apps"):
        if apps_dir.is_dir():
            out[apps_dir.parent.name] = sorted(apps_dir.glob("*.html"))
    return out


async def seed_packaged_apps(pool: asyncpg.Pool) -> None:
    packaged = _app_files()
    if not packaged:
        logger.info("[seed_packaged_apps] no packaged apps found")
        return

    async with pool.acquire() as conn:
        for cartridge_id, html_files in packaged.items():
            names: list[str] = []
            for html_path in html_files:
                meta_path = html_path.with_suffix(".json")
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                    html = html_path.read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning("[seed_packaged_apps] skip %s: %s", html_path, exc)
                    continue
                name = str(meta.get("name") or html_path.stem).strip()
                title = str(meta.get("title") or name.replace("_", " ").title()).strip()
                description = str(meta.get("description") or "").strip()
                datasets_used = _declared_datasets(meta, html)
                await conn.execute(
                    """INSERT INTO analytic_apps
                          (name, title, html, description, cartridge_id, visibility,
                           datasets_used, tenant_id, workspace_id, scope_status, updated_at)
                       VALUES ($1, $2, $3, $4, $5, 'shared', $6::text[],
                               NULL, NULL, 'platform_template', NOW())
                       ON CONFLICT (name) DO UPDATE
                          SET title = EXCLUDED.title,
                              html = EXCLUDED.html,
                              description = EXCLUDED.description,
                              cartridge_id = EXCLUDED.cartridge_id,
                              visibility = EXCLUDED.visibility,
                              datasets_used = EXCLUDED.datasets_used,
                              tenant_id = NULL,
                              workspace_id = NULL,
                              scope_status = 'platform_template',
                              updated_at = NOW()
                        WHERE analytic_apps.created_by_id IS NULL""",
                    name, title, html, description, cartridge_id, datasets_used,
                )
                names.append(name)
            if names:
                await conn.execute(
                    """DELETE FROM analytic_apps
                        WHERE cartridge_id = $1
                          AND scope_status = 'platform_template'
                          AND NOT (name = ANY($2::text[]))""",
                    cartridge_id, names,
                )
                logger.info("[seed_packaged_apps] %s: seeded %d apps", cartridge_id, len(names))
