from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from omega_lakehouse.checksums import sha256_file

try:
    import app.partitioned_parquet as partitioned_parquet
except ModuleNotFoundError:
    import refinement.app.partitioned_parquet as partitioned_parquet


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

    def _pinned_partition_part(self, part: dict[str, Any]) -> pq.ParquetFile:
        key = str(part["key"])
        version = str(part.get("version") or "")
        if not version:
            raise RuntimeError("partition object has no pinned version")
        raw = self.storage.get_bytes(key, expected_version=version)
        if hashlib.sha256(raw).hexdigest() != part["checksum"]:
            raise RuntimeError("prepared materialization object checksum mismatch")
        parquet = pq.ParquetFile(io.BytesIO(raw))
        if int(parquet.metadata.num_rows) != int(part["rows"]):
            raise RuntimeError("prepared materialization row count mismatch")
        return parquet

    def _read_published_table(
        self, object_uri: str, object_version: str, max_rows: int | None = None
    ) -> pa.Table:
        key = self._s3_object_key(object_uri)
        if not key:
            raise RuntimeError("prepared materialization object is outside storage")
        raw = self.storage.get_bytes(key, expected_version=object_version)
        if not partitioned_parquet.is_manifest_key(key):
            return pq.read_table(io.BytesIO(raw))
        manifest = partitioned_parquet.read_manifest(raw, key)
        tables = [manifest["schema"].remove_metadata().empty_table()]
        rows = 0
        for part in manifest["parts"]:
            if max_rows is not None and rows >= max_rows:
                break
            table = self._pinned_partition_part(part).read()
            tables.append(table.replace_schema_metadata(None))
            rows += table.num_rows
        return pa.concat_tables(tables)

    def _verify_parquet_evidence(
        self,
        *,
        object_uri: str,
        object_checksum: str,
        object_version: str,
        row_count: int,
        expected_columns: list[str],
    ) -> tuple[int, list[dict[str, str]]]:
        key = self._s3_object_key(object_uri)
        if not key:
            raise RuntimeError("prepared materialization object is outside storage")
        raw = self.storage.get_bytes(key, expected_version=object_version)
        if hashlib.sha256(raw).hexdigest() != object_checksum:
            raise RuntimeError("prepared materialization object checksum mismatch")
        parquet = pq.ParquetFile(io.BytesIO(raw))
        if partitioned_parquet.is_manifest_key(key):
            manifest = partitioned_parquet.read_manifest(raw, key)
            expected = partitioned_parquet.schema_fields(manifest["schema"])
            for part in manifest["parts"]:
                observed = self._pinned_partition_part(part).schema_arrow
                if partitioned_parquet.schema_fields(observed) != expected:
                    raise RuntimeError("prepared materialization catalog mismatch")
            num_rows = int(manifest["row_count"])
        elif partitioned_parquet.manifest_payload(parquet) is not None:
            raise RuntimeError("prepared materialization object is not a manifest")
        else:
            num_rows = int(parquet.metadata.num_rows)
        if num_rows != int(row_count):
            raise RuntimeError("prepared materialization row count mismatch")
        actual_schema = parquet.schema_arrow
        actual_names = list(actual_schema.names)
        if actual_names != expected_columns:
            raise RuntimeError("prepared materialization catalog mismatch")
        catalog = [
            {"name": field.name, "type": str(field.type)} for field in actual_schema
        ]
        return num_rows, catalog

    def _snapshot_path(
        self, layer: str, cartridge: str, name: str, user_context: dict | None = None
    ) -> str:
        state = self._state()
        if not state:
            return super()._snapshot_path(layer, cartridge, name, user_context)
        run_id = state["identity"].materialization_run_id.hex
        prefix = self._snapshot_prefix(layer, cartridge, name, user_context)
        return self._storage_uri(f"{prefix}_pending/{run_id}/data.parquet")

    def _partition_set_key(self, parquet_path: str) -> str:
        if not self._state():
            return super()._partition_set_key(parquet_path)
        key = self._s3_object_key(parquet_path)
        if not key:
            raise ValueError("partitioned materialization requires managed storage")
        directory = partitioned_parquet.PARTITION_SET_DIRECTORY
        return f"{key.rsplit('/', 1)[0]}/{directory}"

    def _put_partition_object(
        self, local_path: Path, key: str, digest: str
    ) -> tuple[str, str]:
        if not self._state():
            return super()._put_partition_object(local_path, key, digest)
        if self.storage.exists(key):
            stat = self.storage.stat(key)
            if not stat.version or self._object_checksum(key, stat.version) != digest:
                raise RuntimeError("immutable materialization object checksum mismatch")
            return self.storage.uri_for(key), str(stat.version)
        result = self.storage.put_file(
            key,
            Path(local_path),
            overwrite=False,
            checksum_sha256=digest,
            metadata={"publication-state": "pending"},
        )
        if not result.version:
            raise RuntimeError("materialized object version is unavailable")
        return result.uri, str(result.version)

    def _copy_to_parquet(
        self, con: Any, sql: str, parquet_path: str, **options: Any
    ) -> str:
        uri = super()._copy_to_parquet(con, sql, parquet_path, **options)
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
