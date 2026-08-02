from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import uuid
from urllib.parse import urlsplit

import psycopg2
import pyarrow.parquet as pq

from omega_lakehouse import storage_from_env


def _require_immutable_object_versions(storage) -> None:
    config = getattr(storage, "config", None)
    provider = str(getattr(config, "provider", "") or "").lower()
    if provider == "gcs":
        return
    if provider not in {"s3", "minio"}:
        raise RuntimeError("immutable object versioning is unavailable")
    response = storage._client_or_create().get_bucket_versioning(Bucket=config.bucket)
    if response.get("Status") != "Enabled":
        raise RuntimeError("immutable object versioning is unavailable")


def _dsn() -> str:
    value = str(os.environ.get("GOLD_VERIFIER_DATABASE_URL") or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    if not value:
        raise RuntimeError("publication verifier database unavailable")
    return value


def _load_candidate(candidate_id: str) -> tuple:
    uuid.UUID(candidate_id)
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        if cur.fetchone()[0] != "omega_gold_verifier":
            raise RuntimeError("publication verifier identity mismatch")
        cur.execute(
            "SELECT * FROM omega_publication.load_verification_candidate(%s)",
            (candidate_id,),
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError("publication verification candidate unavailable")
    return row


def _object_key(storage, row: tuple) -> str:
    _, _, tenant, workspace, dataset, layer, uri, _, _, _, _, _ = row
    parsed = urlsplit(str(uri))
    key = parsed.path.removeprefix("/")
    config = storage.config
    expected_scheme = "gs" if config.provider == "gcs" else "s3"
    parts = key.split("/")
    if (
        parsed.scheme != expected_scheme
        or parsed.netloc != config.bucket
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or len(parts) < 7
        or parts[0] != layer
        or parts[2] != dataset
        or parts[3] != f"tenant_id={tenant}"
        or parts[4] != f"workspace_id={workspace}"
        or storage.uri_for(key) != uri
    ):
        raise RuntimeError("publication verification object is outside scope")
    return key


def verify_candidate(candidate_id: str) -> None:
    row = _load_candidate(candidate_id)
    _, _, _, _, _, _, _, version, checksum, row_count, _, catalog = row
    storage = storage_from_env()
    _require_immutable_object_versions(storage)
    key = _object_key(storage, row)
    stat = storage.stat(key, expected_version=str(version))
    if str(stat.version or "") != str(version):
        raise RuntimeError("publication object version mismatch")
    raw = storage.get_bytes(key, expected_version=str(version))
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise RuntimeError("publication object checksum mismatch")
    parquet = pq.ParquetFile(io.BytesIO(raw))
    observed = [
        {"name": field.name, "type": str(field.type)} for field in parquet.schema_arrow
    ]
    expected = [{"name": item["name"], "type": item["type"]} for item in catalog]
    if parquet.metadata.num_rows != row_count or observed != expected:
        raise RuntimeError("publication object schema mismatch")
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT omega_publication.record_attestation(%s)", (candidate_id,))


def _ready() -> None:
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT current_user,"
            "has_function_privilege(current_user,"
            "'omega_publication.load_verification_candidate(uuid)','EXECUTE'),"
            "has_function_privilege(current_user,"
            "'omega_publication.record_attestation(uuid)','EXECUTE'),"
            "has_function_privilege(current_user,"
            "'omega_publication.publish_materialization(uuid,uuid)','EXECUTE')"
        )
        role, can_load, can_attest, can_publish = cur.fetchone()
    if role != "omega_gold_verifier" or not can_load or not can_attest or can_publish:
        raise RuntimeError("publication verifier privilege mismatch")
    _require_immutable_object_versions(storage_from_env())


def _authorized_peer(conn: socket.socket) -> bool:
    expected = int(os.environ.get("PUBLICATION_APP_UID", "-1"))
    if expected < 1:
        return False
    if hasattr(socket, "SO_PEERCRED"):
        _, uid, _ = struct.unpack(
            "3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
    elif hasattr(socket, "LOCAL_PEERCRED"):
        _, uid = struct.unpack("II", conn.getsockopt(0, socket.LOCAL_PEERCRED, 8))
    else:
        return False
    return uid == expected


def _handle(conn: socket.socket) -> dict[str, object]:
    if not _authorized_peer(conn):
        raise PermissionError("publication verifier peer rejected")
    line = conn.makefile("rb").readline(513)
    if not line or len(line) > 512:
        raise ValueError("invalid verifier request")
    payload = json.loads(line)
    if payload == {"command": "PING"}:
        _ready()
        return {"ok": True, "status": "ready"}
    if set(payload) == {"candidate_id"}:
        verify_candidate(str(payload["candidate_id"]))
        return {"ok": True}
    raise ValueError("invalid verifier request")


def serve(socket_path: str) -> None:
    path = Path(socket_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        # The app and verifier are distinct users in one private runtime group;
        # group read/write is the narrow IPC capability, with no world access.
        os.chmod(path, 0o660)  # nosec B103
        server.listen(8)
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    result = _handle(conn)
                except Exception as exc:
                    print(
                        f"publication verification rejected: {type(exc).__name__}",
                        file=sys.stderr,
                        flush=True,
                    )
                    result = {"ok": False, "error": "verification unavailable"}
                conn.sendall(json.dumps(result, separators=(",", ":")).encode() + b"\n")


if __name__ == "__main__":
    serve(
        os.environ.get(
            "PUBLICATION_VERIFIER_SOCKET", "/run/omega/publication-verifier.sock"
        )
    )
