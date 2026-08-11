#!/usr/bin/env python3
"""Fail-closed host I/O primitives for the canonical GCP release path.

The helper deliberately owns all network credentials in-process.  Callers pass
only public object identity (URI, generation, size, SHA-256) and filesystem
paths.  It is embedded in first-boot metadata and transported beside every
remote day-2 script so artifact handling has one auditable implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import BinaryIO


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GENERATION_RE = re.compile(r"^[1-9][0-9]*$")
GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
ENVIRONMENT_RE = re.compile(r"^[a-z][a-z0-9-]{0,29}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENV_BYTES = 2 * 1024 * 1024


def _die(message: str) -> None:
    raise SystemExit(f"omega-safe-io: {message}")


def _require_root() -> None:
    if os.geteuid() != 0 and os.environ.get("OMEGA_SAFE_IO_TEST_NON_ROOT") != "1":
        _die("remote host operation requires root")


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_private_parent(path: Path) -> None:
    parent = path.parent
    info = parent.stat(follow_symlinks=False)
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


def _metadata_token() -> str:
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
        payload = json.load(response)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 8192
        or any(char.isspace() for char in token)
    ):
        _die("instance metadata returned an invalid access token")
    return token


def _request_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        payload = json.load(response)
    if not isinstance(payload, dict):
        _die("GCS metadata response is malformed")
    return payload


def command_gcs_release_backup_permissions(args: argparse.Namespace) -> int:
    """Prove effective VM permissions without attempting a destructive call."""
    _require_root()
    if GCS_BUCKET_RE.fullmatch(args.bucket) is None:
        _die("release backup bucket is invalid")
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
        "storage.buckets.setRetentionPolicy",
        "storage.buckets.overrideUnlockedRetentionPolicy",
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
    granted_raw = payload.get("permissions", [])
    if not isinstance(granted_raw, list) or not all(
        isinstance(value, str) for value in granted_raw
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


def command_gcs_download(args: argparse.Namespace) -> int:
    _require_root()
    if not GENERATION_RE.fullmatch(args.generation):
        _die("GCS generation must be an exact positive integer")
    if args.size < 1:
        _die("GCS object size must be positive")
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

    token = _metadata_token()
    quoted_bucket = urllib.parse.quote(bucket, safe="")
    quoted_key = urllib.parse.quote(key, safe="")
    metadata_url = (
        f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o/"
        f"{quoted_key}?{urllib.parse.urlencode({'generation': args.generation, 'fields': 'bucket,name,generation,size,crc32c,md5Hash'})}"
    )
    metadata = _request_json(metadata_url, token)
    if (
        metadata.get("bucket") != bucket
        or metadata.get("name") != key
        or str(metadata.get("generation", "")) != args.generation
        or int(metadata.get("size", -1)) != args.size
    ):
        _die("GCS object metadata differs from the reviewed identity")

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
            urllib.request.urlopen(request, timeout=180) as response,  # nosec B310
            os.fdopen(descriptor, "wb", closefd=False) as target,
        ):
            actual_size, actual_sha256 = _copy_and_hash(response, target)
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
    if actual_size != args.size or actual_sha256 != args.sha256:
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
    if re.fullmatch(r"(?:latest|[1-9][0-9]*)", args.version) is None:
        _die("Secret Manager version is invalid")
    token = _metadata_token()
    project = urllib.parse.quote(args.project, safe="")
    secret = urllib.parse.quote(args.secret, safe="")
    version = urllib.parse.quote(args.version, safe="")
    url = (
        f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/"
        f"{secret}/versions/{version}:access"
    )
    payload = _request_json(url, token).get("payload")
    encoded = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(encoded, str) or not encoded:
        _die("Secret Manager response lacks a payload")
    try:
        import base64

        decoded = base64.b64decode(encoded, validate=True)
    except ValueError:
        _die("Secret Manager payload encoding is invalid")
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
    info = destination.stat(follow_symlinks=False)
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
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or resolved_root != Path(os.path.abspath(root))
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
    info = path.stat(follow_symlinks=False)
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


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        _die(f"startup {label} keys differ from the canonical contract")
    return value


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
            "source",
            "urls",
            "admin_email",
            "cookie_secure",
            "data_disk_size_bytes",
            "lakehouse_bucket",
            "release_backup_bucket",
            "lakehouse_endpoint",
            "enable_airflow_scheduler",
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
    technical_console = _canonical_url(urls["technical_console"])
    technical_workspace = _canonical_url(urls["technical_workspace"])
    if airflow != f"{console}/airflow":
        _die("startup Airflow URL differs from console URL")
    if not technical_console.startswith(
        "http://"
    ) or not technical_workspace.startswith("http://"):
        _die("startup technical URLs must use HTTP load-balancer addresses")
    public_https = console.startswith("https://") and workspace.startswith("https://")
    if root["cookie_secure"] is not public_https:
        _die("startup cookie policy differs from public URL scheme")
    if root["enable_airflow_scheduler"] is not True:
        _die("canonical GCP startup must enable exactly one scheduler")
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
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
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
            r"[a-z0-9]([a-z0-9-]{0,62}\.)+[a-z]{2,63}", root["lakehouse_endpoint"]
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
    backup_permissions.set_defaults(handler=command_gcs_release_backup_permissions)

    secret = commands.add_parser("secret-access")
    secret.add_argument("--project", required=True)
    secret.add_argument("--secret", required=True)
    secret.add_argument("--version", default="latest")
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

    startup = commands.add_parser("startup-config")
    startup.add_argument("--output", required=True)
    startup.set_defaults(handler=command_startup_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
