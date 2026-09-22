from __future__ import annotations

from typing import Any

try:
    from app.publication_snapshot import (
        PublicationSnapshotResolver,
        _require_pinned_version,
    )
except ModuleNotFoundError:
    from refinement.app.publication_snapshot import (
        PublicationSnapshotResolver,
        _require_pinned_version,
    )


def _raw_prefix(engine: Any, source: str, context: dict[str, Any] | None) -> str:
    uri = engine._bronze_path(source, context)
    key = engine._s3_object_key(uri) or ""
    positions = [index for token in ("*", "?") if (index := key.find(token)) >= 0]
    return key[: min(positions)] if positions else key


def _raw_state(
    engine: Any, source: str, context: dict[str, Any] | None
) -> dict[str, Any]:
    """Fingerprint the raw inputs from the listing alone.

    This used to HEAD every object and then stream all of its bytes through
    SHA-256. The prefix sits above the `load_date=` partition, so that was the
    entity's whole accumulated history, once here and again at finalize.

    Measured 2026-09-22 on a host identical to production: the SuccessFactors
    EmployeeTime scope holds 1,462 objects and 14.53 GB, read from S3 at
    ~30 MB/s. The two passes cost 1,000 s against a 300 s caller budget, before
    DuckDB read a single byte. Taking identity from the listing instead brings
    the same two passes to 3.2 s. The entity had been impossible to materialize
    since 2026-06-23, when the accumulated cost crossed that line.

    The digest is a change detector, not a commitment: nothing recomputes it,
    every reader only compares it against a value this same function produced,
    or copies it into a signed envelope as an opaque field. `size` and `etag`
    come back free in the listing and detect the same three things the content
    hash did -- a new object (new key), a removed one (missing key) and a
    rewritten one (S3 mints a new ETag on overwrite).

    Two fields went, not one. The object's `version` went with the checksum,
    because ListObjectsV2 does not return a VersionId and keeping it would mean
    a HEAD per object -- 2,321 of them per pass for EmployeeTime, twice. The
    only thing it detected on its own was a byte-identical re-upload, which
    cannot change any query's answer. Note this is not a weakening of a pinned
    read either: the old call was `_object_checksum(key)` with no version
    argument, so it hashed whatever storage served at that moment, with the
    same time-of-check gap against its own listing that the ETag has.

    The listing itself must stay exactly as wide as it is. The same key list is
    substituted into the SQL (publication_input_binding.py:87-89), and the SAP
    silver SQL deliberately reads the full history and picks the latest row per
    key, so narrowing this to the newest partition would change the query's
    answer -- a correctness bug wearing an optimization's clothes.
    """
    objects = []
    for listed in engine.storage.iter_list(_raw_prefix(engine, source, context)):
        if not str(listed.key).endswith(".parquet"):
            continue
        etag = (listed.etag or "").strip()
        if not etag:
            # An empty identity would make every object look identical to
            # every other, and a changed input look unchanged. Refuse it.
            raise RuntimeError(f"raw input object has no identity: {listed.key}")
        objects.append(
            {
                "key": listed.key,
                "size": int(listed.size),
                "etag": etag,
            }
        )
    return {"source": source, "objects": sorted(objects, key=lambda item: item["key"])}


def _published_state(
    engine: Any, source: str, context: dict[str, Any] | None
) -> dict[str, Any]:
    layer, cartridge, dataset = source.strip("/").split("/", 2)
    snapshot = PublicationSnapshotResolver(engine.storage).published_snapshot(
        {"name": dataset, "layer": layer, "cartridge": cartridge}, context or {}
    )
    head = snapshot.head if snapshot else None
    if not head:
        return {"source": source, "published": None}
    uri = str(head.get("object_uri") or "")
    key = engine._s3_object_key(uri)
    if not key:
        raise RuntimeError("published dependency is outside managed storage")
    version = str(head.get("object_version") or "")
    _require_pinned_version(version, "published dependency")
    # One HEAD against the pinned version, not a full download. A gold dataset
    # can declare a dozen published dependencies, and this runs twice per
    # materialization (before the replay check and again at finalize), so the
    # old _object_checksum call downloaded each dependency four times over.
    # Version + recorded digest answers the same question; the byte-level proof
    # lives in publication_verifier_worker.
    try:
        current = engine.storage.stat(key, expected_version=version)
    except Exception as exc:
        raise RuntimeError("published dependency is unavailable") from exc
    actual_checksum = current.checksum_sha256 or ""
    if not actual_checksum:
        raise RuntimeError("published dependency has no recorded checksum")
    if actual_checksum != str(head.get("object_checksum") or ""):
        raise RuntimeError("published dependency checksum mismatch")
    return {
        "source": source,
        "published": {
            "run": head["materialization_run_id"],
            "generation": head["generation"],
            "checksum": actual_checksum,
            "object_version": version,
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
