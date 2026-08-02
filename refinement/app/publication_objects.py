from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from omega_lakehouse.checksums import sha256_file


class PublicationObjectMixin:
    def _object_checksum(self, key: str, expected_version: str | None = None) -> str:
        digest = hashlib.sha256()
        chunks = getattr(self.storage, "iter_chunks", None)
        if chunks:
            for chunk in chunks(key, expected_version=expected_version):
                digest.update(chunk)
        else:
            digest.update(
                self.storage.get_bytes(key, expected_version=expected_version)
            )
        return digest.hexdigest()

    def _verify_prepared_object(self, values: dict[str, Any]) -> None:
        uri = str(values.get("object_uri") or "")
        expected = str(values.get("object_checksum") or "")
        version = str(values.get("object_version") or "")
        key = self._s3_object_key(uri)
        if not key or not expected or not version:
            raise RuntimeError("prepared materialization object is incomplete")
        try:
            observed = self._object_checksum(key, version)
        except Exception as exc:
            raise RuntimeError(
                "prepared materialization object is unavailable"
            ) from exc
        if observed != expected:
            raise RuntimeError("prepared materialization object checksum mismatch")

    def _verify_parquet_evidence(
        self,
        *,
        object_uri: str,
        object_checksum: str,
        object_version: str,
        row_count: int,
        expected_columns: list[str],
    ) -> tuple[int, list[dict[str, str]]]:
        import pyarrow.parquet as pq

        key = self._s3_object_key(object_uri)
        if not key:
            raise RuntimeError("prepared materialization object is outside storage")
        raw = self.storage.get_bytes(key, expected_version=object_version)
        if hashlib.sha256(raw).hexdigest() != object_checksum:
            raise RuntimeError("prepared materialization object checksum mismatch")
        parquet = pq.ParquetFile(io.BytesIO(raw))
        if int(parquet.metadata.num_rows) != int(row_count):
            raise RuntimeError("prepared materialization row count mismatch")
        actual_schema = parquet.schema_arrow
        actual_names = list(actual_schema.names)
        if actual_names != expected_columns:
            raise RuntimeError("prepared materialization catalog mismatch")
        catalog = [
            {"name": field.name, "type": str(field.type)} for field in actual_schema
        ]
        return int(parquet.metadata.num_rows), catalog

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
            version = str(self.storage.stat(key).version or "")
            if not version:
                raise RuntimeError("materialized object version is unavailable")
            checksum = self._object_checksum(key, version)
            state.update(
                object_uri=uri, object_checksum=checksum, object_version=version
            )
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
            stat = self.storage.stat(key)
            if not stat.version or self._object_checksum(key, stat.version) != digest:
                raise RuntimeError("immutable materialization object checksum mismatch")
            state["object_version"] = str(stat.version)
            return self.storage.uri_for(key)
        result = self.storage.put_file(
            key,
            Path(local_path),
            overwrite=False,
            checksum_sha256=digest,
            metadata={"publication-state": "pending"},
        )
        if not result.version:
            raise RuntimeError("materialized object version is unavailable")
        state["object_version"] = str(result.version)
        return result.uri
