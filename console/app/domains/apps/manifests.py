from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
from typing import Any

from app.domains.apps.payloads import DATASET_NAME_RE

logger = logging.getLogger(__name__)

APP_NAME_RE = DATASET_NAME_RE
CARTRIDGE_ID_RE = DATASET_NAME_RE

_REGISTRY_CANDIDATES = (
    pathlib.Path(os.environ.get("OMEGA_CARTRIDGE_REGISTRY", "/registry/cartridges")),
    pathlib.Path(__file__).resolve().parents[4] / "cartridges",
)

MANIFEST_DIGEST_VERSION = "omega.app-manifest.v1"


def registry_root() -> pathlib.Path | None:
    for candidate in _REGISTRY_CANDIDATES:
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _coerce_datasets(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    return sorted(
        {
            str(item)
            for item in raw
            if isinstance(item, str) and DATASET_NAME_RE.fullmatch(item)
        }
    )


def manifest_digest(
    *, app_name: str, cartridge_id: str, datasets: list[str], html: str
) -> str:
    payload = json.dumps(
        {
            "version": MANIFEST_DIGEST_VERSION,
            "app_name": str(app_name or ""),
            "cartridge_id": str(cartridge_id or ""),
            "datasets": sorted(datasets or []),
            "html_sha256": hashlib.sha256((html or "").encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_packaged_manifests(root: pathlib.Path | None = None) -> dict[str, dict]:
    base = root or registry_root()
    if base is None:
        return {}
    manifests: dict[str, dict] = {}
    for path in sorted(base.glob("*/apps/*.json")):
        cartridge = path.parents[1].name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("[app-manifests] unreadable manifest %s", path)
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("slug") or path.stem).strip()
        declared_cartridge = str(raw.get("cartridge") or cartridge).strip()
        if not APP_NAME_RE.fullmatch(name):
            logger.warning("[app-manifests] rejected app name in %s", path)
            continue
        if declared_cartridge != cartridge:
            logger.warning(
                "[app-manifests] %s claims cartridge %s but ships under %s",
                path, declared_cartridge, cartridge,
            )
        html_path = path.with_suffix(".html")
        try:
            html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
        except OSError:
            html = ""
        datasets = _coerce_datasets(raw.get("datasets_used"))
        manifests[name] = {
            "app_name": name,
            "cartridge_id": cartridge,
            "datasets": datasets,
            "manifest_path": str(path),
            "packaged_html": html,
            "packaged_digest": manifest_digest(
                app_name=name, cartridge_id=cartridge, datasets=datasets, html=html
            ),
        }
    return manifests


def packaged_manifest(app_name: str, root: pathlib.Path | None = None) -> dict | None:
    if not APP_NAME_RE.fullmatch(str(app_name or "")):
        return None
    return load_packaged_manifests(root).get(str(app_name))


def served_digest(app_name: str, html: str, root: pathlib.Path | None = None) -> str | None:
    manifest = packaged_manifest(app_name, root)
    if manifest is None:
        return None
    return manifest_digest(
        app_name=manifest["app_name"],
        cartridge_id=manifest["cartridge_id"],
        datasets=manifest["datasets"],
        html=html or "",
    )


def drift_report(
    *,
    granted: list[str],
    referenced: list[str],
    manifest_datasets: list[str],
    served: str | None,
    packaged: str | None,
) -> dict[str, Any]:
    granted_set = set(granted or [])
    referenced_set = {
        item for item in (referenced or []) if DATASET_NAME_RE.fullmatch(str(item))
    }
    return {
        "requested_but_not_granted": sorted(referenced_set - granted_set),
        "granted_but_not_referenced": sorted(granted_set - referenced_set),
        "manifest_drift": bool(served and packaged and served != packaged),
        "manifest_datasets": sorted(manifest_datasets or []),
    }
