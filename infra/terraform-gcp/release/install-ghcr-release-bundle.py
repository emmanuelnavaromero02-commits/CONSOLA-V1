#!/usr/bin/python3 -I
"""Atomically install the sealed, root-only GCP GHCR release helper bundle."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


CANONICAL_BUNDLE_ROOT = Path("/opt/modecissions/shared/ghcr-release-bundles")
HOST_IDENTITY_PATH = Path("/etc/omega/gcp-host-identity.json")
SECRET_AUTHORITY_PATH = Path("/etc/omega/ghcr-pull-secret-authority.json")
SOURCE_MANIFEST = Path("infra/terraform-gcp/release/ghcr-release-bundle.manifest.json")
MAX_FILE_BYTES = 8 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
EXPECTED_HOST_IDENTITY = {
    "instance_id": "4767392334132429161",
    "instance_name": "omega-staging-app",
    "project_id": "project-dd5ba7fa-374c-4554-ae6",
    "schema_version": 1,
    "service_account_email": (
        "omega-staging-app@project-dd5ba7fa-374c-4554-ae6.iam.gserviceaccount.com"
    ),
    "zone": "us-central1-a",
}
SECRET_AUTHORITY = {
    "environment": "staging",
    "project_id": EXPECTED_HOST_IDENTITY["project_id"],
    "schema_version": 1,
    "secret_id": "omega-staging-ghcr_pull_credentials",
    "secret_version_alias": "active",
}
BUNDLE_FILES = {
    "ghcr-auth-run.sh": ("infra/terraform-gcp/release/ghcr-auth-run.sh", 0o500),
    "secure-ghcr-session.py": (
        "infra/terraform-gcp/release/secure-ghcr-session.py",
        0o400,
    ),
    "preflight-release-images.sh": (
        "infra/terraform-gcp/release/preflight-release-images.sh",
        0o500,
    ),
    "validate-image-authority.py": (
        "infra/terraform-gcp/release/validate-image-authority.py",
        0o400,
    ),
    "validate-lock-output.py": (
        "infra/terraform-gcp/release/validate-lock-output.py",
        0o400,
    ),
    "publish-image-lock.py": (
        "infra/terraform-gcp/release/publish-image-lock.py",
        0o400,
    ),
    "release_images.py": ("scripts/release_images.py", 0o400),
}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def _read_regular(
    path: Path,
    *,
    uid: int,
    gid: int,
    maximum: int = MAX_FILE_BYTES,
) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != uid
            or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError(f"unsafe bundle source: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ValueError(f"short bundle source read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (  # noqa: E731
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ValueError(f"bundle source changed while read: {path}")
        return b"".join(chunks), before
    finally:
        os.close(descriptor)


def _validate_directory(path: Path, *, uid: int, gid: int) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) & 0o022
        or path.resolve(strict=True) != path
    ):
        raise ValueError(f"unsafe bundle directory: {path}")


def _load_source_manifest(
    release_root: Path, *, uid: int, gid: int
) -> dict[str, dict[str, object]]:
    path = release_root / SOURCE_MANIFEST
    current = path.parent
    while current != release_root:
        _validate_directory(current, uid=uid, gid=gid)
        current = current.parent
    raw, _info = _read_regular(path, uid=uid, gid=gid)
    try:
        document = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("bundle source manifest is invalid") from exc
    canonical = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if (
        raw != canonical
        or not isinstance(document, dict)
        or set(document) != {"files", "schema_version"}
        or document.get("schema_version") != 1
        or not isinstance(document.get("files"), dict)
        or set(document["files"]) != set(BUNDLE_FILES)
    ):
        raise ValueError("bundle source manifest contract differs")
    files = document["files"]
    assert isinstance(files, dict)
    for name, (source, mode) in BUNDLE_FILES.items():
        row = files.get(name)
        if (
            not isinstance(row, dict)
            or set(row) != {"mode", "sha256", "source"}
            or row.get("source") != source
            or row.get("mode") != f"{mode:04o}"
            or re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))) is None
        ):
            raise ValueError(f"bundle source manifest entry differs: {name}")
    return files


def _write_file(directory_fd: int, name: str, payload: bytes, mode: int) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=directory_fd,
    )
    try:
        os.fchown(descriptor, os.geteuid(), os.getegid())
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short bundle write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _load_host_identity(*, uid: int, gid: int) -> bytes:
    if (
        not HOST_IDENTITY_PATH.is_absolute()
        or not SECRET_AUTHORITY_PATH.is_absolute()
        or HOST_IDENTITY_PATH.name != "gcp-host-identity.json"
        or SECRET_AUTHORITY_PATH.name != "ghcr-pull-secret-authority.json"
        or HOST_IDENTITY_PATH.parent != SECRET_AUTHORITY_PATH.parent
    ):
        raise ValueError("canonical GHCR host authority paths differ")
    _validate_directory(HOST_IDENTITY_PATH.parent.parent, uid=uid, gid=gid)
    _validate_directory(HOST_IDENTITY_PATH.parent, uid=uid, gid=gid)
    raw, info = _read_regular(
        HOST_IDENTITY_PATH,
        uid=uid,
        gid=gid,
        maximum=4096,
    )
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("canonical GCP host identity is invalid") from exc
    if (
        raw != _canonical_json(value)
        or value != EXPECTED_HOST_IDENTITY
        or stat.S_IMODE(info.st_mode) != 0o400
    ):
        raise ValueError("canonical GCP host identity differs")
    return raw


def _install_secret_authority(*, uid: int, gid: int) -> None:
    identity = _load_host_identity(uid=uid, gid=gid)
    payload = _canonical_json(SECRET_AUTHORITY)
    try:
        existing, info = _read_regular(
            SECRET_AUTHORITY_PATH,
            uid=uid,
            gid=gid,
            maximum=4096,
        )
    except FileNotFoundError:
        existing = b""
        info = None
    if existing:
        if existing != payload or info is None or stat.S_IMODE(info.st_mode) != 0o400:
            raise ValueError("existing GHCR secret authority conflicts")
    else:
        parent_fd = os.open(
            SECRET_AUTHORITY_PATH.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        temporary = (
            f".{SECRET_AUTHORITY_PATH.name}.tmp.{os.getpid()}." f"{os.urandom(8).hex()}"
        )
        try:
            _write_file(parent_fd, temporary, payload, 0o400)
            try:
                os.link(
                    temporary,
                    SECRET_AUTHORITY_PATH.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                concurrent, concurrent_info = _read_regular(
                    SECRET_AUTHORITY_PATH,
                    uid=uid,
                    gid=gid,
                    maximum=4096,
                )
                if (
                    concurrent != payload
                    or stat.S_IMODE(concurrent_info.st_mode) != 0o400
                ):
                    raise ValueError("concurrent GHCR secret authority conflicts")
            os.unlink(temporary, dir_fd=parent_fd)
            temporary = ""
            os.fsync(parent_fd)
        finally:
            if temporary:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)
    if _load_host_identity(uid=uid, gid=gid) != identity:
        raise ValueError("GCP host identity changed during GHCR authority publication")
    installed, installed_info = _read_regular(
        SECRET_AUTHORITY_PATH,
        uid=uid,
        gid=gid,
        maximum=4096,
    )
    if installed != payload or stat.S_IMODE(installed_info.st_mode) != 0o400:
        raise ValueError("GHCR secret authority durable read-back differs")


def _rename_noreplace(root_fd: int, source_name: str, destination_name: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOSYS, "renameat2 is required for bundle publication")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            root_fd,
            os.fsencode(source_name),
            root_fd,
            os.fsencode(destination_name),
            1,  # RENAME_NOREPLACE
        )
    elif sys.platform == "darwin":
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise OSError(
                errno.ENOSYS, "renameatx_np is required for bundle publication"
            )
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            root_fd,
            os.fsencode(source_name),
            root_fd,
            os.fsencode(destination_name),
            0x00000004,  # RENAME_EXCL
        )
    else:
        raise OSError(errno.ENOSYS, "exclusive rename is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _installed_manifest(*, source_sha: str, payloads: dict[str, bytes]) -> bytes:
    document = {
        "files": {
            name: {
                "mode": f"{BUNDLE_FILES[name][1]:04o}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
            for name, payload in sorted(payloads.items())
        },
        "schema_version": 1,
        "source_sha": source_sha,
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _verify_installed(
    destination: Path,
    *,
    source_sha: str,
    payloads: dict[str, bytes],
    uid: int,
    gid: int,
) -> None:
    _validate_directory(destination, uid=uid, gid=gid)
    if stat.S_IMODE(destination.lstat().st_mode) != 0o500:
        raise ValueError("installed bundle directory mode differs")
    expected_names = set(BUNDLE_FILES) | {"bundle-manifest.json"}
    if {item.name for item in destination.iterdir()} != expected_names:
        raise ValueError("installed bundle tree differs")
    expected_manifest = _installed_manifest(source_sha=source_sha, payloads=payloads)
    for name, payload in {
        **payloads,
        "bundle-manifest.json": expected_manifest,
    }.items():
        raw, info = _read_regular(destination / name, uid=uid, gid=gid)
        expected_mode = (
            0o400 if name == "bundle-manifest.json" else BUNDLE_FILES[name][1]
        )
        if raw != payload or stat.S_IMODE(info.st_mode) != expected_mode:
            raise ValueError(f"installed bundle bytes or mode differ: {name}")


def install_bundle(
    release_root: Path,
    source_sha: str,
    *,
    bundle_root: Path = CANONICAL_BUNDLE_ROOT,
    uid: int = 0,
    gid: int = 0,
) -> Path:
    if os.geteuid() != uid or os.getegid() != gid:
        raise ValueError("bundle installation requires the configured owner")
    if (
        SHA.fullmatch(source_sha) is None
        or not release_root.is_absolute()
        or release_root.resolve(strict=True) != release_root
        or release_root.name != source_sha
        or not bundle_root.is_absolute()
        or bundle_root.resolve(strict=True) != bundle_root
        or bundle_root.name != "ghcr-release-bundles"
    ):
        raise ValueError("bundle release/root identity differs")
    _validate_directory(release_root, uid=uid, gid=gid)
    _validate_directory(bundle_root, uid=uid, gid=gid)
    source_manifest = _load_source_manifest(release_root, uid=uid, gid=gid)
    payloads: dict[str, bytes] = {}
    for name, (source_name, _destination_mode) in BUNDLE_FILES.items():
        source = release_root / source_name
        if source.resolve(strict=True) != source:
            raise ValueError(f"bundle source escaped release root: {name}")
        current = source.parent
        while current != release_root:
            _validate_directory(current, uid=uid, gid=gid)
            current = current.parent
        raw, _info = _read_regular(source, uid=uid, gid=gid)
        if hashlib.sha256(raw).hexdigest() != source_manifest[name]["sha256"]:
            raise ValueError(f"bundle source digest differs: {name}")
        payloads[name] = raw

    destination = bundle_root / source_sha
    if os.path.lexists(destination):
        _verify_installed(
            destination,
            source_sha=source_sha,
            payloads=payloads,
            uid=uid,
            gid=gid,
        )
        _install_secret_authority(uid=uid, gid=gid)
        return destination

    staging = bundle_root / f".{source_sha}.tmp.{os.getpid()}"
    if os.path.lexists(staging):
        raise ValueError("bundle staging path already exists")
    os.mkdir(staging, 0o700)
    directory_fd = os.open(
        staging,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    published = False
    try:
        for name, payload in sorted(payloads.items()):
            _write_file(directory_fd, name, payload, BUNDLE_FILES[name][1])
        _write_file(
            directory_fd,
            "bundle-manifest.json",
            _installed_manifest(source_sha=source_sha, payloads=payloads),
            0o400,
        )
        os.fchmod(directory_fd, 0o500)
        os.fsync(directory_fd)
        root_fd = os.open(
            bundle_root,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            _rename_noreplace(root_fd, staging.name, destination.name)
            published = True
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    finally:
        os.close(directory_fd)
        if not published and os.path.lexists(staging):
            for item in staging.iterdir():
                item.unlink()
            staging.rmdir()
    _verify_installed(
        destination,
        source_sha=source_sha,
        payloads=payloads,
        uid=uid,
        gid=gid,
    )
    _install_secret_authority(uid=uid, gid=gid)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        destination = install_bundle(args.release_root, args.source_sha)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"GHCR release bundle installation failed: {exc}") from exc
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
