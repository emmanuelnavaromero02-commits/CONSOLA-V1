#!/usr/bin/python3 -I
"""Fail-closed host I/O primitives for the canonical GCP release path.

The helper deliberately owns all network credentials in-process.  Callers pass
only public object identity (URI, generation, size, SHA-256) and filesystem
paths.  It is embedded in first-boot metadata and transported beside every
remote day-2 script so artifact handling has one auditable implementation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import signal
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GENERATION_RE = re.compile(r"^[1-9][0-9]*$")
GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
ENVIRONMENT_RE = re.compile(r"^[a-z](?:[a-z0-9-]{0,18}[a-z0-9])?$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENV_BYTES = 2 * 1024 * 1024
MAX_HTTP_JSON_BYTES = 4 * 1024 * 1024
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
PROC_ROOT = Path("/proc")
CGROUP_ROOT = Path("/sys/fs/cgroup")
SHIM_EXECUTABLE = Path("/usr/bin/containerd-shim-runc-v2")
SYSTEM_CA_BUNDLE = Path(
    "/private/etc/ssl/cert.pem"
    if sys.platform == "darwin"
    else "/etc/ssl/certs/ca-certificates.crt"
)
CANONICAL_OPT_ROOT = Path("/opt")
CANONICAL_EVIDENCE_ROOT = Path("/opt/modecissions/shared")
OWNER_UNIT = "omega-gcp-operation.service"
OWNER_CGROUP = Path("/sys/fs/cgroup/system.slice/omega-gcp-operation.service")
WATCHDOG_STATE = Path("/run/omega-gcp/watchdog-state.json")
PROC_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
PROC_STAT = Path("/proc/stat")
METADATA_ROOT = "http://metadata.google.internal/computeMetadata/v1"
HOST_IDENTITY_PATH = Path("/etc/omega/gcp-host-identity.json")
HOST_IDENTITY_KEYS = {
    "schema_version",
    "project_id",
    "instance_id",
    "instance_name",
    "zone",
    "service_account_email",
}


def _die(message: str) -> None:
    raise SystemExit(f"omega-safe-io: {message}")


def _require_root() -> None:
    if os.geteuid() != 0:
        _die("remote host operation requires root")


def _exact_file_bytes(
    path: Path,
    *,
    mode: int,
    maximum: int,
    minimum: int = 1,
    uid: int = -1,
    gid: int = -1,
) -> bytes:
    """Read one immutable-enough regular file through its verified descriptor."""
    if uid == -1:
        uid = os.geteuid()
    if gid == -1:
        gid = os.getegid()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != uid
            or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or not minimum <= before.st_size <= maximum
        ):
            _die(f"file identity differs: {path}")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(raw) != before.st_size or before_identity != after_identity:
            _die(f"file changed during authoritative read: {path}")
        return raw
    finally:
        os.close(descriptor)


def _strict_json(raw: bytes, *, label: str) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_json_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _die(f"{label} is not strict JSON")
    if not isinstance(value, dict):
        _die(f"{label} is not a JSON object")
    return value


def _crc32c(payload: bytes) -> int:
    """Return the unsigned Castagnoli CRC-32C used by Secret Manager."""
    return (~_crc32c_update(0xFFFFFFFF, payload)) & 0xFFFFFFFF


def _crc32c_update(crc: int, payload: bytes) -> int:
    for value in payload:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_private_parent(path: Path) -> None:
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        _die("output parent must be a directory owned by the invoking identity")
    if info.st_mode & 0o022:
        _die("output parent must not be group/world writable")


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if (
        parsed.scheme != "gs"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not GCS_BUCKET_RE.fullmatch(bucket)
        or not key
        or len(key.encode()) > 1024
        or any(part in {"", ".", ".."} for part in key.split("/"))
        or any(ord(char) < 0x20 for char in key)
    ):
        _die("invalid canonical GCS URI")
    return bucket, key


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, fp, code, message, headers, new_url
        raise RuntimeError("authenticated HTTP redirects are forbidden")


def _direct_opener() -> urllib.request.OpenerDirector:
    """Return an opener with no ambient proxy and no credential redirects."""
    descriptor = os.open(
        SYSTEM_CA_BUNDLE,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 8 * 1024 * 1024
        ):
            _die("system CA bundle ownership/path is invalid")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die("system CA bundle changed during authoritative read")
    finally:
        os.close(descriptor)
    try:
        ca_data = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        _die("system CA bundle is not ASCII PEM")
    if "-----BEGIN CERTIFICATE-----" not in ca_data:
        _die("system CA bundle contains no certificate")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.keylog_filename = None
    context.load_verify_locations(cadata=ca_data)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
        urllib.request.HTTPSHandler(context=context),
    )


def _json_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _die("authenticated JSON response contains a duplicate key")
        result[key] = value
    return result


def _response_bytes(response: BinaryIO, *, maximum: int, label: str) -> bytes:
    headers = getattr(response, "headers", None)
    content_length = headers.get("Content-Length") if headers is not None else None
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            _die(f"{label} Content-Length is invalid")
        if declared < 1 or declared > maximum:
            _die(f"{label} Content-Length is outside the bounded contract")
    raw = response.read(maximum + 1)
    if not 1 <= len(raw) <= maximum:
        _die(f"{label} exceeds the bounded response contract")
    if content_length is not None and len(raw) != declared:
        _die(f"{label} Content-Length differs from received bytes")
    return raw


def _decode_json_response(response: BinaryIO, *, label: str) -> dict:
    raw = _response_bytes(response, maximum=MAX_HTTP_JSON_BYTES, label=label)
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_json_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _die(f"{label} is not strict JSON")
    if not isinstance(value, dict):
        _die(f"{label} is not a JSON object")
    return value


def _metadata_token(*, deadline: float | None = None) -> str:
    started = time.monotonic()
    absolute_deadline = started + 15 if deadline is None else deadline
    remaining = absolute_deadline - time.monotonic()
    if remaining <= 0:
        _die("metadata token deadline expired")
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with _direct_opener().open(  # nosec B310
        request, timeout=min(10, remaining)
    ) as response:
        headers = getattr(response, "headers", None)
        if headers is None or headers.get("Metadata-Flavor") != "Google":
            _die("metadata response lacks exact Metadata-Flavor")
        payload = _decode_json_response(response, label="metadata token response")
    if time.monotonic() >= absolute_deadline:
        _die("metadata token deadline expired")
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if (
        set(payload) != {"access_token", "expires_in", "token_type"}
        or not isinstance(token, str)
        or re.fullmatch(r"[A-Za-z0-9._~+/=-]{20,8192}", token) is None
        or payload.get("token_type") != "Bearer"
        or type(payload.get("expires_in")) is not int
        or not 60 <= payload["expires_in"] <= 3600
    ):
        _die("instance metadata returned an invalid access token")
    return token


def _metadata_service_account(*, deadline: float | None = None) -> str:
    absolute_deadline = time.monotonic() + 15 if deadline is None else deadline
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/email",
        headers={"Metadata-Flavor": "Google"},
    )
    remaining = absolute_deadline - time.monotonic()
    if remaining <= 0:
        _die("metadata service-account deadline expired")
    with _direct_opener().open(  # nosec B310
        request, timeout=min(10, remaining)
    ) as response:
        headers = getattr(response, "headers", None)
        if headers is None or headers.get("Metadata-Flavor") != "Google":
            _die("metadata service-account response lacks exact Metadata-Flavor")
        raw = _response_bytes(
            response, maximum=512, label="metadata service-account response"
        )
    try:
        identity = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        _die("metadata service-account identity is not ASCII")
    if (
        re.fullmatch(
            r"[a-z][a-z0-9-]{2,62}@[a-z][a-z0-9-]{4,28}[a-z0-9][.]"
            r"iam[.]gserviceaccount[.]com",
            identity,
        )
        is None
    ):
        _die("metadata service-account identity is invalid")
    return identity


def _request_json(
    url: str, token: str, *, deadline: float | None = None, label: str = "API response"
) -> dict:
    absolute_deadline = time.monotonic() + 45 if deadline is None else deadline
    remaining = absolute_deadline - time.monotonic()
    if remaining <= 0:
        _die(f"{label} deadline expired")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with _direct_opener().open(  # nosec B310
        request, timeout=min(30, remaining)
    ) as response:
        payload = _decode_json_response(response, label=label)
    if time.monotonic() >= absolute_deadline:
        _die(f"{label} deadline expired")
    return payload


def command_gcs_release_backup_permissions(args: argparse.Namespace) -> int:
    """Prove effective VM permissions without attempting a destructive call."""
    _require_root()
    if GCS_BUCKET_RE.fullmatch(args.bucket) is None:
        _die("release backup bucket is invalid")
    if (
        re.fullmatch(
            r"[a-z][a-z0-9-]{2,62}@[a-z][a-z0-9-]{4,28}[a-z0-9][.]"
            r"iam[.]gserviceaccount[.]com",
            args.expected_service_account,
        )
        is None
    ):
        _die("expected backup service account is invalid")
    if _metadata_service_account() != args.expected_service_account:
        _die("live VM service-account identity differs")
    required = {
        "storage.buckets.get",
        "storage.objects.create",
        "storage.objects.get",
    }
    forbidden = {
        "storage.objects.delete",
        "storage.objects.update",
        "storage.objects.restore",
        "storage.objects.list",
        "storage.buckets.update",
        "storage.buckets.delete",
        "storage.buckets.setIamPolicy",
        "storage.buckets.getIamPolicy",
        "storage.buckets.setRetentionPolicy",
        "storage.buckets.overrideUnlockedRetentionPolicy",
        "storage.objects.setIamPolicy",
        "storage.objects.getIamPolicy",
        "storage.objects.setRetention",
        "storage.objects.overrideUnlockedRetention",
    }
    requested = sorted(required | forbidden)
    query = urllib.parse.urlencode(
        [("permissions", permission) for permission in requested]
    )
    bucket = urllib.parse.quote(args.bucket, safe="")
    payload = _request_json(
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam/testPermissions?{query}",
        _metadata_token(),
    )
    if set(payload) != {"permissions"}:
        _die("release backup permission response shape differs")
    granted_raw = payload.get("permissions")
    if (
        not isinstance(granted_raw, list)
        or not all(isinstance(value, str) for value in granted_raw)
        or len(granted_raw) != len(set(granted_raw))
        or set(granted_raw) - set(requested)
    ):
        _die("release backup permission response is malformed")
    granted = set(granted_raw)
    if not required.issubset(granted) or granted & forbidden:
        _die("effective release backup permissions are not least privilege")
    print(
        json.dumps(
            {
                "status": "PASS",
                "required_granted": len(required),
                "forbidden_granted": 0,
                "bucket": args.bucket,
                "service_account": args.expected_service_account,
            },
            sort_keys=True,
        )
    )
    return 0


def _copy_and_hash(source: BinaryIO, target: BinaryIO) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
        target.write(chunk)
    return size, digest.hexdigest()


def _copy_exact_and_hash(
    source: BinaryIO,
    target: BinaryIO,
    *,
    expected_size: int,
    deadline: float,
) -> tuple[int, str, str, str]:
    size = 0
    sha256_digest = hashlib.sha256()
    md5_digest = hashlib.md5(usedforsecurity=False)  # nosec B324: GCS integrity
    crc32c = 0xFFFFFFFF
    while size < expected_size:
        if time.monotonic() >= deadline:
            _die("GCS download exceeded its overall deadline")
        chunk = source.read(min(1024 * 1024, expected_size - size))
        if not chunk:
            _die("GCS download ended before the reviewed size")
        size += len(chunk)
        sha256_digest.update(chunk)
        md5_digest.update(chunk)
        crc32c = _crc32c_update(crc32c, chunk)
        target.write(chunk)
    if time.monotonic() >= deadline:
        _die("GCS download exceeded its overall deadline")
    if source.read(1):
        _die("GCS download exceeded the reviewed size")
    return (
        size,
        sha256_digest.hexdigest(),
        base64.b64encode(((~crc32c) & 0xFFFFFFFF).to_bytes(4, "big")).decode(),
        base64.b64encode(md5_digest.digest()).decode(),
    )


def command_gcs_download(args: argparse.Namespace) -> int:
    _require_root()
    if not GENERATION_RE.fullmatch(args.generation):
        _die("GCS generation must be an exact positive integer")
    if args.size < 1 or args.size > MAX_ARCHIVE_BYTES:
        _die("GCS object size is outside the bounded contract")
    if not SHA256_RE.fullmatch(args.sha256):
        _die("GCS object SHA-256 is invalid")
    bucket, key = _parse_gcs_uri(args.uri)
    destination = Path(args.output)
    if (
        not destination.is_absolute()
        or destination.exists()
        or destination.is_symlink()
    ):
        _die("download output must be a new absolute non-symlink path")
    _require_private_parent(destination)

    deadline = time.monotonic() + 600
    token = _metadata_token(deadline=deadline)
    quoted_bucket = urllib.parse.quote(bucket, safe="")
    quoted_key = urllib.parse.quote(key, safe="")
    metadata_url = (
        f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o/"
        f"{quoted_key}?{urllib.parse.urlencode({'generation': args.generation, 'fields': 'bucket,name,generation,size,crc32c,md5Hash'})}"
    )
    metadata = _request_json(
        metadata_url, token, deadline=deadline, label="GCS metadata response"
    )
    if set(metadata) != {"bucket", "name", "generation", "size", "crc32c", "md5Hash"}:
        _die("GCS object metadata shape differs from the reviewed identity")
    metadata_generation = metadata.get("generation")
    metadata_size = metadata.get("size")
    metadata_crc32c = metadata.get("crc32c")
    metadata_md5 = metadata.get("md5Hash")
    if (
        metadata.get("bucket") != bucket
        or metadata.get("name") != key
        or not isinstance(metadata_generation, str)
        or GENERATION_RE.fullmatch(metadata_generation) is None
        or metadata_generation != args.generation
        or not isinstance(metadata_size, str)
        or re.fullmatch(r"[1-9][0-9]*", metadata_size) is None
        or int(metadata_size) != args.size
        or not isinstance(metadata_crc32c, str)
        or re.fullmatch(r"[A-Za-z0-9+/]{6}==", metadata_crc32c) is None
        or not isinstance(metadata_md5, str)
        or re.fullmatch(r"[A-Za-z0-9+/]{22}==", metadata_md5) is None
    ):
        _die("GCS object metadata differs from the reviewed identity")
    try:
        if (
            len(base64.b64decode(metadata_crc32c, validate=True)) != 4
            or len(base64.b64decode(metadata_md5, validate=True)) != 16
        ):
            raise ValueError
    except ValueError:
        _die("GCS object metadata checksums are invalid")

    media_url = (
        f"https://storage.googleapis.com/download/storage/v1/b/{quoted_bucket}/o/"
        f"{quoted_key}?{urllib.parse.urlencode({'alt': 'media', 'generation': args.generation})}"
    )
    request = urllib.request.Request(
        media_url, headers={"Authorization": f"Bearer {token}"}
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        with (
            _direct_opener().open(  # nosec B310
                request, timeout=min(180, max(1, deadline - time.monotonic()))
            ) as response,
            os.fdopen(descriptor, "wb", closefd=False) as target,
        ):
            headers = getattr(response, "headers", None)
            content_length = (
                headers.get("Content-Length") if headers is not None else None
            )
            if content_length is not None and content_length != str(args.size):
                _die("GCS media Content-Length differs from reviewed size")
            actual_size, actual_sha256, actual_crc32c, actual_md5 = (
                _copy_exact_and_hash(
                    response, target, expected_size=args.size, deadline=deadline
                )
            )
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    if (
        actual_size != args.size
        or actual_sha256 != args.sha256
        or actual_crc32c != metadata_crc32c
        or actual_md5 != metadata_md5
    ):
        destination.unlink(missing_ok=True)
        _fsync_dir(destination.parent)
        _die("downloaded GCS bytes differ in size or SHA-256")
    _fsync_dir(destination.parent)
    print(
        json.dumps(
            {
                "status": "PASS",
                "uri": args.uri,
                "generation": args.generation,
                "size_bytes": actual_size,
                "sha256": actual_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


def command_secret_access(args: argparse.Namespace) -> int:
    """Return one secret payload while keeping the OAuth bearer out of argv."""
    _require_root()
    if not PROJECT_RE.fullmatch(args.project):
        _die("Secret Manager project id is invalid")
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,254}", args.secret) is None:
        _die("Secret Manager secret id is invalid")
    if re.fullmatch(r"[1-9][0-9]*", args.version) is None:
        _die("Secret Manager version is invalid")
    token = _metadata_token()
    project = urllib.parse.quote(args.project, safe="")
    secret = urllib.parse.quote(args.secret, safe="")
    version = urllib.parse.quote(args.version, safe="")
    url = (
        f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/"
        f"{secret}/versions/{version}:access"
    )
    try:
        response = _request_json(url, token)
    except urllib.error.HTTPError as exc:
        if args.allow_missing and exc.code == 404:
            return 3
        raise
    expected_name = (
        f"projects/{args.project}/secrets/{args.secret}/versions/{args.version}"
    )
    if not isinstance(response, dict) or set(response) != {"name", "payload"}:
        _die("Secret Manager response shape differs")
    payload = response.get("payload")
    if (
        response.get("name") != expected_name
        or not isinstance(payload, dict)
        or set(payload) != {"data", "dataCrc32c"}
    ):
        _die("Secret Manager response identity/payload differs")
    encoded = payload.get("data")
    crc32c = payload.get("dataCrc32c")
    if (
        not isinstance(encoded, str)
        or not encoded
        or not isinstance(crc32c, str)
        or re.fullmatch(r"[0-9]+", crc32c) is None
    ):
        _die("Secret Manager response lacks an exact payload/CRC32C")
    try:
        import base64

        decoded = base64.b64decode(encoded, validate=True)
    except ValueError:
        _die("Secret Manager payload encoding is invalid")
    if _crc32c(decoded) != int(crc32c):
        _die("Secret Manager payload CRC32C differs")
    if b"\x00" in decoded or b"\r" in decoded or b"\n" in decoded:
        _die("Secret Manager payload contains a forbidden delimiter")
    sys.stdout.buffer.write(decoded)
    return 0


def _safe_member_name(raw_name: str) -> PurePosixPath:
    name = raw_name[:-1] if raw_name.endswith("/") else raw_name
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(ord(char) < 0x20 for char in name)
    ):
        _die("archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) > 64 or len(name.encode()) > 4096:
        _die("archive path exceeds canonical limits")
    return path


def _open_new_file(path: Path, mode: int) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    return os.fdopen(descriptor, "wb")


def command_safe_extract(args: argparse.Namespace) -> int:
    _require_root()
    archive_path = Path(args.archive)
    destination = Path(args.destination)
    if (
        not archive_path.is_absolute()
        or archive_path.is_symlink()
        or not archive_path.is_file()
    ):
        _die("archive must be an absolute regular non-symlink file")
    if not destination.is_absolute() or destination.is_symlink():
        _die("extraction destination must be absolute and non-symlink")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            _die("extraction destination must be a new or empty directory")
    else:
        _require_private_parent(destination)
        destination.mkdir(mode=0o700)
        _fsync_dir(destination.parent)
    info = destination.lstat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        _die("extraction destination must be private and caller-owned")

    seen: set[str] = set()
    total_size = 0
    extracted = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            _die("archive member inventory is empty or excessive")
        normalized: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in members:
            path = _safe_member_name(member.name)
            key = path.as_posix()
            if key in seen:
                _die("archive contains duplicate paths")
            seen.add(key)
            if not (member.isdir() or member.isreg()):
                _die("archive links, devices, fifos, and special files are forbidden")
            if member.size < 0:
                _die("archive member size is invalid")
            total_size += member.size
            if total_size > MAX_ARCHIVE_BYTES:
                _die("archive expanded size exceeds the canonical limit")
            normalized.append((member, path))

        kinds = {
            path.as_posix(): "directory" if member.isdir() else "file"
            for member, path in normalized
        }
        ordered_paths = sorted(kinds)
        for index, key in enumerate(ordered_paths[:-1]):
            if kinds[key] == "file" and ordered_paths[index + 1].startswith(f"{key}/"):
                _die("archive file/directory paths collide")

        for member, path in normalized:
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                if target.is_symlink() or not target.is_dir():
                    _die("archive directory collided with a non-directory")
                target.chmod(0o755)
                continue
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            if any(
                parent.is_symlink()
                for parent in target.parents
                if parent != destination.parent
            ):
                _die("archive extraction encountered a symlink parent")
            source = archive.extractfile(member)
            if source is None:
                _die("archive regular file has no payload")
            file_mode = 0o755 if member.mode & 0o111 else 0o644
            with source, _open_new_file(target, file_mode) as output:
                size, _digest = _copy_and_hash(source, output)
                output.flush()
                os.fsync(output.fileno())
            if size != member.size:
                _die("archive member size changed during extraction")
            target.chmod(file_mode)
            extracted += 1

    directories = [destination, *[p for p in destination.rglob("*") if p.is_dir()]]
    for directory in sorted(
        directories, key=lambda item: len(item.parts), reverse=True
    ):
        _fsync_dir(directory)
    _fsync_dir(destination.parent)
    print(
        json.dumps(
            {
                "status": "PASS",
                "regular_files": extracted,
                "expanded_bytes": total_size,
                "links": 0,
                "special_files": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def _tree_digest(
    root: Path, excludes: set[str], *, require_read_only: bool = False
) -> str:
    if not root.is_absolute():
        _die("release tree root must be absolute")
    try:
        root_info = root.lstat()
        resolved_root = root.resolve(strict=True)
    except OSError:
        _die("release tree root must be a regular directory")
    if not stat.S_ISDIR(root_info.st_mode) or resolved_root != Path(
        os.path.abspath(root)
    ):
        _die("release tree root must be a regular non-symlink directory")

    def checked_mode(info: os.stat_result) -> int:
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.geteuid() or info.st_gid != os.getegid():
            _die("release tree must be owned by the invoking identity")
        if mode & 0o022:
            _die("release tree must not be group/world writable")
        if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            _die("release tree contains special permission bits")
        if require_read_only and mode & 0o222:
            _die("release tree must be immutable (read-only)")
        return mode

    digest = hashlib.sha256()
    root_mode = checked_mode(root_info)
    digest.update(f"d\0.\0{root_mode:04o}\0".encode())
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        if relative in excludes:
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            _die("release tree contains a link or special file")
        kind = "d" if stat.S_ISDIR(info.st_mode) else "f"
        mode = checked_mode(info)
        digest.update(f"{kind}\0{relative}\0{mode:04o}\0".encode())
        if kind == "f":
            digest.update(str(info.st_size).encode() + b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def command_tree_sha256(args: argparse.Namespace) -> int:
    excludes = set(args.exclude or [])
    for value in excludes:
        _safe_member_name(value)
    print(
        _tree_digest(
            Path(args.root), excludes, require_read_only=args.require_read_only
        )
    )
    return 0


def _read_env(path: Path, forbidden_prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    if path.is_symlink() or not path.is_file():
        _die("env input must be a regular non-symlink file")
    info = path.lstat()
    if info.st_size > MAX_ENV_BYTES:
        _die("env input exceeds the canonical size limit")
    if info.st_mode & 0o077:
        _die("env input must be mode 0600 or stricter")
    if info.st_uid != os.geteuid():
        _die("env input must be owned by the invoking identity")
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    text = path.read_text(encoding="utf-8")
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        if raw[0].isspace() or "=" not in raw:
            _die(f"env line {number} is not a canonical assignment")
        key, value = raw.split("=", 1)
        if not ENV_KEY_RE.fullmatch(key) or key in seen:
            _die(f"env line {number} has an invalid or duplicate key")
        if any(key.startswith(prefix) for prefix in forbidden_prefixes):
            _die(f"env line {number} uses a forbidden control-plane prefix")
        if any(ord(char) < 0x20 and char != "\t" for char in value):
            _die(f"env line {number} contains control characters")
        if "`" in value or "$" in value:
            _die(f"env line {number} contains executable/interpolated syntax")
        seen.add(key)
        rows.append((key, value))
    if not rows:
        _die("env input contains no assignments")
    return rows


def command_env_validate(args: argparse.Namespace) -> int:
    rows = _read_env(Path(args.path), tuple(args.forbid_prefix or []))
    print(json.dumps({"status": "PASS", "assignments": len(rows)}, sort_keys=True))
    return 0


def _decode_env_value(value: str) -> str:
    """Decode the literal subset emitted by ``env-set`` without evaluation."""
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            _die("env contains an unterminated literal value")
        body = value[1:-1]
        output: list[str] = []
        index = 0
        while index < len(body):
            char = body[index]
            if char == "\\":
                index += 1
                if index >= len(body) or body[index] not in {"\\", "'"}:
                    _die("env contains a noncanonical literal escape")
                output.append(body[index])
            elif char == "'":
                _die("env contains an unescaped literal quote")
            else:
                output.append(char)
            index += 1
        return "".join(output)
    if re.fullmatch(r"[A-Za-z0-9._:/@,+-]*", value) is None:
        _die("env contains an ambiguous unquoted value")
    return value


def _runtime_storage_contract(
    path: Path, *, require_backup: bool
) -> tuple[str, str | None]:
    rows = dict(_read_env(path, ("OMEGA_MIGRATION_",)))
    required = (
        "LAKEHOUSE_PROVIDER",
        "LAKEHOUSE_BUCKET",
        "GCS_BUCKET",
        "S3_BUCKET_NAME",
        "MINIO_BUCKET",
    )
    if any(key not in rows for key in required):
        _die("lakehouse env contract is incomplete")
    values = {key: _decode_env_value(rows[key]) for key in required}
    bucket = values["LAKEHOUSE_BUCKET"]
    if (
        values["LAKEHOUSE_PROVIDER"] != "gcs"
        or not GCS_BUCKET_RE.fullmatch(bucket)
        or any(values[key] != bucket for key in required[2:])
    ):
        _die("lakehouse env contract is not one canonical GCS bucket")
    backup_bucket = None
    if "RELEASE_BACKUP_BUCKET" in rows:
        backup_bucket = _decode_env_value(rows["RELEASE_BACKUP_BUCKET"])
        if not GCS_BUCKET_RE.fullmatch(backup_bucket) or backup_bucket == bucket:
            _die("release backup bucket is invalid or not isolated")
    elif require_backup:
        _die("release backup env contract is missing")
    return bucket, backup_bucket


def command_lakehouse_contract(args: argparse.Namespace) -> int:
    bucket, _backup_bucket = _runtime_storage_contract(
        Path(args.path), require_backup=False
    )
    if args.expected_bucket is not None and args.expected_bucket != bucket:
        _die("requested lakehouse bucket differs from the canonical host contract")
    sys.stdout.write(bucket)
    return 0


def command_release_backup_contract(args: argparse.Namespace) -> int:
    _lakehouse_bucket, backup_bucket = _runtime_storage_contract(
        Path(args.path), require_backup=True
    )
    assert backup_bucket is not None
    if args.expected_bucket is not None and args.expected_bucket != backup_bucket:
        _die("requested backup bucket differs from the canonical host contract")
    sys.stdout.write(backup_bucket)
    return 0


def _encode_env_value(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        _die("env value contains a forbidden delimiter")
    if any(ord(char) < 0x20 for char in value):
        _die("env value contains control characters")
    if "`" in value or "$" in value:
        _die("env value contains executable/interpolated syntax")
    # Compose dotenv treats single-quoted values as literal data.  Backslash and
    # quote escaping follows compose-go/dotenv and prevents interpolation.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def command_env_set(args: argparse.Namespace) -> int:
    _require_root()
    if not ENV_KEY_RE.fullmatch(args.key) or args.key.startswith("OMEGA_MIGRATION_"):
        _die("env key is invalid or reserved for control-plane inputs")
    value = sys.stdin.read(MAX_ENV_BYTES + 1)
    if len(value.encode()) > MAX_ENV_BYTES:
        _die("env value exceeds the canonical size limit")
    path = Path(args.path)
    rows = _read_env(path, ("OMEGA_MIGRATION_",)) if path.exists() else []
    encoded = _encode_env_value(value)
    output: list[str] = []
    replaced = False
    for key, existing in rows:
        if key == args.key:
            output.append(f"{key}={encoded}")
            replaced = True
        else:
            output.append(f"{key}={existing}")
    if not replaced:
        output.append(f"{args.key}={encoded}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_parent(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return 0


def command_fsync_dir(args: argparse.Namespace) -> int:
    for raw in args.path:
        path = Path(raw)
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            _die("fsync target must be an absolute regular directory")
        _fsync_dir(path)
    return 0


def command_fsync_file(args: argparse.Namespace) -> int:
    for raw in args.path:
        path = Path(raw)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            _die("fsync target must be an absolute regular file")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return 0


def command_symlink_publish(args: argparse.Namespace) -> int:
    _require_root()
    link = Path(args.link)
    target = Path(args.target)
    if not link.is_absolute() or not target.is_absolute():
        _die("symlink publication requires absolute link and target")
    _require_private_parent(link)
    try:
        target_info = target.lstat()
    except OSError:
        _die("symlink publication target is missing")
    if not stat.S_ISDIR(target_info.st_mode) or target.is_symlink():
        _die("symlink publication target must be one real directory")
    try:
        os.symlink(target, link, target_is_directory=True)
    except FileExistsError:
        _die("symlink publication destination already exists")
    _fsync_dir(link.parent)
    return 0


DOCKER_DATA_DEVICE = Path("/dev/disk/by-id/google-omega-docker-data")
DOCKER_DATA_MOUNTPOINT = Path("/var/lib/docker")
SYSTEM_FSTAB = Path("/etc/fstab")
SYSTEM_APT_KEYRINGS = Path("/etc/apt/keyrings")
SYSTEM_APT_SOURCES = Path("/etc/apt/sources.list.d")
SYSTEM_APT_PREFERENCES = Path("/etc/apt/preferences.d")
DOCKER_DAEMON_CONFIG = Path("/etc/docker/daemon.json")
DOCKER_SERVICE_LOCALITY = Path(
    "/etc/systemd/system/docker.service.d/omega-local-runtime.conf"
)
DOCKER_SOCKET_LOCALITY = Path(
    "/etc/systemd/system/docker.socket.d/omega-local-runtime.conf"
)
DOCKER_DAEMON_CONFIG_BYTES = b'{"data-root":"/var/lib/docker","live-restore":false}\n'
DOCKER_SERVICE_LOCALITY_BYTES = (
    b"[Service]\n"
    b"ExecStart=\n"
    b"ExecStart=/usr/bin/dockerd -H fd:// "
    b"--containerd=/run/containerd/containerd.sock\n"
)
DOCKER_SOCKET_LOCALITY_BYTES = (
    b"[Socket]\n"
    b"ListenStream=\n"
    b"ListenStream=/run/docker.sock\n"
    b"SocketMode=0600\n"
    b"SocketUser=root\n"
    b"SocketGroup=root\n"
)
DOCKER_RESTART_FENCE = Path(
    "/etc/systemd/system/docker.service.d/omega-restart-policy-fence.conf"
)
DOCKER_RESTART_FENCE_BYTES = (
    b"[Service]\n"
    b"TimeoutStopSec=480s\n"
    b"ExecStop=/usr/local/sbin/omega-runtime-contract restart-policy prepare "
    b"--app-root /opt/modecissions --compose-project infra "
    b"--state /opt/modecissions/shared/restart-policy-fence.json\n"
)
DOCKER_FSTAB_ROW = (
    f"{DOCKER_DATA_DEVICE} {DOCKER_DATA_MOUNTPOINT} " "ext4 discard,defaults,nofail 0 2"
)


def _require_exact_root_directory(
    path: Path,
    *,
    mode: int,
    create: bool = False,
) -> None:
    created = False
    if create:
        try:
            parent_descriptor = os.open(
                path.parent,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.mkdir(path.name, mode=mode, dir_fd=parent_descriptor)
                created = True
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        except FileExistsError:
            pass
    if created:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != mode
        or path.resolve(strict=True) != path
    ):
        _die(f"system directory ownership/path is invalid: {path}")


def command_docker_apt_paths(_args: argparse.Namespace) -> int:
    _require_root()
    _require_exact_root_directory(Path("/etc"), mode=0o755)
    _require_exact_root_directory(Path("/etc/apt"), mode=0o755)
    _require_exact_root_directory(SYSTEM_APT_SOURCES, mode=0o755)
    _require_exact_root_directory(SYSTEM_APT_PREFERENCES, mode=0o755)
    _require_exact_root_directory(SYSTEM_APT_KEYRINGS, mode=0o755, create=True)
    return 0


def command_runtime_auth_paths(_args: argparse.Namespace) -> int:
    _require_root()
    for path in (Path("/run"), Path("/run/systemd"), Path("/run/systemd/system")):
        _require_exact_root_directory(path, mode=0o755)
    _require_exact_root_directory(
        Path("/run/systemd/system/docker.service.d"), mode=0o755, create=True
    )
    return 0


def _atomic_write_system_file(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _die("system contract write did not make progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = ""
        _fsync_dir(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _read_exact_system_file(path: Path, expected: bytes, *, mode: int) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or before.st_size != len(expected)
        ):
            _die(f"Docker locality file identity differs: {path}")
        raw = os.read(descriptor, len(expected) + 1)
        after = os.fstat(descriptor)
        if raw != expected or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die(f"Docker locality file content changed or differs: {path}")
    finally:
        os.close(descriptor)


def _bounded_command_text(command: list[str], *, label: str) -> str:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "DOCKER_HOST": "unix:///run/docker.sock",
            },
        )
    except subprocess.TimeoutExpired:
        _die(f"{label} timed out")
    if result.returncode != 0 or len(result.stdout) > 65536:
        _die(f"{label} is unreadable")
    try:
        return result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        _die(f"{label} is not UTF-8")


def _verify_docker_locality_files() -> None:
    _require_exact_root_directory(Path("/etc"), mode=0o755)
    _require_exact_root_directory(Path("/etc/docker"), mode=0o755)
    _require_exact_root_directory(Path("/etc/systemd"), mode=0o755)
    _require_exact_root_directory(Path("/etc/systemd/system"), mode=0o755)
    _require_exact_root_directory(
        Path("/etc/systemd/system/docker.service.d"), mode=0o755
    )
    _require_exact_root_directory(
        Path("/etc/systemd/system/docker.socket.d"), mode=0o755
    )
    _read_exact_system_file(
        DOCKER_DAEMON_CONFIG, DOCKER_DAEMON_CONFIG_BYTES, mode=0o644
    )
    _read_exact_system_file(
        DOCKER_SERVICE_LOCALITY, DOCKER_SERVICE_LOCALITY_BYTES, mode=0o644
    )
    _read_exact_system_file(
        DOCKER_SOCKET_LOCALITY, DOCKER_SOCKET_LOCALITY_BYTES, mode=0o644
    )
    _read_exact_system_file(
        DOCKER_RESTART_FENCE, DOCKER_RESTART_FENCE_BYTES, mode=0o644
    )


def _systemctl_properties(unit: str, names: tuple[str, ...]) -> dict[str, str]:
    output = _bounded_command_text(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            f"--property={','.join(names)}",
            "--no-pager",
        ],
        label=f"effective systemd properties for {unit}",
    )
    values: dict[str, str] = {}
    for row in output.splitlines():
        key, separator, value = row.partition("=")
        if not separator or key not in names or key in values:
            _die(f"effective systemd property output is malformed: {unit}")
        values[key] = value
    if set(values) != set(names):
        _die(f"effective systemd property inventory is incomplete: {unit}")
    return values


def _systemd_argv(value: str) -> list[str]:
    return re.findall(r"argv\[\]=([^;]+?)\s*;", value)


def _verify_docker_systemd_locality() -> None:
    service = _systemctl_properties(
        "docker.service",
        (
            "ExecStart",
            "ExecStartPre",
            "ExecStartPost",
            "ExecStop",
            "Environment",
            "EnvironmentFiles",
            "PassEnvironment",
            "User",
            "Group",
            "FragmentPath",
            "DropInPaths",
        ),
    )
    expected_start = (
        "/usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock"
    )
    if (
        _systemd_argv(service["ExecStart"]) != [expected_start]
        or "path=/usr/bin/dockerd" not in service["ExecStart"]
        or _systemd_argv(service["ExecStartPre"])
        != [
            "/usr/local/sbin/omega-metadata-firewall enforce",
            "/usr/local/sbin/omega-operation-gate authorize-start",
        ]
        or _systemd_argv(service["ExecStartPost"])
        or _systemd_argv(service["ExecStop"])
        != [
            "/usr/local/sbin/omega-runtime-contract restart-policy prepare "
            "--app-root /opt/modecissions --compose-project infra "
            "--state /opt/modecissions/shared/restart-policy-fence.json"
        ]
        or service["EnvironmentFiles"]
        or service["PassEnvironment"]
        or service["User"] not in {"", "root"}
        or service["Group"] not in {"", "root"}
        or service["FragmentPath"]
        not in {
            "/lib/systemd/system/docker.service",
            "/usr/lib/systemd/system/docker.service",
        }
    ):
        _die("effective Docker service command/hooks/identity are not exact")
    expected_dropins = {
        "/etc/systemd/system/docker.service.d/omega-local-runtime.conf",
        "/etc/systemd/system/docker.service.d/omega-metadata-firewall.conf",
        "/etc/systemd/system/docker.service.d/omega-operation-gate.conf",
        "/etc/systemd/system/docker.service.d/omega-restart-policy-fence.conf",
    }
    volatile_dropins = {
        "/run/systemd/system/docker.service.d/omega-initial-bootstrap.conf",
        "/run/systemd/system/docker.service.d/omega-reboot-recovery.conf",
    }
    try:
        observed_dropins = set(shlex.split(service["DropInPaths"]))
        environment = set(shlex.split(service["Environment"]))
    except ValueError:
        _die("effective Docker service environment/drop-ins are malformed")
    active_volatile = observed_dropins & volatile_dropins
    if (
        not expected_dropins.issubset(observed_dropins)
        or observed_dropins - expected_dropins - volatile_dropins
        or len(active_volatile) > 1
    ):
        _die("effective Docker service drop-in inventory is not exact")
    allowed_environments = {
        frozenset(),
        frozenset(
            {
                "OMEGA_GCP_INITIAL_BOOTSTRAP=1",
                "OMEGA_GCP_ALLOW_OPERATION_MARKER=1",
                "OMEGA_GCP_RUNTIME_START_AUTHORIZED=1",
            }
        ),
        frozenset(
            {
                "OMEGA_GCP_ALLOW_OPERATION_MARKER=1",
                "OMEGA_GCP_RUNTIME_START_AUTHORIZED=1",
            }
        ),
        frozenset(
            {
                "OMEGA_GCP_ALLOW_OPERATION_MARKER=1",
                "OMEGA_GCP_RUNTIME_START_AUTHORIZED=1",
                "OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE=1",
            }
        ),
    }
    if frozenset(environment) not in allowed_environments:
        _die("effective Docker service environment is not allowlisted")

    socket = _systemctl_properties(
        "docker.socket",
        (
            "Listen",
            "SocketMode",
            "SocketUser",
            "SocketGroup",
            "Service",
            "FragmentPath",
            "DropInPaths",
        ),
    )
    normalized = socket["Listen"].replace("Stream:", "").replace(" (Stream)", "")
    if (
        normalized != "/run/docker.sock"
        or "\n" in socket["Listen"]
        or "tcp" in socket["Listen"].lower()
        or socket["SocketMode"] not in {"0600", "600"}
        or socket["SocketUser"] != "root"
        or socket["SocketGroup"] != "root"
        or socket["Service"] not in {"", "docker.service"}
        or socket["FragmentPath"]
        not in {
            "/lib/systemd/system/docker.socket",
            "/usr/lib/systemd/system/docker.socket",
        }
        or set(shlex.split(socket["DropInPaths"])) != {str(DOCKER_SOCKET_LOCALITY)}
    ):
        _die("effective Docker socket listener/identity is not exact/local")


def _verify_live_docker_socket() -> None:
    run = Path("/run")
    socket_path = Path("/run/docker.sock")
    _require_exact_root_directory(run, mode=0o755)
    info = socket_path.lstat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or socket_path.is_symlink()
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or socket_path.resolve(strict=True) != socket_path
    ):
        _die("live Docker socket identity/permissions are invalid")
    root_dir = _bounded_command_text(
        [
            "/usr/bin/docker",
            "--host",
            "unix:///run/docker.sock",
            "info",
            "--format",
            "{{json .DockerRootDir}}",
        ],
        label="live Docker root directory",
    )
    if root_dir != '"/var/lib/docker"':
        _die("live Docker root directory differs from /var/lib/docker")


def command_docker_runtime(args: argparse.Namespace) -> int:
    _require_root()
    action = args.runtime_action
    if action == "verify-socket":
        _verify_live_docker_socket()
        return 0
    if action == "install-contract":
        _require_exact_root_directory(Path("/etc"), mode=0o755)
        _require_exact_root_directory(Path("/etc/docker"), mode=0o755, create=True)
        _require_exact_root_directory(Path("/etc/systemd"), mode=0o755)
        _require_exact_root_directory(Path("/etc/systemd/system"), mode=0o755)
        _require_exact_root_directory(
            Path("/etc/systemd/system/docker.service.d"), mode=0o755, create=True
        )
        _require_exact_root_directory(
            Path("/etc/systemd/system/docker.socket.d"), mode=0o755, create=True
        )
        _atomic_write_system_file(
            DOCKER_DAEMON_CONFIG, DOCKER_DAEMON_CONFIG_BYTES, mode=0o644
        )
        _atomic_write_system_file(
            DOCKER_SERVICE_LOCALITY, DOCKER_SERVICE_LOCALITY_BYTES, mode=0o644
        )
        _atomic_write_system_file(
            DOCKER_SOCKET_LOCALITY, DOCKER_SOCKET_LOCALITY_BYTES, mode=0o644
        )
        return 0
    _verify_docker_locality_files()
    _verify_docker_systemd_locality()
    if action == "verify-prestart":
        return 0
    if action != "verify-live":
        _die("Docker runtime locality action is invalid")
    _verify_live_docker_socket()
    return 0


def _bounded_json_command(
    command: list[str], *, label: str, not_found_is_empty: bool = False
) -> object:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
    except subprocess.TimeoutExpired:
        _die(f"{label} timed out")
    if not_found_is_empty and result.returncode == 1 and not result.stdout:
        return {"filesystems": []}
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        _die(f"{label} is unreadable")
    try:
        return json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _die(f"{label} is malformed")


def _validate_unused_block_inventory(payload: object, *, expected: Path) -> None:
    rows = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        _die("Docker data block inventory is not exact")
    row = rows[0]
    mountpoints = row.get("mountpoints")
    if mountpoints is None:
        legacy_mountpoint = row.get("mountpoint")
        mountpoints = [] if legacy_mountpoint is None else [legacy_mountpoint]
    if (
        row.get("path") != str(expected)
        or row.get("type") != "disk"
        or row.get("pkname") not in (None, "")
        or row.get("children") not in (None, [])
        or not isinstance(mountpoints, list)
        or any(value not in (None, "") for value in mountpoints)
    ):
        _die("Docker data device is mounted, partitioned, layered, or not one raw disk")


def _verify_docker_device_unused(
    *,
    device: Path = DOCKER_DATA_DEVICE,
    sys_dev_block: Path = Path("/sys/dev/block"),
    proc_swaps: Path = Path("/proc/swaps"),
) -> None:
    try:
        device_info = device.stat()
        resolved = device.resolve(strict=True)
    except OSError:
        _die("Docker data device is missing")
    if not stat.S_ISBLK(device_info.st_mode) or not str(resolved).startswith("/dev/"):
        _die("Docker data device is not one canonical block device")
    payload = _bounded_json_command(
        [
            "/usr/bin/lsblk",
            "--bytes",
            "--json",
            "--paths",
            "--output",
            "PATH,TYPE,PKNAME,MOUNTPOINTS",
            str(resolved),
        ],
        label="Docker data block inventory",
    )
    _validate_unused_block_inventory(payload, expected=resolved)
    holders = (
        sys_dev_block
        / f"{os.major(device_info.st_rdev)}:{os.minor(device_info.st_rdev)}"
        / "holders"
    )
    try:
        if any(holders.iterdir()):
            _die("Docker data device has active holders")
    except OSError:
        _die("Docker data device holder inventory is unreadable")
    try:
        swap_rows = proc_swaps.read_text(encoding="utf-8").splitlines()
    except OSError:
        _die("swap inventory is unreadable")
    if not swap_rows or swap_rows[0].split() != [
        "Filename",
        "Type",
        "Size",
        "Used",
        "Priority",
    ]:
        _die("swap inventory header is malformed")
    for row in swap_rows[1:]:
        fields = row.split()
        if (
            len(fields) != 5
            or fields[1] not in {"file", "partition"}
            or not all(value.lstrip("-").isdigit() for value in fields[2:])
        ):
            _die("swap inventory is malformed")
        try:
            swap_source = Path(fields[0]).resolve(strict=True)
        except OSError:
            _die("swap source identity is unreadable")
        if swap_source == resolved:
            _die("Docker data device is active swap")
    source_payload = _bounded_json_command(
        [
            "/usr/bin/findmnt",
            "--json",
            "--source",
            str(resolved),
            "--output",
            "SOURCE,TARGET,FSTYPE",
        ],
        label="Docker data source mount inventory",
        not_found_is_empty=True,
    )
    source_rows = (
        source_payload.get("filesystems") if isinstance(source_payload, dict) else None
    )
    if source_rows not in (None, []):
        _die("Docker data device is already mounted at another target")


def command_ubuntu_codename(_args: argparse.Namespace) -> int:
    _require_root()
    path = Path("/usr/lib/os-release")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 32768
        ):
            _die("Ubuntu release descriptor identity is invalid")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die("Ubuntu release identity changed during authoritative read")
    finally:
        os.close(descriptor)
    try:
        rows = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        _die("Ubuntu release data is not UTF-8")
    values: dict[str, str] = {}
    for row in rows:
        if not row or row.startswith("#"):
            continue
        key, separator, value = row.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or not 1 <= len(value) <= 1024
            or any(ord(character) < 0x20 for character in value)
        ):
            _die("Ubuntu release data is malformed")
        quoted = value.startswith('"') or value.endswith('"')
        if quoted:
            if (
                not (value.startswith('"') and value.endswith('"'))
                or len(value) < 2
                or "\\" in value[1:-1]
                or '"' in value[1:-1]
            ):
                _die("Ubuntu release data contains malformed quoting")
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:
            _die("Ubuntu release scalar grammar is invalid")
        values[key] = value
    if (
        values.get("ID") != "ubuntu"
        or values.get("VERSION_ID") != "22.04"
        or values.get("VERSION_CODENAME") != "jammy"
    ):
        _die("host OS is not the reviewed Ubuntu 22.04 jammy contract")
    sys.stdout.write("jammy")
    return 0


def _validate_docker_storage_paths(
    *,
    mountpoint: Path,
    fstab: Path,
    owner_uid: int,
    owner_gid: int,
    create_mountpoint: bool,
) -> None:
    if create_mountpoint:
        parent = mountpoint.parent
        parent_info = parent.lstat()
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent.is_symlink()
            or parent_info.st_uid != owner_uid
            or parent_info.st_gid != owner_gid
            or stat.S_IMODE(parent_info.st_mode) & 0o022
            or parent.resolve(strict=True) != parent
        ):
            _die("Docker data mountpoint parent ownership/path is invalid")
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        created = False
        try:
            try:
                os.mkdir(mountpoint.name, mode=0o755, dir_fd=parent_descriptor)
                created = True
                os.fsync(parent_descriptor)
            except FileExistsError:
                pass
        finally:
            os.close(parent_descriptor)
        if created:
            mount_descriptor = os.open(
                mountpoint,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                # The root-owned Docker mountpoint must be traversable by the
                # daemon; it contains no secret bytes and its parent was just
                # validated through an O_NOFOLLOW descriptor.
                os.fchmod(mount_descriptor, 0o755)  # nosec B103
                os.fsync(mount_descriptor)
            finally:
                os.close(mount_descriptor)
    mount_info = mountpoint.lstat()
    fstab_info = fstab.lstat()
    if (
        not stat.S_ISDIR(mount_info.st_mode)
        or mountpoint.is_symlink()
        or mount_info.st_uid != owner_uid
        or mount_info.st_gid != owner_gid
        or stat.S_IMODE(mount_info.st_mode) != 0o755
        or mountpoint.resolve(strict=True) != mountpoint
    ):
        _die("Docker data mountpoint ownership/path is invalid")
    if (
        not stat.S_ISREG(fstab_info.st_mode)
        or fstab.is_symlink()
        or fstab_info.st_uid != owner_uid
        or fstab_info.st_gid != owner_gid
        or stat.S_IMODE(fstab_info.st_mode) != 0o644
        or fstab_info.st_nlink != 1
        or fstab.resolve(strict=True) != fstab
    ):
        _die("fstab ownership/path is invalid")


def _reconcile_docker_fstab(
    *,
    fstab: Path,
    device: Path,
    mountpoint: Path,
    append_missing: bool,
) -> None:
    flags = os.O_RDWR if append_missing else os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(fstab, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1
            or info.st_size > 1024 * 1024
        ):
            _die("fstab descriptor identity is invalid")
        raw = os.pread(descriptor, info.st_size, 0)
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _die("fstab is not UTF-8")
        if "\x00" in text:
            _die("fstab contains a forbidden delimiter")
        canonical = f"{device} {mountpoint} ext4 discard,defaults,nofail 0 2"
        relevant: list[str] = []
        for row in text.splitlines():
            stripped = row.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) >= 2 and (
                fields[0] == str(device) or fields[1] == str(mountpoint)
            ):
                relevant.append(row)
        if relevant == [canonical]:
            return
        if relevant:
            _die("fstab Docker data inventory is duplicated or noncanonical")
        if not append_missing:
            _die("fstab canonical Docker data row is missing")
        suffix = (b"" if not raw or raw.endswith(b"\n") else b"\n") + (
            canonical + "\n"
        ).encode()
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(suffix)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _die("fstab append did not make progress")
            view = view[written:]
        os.fsync(descriptor)
        _fsync_dir(fstab.parent)
    finally:
        os.close(descriptor)


def _findmnt_docker_row(mountpoint: Path) -> dict[str, str] | None:
    payload = _bounded_json_command(
        [
            "/usr/bin/findmnt",
            "--json",
            "--mountpoint",
            str(mountpoint),
            "--output",
            "SOURCE,TARGET,FSTYPE",
        ],
        label="Docker data mount inventory",
        not_found_is_empty=True,
    )
    rows = payload.get("filesystems") if isinstance(payload, dict) else None
    if rows == []:
        return None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        _die("Docker data mount inventory is not exact")
    return {str(key): str(value) for key, value in rows[0].items()}


def command_docker_storage(args: argparse.Namespace) -> int:
    _require_root()
    action = args.storage_action
    _validate_docker_storage_paths(
        mountpoint=DOCKER_DATA_MOUNTPOINT,
        fstab=SYSTEM_FSTAB,
        owner_uid=0,
        owner_gid=0,
        create_mountpoint=action == "paths",
    )
    if action == "paths":
        return 0
    if action == "verify-unused":
        _verify_docker_device_unused()
        return 0
    _reconcile_docker_fstab(
        fstab=SYSTEM_FSTAB,
        device=DOCKER_DATA_DEVICE,
        mountpoint=DOCKER_DATA_MOUNTPOINT,
        append_missing=action == "prepare",
    )
    row = _findmnt_docker_row(DOCKER_DATA_MOUNTPOINT)
    if action == "prepare":
        if row is not None or any(DOCKER_DATA_MOUNTPOINT.iterdir()):
            _die("Docker data mountpoint is mounted or non-empty before mount")
        return 0
    if action != "verify-mounted" or row is None:
        _die("Docker data mount is missing")
    source = Path(row.get("source", ""))
    target = row.get("target")
    fstype = row.get("fstype")
    try:
        source_info = source.stat()
        source_resolved = source.resolve(strict=True)
        device_resolved = DOCKER_DATA_DEVICE.resolve(strict=True)
    except OSError:
        _die("Docker data mount source is missing")
    if (
        not stat.S_ISBLK(source_info.st_mode)
        or source_resolved != device_resolved
        or target != str(DOCKER_DATA_MOUNTPOINT)
        or fstype != "ext4"
    ):
        _die("Docker data mount source/target/filesystem differs")
    return 0


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        _die(f"startup {label} keys differ from the canonical contract")
    return value


def _verify_directory(descriptor: int, *, mode: int, label: str) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != mode
    ):
        _die(f"unsafe canonical directory: {label}")


def _open_canonical_shared(*, create: bool) -> tuple[int, int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    opt = os.open(CANONICAL_OPT_ROOT, flags)
    try:
        _verify_directory(opt, mode=0o755, label=str(CANONICAL_OPT_ROOT))
        if create:
            try:
                os.mkdir("modecissions", 0o755, dir_fd=opt)
                os.fsync(opt)
            except FileExistsError:
                pass
        app = os.open("modecissions", flags, dir_fd=opt)
        try:
            _verify_directory(app, mode=0o755, label="/opt/modecissions")
            if create:
                try:
                    os.mkdir("shared", 0o755, dir_fd=app)
                    os.fsync(app)
                except FileExistsError:
                    pass
            shared = os.open("shared", flags, dir_fd=app)
            try:
                _verify_directory(
                    shared, mode=0o755, label=str(CANONICAL_EVIDENCE_ROOT)
                )
                return opt, app, shared
            except BaseException:
                os.close(shared)
                raise
        except BaseException:
            os.close(app)
            raise
    except BaseException:
        os.close(opt)
        raise


def command_evidence_root(_args: argparse.Namespace) -> int:
    """Create and verify the canonical host evidence root by descriptor."""
    _require_root()
    descriptors = _open_canonical_shared(create=True)
    for descriptor in reversed(descriptors):
        os.close(descriptor)
    return 0


def _systemctl_property(unit: str, property_name: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            f"--property={property_name}",
            "--value",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=10,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode != 0 or len(result.stdout) > 128:
        _die("operation owner unit state is unavailable")
    try:
        return result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        _die("operation owner unit state is malformed")


def _owner_cgroup_empty(path: Path = OWNER_CGROUP) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    try:
        raw = _read_nofollow_text(path / "cgroup.events", limit=4096)
        rows = dict(
            row.split(maxsplit=1)
            for row in raw.splitlines()
            if len(row.split(maxsplit=1)) == 2
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return rows.get("populated") == "0"


def command_owner_unit_empty(_args: argparse.Namespace) -> int:
    """Prove the fixed transient owner is stopped and its cgroup is empty."""
    _require_root()
    load = _systemctl_property(OWNER_UNIT, "LoadState")
    if load != "not-found":
        values = tuple(
            _systemctl_property(OWNER_UNIT, name)
            for name in ("ActiveState", "SubState", "MainPID", "ControlPID")
        )
        if values != ("inactive", "dead", "0", "0"):
            return 1
    return 0 if _owner_cgroup_empty() else 1


def _terminal_receipt_value(
    raw: bytes,
    *,
    deploy_ref: str,
    helper_ref: str,
    startup_contract_sha256: str,
) -> tuple[dict, bytes]:
    payload = _strict_json(
        raw,
        label="startup terminal receipt",
    )
    state = payload.get("terminal_state")
    version = payload.get("version")
    try:
        completed = datetime.fromisoformat(str(payload["completed_at"]))
    except (KeyError, TypeError, ValueError):
        _die("startup terminal receipt completion time is invalid")
    if (
        set(payload)
        != {
            "schema_version",
            "terminal_state",
            "deploy_ref",
            "version",
            "helper_ref",
            "startup_contract_sha256",
            "completed_at",
            "foundation_marker_sha256",
            "foundation_watchdog_state_sha256",
        }
        or payload.get("schema_version") != 1
        or state not in {"foundation-fenced", "live-recovered", "live-bootstrapped"}
        or payload.get("deploy_ref") != deploy_ref
        or payload.get("helper_ref") != helper_ref
        or payload.get("startup_contract_sha256") != startup_contract_sha256
        or completed.tzinfo is None
        or completed.utcoffset() != timezone.utc.utcoffset(completed)
        or (state == "foundation-fenced" and version != "")
        or (
            state == "foundation-fenced"
            and (
                SHA256_RE.fullmatch(str(payload.get("foundation_marker_sha256", "")))
                is None
                or SHA256_RE.fullmatch(
                    str(payload.get("foundation_watchdog_state_sha256", ""))
                )
                is None
            )
        )
        or (
            state != "foundation-fenced"
            and (
                payload.get("foundation_marker_sha256") != ""
                or payload.get("foundation_watchdog_state_sha256") != ""
                or re.fullmatch(
                    r"[0-9]+[.][0-9]+[.][0-9]+(?:-[0-9A-Za-z.-]+)?",
                    str(version),
                )
                is None
            )
        )
    ):
        _die("startup terminal receipt differs from the exact contract")
    return payload, (
        "\t".join(
            (
                str(state),
                str(payload["foundation_marker_sha256"]),
                str(payload["foundation_watchdog_state_sha256"]),
            )
        )
        + "\n"
    ).encode()


def _terminal_receipt(
    path: Path,
    *,
    deploy_ref: str,
    helper_ref: str,
    startup_contract_sha256: str,
) -> tuple[dict, bytes]:
    return _terminal_receipt_value(
        _exact_file_bytes(path, mode=0o600, maximum=4096),
        deploy_ref=deploy_ref,
        helper_ref=helper_ref,
        startup_contract_sha256=startup_contract_sha256,
    )


def command_terminal_receipt(args: argparse.Namespace) -> int:
    """Validate and emit the bounded terminal receipt projection."""
    _require_root()
    if (
        FULL_SHA_RE.fullmatch(args.deploy_ref) is None
        or FULL_SHA_RE.fullmatch(args.helper_ref) is None
        or SHA256_RE.fullmatch(args.startup_contract_sha256) is None
    ):
        _die("terminal receipt expected identity is invalid")
    _payload, projection = _terminal_receipt(
        Path(args.path),
        deploy_ref=args.deploy_ref,
        helper_ref=args.helper_ref,
        startup_contract_sha256=args.startup_contract_sha256,
    )
    sys.stdout.buffer.write(projection)
    return 0


def _metadata_attribute(path: str, *, maximum: int) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", path) or path.startswith("/"):
        _die("metadata attribute path is invalid")
    request = urllib.request.Request(
        f"{METADATA_ROOT}/{path}", headers={"Metadata-Flavor": "Google"}
    )
    try:
        with _direct_opener().open(request, timeout=10) as response:  # nosec B310
            headers = getattr(response, "headers", None)
            if headers is None or headers.get("Metadata-Flavor") != "Google":
                _die("metadata response lacks exact Metadata-Flavor")
            return _response_bytes(
                response, maximum=maximum, label="metadata attribute"
            )
    except (OSError, urllib.error.URLError, TimeoutError):
        _die("metadata attribute is unavailable")


def _ascii_metadata(path: str, *, maximum: int, label: str) -> str:
    try:
        value = _metadata_attribute(path, maximum=maximum).decode(
            "ascii", errors="strict"
        )
    except UnicodeDecodeError:
        _die(f"metadata {label} is not ASCII")
    if not value or value != value.strip():
        _die(f"metadata {label} is not canonical")
    return value


def _live_host_identity() -> dict[str, object]:
    zone_path = _ascii_metadata("instance/zone", maximum=256, label="zone")
    zones = re.fullmatch(
        r"projects/[1-9][0-9]*/zones/([a-z](?:[-a-z0-9]{0,61}[a-z0-9])?)",
        zone_path,
    )
    value: dict[str, object] = {
        "schema_version": 1,
        "project_id": _ascii_metadata(
            "project/project-id", maximum=64, label="project ID"
        ),
        "instance_id": _ascii_metadata("instance/id", maximum=64, label="instance ID"),
        "instance_name": _ascii_metadata(
            "instance/name", maximum=64, label="instance name"
        ),
        "zone": zones.group(1) if zones else "",
        "service_account_email": _ascii_metadata(
            "instance/service-accounts/default/email",
            maximum=512,
            label="service-account email",
        ),
    }
    if (
        not PROJECT_RE.fullmatch(str(value["project_id"]))
        or re.fullmatch(r"[1-9][0-9]{0,19}", str(value["instance_id"])) is None
        or re.fullmatch(
            r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?",
            str(value["instance_name"]),
        )
        is None
        or not value["zone"]
        or re.fullmatch(
            r"[a-z][a-z0-9-]{2,62}@[a-z][a-z0-9-]{4,28}[a-z0-9][.]"
            r"iam[.]gserviceaccount[.]com",
            str(value["service_account_email"]),
        )
        is None
    ):
        _die("live GCP host identity is invalid")
    return value


def command_host_identity_install(args: argparse.Namespace) -> int:
    """Publish one root-owned identity only after exact metadata read-back."""
    _require_root()
    source = Path(args.startup_contract)
    output = Path(args.output)
    if (
        output != HOST_IDENTITY_PATH
        or not source.is_absolute()
        or re.fullmatch(
            r"/run/omega-gcp-bootstrap[.][A-Za-z0-9]+/startup-contract[.]json",
            str(source),
        )
        is None
    ):
        _die("host identity paths are not canonical")
    owner_uid = os.geteuid()
    owner_gid = os.getegid()
    contract_raw = _exact_file_bytes(
        source,
        mode=0o600,
        maximum=1024 * 1024,
        uid=owner_uid,
        gid=owner_gid,
    )
    contract = _strict_json(contract_raw, label="startup identity contract")
    expected = contract.get("host_identity")
    if not isinstance(expected, dict) or set(expected) != HOST_IDENTITY_KEYS - {
        "schema_version"
    }:
        _die("startup host identity shape differs")
    expected_value: dict[str, object] = {"schema_version": 1, **expected}
    before = _live_host_identity()
    if before != expected_value:
        _die("live GCP host identity differs from Terraform authority")
    raw = (
        json.dumps(expected_value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()

    parent = output.parent
    etc = parent.parent
    etc_info = etc.lstat()
    if (
        stat.S_ISLNK(etc_info.st_mode)
        or not stat.S_ISDIR(etc_info.st_mode)
        or etc_info.st_uid != owner_uid
        or etc_info.st_gid != owner_gid
        or stat.S_IMODE(etc_info.st_mode) & 0o022
    ):
        _die("canonical /etc identity differs")
    try:
        os.mkdir(parent, 0o755)
        _fsync_dir(etc)
    except FileExistsError:
        pass
    parent_info = parent.lstat()
    if (
        stat.S_ISLNK(parent_info.st_mode)
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != owner_uid
        or parent_info.st_gid != owner_gid
        or stat.S_IMODE(parent_info.st_mode) != 0o755
    ):
        _die("canonical host identity directory differs")

    try:
        existing = _exact_file_bytes(
            output,
            mode=0o400,
            maximum=4096,
            uid=owner_uid,
            gid=owner_gid,
        )
    except FileNotFoundError:
        existing = b""
    if existing:
        if existing != raw:
            _die("existing host identity conflicts with Terraform authority")
    else:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=parent)
        try:
            os.fchmod(descriptor, 0o400)
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    _die("host identity write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary, output, follow_symlinks=False)
            except FileExistsError:
                concurrent = _exact_file_bytes(
                    output,
                    mode=0o400,
                    maximum=4096,
                    uid=owner_uid,
                    gid=owner_gid,
                )
                if concurrent != raw:
                    _die("concurrent host identity publication conflicts")
            os.unlink(temporary)
            temporary = ""
            _fsync_dir(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    if _live_host_identity() != before:
        _die("live GCP host identity changed during publication")
    if (
        _exact_file_bytes(
            output,
            mode=0o400,
            maximum=4096,
            uid=owner_uid,
            gid=owner_gid,
        )
        != raw
    ):
        _die("host identity durable read-back differs")
    print(
        json.dumps(
            {
                "path": str(output),
                "schema_version": 1,
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _virtual_text(path: Path, *, maximum: int, label: str) -> str:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
            _die(f"{label} descriptor identity differs")
        raw = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        if len(raw) > maximum or (before.st_dev, before.st_ino) != (
            after.st_dev,
            after.st_ino,
        ):
            _die(f"{label} changed during read")
    finally:
        os.close(descriptor)
    try:
        return raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        _die(f"{label} is not ASCII")


def _foundation_marker(
    raw: bytes, *, deploy_ref: str, helper_ref: str, contract: str
) -> None:
    marker = _strict_json(raw, label="foundation marker")
    helper_names = {
        "bootstrap_runtime",
        "safe_io",
        "metadata_firewall",
        "operation_gate",
        "operation_watchdog",
        "reboot_runtime",
        "runtime_contract",
    }
    helpers = marker.get("helper_sha256")
    if (
        set(marker)
        != {
            "schema_version",
            "operation",
            "state",
            "deploy_ref",
            "helper_ref",
            "updated_at",
            "startup_contract_sha256",
            "helper_sha256",
        }
        or marker.get("schema_version") != 2
        or marker.get("operation") != "foundation-ready"
        or marker.get("state") != "awaiting-runtime-authority"
        or marker.get("deploy_ref") != deploy_ref
        or marker.get("helper_ref") != helper_ref
        or marker.get("startup_contract_sha256") != contract
        or not isinstance(helpers, dict)
        or set(helpers) != helper_names
        or any(SHA256_RE.fullmatch(str(value)) is None for value in helpers.values())
    ):
        _die("foundation marker differs from the exact handoff contract")
    try:
        updated = datetime.fromisoformat(str(marker["updated_at"]))
    except (TypeError, ValueError):
        _die("foundation marker timestamp is invalid")
    if updated.tzinfo is None or updated.utcoffset() != timezone.utc.utcoffset(updated):
        _die("foundation marker timestamp is invalid")


def _live_boot_identity() -> tuple[str, int]:
    boot_id = _virtual_text(PROC_BOOT_ID, maximum=128, label="kernel boot ID").strip()
    stat_rows = _virtual_text(
        PROC_STAT, maximum=1024 * 1024, label="kernel stat"
    ).splitlines()
    btime = [row.split()[1] for row in stat_rows if re.fullmatch(r"btime [0-9]+", row)]
    if (
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            boot_id,
        )
        is None
        or len(btime) != 1
        or re.fullmatch(r"[1-9][0-9]{0,19}", btime[0]) is None
    ):
        _die("host boot identity is invalid")
    return boot_id, int(btime[0])


def _foundation_receipt_payload(args: argparse.Namespace) -> dict[str, object]:
    terminal_path = Path(args.terminal_receipt)
    terminal_raw = _exact_file_bytes(terminal_path, mode=0o600, maximum=4096)
    terminal, _projection = _terminal_receipt_value(
        terminal_raw,
        deploy_ref=args.deploy_ref,
        helper_ref=args.helper_ref,
        startup_contract_sha256=args.startup_contract_sha256,
    )
    if terminal["terminal_state"] != "foundation-fenced":
        _die("durable foundation receipt requires a foundation-fenced terminal")
    marker_path = Path(args.operation_marker)
    marker_raw = _exact_file_bytes(marker_path, mode=0o600, maximum=32768)
    state_raw = _exact_file_bytes(WATCHDOG_STATE, mode=0o600, maximum=32768)
    marker_sha = hashlib.sha256(marker_raw).hexdigest()
    state_sha = hashlib.sha256(state_raw).hexdigest()
    if (
        marker_sha != terminal["foundation_marker_sha256"]
        or state_sha != terminal["foundation_watchdog_state_sha256"]
    ):
        _die("foundation evidence differs from terminal receipt")
    _foundation_marker(
        marker_raw,
        deploy_ref=args.deploy_ref,
        helper_ref=args.helper_ref,
        contract=args.startup_contract_sha256,
    )
    startup_raw = _metadata_attribute(
        "instance/attributes/startup-script", maximum=256 * 1024
    )
    instance_id = _metadata_attribute("instance/id", maximum=64).decode(
        "ascii", errors="strict"
    )
    zone_path = _metadata_attribute("instance/zone", maximum=256).decode(
        "ascii", errors="strict"
    )
    boot_id, boot_started_epoch = _live_boot_identity()
    if (
        re.fullmatch(r"[1-9][0-9]{0,19}", instance_id) is None
        or re.fullmatch(r"projects/[1-9][0-9]*/zones/[a-z0-9-]+", zone_path) is None
    ):
        _die("host metadata identity is invalid")
    return {
        "schema_version": 1,
        "state": "foundation-fenced",
        "deploy_ref": args.deploy_ref,
        "source_sha": args.deploy_ref,
        "helper_ref": args.helper_ref,
        "controller_ref": args.helper_ref,
        "startup_contract_sha256": args.startup_contract_sha256,
        "live_startup_script_sha256": hashlib.sha256(startup_raw).hexdigest(),
        "foundation_marker_sha256": marker_sha,
        "foundation_watchdog_state_sha256": state_sha,
        "terminal_receipt_sha256": hashlib.sha256(terminal_raw).hexdigest(),
        "instance_id": instance_id,
        "zone": zone_path.rsplit("/", 1)[-1],
        "boot_id": boot_id,
        "boot_started_epoch": boot_started_epoch,
        "completed_at": terminal["completed_at"],
    }


FOUNDATION_RECEIPT_KEYS = {
    "schema_version",
    "state",
    "deploy_ref",
    "source_sha",
    "helper_ref",
    "controller_ref",
    "startup_contract_sha256",
    "live_startup_script_sha256",
    "foundation_marker_sha256",
    "foundation_watchdog_state_sha256",
    "terminal_receipt_sha256",
    "instance_id",
    "zone",
    "boot_id",
    "boot_started_epoch",
    "completed_at",
}


def _validate_foundation_receipt(
    value: object,
    *,
    deploy_ref: str,
    helper_ref: str,
    startup_contract_sha256: str,
) -> dict:
    if not isinstance(value, dict) or set(value) != FOUNDATION_RECEIPT_KEYS:
        _die("durable foundation receipt shape differs")
    sha_fields = (
        "startup_contract_sha256",
        "live_startup_script_sha256",
        "foundation_marker_sha256",
        "foundation_watchdog_state_sha256",
        "terminal_receipt_sha256",
    )
    string_fields = FOUNDATION_RECEIPT_KEYS - {"schema_version", "boot_started_epoch"}
    if any(type(value[name]) is not str for name in string_fields):
        _die("durable foundation receipt value types differ")
    try:
        completed = datetime.fromisoformat(value["completed_at"])
    except (TypeError, ValueError):
        _die("durable foundation receipt timestamp is invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["state"] != "foundation-fenced"
        or value["deploy_ref"] != deploy_ref
        or value["source_sha"] != deploy_ref
        or value["helper_ref"] != helper_ref
        or value["controller_ref"] != helper_ref
        or value["startup_contract_sha256"] != startup_contract_sha256
        or any(SHA256_RE.fullmatch(value[name]) is None for name in sha_fields)
        or re.fullmatch(r"[1-9][0-9]{0,19}", value["instance_id"]) is None
        or re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", value["zone"]) is None
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value["boot_id"],
        )
        is None
        or type(value["boot_started_epoch"]) is not int
        or value["boot_started_epoch"] < 1
        or completed.tzinfo is None
        or completed.utcoffset() != timezone.utc.utcoffset(completed)
        or completed.timestamp() < value["boot_started_epoch"]
    ):
        _die("durable foundation receipt identity differs")
    return value


def _read_dir_member(
    directory: int, name: str, *, maximum: int, allowed_links: set[int] | None = None
) -> tuple[bytes, os.stat_result]:
    if allowed_links is None:
        allowed_links = {1}
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory,
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink not in allowed_links
            or not 1 <= before.st_size <= maximum
        ):
            _die("durable receipt descriptor identity differs")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die("durable receipt changed during read")
        return raw, after
    finally:
        os.close(descriptor)


def _publish_foundation_receipt(value: dict[str, object]) -> tuple[Path, bytes]:
    descriptors = _open_canonical_shared(create=False)
    shared = descriptors[-1]
    receipts = -1
    try:
        try:
            os.mkdir("foundation-receipts", 0o700, dir_fd=shared)
            os.fsync(shared)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        receipts = os.open("foundation-receipts", flags, dir_fd=shared)
        _verify_directory(receipts, mode=0o700, label="foundation receipts")
        name = f"{value['helper_ref']}.json"
        temporary_name = f".{value['helper_ref']}.tmp"
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        try:
            existing, existing_info = _read_dir_member(
                receipts, name, maximum=8192, allowed_links={1, 2}
            )
        except FileNotFoundError:
            existing = b""
        if existing:
            if existing_info.st_nlink == 2:
                temporary, temporary_info = _read_dir_member(
                    receipts,
                    temporary_name,
                    maximum=8192,
                    allowed_links={2},
                )
                if temporary != existing or (
                    temporary_info.st_dev,
                    temporary_info.st_ino,
                ) != (existing_info.st_dev, existing_info.st_ino):
                    _die("linked durable foundation receipt recovery differs")
                os.unlink(temporary_name, dir_fd=receipts)
                os.fsync(receipts)
                existing, existing_info = _read_dir_member(receipts, name, maximum=8192)
            existing_value = _strict_json(existing, label="durable foundation receipt")
            _validate_foundation_receipt(
                existing_value,
                deploy_ref=value["deploy_ref"],
                helper_ref=value["helper_ref"],
                startup_contract_sha256=value["startup_contract_sha256"],
            )
            if existing != raw:
                _die("existing durable foundation receipt conflicts")
            return CANONICAL_EVIDENCE_ROOT / "foundation-receipts" / name, existing
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=receipts,
            )
        except FileExistsError:
            temporary, _info = _read_dir_member(receipts, temporary_name, maximum=8192)
            if temporary != raw:
                _die("incomplete durable foundation receipt conflicts")
        else:
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        _die("durable foundation receipt write failed")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            temporary, _info = _read_dir_member(receipts, temporary_name, maximum=8192)
            if temporary != raw:
                _die("durable foundation receipt temporary read-back differs")
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=receipts,
                dst_dir_fd=receipts,
                follow_symlinks=False,
            )
        except FileExistsError:
            concurrent, _info = _read_dir_member(receipts, name, maximum=8192)
            if concurrent != raw:
                _die("concurrent durable foundation receipt conflicts")
        os.unlink(temporary_name, dir_fd=receipts)
        os.fsync(receipts)
        readback, _info = _read_dir_member(receipts, name, maximum=8192)
        if readback != raw:
            _die("durable foundation receipt read-back differs")
        return CANONICAL_EVIDENCE_ROOT / "foundation-receipts" / name, raw
    finally:
        if receipts >= 0:
            os.close(receipts)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def command_foundation_receipt_publish(args: argparse.Namespace) -> int:
    _require_root()
    if (
        FULL_SHA_RE.fullmatch(args.deploy_ref) is None
        or FULL_SHA_RE.fullmatch(args.helper_ref) is None
        or SHA256_RE.fullmatch(args.startup_contract_sha256) is None
    ):
        _die("foundation receipt expected identity is invalid")
    value = _foundation_receipt_payload(args)
    path, raw = _publish_foundation_receipt(value)
    print(
        json.dumps(
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "state": "foundation-fenced",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def command_foundation_receipt_collect(args: argparse.Namespace) -> int:
    _require_root()
    path = Path(args.path)
    expected = (
        CANONICAL_EVIDENCE_ROOT / "foundation-receipts" / f"{args.helper_ref}.json"
    )
    if path != expected:
        _die("durable foundation receipt path is not canonical")
    descriptors = _open_canonical_shared(create=False)
    shared = descriptors[-1]
    receipts = -1
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        receipts = os.open("foundation-receipts", flags, dir_fd=shared)
        _verify_directory(receipts, mode=0o700, label="foundation receipts")
        raw, _info = _read_dir_member(receipts, path.name, maximum=8192)
    finally:
        if receipts >= 0:
            os.close(receipts)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    value = _validate_foundation_receipt(
        _strict_json(raw, label="durable foundation receipt"),
        deploy_ref=args.deploy_ref,
        helper_ref=args.helper_ref,
        startup_contract_sha256=args.startup_contract_sha256,
    )
    live_boot_id, live_boot_started_epoch = _live_boot_identity()
    if (
        value["boot_id"] != live_boot_id
        or value["boot_started_epoch"] != live_boot_started_epoch
    ):
        _die("durable foundation receipt is not from the current boot")
    marker_raw = _exact_file_bytes(
        CANONICAL_EVIDENCE_ROOT / "operation-state.json",
        mode=0o600,
        maximum=32768,
    )
    watchdog_raw = _exact_file_bytes(WATCHDOG_STATE, mode=0o600, maximum=32768)
    startup_raw = _metadata_attribute(
        "instance/attributes/startup-script", maximum=256 * 1024
    )
    instance_id = _ascii_metadata("instance/id", maximum=64, label="instance ID")
    zone_path = _ascii_metadata("instance/zone", maximum=256, label="zone")
    if (
        hashlib.sha256(marker_raw).hexdigest() != value["foundation_marker_sha256"]
        or hashlib.sha256(watchdog_raw).hexdigest()
        != value["foundation_watchdog_state_sha256"]
        or hashlib.sha256(startup_raw).hexdigest()
        != value["live_startup_script_sha256"]
        or instance_id != value["instance_id"]
        or zone_path.rsplit("/", 1)[-1] != value["zone"]
    ):
        _die("durable foundation receipt live evidence differs")
    _foundation_marker(
        marker_raw,
        deploy_ref=args.deploy_ref,
        helper_ref=args.helper_ref,
        contract=args.startup_contract_sha256,
    )
    print(
        json.dumps(
            {"receipt": value, "sha256": hashlib.sha256(raw).hexdigest()},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _gate_fail(message: str) -> None:
    print(f"omega: {message}; Docker remains fenced", file=sys.stderr)
    raise SystemExit(76)


def _gate_path(
    path: Path,
    *,
    kind: str,
    uid: int,
    gid: int,
    mode: int | None = None,
) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        _gate_fail(f"required {kind} is missing: {path.name}")
    matches = {
        "directory": stat.S_ISDIR(info.st_mode),
        "file": stat.S_ISREG(info.st_mode),
        "symlink": stat.S_ISLNK(info.st_mode),
    }
    if (
        not matches.get(kind, False)
        or info.st_uid != uid
        or info.st_gid != gid
        or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
    ):
        _gate_fail(f"required {kind} ownership/mode differs: {path.name}")
    return info


def _gate_read(
    path: Path,
    *,
    mode: int,
    maximum: int,
    label: str,
    uid: int,
    gid: int,
    minimum: int = 1,
) -> bytes:
    try:
        return _exact_file_bytes(
            path,
            mode=mode,
            maximum=maximum,
            minimum=minimum,
            uid=uid,
            gid=gid,
        )
    except (OSError, SystemExit):
        _gate_fail(f"{label} descriptor identity differs")


def _release_tree_digest(
    root: Path,
    *,
    uid: int,
    gid: int,
) -> str:
    digest = hashlib.sha256()
    total_bytes = 0
    count = 0

    def checked(path: Path) -> tuple[os.stat_result, int, str]:
        nonlocal count
        try:
            info = path.lstat()
        except OSError:
            _gate_fail(f"immutable release entry disappeared: {path.name}")
        if (
            info.st_uid != uid
            or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) & 0o222
            or stat.S_IMODE(info.st_mode) & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
        ):
            _gate_fail(f"immutable release ownership/mode differs: {path.name}")
        if stat.S_ISDIR(info.st_mode):
            kind = "d"
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            kind = "f"
        else:
            _gate_fail(f"immutable release contains a link/special file: {path.name}")
        count += 1
        if count > 100_000:
            _gate_fail("immutable release contains too many entries")
        return info, stat.S_IMODE(info.st_mode), kind

    root_info, root_mode, root_kind = checked(root)
    if root_kind != "d" or root.resolve(strict=True) != root:
        _gate_fail("immutable release root is not a real directory")
    digest.update(f"d\0.\0{root_mode:04o}\0".encode())
    entries: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        entries.extend(directory_path / name for name in directory_names)
        entries.extend(directory_path / name for name in file_names)
    for path in sorted(entries, key=lambda item: item.relative_to(root).as_posix()):
        info, file_mode, kind = checked(path)
        relative = path.relative_to(root).as_posix()
        digest.update(f"{kind}\0{relative}\0{file_mode:04o}\0".encode())
        if kind == "f":
            total_bytes += info.st_size
            if total_bytes > MAX_ARCHIVE_BYTES:
                _gate_fail("immutable release byte inventory is oversized")
            raw = _gate_read(
                path,
                mode=file_mode,
                maximum=max(1, info.st_size),
                minimum=0,
                label=f"release file {relative}",
                uid=uid,
                gid=gid,
            )
            digest.update(str(len(raw)).encode() + b"\0")
            digest.update(raw)
    root_after = root.lstat()
    if (
        root_info.st_dev,
        root_info.st_ino,
        root_info.st_mtime_ns,
        root_info.st_ctime_ns,
    ) != (
        root_after.st_dev,
        root_after.st_ino,
        root_after.st_mtime_ns,
        root_after.st_ctime_ns,
    ):
        _gate_fail("immutable release root changed during hashing")
    return digest.hexdigest()


def _first_boot_gate(app_root: Path, marker: Path, *, uid: int, gid: int) -> int:
    if uid == 0 and app_root != Path("/opt/modecissions"):
        _gate_fail("application root is not canonical")
    if uid == 0:
        _gate_path(Path("/opt"), kind="directory", mode=0o755, uid=uid, gid=gid)
    _gate_path(app_root, kind="directory", mode=0o755, uid=uid, gid=gid)
    shared = app_root / "shared"
    _gate_path(shared, kind="directory", mode=0o755, uid=uid, gid=gid)
    if uid == 0 and (
        Path("/opt").resolve(strict=True) != Path("/opt")
        or app_root.resolve(strict=True) != app_root
        or shared.resolve(strict=True) != shared
    ):
        _gate_fail("application root component confinement differs")
    try:
        payload = _strict_json(
            _gate_read(
                marker,
                mode=0o600,
                maximum=32768,
                label="bootstrap operation marker",
                uid=uid,
                gid=gid,
            ),
            label="bootstrap operation marker",
        )
    except SystemExit:
        raise SystemExit(76) from None
    if (
        set(payload) != {"schema_version", "operation", "state", "deploy_ref"}
        or payload.get("schema_version") != 1
        or payload.get("operation") != "bootstrap"
        or payload.get("state") != "initializing"
        or FULL_SHA_RE.fullmatch(str(payload.get("deploy_ref", ""))) is None
    ):
        _gate_fail("bootstrap operation marker differs")
    for path in (
        app_root / "current",
        app_root / "previous",
        shared / "runtime-state",
        shared / "day2-initialized.json",
        shared / "infra.env",
        shared / "docker-compose.gcp.yml",
        shared / "bootstrap-state.json",
        shared / "runtime-provenance.json",
    ):
        if path.exists() or path.is_symlink():
            _gate_fail("prior runtime evidence exists during first boot")
    for root, root_mode in (
        (app_root / "releases", 0o755),
        (shared / "state-bundles", 0o700),
    ):
        if root.exists() or root.is_symlink():
            _gate_path(root, kind="directory", mode=root_mode, uid=uid, gid=gid)
        if root.exists() and any(root.iterdir()):
            _gate_fail("prior runtime inventory exists during first boot")
    return 0


def _gate_runtime_pair(
    app_root: Path,
    state_link: Path,
    *,
    uid: int,
    gid: int,
) -> tuple[dict, dict, bytes, Path, Path, os.stat_result, str]:
    shared_root = app_root / "shared"
    bundles_root = shared_root / "state-bundles"
    if uid == 0 and app_root != Path("/opt/modecissions"):
        _gate_fail("application root is not canonical")
    if uid == 0:
        _gate_path(Path("/opt"), kind="directory", mode=0o755, uid=uid, gid=gid)
    _gate_path(app_root, kind="directory", mode=0o755, uid=uid, gid=gid)
    _gate_path(shared_root, kind="directory", mode=0o755, uid=uid, gid=gid)
    if uid == 0 and (
        Path("/opt").resolve(strict=True) != Path("/opt")
        or app_root.resolve(strict=True) != app_root
        or shared_root.resolve(strict=True) != shared_root
    ):
        _gate_fail("application root component confinement differs")
    _gate_path(bundles_root, kind="directory", mode=0o700, uid=uid, gid=gid)
    link_info = _gate_path(state_link, kind="symlink", uid=uid, gid=gid)
    raw_target = os.readlink(state_link)
    if not os.path.isabs(raw_target):
        _gate_fail("canonical runtime-state target is not absolute")
    try:
        state_dir = state_link.resolve(strict=True)
    except (FileNotFoundError, ValueError, RuntimeError):
        _gate_fail("canonical runtime-state target is missing or unconfined")
    if state_dir.parent != bundles_root.resolve(strict=True):
        _gate_fail("canonical runtime-state target is not one direct bundle")
    state_info = _gate_path(state_dir, kind="directory", mode=0o700, uid=uid, gid=gid)
    state_names = {entry.name for entry in state_dir.iterdir()}
    if state_names != {"bootstrap-state.json", "runtime-provenance.json"}:
        _gate_fail("canonical runtime-state bundle inventory is not exact")
    bootstrap_raw = _gate_read(
        state_dir / "bootstrap-state.json",
        mode=0o600,
        maximum=32768,
        label="bootstrap state",
        uid=uid,
        gid=gid,
    )
    provenance_raw = _gate_read(
        state_dir / "runtime-provenance.json",
        mode=0o600,
        maximum=262144,
        label="runtime provenance",
        uid=uid,
        gid=gid,
    )
    try:
        bootstrap = _strict_json(bootstrap_raw, label="bootstrap state")
        provenance = _strict_json(provenance_raw, label="runtime provenance")
    except SystemExit:
        _gate_fail("canonical runtime-state pair is unreadable")
    link_after = state_link.lstat()
    state_after = state_dir.lstat()
    if (
        (link_info.st_dev, link_info.st_ino, link_info.st_ctime_ns)
        != (link_after.st_dev, link_after.st_ino, link_after.st_ctime_ns)
        or os.readlink(state_link) != raw_target
        or (state_info.st_dev, state_info.st_ino, state_info.st_ctime_ns)
        != (state_after.st_dev, state_after.st_ino, state_after.st_ctime_ns)
        or {entry.name for entry in state_dir.iterdir()} != state_names
    ):
        _gate_fail("canonical runtime-state changed during authoritative read")
    return (
        bootstrap,
        provenance,
        provenance_raw,
        shared_root,
        bundles_root,
        link_info,
        raw_target,
    )


def _gate_runtime_identity(
    bootstrap: dict,
    provenance: dict,
    provenance_raw: bytes,
    *,
    mode: str,
) -> tuple[str, str]:
    deploy_ref = bootstrap.get("deploy_ref")
    version = bootstrap.get("version")
    if (
        set(bootstrap)
        != {
            "schema_version",
            "state",
            "deploy_ref",
            "version",
            "runtime_provenance_sha256",
            "release_tree_sha256",
            "canonical_writer",
            "secret_versions",
            "reboot_helper",
            "completed_at",
        }
        or bootstrap.get("schema_version") != 2
        or bootstrap.get("state") != "complete"
        or not isinstance(deploy_ref, str)
        or FULL_SHA_RE.fullmatch(deploy_ref) is None
        or not isinstance(version, str)
        or re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+(?:-[0-9A-Za-z.-]+)?", version)
        is None
        or not isinstance(bootstrap.get("completed_at"), str)
        or SHA256_RE.fullmatch(str(bootstrap.get("release_tree_sha256", ""))) is None
        or type(bootstrap.get("canonical_writer")) is not bool
    ):
        _gate_fail("bootstrap state identity is invalid")
    secrets = bootstrap.get("secret_versions")
    required = {
        "control_room_evidence_signing_key_id",
        "control_room_evidence_signing_key",
        "control_room_evidence_signing_previous_keys",
    }
    all_names = required | {"gcs_hmac_access_key_id", "gcs_hmac_secret_access_key"}
    if not isinstance(secrets, dict) or set(secrets) != all_names:
        _gate_fail("secret version provenance inventory differs")
    if any(GENERATION_RE.fullmatch(str(secrets[name])) is None for name in required):
        _gate_fail("required secret version provenance is invalid")
    hmac_versions = (
        secrets["gcs_hmac_access_key_id"],
        secrets["gcs_hmac_secret_access_key"],
    )
    if hmac_versions != ("", "") and any(
        GENERATION_RE.fullmatch(str(value)) is None for value in hmac_versions
    ):
        _gate_fail("HMAC secret version provenance is partial/invalid")
    if (
        bootstrap.get("runtime_provenance_sha256")
        != hashlib.sha256(provenance_raw).hexdigest()
    ):
        _gate_fail("runtime provenance checksum differs from bootstrap state")
    legacy = (
        mode in {"verify", "authorize-start"}
        and os.environ.get("OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE") == "1"
    )
    schema = provenance.get("schema_version")
    if schema not in {1, 2} or (schema == 1 and not legacy):
        _gate_fail("runtime provenance schema requires explicit adoption")
    for key, expected in (("deploy_ref", deploy_ref), ("version", version)):
        if provenance.get(key) != expected:
            _gate_fail(f"runtime provenance identity differs: {key}")
    return deploy_ref, version


def _gate_current_release(
    app_root: Path,
    *,
    deploy_ref: str,
    version: str,
    expected_tree_sha256: object,
    uid: int,
    gid: int,
) -> Path:
    current_link = Path(
        os.environ.get("OMEGA_GCP_CURRENT_LINK_OVERRIDE", str(app_root / "current"))
    )
    if not current_link.is_symlink():
        _gate_fail("current release link is missing")
    try:
        raw_target = os.readlink(current_link)
        releases_root = (app_root / "releases").resolve(strict=True)
        current_release = current_link.resolve(strict=True)
    except (FileNotFoundError, ValueError, RuntimeError):
        _gate_fail("current release is missing or unconfined")
    _gate_path(app_root / "releases", kind="directory", mode=0o755, uid=uid, gid=gid)
    link_info = _gate_path(current_link, kind="symlink", uid=uid, gid=gid)
    if current_release.parent != releases_root:
        _gate_fail("current release is not one direct child")
    current_info = _gate_path(current_release, kind="directory", uid=uid, gid=gid)
    if stat.S_IMODE(current_info.st_mode) & 0o022:
        _gate_fail("current release directory is group/world writable")
    if current_release.name != deploy_ref:
        _gate_fail("current release ref differs from bootstrap state")
    raw_version = _gate_read(
        current_release / "VERSION",
        mode=0o444,
        maximum=128,
        label="current VERSION",
        uid=uid,
        gid=gid,
    )
    try:
        current_version = raw_version.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        _gate_fail("current VERSION is unreadable")
    if (
        raw_version
        not in {
            current_version.encode("ascii"),
            (current_version + "\n").encode("ascii"),
        }
        or current_version != version
    ):
        _gate_fail("current VERSION differs from bootstrap state")
    if _release_tree_digest(current_release, uid=uid, gid=gid) != expected_tree_sha256:
        _gate_fail("immutable release tree checksum differs from bootstrap state")
    link_after = current_link.lstat()
    if (
        link_info.st_dev,
        link_info.st_ino,
        link_info.st_ctime_ns,
    ) != (
        link_after.st_dev,
        link_after.st_ino,
        link_after.st_ctime_ns,
    ) or os.readlink(current_link) != raw_target:
        _gate_fail("current release changed during immutable tree verification")
    return current_release


def _gate_runtime_inputs(
    provenance: dict,
    *,
    current_release: Path,
    shared_root: Path,
    uid: int,
    gid: int,
) -> bool:
    legacy = os.environ.get("OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE") == "1"
    inputs = provenance.get("runtime_input_sha256")
    if inputs is None and legacy:
        return legacy
    if not isinstance(inputs, dict) or not all(
        isinstance(key, str)
        and isinstance(value, str)
        and SHA256_RE.fullmatch(value) is not None
        for key, value in inputs.items()
    ):
        _gate_fail("runtime input checksum inventory is invalid")
    mode_name = provenance.get("mode")
    base_keys = {"shared_env", "base_compose", "gcp_compose"}
    if mode_name == "day2":
        expected_keys = base_keys | {"release_compose"}
    elif mode_name == "bootstrap":
        expected_keys = set(base_keys)
        if "legacy_image_compose" in inputs:
            expected_keys.add("legacy_image_compose")
    else:
        _gate_fail("runtime provenance mode is invalid")
    if set(inputs) != expected_keys:
        _gate_fail("runtime input checksum inventory is not exact")
    paths = {
        "shared_env": shared_root / "infra.env",
        "base_compose": current_release / "infra/docker-compose.yml",
        "gcp_compose": shared_root / "docker-compose.gcp.yml",
        "legacy_image_compose": shared_root / "docker-compose.legacy-images.gcp.yml",
        "release_compose": current_release
        / "infra/terraform-gcp/release/docker-compose.release.yml",
    }
    for key in sorted(expected_keys):
        path = paths[key]
        file_mode = (
            0o600
            if key in {"shared_env", "gcp_compose", "legacy_image_compose"}
            else 0o444
        )
        raw = _gate_read(
            path,
            mode=file_mode,
            maximum=2 * 1024 * 1024,
            label=f"runtime input {key}",
            uid=uid,
            gid=gid,
        )
        if hashlib.sha256(raw).hexdigest() != inputs[key]:
            _gate_fail(f"runtime input checksum differs: {key}")
    return legacy


def _gate_helpers(
    bootstrap: dict,
    *,
    deploy_ref: str,
    current_release: Path,
    shared_root: Path,
    legacy: bool,
    uid: int,
    gid: int,
) -> tuple[Path, Path, Path]:
    helper = bootstrap.get("reboot_helper")
    if not isinstance(helper, dict):
        _gate_fail("reboot helper provenance is missing")
    helper_mode = helper.get("mode")
    source_ref = helper.get("source_ref")
    if not isinstance(source_ref, str) or FULL_SHA_RE.fullmatch(source_ref) is None:
        _gate_fail("reboot helper source ref is invalid")
    base_keys = {
        "mode",
        "source_ref",
        "source_artifact_uri",
        "reboot_runtime_sha256",
        "runtime_contract_sha256",
    }
    normal_keys = base_keys | {
        "source_artifact_generation",
        "source_artifact_size_bytes",
        "source_artifact_sha256",
        "safe_io_sha256",
    }
    legacy_keys = (
        base_keys
        if helper_mode == "current"
        else base_keys | {"source_artifact_sha256"}
    )
    if set(helper) != (legacy_keys if legacy else normal_keys):
        _gate_fail("reboot helper provenance keys differ from the reviewed shape")
    artifact_uri = helper.get("source_artifact_uri")
    artifact_generation = helper.get("source_artifact_generation")
    artifact_size = helper.get("source_artifact_size_bytes")
    artifact_sha = helper.get("source_artifact_sha256")
    safe_io_sha = helper.get("safe_io_sha256")
    if (
        not isinstance(artifact_uri, str)
        or re.fullmatch(
            rf"gs://[^/]+/deploy-artifacts/{re.escape(source_ref)}/repo[.]tar[.]gz",
            artifact_uri,
        )
        is None
    ):
        _gate_fail("reboot helper artifact provenance is invalid")
    if legacy:
        if helper_mode == "shared" and (
            not isinstance(artifact_sha, str)
            or SHA256_RE.fullmatch(artifact_sha) is None
        ):
            _gate_fail("legacy shared helper artifact SHA-256 is invalid")
    elif (
        not isinstance(artifact_generation, str)
        or GENERATION_RE.fullmatch(artifact_generation) is None
        or type(artifact_size) is not int
        or artifact_size < 1
        or not isinstance(artifact_sha, str)
        or SHA256_RE.fullmatch(artifact_sha) is None
        or not isinstance(safe_io_sha, str)
        or SHA256_RE.fullmatch(safe_io_sha) is None
    ):
        _gate_fail("reboot helper artifact byte identity is incomplete")
    if helper_mode == "current":
        if source_ref != deploy_ref:
            _gate_fail("current reboot helper ref differs from current release")
        helper_root = current_release / "scripts/gcp"
    elif helper_mode == "shared":
        helper_root = shared_root / "bin" / source_ref
        try:
            helper_root.resolve(strict=True).relative_to(
                (shared_root / "bin").resolve(strict=True)
            )
        except (FileNotFoundError, ValueError, RuntimeError):
            _gate_fail("shared reboot helper directory is missing or unconfined")
    else:
        _gate_fail("reboot helper mode is invalid")
    paths = (
        helper_root / "reboot-runtime.sh",
        helper_root / "runtime_contract.py",
        helper_root / "safe_io.py",
    )
    for path, field in zip(
        paths,
        ("reboot_runtime_sha256", "runtime_contract_sha256", "safe_io_sha256"),
    ):
        if legacy and field == "safe_io_sha256":
            continue
        expected = helper.get(field)
        helper_mode_bits = 0o555 if helper_mode == "current" else 0o755
        raw = _gate_read(
            path,
            mode=helper_mode_bits,
            maximum=2 * 1024 * 1024,
            label=f"reboot helper {path.name}",
            uid=uid,
            gid=gid,
        )
        if (
            not isinstance(expected, str)
            or SHA256_RE.fullmatch(expected) is None
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            _gate_fail(f"reboot helper checksum differs: {path.name}")
    return paths


def command_operation_gate(args: argparse.Namespace) -> int:
    """Verify the exact local runtime evidence used by Docker ExecStartPre."""
    _require_root()
    app_root = Path(args.app_root)
    state_link = Path(args.state_link)
    marker = Path(args.operation_marker)
    mode = args.mode
    uid = os.geteuid()
    gid = os.getegid()
    if mode not in {"verify", "print-helpers", "authorize-start"}:
        _gate_fail("unsupported operation-gate mode")
    if not state_link.exists() and not state_link.is_symlink():
        if (
            mode == "print-helpers"
            or os.environ.get("OMEGA_GCP_INITIAL_BOOTSTRAP") != "1"
        ):
            _gate_fail("canonical runtime-state is missing")
        return _first_boot_gate(app_root, marker, uid=uid, gid=gid)
    (
        bootstrap,
        provenance,
        provenance_raw,
        shared_root,
        _bundles_root,
        _link_info,
        _raw_target,
    ) = _gate_runtime_pair(app_root, state_link, uid=uid, gid=gid)
    deploy_ref, version = _gate_runtime_identity(
        bootstrap, provenance, provenance_raw, mode=mode
    )
    current_release = _gate_current_release(
        app_root,
        deploy_ref=deploy_ref,
        version=version,
        expected_tree_sha256=bootstrap["release_tree_sha256"],
        uid=uid,
        gid=gid,
    )
    legacy = _gate_runtime_inputs(
        provenance,
        current_release=current_release,
        shared_root=shared_root,
        uid=uid,
        gid=gid,
    )
    helpers = _gate_helpers(
        bootstrap,
        deploy_ref=deploy_ref,
        current_release=current_release,
        shared_root=shared_root,
        legacy=legacy,
        uid=uid,
        gid=gid,
    )
    if mode == "print-helpers":
        print("\t".join(str(path) for path in helpers))
    return 0


def _canonical_url(value: object, *, path: str = "") -> str:
    if not isinstance(value, str) or len(value) > 512:
        _die("startup URL is invalid")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != path
        or parsed.netloc != parsed.hostname
        or HOSTNAME_RE.fullmatch(parsed.hostname) is None
    ):
        _die("startup URL is not canonical")
    return value.rstrip("/")


def command_startup_config(args: argparse.Namespace) -> int:
    """Decode and validate the only Terraform-to-root-shell data boundary."""
    _require_root()
    encoded = sys.stdin.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024 or not re.fullmatch(r"[A-Za-z0-9+/=]+", encoded):
        _die("startup contract encoding is invalid")
    try:
        import base64

        payload = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _die(f"startup contract cannot be decoded: {type(exc).__name__}")
    root = _exact_keys(
        payload,
        {
            "schema_version",
            "project_id",
            "environment",
            "controller_ref",
            "foundation_predecessor",
            "host_identity",
            "source",
            "urls",
            "admin_email",
            "cookie_secure",
            "data_disk_size_bytes",
            "host_package_versions",
            "secret_versions",
            "lakehouse_bucket",
            "release_backup_bucket",
            "lakehouse_endpoint",
            "canonical_writer",
            "enable_airflow_scheduler",
            "exact_runtime_contract_ready",
            "secret_prefix",
            "compose_override",
        },
        "root",
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        _die("startup contract schema is unsupported")
    project = root["project_id"]
    environment = root["environment"]
    if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
        _die("startup project id is invalid")
    if not isinstance(environment, str) or not ENVIRONMENT_RE.fullmatch(environment):
        _die("startup environment is invalid")
    if (
        not isinstance(root["controller_ref"], str)
        or FULL_SHA_RE.fullmatch(root["controller_ref"]) is None
    ):
        _die("startup controller ref is invalid")
    host_identity = _exact_keys(
        root["host_identity"],
        {
            "project_id",
            "instance_id",
            "instance_name",
            "zone",
            "service_account_email",
        },
        "host identity",
    )
    if (
        host_identity["project_id"] != project
        or not isinstance(host_identity["instance_id"], str)
        or re.fullmatch(r"[1-9][0-9]{0,19}", host_identity["instance_id"]) is None
        or not isinstance(host_identity["instance_name"], str)
        or re.fullmatch(
            r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?",
            host_identity["instance_name"],
        )
        is None
        or not isinstance(host_identity["zone"], str)
        or re.fullmatch(r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?", host_identity["zone"])
        is None
        or not isinstance(host_identity["service_account_email"], str)
        or re.fullmatch(
            r"[a-z][a-z0-9-]{2,62}@[a-z][a-z0-9-]{4,28}[a-z0-9][.]"
            r"iam[.]gserviceaccount[.]com",
            host_identity["service_account_email"],
        )
        is None
    ):
        _die("startup host identity is invalid")
    predecessor = root["foundation_predecessor"]
    if predecessor is not None:
        predecessor = _exact_keys(
            predecessor,
            {
                "marker_sha256",
                "watchdog_state_sha256",
                "deploy_ref",
                "helper_ref",
                "startup_config_sha256",
            },
            "foundation predecessor",
        )
        if (
            not isinstance(predecessor["marker_sha256"], str)
            or SHA256_RE.fullmatch(predecessor["marker_sha256"]) is None
            or not isinstance(predecessor["watchdog_state_sha256"], str)
            or SHA256_RE.fullmatch(predecessor["watchdog_state_sha256"]) is None
            or not isinstance(predecessor["deploy_ref"], str)
            or FULL_SHA_RE.fullmatch(predecessor["deploy_ref"]) is None
            or not isinstance(predecessor["helper_ref"], str)
            or FULL_SHA_RE.fullmatch(predecessor["helper_ref"]) is None
            or not isinstance(predecessor["startup_config_sha256"], str)
            or SHA256_RE.fullmatch(predecessor["startup_config_sha256"]) is None
        ):
            _die("startup foundation predecessor identity is invalid")
    source = _exact_keys(
        root["source"],
        {"bucket", "object", "ref", "generation", "size_bytes", "archive_sha256"},
        "source",
    )
    if not isinstance(source["ref"], str) or not FULL_SHA_RE.fullmatch(source["ref"]):
        _die("startup source ref is invalid")
    expected_object = f"deploy-artifacts/{source['ref']}/repo.tar.gz"
    if (
        not isinstance(source["bucket"], str)
        or not GCS_BUCKET_RE.fullmatch(source["bucket"])
        or source["object"] != expected_object
        or not isinstance(source["generation"], str)
        or not GENERATION_RE.fullmatch(source["generation"])
        or not isinstance(source["size_bytes"], int)
        or isinstance(source["size_bytes"], bool)
        or source["size_bytes"] < 1
        or source["size_bytes"] > MAX_ARCHIVE_BYTES
        or not isinstance(source["archive_sha256"], str)
        or not SHA256_RE.fullmatch(source["archive_sha256"])
    ):
        _die("startup source artifact identity is invalid")
    urls = _exact_keys(
        root["urls"],
        {
            "public_console",
            "public_workspace",
            "public_airflow",
            "technical_console",
            "technical_workspace",
        },
        "urls",
    )
    console = _canonical_url(urls["public_console"])
    workspace = _canonical_url(urls["public_workspace"])
    airflow = _canonical_url(urls["public_airflow"], path="/airflow")
    if airflow != f"{console}/airflow":
        _die("startup Airflow URL differs from console URL")
    public_https = console.startswith("https://") and workspace.startswith("https://")
    if public_https:
        if urls["technical_console"] != "" or urls["technical_workspace"] != "":
            _die("startup HTTPS mode must not expose technical HTTP URLs")
    else:
        technical_console = _canonical_url(urls["technical_console"])
        technical_workspace = _canonical_url(urls["technical_workspace"])
        if not technical_console.startswith(
            "http://"
        ) or not technical_workspace.startswith("http://"):
            _die("startup technical URLs must use HTTP load-balancer addresses")
    if root["cookie_secure"] is not public_https:
        _die("startup cookie policy differs from public URL scheme")
    if type(root["canonical_writer"]) is not bool:
        _die("startup canonical-writer role is invalid")
    if type(root["enable_airflow_scheduler"]) is not bool:
        _die("startup scheduler flag is invalid")
    if root["enable_airflow_scheduler"] is not root["canonical_writer"]:
        _die("startup scheduler must exactly match the canonical-writer role")
    if type(root["exact_runtime_contract_ready"]) is not bool:
        _die("exact runtime handoff flag is invalid")
    package_versions = _exact_keys(
        root["host_package_versions"],
        {
            "ca_certificates",
            "containerd_io",
            "curl",
            "docker_buildx_plugin",
            "docker_ce",
            "docker_ce_cli",
            "docker_compose_plugin",
            "gnupg",
            "iptables",
            "jq",
            "lsof",
            "openssl",
            "python3",
        },
        "Docker package version",
    )
    if any(
        not isinstance(value, str)
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+:~_-]{1,159}", value) is None
        for value in package_versions.values()
    ):
        _die("startup host package versions are invalid")
    secret_versions = _exact_keys(
        root["secret_versions"],
        {
            "control_room_evidence_signing_key_id",
            "control_room_evidence_signing_key",
            "control_room_evidence_signing_previous_keys",
            "gcs_hmac_access_key_id",
            "gcs_hmac_secret_access_key",
        },
        "secret version",
    )
    required_secret_names = {
        "control_room_evidence_signing_key_id",
        "control_room_evidence_signing_key",
        "control_room_evidence_signing_previous_keys",
    }
    if any(
        not isinstance(secret_versions[name], str)
        or GENERATION_RE.fullmatch(secret_versions[name]) is None
        for name in required_secret_names
    ):
        _die("startup required secret versions must be numeric")
    hmac_versions = (
        secret_versions["gcs_hmac_access_key_id"],
        secret_versions["gcs_hmac_secret_access_key"],
    )
    if hmac_versions != ("", "") and any(
        not isinstance(value, str) or GENERATION_RE.fullmatch(value) is None
        for value in hmac_versions
    ):
        _die("startup HMAC secret versions must be both absent or numeric")
    data_disk_size = root["data_disk_size_bytes"]
    if (
        type(data_disk_size) is not int
        or data_disk_size < 10 * 1024**3
        or data_disk_size > 65536 * 1024**3
        or data_disk_size % (1024**3) != 0
    ):
        _die("startup data disk byte identity is invalid")
    if root["secret_prefix"] != f"omega-{environment}-":
        _die("startup secret prefix differs from environment")
    if (
        not isinstance(root["admin_email"], str)
        or len(root["admin_email"]) > 254
        or re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
            root["admin_email"],
        )
        is None
    ):
        _die("startup administrator email is invalid")
    for key in ("lakehouse_bucket", "release_backup_bucket", "lakehouse_endpoint"):
        if not isinstance(root[key], str):
            _die(f"startup {key} is invalid")
    if not GCS_BUCKET_RE.fullmatch(root["lakehouse_bucket"]):
        _die("startup lakehouse bucket is invalid")
    if (
        not GCS_BUCKET_RE.fullmatch(root["release_backup_bucket"])
        or root["release_backup_bucket"] == root["lakehouse_bucket"]
    ):
        _die("startup release backup bucket is invalid or not isolated")
    if (
        re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
            root["lakehouse_endpoint"],
        )
        is None
    ):
        _die("startup lakehouse endpoint is invalid")
    compose = root["compose_override"]
    if (
        not isinstance(compose, str)
        or not compose
        or len(compose.encode()) > 256_000
        or "\x00" in compose
    ):
        _die("startup Compose override is invalid")

    output = Path(args.output)
    if not output.is_absolute() or output.exists() or output.is_symlink():
        _die("startup config output must be a new absolute path")
    _require_private_parent(output)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(root, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        _fsync_dir(output.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return 0


def command_startup_identities(args: argparse.Namespace) -> int:
    """Emit identities from the private normalized output of startup-config."""
    path = Path(args.path)
    if not path.is_absolute():
        _die("startup identity contract path must be absolute")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 1024 * 1024
        ):
            _die("startup identity contract is not an exact private file")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die("startup identity contract changed during its read")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
        deploy_ref = payload["source"]["ref"]
        helper_ref = payload["controller_ref"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        _die("startup identity contract shape is invalid")
    if (
        not isinstance(deploy_ref, str)
        or FULL_SHA_RE.fullmatch(deploy_ref) is None
        or not isinstance(helper_ref, str)
        or FULL_SHA_RE.fullmatch(helper_ref) is None
    ):
        _die("startup identities are invalid")
    sys.stdout.write(f"{deploy_ref}\t{helper_ref}\n")
    return 0


def command_startup_predecessor(args: argparse.Namespace) -> int:
    """Emit only the reviewed predecessor identity from normalized config."""
    path = Path(args.path)
    if not path.is_absolute():
        _die("startup predecessor contract path must be absolute")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 1024 * 1024
        ):
            _die("startup predecessor contract is not an exact private file")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die("startup predecessor contract changed during its read")
    finally:
        os.close(descriptor)
    try:
        predecessor = json.loads(raw.decode("utf-8", errors="strict"))[
            "foundation_predecessor"
        ]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        _die("startup predecessor contract shape is invalid")
    if predecessor is None:
        sys.stdout.write("none\n")
        return 0
    if not isinstance(predecessor, dict) or set(predecessor) != {
        "marker_sha256",
        "watchdog_state_sha256",
        "deploy_ref",
        "helper_ref",
        "startup_config_sha256",
    }:
        _die("startup predecessor contract shape is invalid")
    values = (
        predecessor["marker_sha256"],
        predecessor["watchdog_state_sha256"],
        predecessor["deploy_ref"],
        predecessor["helper_ref"],
        predecessor["startup_config_sha256"],
    )
    if (
        SHA256_RE.fullmatch(str(values[0])) is None
        or SHA256_RE.fullmatch(str(values[1])) is None
        or FULL_SHA_RE.fullmatch(str(values[2])) is None
        or FULL_SHA_RE.fullmatch(str(values[3])) is None
        or SHA256_RE.fullmatch(str(values[4])) is None
    ):
        _die("startup predecessor identity is invalid")
    sys.stdout.write("\t".join(values) + "\n")
    return 0


def command_compose_reboot_services(args: argparse.Namespace) -> int:
    """Select the exact 16 post-fence services from full Compose inventory."""
    path = Path(args.path)
    if not path.is_absolute():
        _die("Compose service inventory path must be absolute")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 32768
        ):
            _die("Compose service inventory is not an exact private file")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _die("Compose service inventory changed during its read")
    finally:
        os.close(descriptor)
    try:
        rows = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        _die("Compose service inventory is not UTF-8")
    applications = {
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "salesforce",
        "banxico",
        "inegi",
        "sec-edgar",
        "sap-hcm",
        "sap-successfactors",
        "sap-s4hana",
        "superset",
    }
    excluded = {
        "postgres",
        "postgres_gold",
        "redis",
        "minio",
        "mailhog",
        "airflow-scheduler",
        "airflow-init",
        "minio-init",
        "postgres_dev_seed",
        "superset-init",
    }
    if (
        not rows
        or any(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", row) is None for row in rows)
        or len(rows) != len(set(rows))
        or set(rows) != applications | excluded
    ):
        _die("Compose service inventory is not the exact 26-service contract")
    selected = [service for service in rows if service in applications]
    if len(selected) != 16:
        _die("Compose post-fence service allowlist is incomplete")
    sys.stdout.write("\n".join(selected) + "\n")
    return 0


def _moby_cgroup_identity(value: str) -> tuple[str, PurePosixPath] | None:
    components = value.split("/")[1:]
    raw_marker_components = value.split("/")
    docker_intent = any(
        component == "docker"
        or component.startswith("docker-")
        or CONTAINER_ID_RE.fullmatch(component) is not None
        for component in raw_marker_components
    )
    docker_markers = [
        index
        for index, component in enumerate(components)
        if component == "docker"
        or component.startswith("docker-")
        or CONTAINER_ID_RE.fullmatch(component) is not None
    ]
    if not value.startswith("/") or "\x00" in value:
        if docker_intent:
            raise RuntimeError("Docker cgroup identity is ambiguous or malformed")
        return None
    if not components or any(part in {"", ".", ".."} for part in components):
        if docker_intent:
            raise RuntimeError("Docker cgroup identity is ambiguous or malformed")
        return None
    matches: list[tuple[str, int]] = []
    for index, component in enumerate(components):
        systemd_match = re.fullmatch(r"docker-([0-9a-f]{64})\.scope", component)
        if systemd_match is not None:
            matches.append((systemd_match.group(1), index))
        if (
            component == "docker"
            and index + 1 < len(components)
            and CONTAINER_ID_RE.fullmatch(components[index + 1])
        ):
            matches.append((components[index + 1], index + 1))
    if not matches and not docker_intent:
        return None
    if len(matches) != 1:
        raise RuntimeError("Docker cgroup identity is ambiguous or malformed")
    container_id, final_index = matches[0]
    accepted_marker = (
        final_index
        if components[final_index].startswith("docker-")
        else final_index - 1
    )
    expected_markers = {accepted_marker, final_index}
    if any(index not in expected_markers for index in docker_markers):
        raise RuntimeError("Docker cgroup identity is ambiguous or malformed")
    return container_id, PurePosixPath("/", *components[: final_index + 1])


def _container_id_from_cgroup_path(value: str) -> str | None:
    identity = _moby_cgroup_identity(value)
    return identity[0] if identity is not None else None


def _proc_process_identity(pid_root: Path) -> tuple[str, int, str] | None:
    try:
        value = (pid_root / "stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("cannot read process start identity") from exc
    match = re.fullmatch(r"[0-9]+ \(.*\) (.*)\n?", value)
    if match is None:
        raise RuntimeError("process start identity is malformed")
    fields = match.group(1).split()
    if len(fields) < 20 or not fields[1].isdigit() or not fields[19].isdigit():
        raise RuntimeError("process start identity is malformed")
    return fields[0], int(fields[1]), fields[19]


def _proc_state_starttime(pid_root: Path) -> tuple[str, str] | None:
    identity = _proc_process_identity(pid_root)
    return (identity[0], identity[2]) if identity is not None else None


def _proc_starttime(pid_root: Path) -> str | None:
    identity = _proc_state_starttime(pid_root)
    return identity[1] if identity is not None else None


def _proc_real_uid(pid_root: Path) -> int | None:
    try:
        rows = (pid_root / "status").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("cannot read process ownership") from exc
    for row in rows:
        if row.startswith("Uid:"):
            fields = row.split()
            if len(fields) >= 2 and fields[1].isdigit():
                return int(fields[1])
            raise RuntimeError("process ownership is malformed")
    raise RuntimeError("process ownership is malformed")


def _read_moby_shim(
    pid_root: Path,
    shim_executable: Path,
) -> tuple[int, str, str] | None:
    if not pid_root.name.isdigit() or int(pid_root.name) <= 1:
        return None
    try:
        executable = (pid_root / "exe").resolve(strict=True)
    except FileNotFoundError:
        # A missing exe is normal for an exited task or a kernel thread.
        return None
    except OSError as exc:
        raise RuntimeError("cannot read process executable identity") from exc
    if executable != shim_executable.resolve(strict=False):
        return None
    try:
        raw_arguments = (pid_root / "cmdline").read_bytes()
    except FileNotFoundError:
        raise RuntimeError("shim argument identity disappeared after pidfd capture")
    except OSError as exc:
        raise RuntimeError("cannot read shim argument identity") from exc
    try:
        arguments = [
            value.decode("utf-8", errors="strict")
            for value in raw_arguments.split(b"\0")
            if value
        ]
    except UnicodeDecodeError as exc:
        raise RuntimeError("shim argument identity is malformed") from exc
    uid = _proc_real_uid(pid_root)
    if uid is None:
        raise RuntimeError("shim process ownership disappeared after pidfd capture")
    if uid != 0:
        raise RuntimeError("containerd shim is not root-owned")

    def exact_flag(name: str) -> str | None:
        positions = [index for index, value in enumerate(arguments) if value == name]
        if len(positions) != 1 or positions[0] + 1 >= len(arguments):
            return None
        return arguments[positions[0] + 1]

    namespace = exact_flag("-namespace")
    container_id = exact_flag("-id")
    starttime = _proc_starttime(pid_root)
    if namespace != "moby":
        raise RuntimeError(
            "non-moby containerd shim is forbidden on the dedicated host"
        )
    if (
        not isinstance(container_id, str)
        or CONTAINER_ID_RE.fullmatch(container_id) is None
        or starttime is None
    ):
        raise RuntimeError("shim argument identity is malformed")
    return int(pid_root.name), container_id, starttime


def _read_unified_cgroup(pid_root: Path) -> str | None:
    try:
        rows = (pid_root / "cgroup").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("cannot read process cgroup identity") from exc
    unified: list[str] = []
    for row in rows:
        fields = row.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            unified.append(fields[2])
    if len(unified) != 1:
        raise RuntimeError("process lacks one exact unified cgroup-v2 identity")
    return unified[0]


def _open_process_pidfd(pid: int) -> int:
    opener = getattr(os, "pidfd_open", None)
    if opener is None:
        raise RuntimeError("pidfd_open is unavailable")
    return int(opener(pid, 0))


def _send_process_pidfd(pidfd: int, requested_signal: int) -> None:
    sender = getattr(signal, "pidfd_send_signal", None)
    if sender is None:
        raise RuntimeError("pidfd_send_signal is unavailable")
    sender(pidfd, requested_signal)


def _moby_snapshot(
    proc_root: Path,
    shim_executable: Path,
    open_pidfd: Callable[[int], int],
    send_pidfd_signal: Callable[[int, int], None],
    close_pidfd: Callable[[int], None],
) -> tuple[
    dict[tuple[int, str, str], int],
    dict[tuple[int, str, str], int],
    dict[str, set[PurePosixPath]],
]:
    shims: dict[tuple[int, str, str], int] = {}
    members: dict[tuple[int, str, str], int] = {}
    cgroups: dict[str, set[PurePosixPath]] = {}
    try:
        pid_roots = sorted(
            (path for path in proc_root.iterdir() if path.name.isdigit()),
            key=lambda path: int(path.name),
        )
    except OSError as exc:
        raise RuntimeError("cannot enumerate procfs") from exc
    try:
        for pid_root in pid_roots:
            pid = int(pid_root.name)
            try:
                pidfd = open_pidfd(pid)
            except ProcessLookupError:
                continue
            except OSError as exc:
                raise RuntimeError("cannot acquire process pidfd identity") from exc
            retained = False
            try:
                shim = _read_moby_shim(pid_root, shim_executable)
                cgroup_path = _read_unified_cgroup(pid_root)
                if cgroup_path is None:
                    continue
                cgroup_identity = _moby_cgroup_identity(cgroup_path)
                if shim is None and cgroup_identity is None:
                    continue
                if pid <= 1:
                    raise RuntimeError("Docker cgroup contains a forbidden host PID")

                if shim is not None:
                    identity = shim
                    if cgroup_identity is not None and shim[1] != cgroup_identity[0]:
                        raise RuntimeError(
                            "shim argv and cgroup container identities differ"
                        )
                    if _read_moby_shim(pid_root, shim_executable) != identity:
                        raise RuntimeError("shim identity changed during pidfd capture")
                    shims[identity] = pidfd
                    retained = True
                elif cgroup_identity is not None:
                    container_id, _kill_scope = cgroup_identity
                    starttime = _proc_starttime(pid_root)
                    if starttime is None:
                        continue
                    identity = (pid, container_id, starttime)
                    if _proc_starttime(pid_root) != starttime:
                        raise RuntimeError(
                            "process identity changed during pidfd capture"
                        )
                    members[identity] = pidfd
                    retained = True

                try:
                    send_pidfd_signal(pidfd, 0)
                except ProcessLookupError:
                    if retained:
                        shims.pop(identity, None)
                        members.pop(identity, None)
                        retained = False
                    continue
                except OSError as exc:
                    raise RuntimeError(
                        "cannot revalidate process pidfd identity"
                    ) from exc
                if cgroup_identity is not None:
                    container_id, kill_scope = cgroup_identity
                    cgroups.setdefault(container_id, set()).add(kill_scope)
            finally:
                if not retained:
                    close_pidfd(pidfd)
    except BaseException:
        for pidfd in {*shims.values(), *members.values()}:
            close_pidfd(pidfd)
        raise
    return shims, members, cgroups


def _resolve_moby_cgroup(
    cgroup_root: Path,
    relative: PurePosixPath,
    container_id: str,
) -> Path:
    if _moby_cgroup_identity(str(relative)) != (container_id, relative):
        raise RuntimeError("cgroup path is not an exact Docker container scope")
    try:
        root_info = cgroup_root.lstat()
    except OSError as exc:
        raise RuntimeError("cgroup-v2 root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise RuntimeError("cgroup-v2 root is not a regular directory")
    root = cgroup_root.resolve(strict=True)
    candidate = cgroup_root
    for component in relative.parts[1:]:
        candidate = candidate / component
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise RuntimeError("cannot inspect Docker cgroup path") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Docker cgroup contains a link or non-directory")
    resolved_candidate = candidate.resolve(strict=True)
    try:
        resolved_candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Docker cgroup escaped the cgroup-v2 root") from exc
    return resolved_candidate


def _require_regular_nonsymlink(path: Path, message: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError(message) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(message)


def _read_nofollow_text(path: Path, limit: int = 4096) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        payload = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(payload) > limit:
        raise RuntimeError("cgroup control file exceeds its canonical bound")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError("cgroup control file is malformed") from exc


def _resolve_cgroup_root(cgroup_root: Path) -> Path:
    try:
        info = cgroup_root.lstat()
    except OSError as exc:
        raise RuntimeError("unified cgroup-v2 is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("unified cgroup-v2 root is not a regular directory")
    root = cgroup_root.resolve(strict=True)
    _require_regular_nonsymlink(
        root / "cgroup.controllers", "unified cgroup-v2 is unavailable"
    )
    return root


def _write_cgroup_kill(path: Path) -> None:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.write(descriptor, b"1\n")
    finally:
        os.close(descriptor)


def _cgroup_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    _require_regular_nonsymlink(
        path / "cgroup.kill", "Docker cgroup lacks cgroup-v2 kill support"
    )
    _require_regular_nonsymlink(
        path / "cgroup.events", "Docker cgroup events are unreadable"
    )
    try:
        fields = dict(
            row.split(maxsplit=1)
            for row in _read_nofollow_text(path / "cgroup.events").splitlines()
            if len(row.split(maxsplit=1)) == 2
        )
    except OSError as exc:
        raise RuntimeError("Docker cgroup events are unreadable") from exc
    if fields.get("populated") not in {"0", "1"}:
        raise RuntimeError("Docker cgroup populated state is malformed")
    return fields["populated"] == "0"


def _process_identity_alive(
    proc_root: Path,
    identity: tuple[int, str, str],
    pidfd: int,
    send_pidfd_signal: Callable[[int, int], None],
) -> bool:
    pid, _container_id, starttime = identity
    if pid <= 1 or not starttime.isdigit():
        raise RuntimeError("tracked Docker process identity is invalid")
    if _proc_starttime(proc_root / str(pid)) != starttime:
        return False
    try:
        send_pidfd_signal(pidfd, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RuntimeError("cannot revalidate tracked process pidfd") from exc
    return True


def _shim_identity_alive(
    proc_root: Path,
    shim_executable: Path,
    identity: tuple[int, str, str],
    pidfd: int,
    send_pidfd_signal: Callable[[int, int], None],
) -> bool:
    pid, _container_id, starttime = identity
    current = _read_moby_shim(proc_root / str(pid), shim_executable)
    if current != identity:
        # A same-starttime process that altered its observable shim identity
        # must block PASS, but is never signalled under a weaker identity.
        return _proc_starttime(proc_root / str(pid)) == starttime
    try:
        send_pidfd_signal(pidfd, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RuntimeError("cannot revalidate tracked shim pidfd") from exc
    return True


def _signal_tracked_processes(
    *,
    proc_root: Path,
    shim_executable: Path,
    members: dict[tuple[int, str, str], int],
    shims: dict[tuple[int, str, str], int],
    requested_signal: int,
    send_pidfd_signal: Callable[[int, int], None],
) -> None:
    for identity, pidfd in sorted(members.items()):
        if not _process_identity_alive(proc_root, identity, pidfd, send_pidfd_signal):
            continue
        try:
            send_pidfd_signal(pidfd, requested_signal)
        except ProcessLookupError:
            pass
        except OSError as exc:
            raise RuntimeError("cannot signal tracked process pidfd") from exc
    for identity, pidfd in sorted(shims.items()):
        pid = identity[0]
        if _read_moby_shim(proc_root / str(pid), shim_executable) != identity:
            continue
        try:
            send_pidfd_signal(pidfd, requested_signal)
        except ProcessLookupError:
            pass
        except OSError as exc:
            raise RuntimeError("cannot signal tracked shim pidfd") from exc


def _merge_moby_snapshot(
    *,
    shims: dict[tuple[int, str, str], int],
    members: dict[tuple[int, str, str], int],
    cgroups: dict[str, set[PurePosixPath]],
    known_ids: set[str],
    known_shims: dict[tuple[int, str, str], int],
    known_members: dict[tuple[int, str, str], int],
    known_cgroups: dict[str, set[PurePosixPath]],
    close_pidfd: Callable[[int], None],
) -> None:
    for incoming, known in ((shims, known_shims), (members, known_members)):
        for identity, pidfd in incoming.items():
            known_ids.add(identity[1])
            if identity in known:
                close_pidfd(pidfd)
            else:
                known[identity] = pidfd
    for container_id, paths in cgroups.items():
        known_cgroups.setdefault(container_id, set()).update(paths)


def _fence_moby_workloads(
    *,
    proc_root: Path = PROC_ROOT,
    cgroup_root: Path = CGROUP_ROOT,
    shim_executable: Path = SHIM_EXECUTABLE,
    container_ids: tuple[str, ...] = (),
    write_cgroup_kill: Callable[[Path], None] = _write_cgroup_kill,
    open_pidfd: Callable[[int], int] = _open_process_pidfd,
    send_pidfd_signal: Callable[[int, int], None] = _send_process_pidfd,
    close_pidfd: Callable[[int], None] = os.close,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, int | str]:
    if any(CONTAINER_ID_RE.fullmatch(value) is None for value in container_ids):
        raise RuntimeError("Docker supplied a non-canonical container id")
    root = _resolve_cgroup_root(cgroup_root)

    known_ids = set(container_ids)
    known_shims: dict[tuple[int, str, str], int] = {}
    known_members: dict[tuple[int, str, str], int] = {}
    known_cgroups: dict[str, set[PurePosixPath]] = {}
    stable_empty = 0
    try:
        for _sweep in range(12):
            shims, members, cgroups = _moby_snapshot(
                proc_root,
                shim_executable,
                open_pidfd,
                send_pidfd_signal,
                close_pidfd,
            )
            _merge_moby_snapshot(
                shims=shims,
                members=members,
                cgroups=cgroups,
                known_ids=known_ids,
                known_shims=known_shims,
                known_members=known_members,
                known_cgroups=known_cgroups,
                close_pidfd=close_pidfd,
            )

            for container_id in sorted(known_cgroups):
                for relative in sorted(known_cgroups[container_id], key=str):
                    try:
                        cgroup = _resolve_moby_cgroup(root, relative, container_id)
                    except FileNotFoundError:
                        continue
                    kill_file = cgroup / "cgroup.kill"
                    _require_regular_nonsymlink(
                        kill_file, "Docker cgroup lacks cgroup-v2 kill support"
                    )
                    write_cgroup_kill(kill_file)

            _signal_tracked_processes(
                proc_root=proc_root,
                shim_executable=shim_executable,
                members=known_members,
                shims=known_shims,
                requested_signal=signal.SIGTERM,
                send_pidfd_signal=send_pidfd_signal,
            )
            sleeper(0.1)
            _signal_tracked_processes(
                proc_root=proc_root,
                shim_executable=shim_executable,
                members=known_members,
                shims=known_shims,
                requested_signal=signal.SIGKILL,
                send_pidfd_signal=send_pidfd_signal,
            )

            sleeper(0.2)
            verify_shims, verify_members, verify_cgroups = _moby_snapshot(
                proc_root,
                shim_executable,
                open_pidfd,
                send_pidfd_signal,
                close_pidfd,
            )
            _merge_moby_snapshot(
                shims=verify_shims,
                members=verify_members,
                cgroups=verify_cgroups,
                known_ids=known_ids,
                known_shims=known_shims,
                known_members=known_members,
                known_cgroups=known_cgroups,
                close_pidfd=close_pidfd,
            )
            empty = not verify_shims and not verify_members
            empty = empty and not any(
                _process_identity_alive(proc_root, identity, pidfd, send_pidfd_signal)
                for identity, pidfd in known_members.items()
            )
            empty = empty and not any(
                _shim_identity_alive(
                    proc_root,
                    shim_executable,
                    identity,
                    pidfd,
                    send_pidfd_signal,
                )
                for identity, pidfd in known_shims.items()
            )
            for container_id, paths in known_cgroups.items():
                for relative in paths:
                    try:
                        cgroup = _resolve_moby_cgroup(root, relative, container_id)
                    except FileNotFoundError:
                        continue
                    if not _cgroup_is_empty(cgroup):
                        empty = False
            stable_empty = stable_empty + 1 if empty else 0
            if stable_empty >= 2:
                return {
                    "status": "PASS",
                    "container_ids_fenced": len(known_ids),
                    "stable_empty_sweeps": stable_empty,
                }
        raise RuntimeError("Docker/moby cgroups did not reach two stable empty sweeps")
    finally:
        for pidfd in {*known_shims.values(), *known_members.values()}:
            close_pidfd(pidfd)


def command_moby_fence(args: argparse.Namespace) -> int:
    _require_root()
    try:
        result = _fence_moby_workloads(container_ids=tuple(args.container_id or ()))
    except (OSError, RuntimeError) as exc:
        _die(f"Docker/moby cgroup fence failed: {type(exc).__name__}")
    print(json.dumps(result, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    download = commands.add_parser("gcs-download")
    download.add_argument("--uri", required=True)
    download.add_argument("--generation", required=True)
    download.add_argument("--size", type=int, required=True)
    download.add_argument("--sha256", required=True)
    download.add_argument("--output", required=True)
    download.set_defaults(handler=command_gcs_download)

    backup_permissions = commands.add_parser("gcs-release-backup-permissions")
    backup_permissions.add_argument("--bucket", required=True)
    backup_permissions.add_argument("--expected-service-account", required=True)
    backup_permissions.set_defaults(handler=command_gcs_release_backup_permissions)

    secret = commands.add_parser("secret-access")
    secret.add_argument("--project", required=True)
    secret.add_argument("--secret", required=True)
    secret.add_argument("--version", required=True)
    secret.add_argument("--allow-missing", action="store_true")
    secret.set_defaults(handler=command_secret_access)

    extract = commands.add_parser("safe-extract")
    extract.add_argument("--archive", required=True)
    extract.add_argument("--destination", required=True)
    extract.set_defaults(handler=command_safe_extract)

    tree = commands.add_parser("tree-sha256")
    tree.add_argument("--root", required=True)
    tree.add_argument("--exclude", action="append")
    tree.add_argument("--require-read-only", action="store_true")
    tree.set_defaults(handler=command_tree_sha256)

    env_validate = commands.add_parser("env-validate")
    env_validate.add_argument("--path", required=True)
    env_validate.add_argument("--forbid-prefix", action="append")
    env_validate.set_defaults(handler=command_env_validate)

    env_set = commands.add_parser("env-set")
    env_set.add_argument("--path", required=True)
    env_set.add_argument("--key", required=True)
    env_set.set_defaults(handler=command_env_set)

    lakehouse = commands.add_parser("lakehouse-contract")
    lakehouse.add_argument("--path", required=True)
    lakehouse.add_argument("--expected-bucket")
    lakehouse.set_defaults(handler=command_lakehouse_contract)

    release_backup = commands.add_parser("release-backup-contract")
    release_backup.add_argument("--path", required=True)
    release_backup.add_argument("--expected-bucket")
    release_backup.set_defaults(handler=command_release_backup_contract)

    fsync_dir = commands.add_parser("fsync-dir")
    fsync_dir.add_argument("path", nargs="+")
    fsync_dir.set_defaults(handler=command_fsync_dir)

    fsync_file = commands.add_parser("fsync-file")
    fsync_file.add_argument("path", nargs="+")
    fsync_file.set_defaults(handler=command_fsync_file)

    symlink_publish = commands.add_parser("symlink-publish")
    symlink_publish.add_argument("--target", required=True)
    symlink_publish.add_argument("--link", required=True)
    symlink_publish.set_defaults(handler=command_symlink_publish)

    docker_storage = commands.add_parser("docker-storage")
    docker_storage.add_argument(
        "storage_action",
        choices=("paths", "verify-unused", "prepare", "verify-mounted"),
    )
    docker_storage.set_defaults(handler=command_docker_storage)

    docker_apt_paths = commands.add_parser("docker-apt-paths")
    docker_apt_paths.set_defaults(handler=command_docker_apt_paths)

    runtime_auth_paths = commands.add_parser("runtime-auth-paths")
    runtime_auth_paths.set_defaults(handler=command_runtime_auth_paths)

    ubuntu_codename = commands.add_parser("ubuntu-codename")
    ubuntu_codename.set_defaults(handler=command_ubuntu_codename)

    docker_runtime = commands.add_parser("docker-runtime")
    docker_runtime.add_argument(
        "runtime_action",
        choices=(
            "install-contract",
            "verify-socket",
            "verify-prestart",
            "verify-live",
        ),
    )
    docker_runtime.set_defaults(handler=command_docker_runtime)

    evidence_root = commands.add_parser("evidence-root")
    evidence_root.set_defaults(handler=command_evidence_root)

    owner_empty = commands.add_parser("owner-unit-empty")
    owner_empty.set_defaults(handler=command_owner_unit_empty)

    terminal_receipt = commands.add_parser("terminal-receipt")
    terminal_receipt.add_argument("--path", required=True)
    terminal_receipt.add_argument("--deploy-ref", required=True)
    terminal_receipt.add_argument("--helper-ref", required=True)
    terminal_receipt.add_argument("--startup-contract-sha256", required=True)
    terminal_receipt.set_defaults(handler=command_terminal_receipt)

    foundation_publish = commands.add_parser("foundation-receipt-publish")
    foundation_publish.add_argument("--terminal-receipt", required=True)
    foundation_publish.add_argument("--operation-marker", required=True)
    foundation_publish.add_argument("--deploy-ref", required=True)
    foundation_publish.add_argument("--helper-ref", required=True)
    foundation_publish.add_argument("--startup-contract-sha256", required=True)
    foundation_publish.set_defaults(handler=command_foundation_receipt_publish)

    foundation_collect = commands.add_parser("foundation-receipt-collect")
    foundation_collect.add_argument("--path", required=True)
    foundation_collect.add_argument("--deploy-ref", required=True)
    foundation_collect.add_argument("--helper-ref", required=True)
    foundation_collect.add_argument("--startup-contract-sha256", required=True)
    foundation_collect.set_defaults(handler=command_foundation_receipt_collect)

    host_identity = commands.add_parser("host-identity-install")
    host_identity.add_argument("--startup-contract", required=True)
    host_identity.add_argument("--output", required=True)
    host_identity.set_defaults(handler=command_host_identity_install)

    operation_gate = commands.add_parser("operation-gate")
    operation_gate.add_argument("--app-root", required=True)
    operation_gate.add_argument("--state-link", required=True)
    operation_gate.add_argument("--operation-marker", required=True)
    operation_gate.add_argument(
        "--mode", choices=("verify", "print-helpers", "authorize-start"), required=True
    )
    operation_gate.set_defaults(handler=command_operation_gate)

    startup = commands.add_parser("startup-config")
    startup.add_argument("--output", required=True)
    startup.set_defaults(handler=command_startup_config)

    startup_identities = commands.add_parser("startup-identities")
    startup_identities.add_argument("--path", required=True)
    startup_identities.set_defaults(handler=command_startup_identities)

    startup_predecessor = commands.add_parser("startup-predecessor")
    startup_predecessor.add_argument("--path", required=True)
    startup_predecessor.set_defaults(handler=command_startup_predecessor)

    compose_reboot_services = commands.add_parser("compose-reboot-services")
    compose_reboot_services.add_argument("--path", required=True)
    compose_reboot_services.set_defaults(handler=command_compose_reboot_services)

    moby_fence = commands.add_parser("moby-fence")
    moby_fence.add_argument("--container-id", action="append")
    moby_fence.set_defaults(handler=command_moby_fence)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
