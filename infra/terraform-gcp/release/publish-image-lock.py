#!/usr/bin/env python3
"""Crash-consistently publish the three GCP release image-lock artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
from dataclasses import dataclass
from pathlib import Path


MAX_LOCK_BYTES = 128 * 1024
MAX_AUTHORITY_BYTES = 1024 * 1024
MAX_COMMIT_BYTES = 16 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?")


@dataclass(frozen=True)
class PairState:
    destination: bool
    pending: bool


def _read_fd(descriptor: int, maximum: int) -> bytes:
    info = os.fstat(descriptor)
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) != info.st_size or not raw or len(raw) > maximum:
        raise ValueError("publication file has an invalid size or changed while read")
    return raw


def _read_path(path: Path, *, mode: int, maximum: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_nlink != 1
        ):
            raise ValueError("unsafe publication source")
        return _read_fd(descriptor, maximum)
    finally:
        os.close(descriptor)


class Publisher:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory_fd = os.open(
            directory,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(self.directory_fd)
        path_info = os.lstat(directory)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            os.close(self.directory_fd)
            raise ValueError("unsafe publication directory")

    def close(self) -> None:
        os.close(self.directory_fd)

    def _crash(self, artifact: str, phase: str) -> None:
        # The canonical auth wrapper rebuilds a minimal environment and never
        # forwards either test variable. They exist solely for subprocess
        # crash-injection tests of every persistence boundary.
        if (
            os.environ.get("OMEGA_IMAGE_LOCK_TESTING") == "1"
            and os.environ.get("OMEGA_IMAGE_LOCK_CRASH_AT") == f"{artifact}:{phase}"
        ):
            os.kill(os.getpid(), signal.SIGKILL)

    def _stat(self, name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def _read_name(
        self, name: str, *, mode: int, maximum: int, links: tuple[int, ...]
    ) -> bytes:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=self.directory_fd,
        )
        try:
            info = os.fstat(descriptor)
            path_info = self._stat(name)
            if (
                path_info is None
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) != mode
                or info.st_nlink not in links
                or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
            ):
                raise ValueError("unsafe publication entry")
            return _read_fd(descriptor, maximum)
        finally:
            os.close(descriptor)

    def inspect_pair(
        self, destination: str, *, mode: int, maximum: int, payload: bytes
    ) -> PairState:
        pending = f"{destination}.pending"
        destination_info = self._stat(destination)
        pending_info = self._stat(pending)
        if destination_info is not None and pending_info is not None:
            if (destination_info.st_dev, destination_info.st_ino) != (
                pending_info.st_dev,
                pending_info.st_ino,
            ):
                raise ValueError("destination and pending names are ambiguous")
        if destination_info is not None:
            expected_links = (2,) if pending_info is not None else (1,)
            if (
                self._read_name(
                    destination, mode=mode, maximum=maximum, links=expected_links
                )
                != payload
            ):
                raise ValueError("published artifact differs from exact recovery bytes")
        if pending_info is not None:
            expected_links = (2,) if destination_info is not None else (1,)
            if (
                self._read_name(
                    pending, mode=mode, maximum=maximum, links=expected_links
                )
                != payload
            ):
                raise ValueError("pending artifact differs from exact recovery bytes")
        return PairState(destination_info is not None, pending_info is not None)

    def _write_all(self, descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short publication write")
            offset += written

    def normalize(
        self,
        artifact: str,
        destination: str,
        payload: bytes,
        *,
        mode: int,
        maximum: int,
    ) -> None:
        state = self.inspect_pair(
            destination, mode=mode, maximum=maximum, payload=payload
        )
        pending = f"{destination}.pending"
        if not state.destination and not state.pending:
            descriptor = os.open(
                pending,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                mode,
                dir_fd=self.directory_fd,
            )
            try:
                self._write_all(descriptor, payload)
                os.fsync(descriptor)
                self._crash(artifact, "pending-file-fsync")
            finally:
                os.close(descriptor)
            os.fsync(self.directory_fd)
            self._crash(artifact, "pending-dir-fsync")
            state = self.inspect_pair(
                destination, mode=mode, maximum=maximum, payload=payload
            )
        if not state.destination:
            os.link(
                pending,
                destination,
                src_dir_fd=self.directory_fd,
                dst_dir_fd=self.directory_fd,
                follow_symlinks=False,
            )
            self._crash(artifact, "link")
            os.fsync(self.directory_fd)
            self._crash(artifact, "link-dir-fsync")
            state = self.inspect_pair(
                destination, mode=mode, maximum=maximum, payload=payload
            )
        if state.pending:
            os.unlink(pending, dir_fd=self.directory_fd)
            self._crash(artifact, "unlink")
            os.fsync(self.directory_fd)
            self._crash(artifact, "unlink-dir-fsync")
        final = self.inspect_pair(
            destination, mode=mode, maximum=maximum, payload=payload
        )
        if final != PairState(True, False):
            raise ValueError("publication did not reach one canonical final name")


def _commit_payload(
    authority: bytes,
    lock: bytes,
    *,
    source_sha: str,
    version: str,
    image_tag: str,
    authority_mode: str,
) -> bytes:
    document = {
        "authority_mode": authority_mode,
        "authority_sha256": "sha256:" + hashlib.sha256(authority).hexdigest(),
        "authority_size": len(authority),
        "image_tag": image_tag,
        "lock_sha256": "sha256:" + hashlib.sha256(lock).hexdigest(),
        "lock_size": len(lock),
        "schema_version": 1,
        "source_sha": source_sha,
        "version": version,
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def publish(args: argparse.Namespace) -> None:
    if (
        SHA.fullmatch(args.source_sha) is None
        or VERSION.fullmatch(args.version) is None
        or not args.destination_lock.is_absolute()
        or os.fspath(args.destination_lock) != os.path.abspath(args.destination_lock)
        or args.destination_lock.name != "release-images.env"
        or (
            args.authority_mode == "candidate"
            and args.image_tag != f"candidate-{args.source_sha}"
        )
        or (args.authority_mode != "candidate" and args.image_tag != f"v{args.version}")
    ):
        raise ValueError("publication identity is not exact")
    authority = _read_path(
        args.source_authority, mode=0o600, maximum=MAX_AUTHORITY_BYTES
    )
    lock = _read_path(args.source_lock, mode=0o600, maximum=MAX_LOCK_BYTES)
    commit = _commit_payload(
        authority,
        lock,
        source_sha=args.source_sha,
        version=args.version,
        image_tag=args.image_tag,
        authority_mode=args.authority_mode,
    )
    directory = args.destination_lock.parent
    names = {
        "lock": args.destination_lock.name,
        "authority": f"{args.destination_lock.name}.authority.json",
        "commit": f"{args.destination_lock.name}.commit.json",
    }
    if args.destination_lock != directory / names["lock"]:
        raise ValueError("destination lock path is not canonical")
    publisher = Publisher(directory)
    try:
        # Reject impossible orderings before making any repair. A commit name
        # (including its one-link pending form) can exist only after both data
        # artifacts reached their destination names; a lock can exist only
        # after authority did. This distinguishes recoverable crashes from
        # deletion/replacement or forged ambiguous state.
        authority_state = publisher.inspect_pair(
            names["authority"],
            mode=0o600,
            maximum=MAX_AUTHORITY_BYTES,
            payload=authority,
        )
        lock_state = publisher.inspect_pair(
            names["lock"], mode=0o600, maximum=MAX_LOCK_BYTES, payload=lock
        )
        commit_state = publisher.inspect_pair(
            names["commit"], mode=0o400, maximum=MAX_COMMIT_BYTES, payload=commit
        )
        if (
            lock_state.destination or lock_state.pending
        ) and authority_state != PairState(True, False):
            raise ValueError("lock publication exists before durable authority")
        if (commit_state.destination or commit_state.pending) and not (
            authority_state == PairState(True, False)
            and lock_state == PairState(True, False)
        ):
            raise ValueError("commit publication exists before durable data outputs")

        publisher.normalize(
            "authority",
            names["authority"],
            authority,
            mode=0o600,
            maximum=MAX_AUTHORITY_BYTES,
        )
        publisher.normalize(
            "lock",
            names["lock"],
            lock,
            mode=0o600,
            maximum=MAX_LOCK_BYTES,
        )
        publisher.normalize(
            "commit",
            names["commit"],
            commit,
            mode=0o400,
            maximum=MAX_COMMIT_BYTES,
        )
        os.fsync(publisher.directory_fd)
    finally:
        publisher.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-lock", type=Path, required=True)
    parser.add_argument("--source-authority", type=Path, required=True)
    parser.add_argument("--destination-lock", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument(
        "--authority-mode",
        choices=("candidate", "published", "legacy-rollback"),
        required=True,
    )
    return parser


def main() -> int:
    try:
        publish(build_parser().parse_args())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"image-lock publication failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
