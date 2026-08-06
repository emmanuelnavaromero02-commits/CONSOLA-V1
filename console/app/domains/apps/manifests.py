"""Reviewed, packaged manifests — the only description of an app we trust.

A published app's own HTML cannot say what data it may read. It is authored
outside this repository, it ships inside the app record an attacker may
control, and scraping it does not even work reliably: the regex that used to
derive authority yields fragments like ``sale`` and ``pnl_men`` out of the
packaged apps' template literals.

What we do trust is the manifest reviewed and baked into the image next to each
app, at ``cartridges/<cartridge>/apps/<app>.json``. This module reads those,
computes a deterministic digest that covers both the manifest and the HTML
revision it was reviewed against, and refuses to answer for anything that is
not packaged. Everything else — user-created apps, unknown names — gets no
datasets at all.
"""

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

# The registry as it exists inside the image; the repository layout is the
# fallback so tests and local runs resolve the same files.
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
    """Accept only well-formed dataset names, sorted and de-duplicated.

    A manifest is reviewed, not trusted blindly: a malformed entry is dropped
    rather than carried into a grant.
    """
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
    """Deterministic identity of "this app, as reviewed".

    Covers the app name, its cartridge, the sorted dataset list and the HTML
    revision. Any edit to the served HTML or to the manifest moves the digest,
    which strands the grants made under the old one — that is the drift
    detection, and it is why the digest includes the HTML and not just the
    manifest.

    The payload is canonical JSON with sorted keys, so the digest depends on
    values only and never on formatting or field order.
    """
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
    """Every packaged app manifest, keyed by app name.

    One packaged app (hubspot) keys its name as ``slug``; both spellings are
    accepted, and the file stem is the last resort so the key always matches
    the name the route is asked for.
    """
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
            # The directory wins: a manifest cannot claim another cartridge.
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
    """Digest of the app *as currently served*, or ``None`` if not packaged.

    The HTML passed here is what the route is about to render. If it differs
    from the reviewed revision the digest differs too, and no grant matches —
    which is the intended outcome: a modified app has no data access until the
    server reconciles it.
    """
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
    """Descriptive only. Never widens anything.

    The scraped values keep their diagnostic use — an app asking for something
    it was not granted is worth surfacing — but they inform an operator, they
    do not inform the allowlist.
    """
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
