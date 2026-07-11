from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from omega_lakehouse import LakehouseStorage, ObjectNotFound

from app.services.hash_utils import canonical_json_bytes, sha256_bytes


class ImmutableBatchConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchFile:
    name: str
    key: str
    sha256: str
    size: int
    uri: str


class BronzeWriter:
    def __init__(self, storage: LakehouseStorage) -> None:
        self.storage = storage

    def write_batch(
        self,
        *,
        entity: str,
        rows: list[dict[str, Any]],
        source_payload: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        final_prefix = str(manifest["final_prefix"])
        staging_prefix = str(manifest["staging_prefix"])
        request_hash = str(manifest["request_hash"])
        payload_hash = str(manifest["payload_hash"])
        manifest_key = f"{final_prefix}manifest.json"
        existing = self._existing_manifest(manifest_key)
        if existing:
            if (
                existing.get("status") == "complete"
                and existing.get("payload_hash") == payload_hash
                and existing.get("request_hash") == request_hash
            ):
                return {"status": "success", "idempotent": True, "manifest": existing}
            raise ImmutableBatchConflict("complete SEC EDGAR batch already exists with different hashes")

        files = [
            self._publish_parquet(entity, rows, staging_prefix, final_prefix, manifest),
            self._publish_json("source_response.json", source_payload, staging_prefix, final_prefix, manifest),
        ]
        complete = {
            **manifest,
            "status": "complete",
            "files": [file.__dict__ for file in files],
            "row_count": len(rows),
        }
        manifest_file = self._publish_json(
            "manifest.json",
            complete,
            staging_prefix,
            final_prefix,
            manifest,
        )
        complete["files"].append(manifest_file.__dict__)
        return {"status": "success", "idempotent": False, "manifest": complete}

    def _publish_parquet(
        self,
        entity: str,
        rows: list[dict[str, Any]],
        staging_prefix: str,
        final_prefix: str,
        manifest: dict[str, Any],
    ) -> BatchFile:
        name = f"{entity}.parquet"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / name
            df = pd.DataFrame(rows)
            df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
            data = path.read_bytes()
            sha = sha256_bytes(data)
            staging_key = f"{staging_prefix}{name}"
            final_key = f"{final_prefix}{name}"
            return self._publish_file(path, name, staging_key, final_key, sha, manifest)

    def _publish_json(
        self,
        name: str,
        payload: dict[str, Any],
        staging_prefix: str,
        final_prefix: str,
        manifest: dict[str, Any],
    ) -> BatchFile:
        data = canonical_json_bytes(payload)
        sha = sha256_bytes(data)
        staging_key = f"{staging_prefix}{name}"
        final_key = f"{final_prefix}{name}"
        existing = self._matching_existing(final_key, sha)
        if existing:
            return BatchFile(name, final_key, sha, existing.size, existing.uri)
        self.storage.put_bytes(
            staging_key,
            data,
            overwrite=True,
            checksum_sha256=sha,
            metadata=self._metadata(manifest),
        )
        result = self.storage.publish(
            staging_key,
            final_key,
            overwrite=False,
            expected_sha256=sha,
        )
        return BatchFile(name, final_key, sha, len(data), result.final_uri)

    def _publish_file(
        self,
        path: Path,
        name: str,
        staging_key: str,
        final_key: str,
        sha: str,
        manifest: dict[str, Any],
    ) -> BatchFile:
        existing = self._matching_existing(final_key, sha)
        if existing:
            return BatchFile(name, final_key, sha, existing.size, existing.uri)
        self.storage.put_file(
            staging_key,
            path,
            overwrite=True,
            checksum_sha256=sha,
            metadata=self._metadata(manifest),
        )
        result = self.storage.publish(staging_key, final_key, overwrite=False, expected_sha256=sha)
        return BatchFile(name, final_key, sha, path.stat().st_size, result.final_uri)

    def _matching_existing(self, key: str, sha: str):
        try:
            stat = self.storage.stat(key)
        except ObjectNotFound:
            return None
        if stat.checksum_sha256 != sha:
            raise ImmutableBatchConflict(f"published SEC EDGAR object differs: {key}")
        return stat

    def _existing_manifest(self, key: str) -> dict[str, Any] | None:
        try:
            data = self.storage.get_bytes(key)
        except ObjectNotFound:
            return None
        loaded = json.loads(data.decode("utf-8"))
        return loaded if isinstance(loaded, dict) else None

    def _metadata(self, manifest: dict[str, Any]) -> dict[str, str]:
        keys = ("cartridge_id", "entity", "tenant_id", "workspace_id", "run_id", "request_hash", "payload_hash")
        return {f"omega-{key.replace('_', '-')}": str(manifest[key]) for key in keys if key in manifest}
