from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from omega_lakehouse.checksums import sha256_file


class PublicationObjectMixin:
    def _object_checksum(self, key: str) -> str:
        digest = hashlib.sha256()
        chunks = getattr(self.storage, "iter_chunks", None)
        if chunks:
            for chunk in chunks(key):
                digest.update(chunk)
        else:
            digest.update(self.storage.get_bytes(key))
        return digest.hexdigest()

    def _verify_prepared_object(self, values: dict[str, Any]) -> None:
        uri = str(values.get("object_uri") or "")
        expected = str(values.get("object_checksum") or "")
        key = self._s3_object_key(uri)
        if not key or not expected:
            raise RuntimeError("prepared materialization object is incomplete")
        if self._object_checksum(key) != expected:
            raise RuntimeError("prepared materialization object checksum mismatch")

    def _snapshot_path(
        self, layer: str, cartridge: str, name: str, user_context: dict | None = None
    ) -> str:
        state = self._state()
        if not state:
            return super()._snapshot_path(layer, cartridge, name, user_context)
        run_id = state["identity"].materialization_run_id.hex
        prefix = self._snapshot_prefix(layer, cartridge, name, user_context)
        return self._storage_uri(f"{prefix}_pending/{run_id}/data.parquet")

    def _copy_to_parquet(self, con: Any, sql: str, parquet_path: str) -> str:
        uri = super()._copy_to_parquet(con, sql, parquet_path)
        state = self._state()
        if state:
            key = self._s3_object_key(uri)
            if not key:
                raise RuntimeError("materialized object is outside managed storage")
            checksum = self._object_checksum(key)
            state.update(object_uri=uri, object_checksum=checksum)
        return uri

    def _upload_local_parquet(self, local_path: str, parquet_path: str) -> str:
        state = self._state()
        if not state:
            return super()._upload_local_parquet(local_path, parquet_path)
        key = self._s3_object_key(parquet_path)
        if not key:
            return parquet_path
        digest = sha256_file(Path(local_path))
        key = f"{key.rsplit('/', 1)[0]}/{digest}.parquet"
        if self.storage.exists(key):
            if self._object_checksum(key) != digest:
                raise RuntimeError("immutable materialization object checksum mismatch")
            return self.storage.uri_for(key)
        return self.storage.put_file(
            key,
            Path(local_path),
            overwrite=False,
            checksum_sha256=digest,
            metadata={"publication-state": "pending"},
        ).uri
