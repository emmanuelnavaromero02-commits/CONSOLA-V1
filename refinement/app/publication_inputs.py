from __future__ import annotations

from typing import Any

try:
    from app.publication_reader import PublicationReader
except ModuleNotFoundError:
    from refinement.app.publication_reader import PublicationReader


def _raw_prefix(engine: Any, source: str, context: dict[str, Any] | None) -> str:
    uri = engine._bronze_path(source, context)
    key = engine._s3_object_key(uri) or ""
    positions = [index for token in ("*", "?") if (index := key.find(token)) >= 0]
    return key[: min(positions)] if positions else key


def _raw_state(
    engine: Any, source: str, context: dict[str, Any] | None
) -> dict[str, Any]:
    objects = []
    for listed in engine.storage.iter_list(_raw_prefix(engine, source, context)):
        if not str(listed.key).endswith(".parquet"):
            continue
        current = engine.storage.stat(listed.key)
        objects.append(
            {
                "key": current.key,
                "size": int(current.size),
                "etag": current.etag or "",
                "version": current.version or "",
                "checksum": engine._object_checksum(current.key),
            }
        )
    return {"source": source, "objects": sorted(objects, key=lambda item: item["key"])}


def _published_state(
    engine: Any, source: str, context: dict[str, Any] | None
) -> dict[str, Any]:
    layer, cartridge, dataset = source.strip("/").split("/", 2)
    head = PublicationReader(engine._publication_store).published_head(
        layer, cartridge, dataset, context
    )
    if not head:
        return {"source": source, "published": None}
    uri = str(head.get("object_uri") or "")
    key = engine._s3_object_key(uri)
    if not key:
        raise RuntimeError("published dependency is outside managed storage")
    actual_checksum = engine._object_checksum(key)
    if actual_checksum != str(head.get("object_checksum") or ""):
        raise RuntimeError("published dependency checksum mismatch")
    return {
        "source": source,
        "published": {
            "run": head["materialization_run_id"],
            "generation": head["generation"],
            "checksum": actual_checksum,
            "uri": uri,
            "status": head.get("status"),
            "gold_table": head.get("gold_table"),
        },
    }


def resolve_input_state(
    engine: Any, dataset: dict[str, Any], context: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Fingerprint only server-resolved raw objects and published dependencies."""
    resolved = []
    for raw in dataset.get("sources") or []:
        source = str(raw or "").strip().strip("/")
        parts = source.split("/")
        if len(parts) == 3 and parts[0] == "raw":
            resolved.append(_raw_state(engine, source, context))
        elif len(parts) == 3 and parts[0] in {"silver", "gold"}:
            resolved.append(_published_state(engine, source, context))
        else:
            resolved.append({"source": source, "unsupported": True})
    return resolved
