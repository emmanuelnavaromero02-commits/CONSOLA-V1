#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

SHA256 = re.compile(r"[0-9a-f]{64}")


class SecureOutputError(RuntimeError):
    pass


def _write_all(fd: int, body: bytes) -> None:
    view = memoryview(body)
    while view:
        written = os.write(fd, view)
        if written <= 0:  # pragma: no cover - os.write either writes or raises
            raise SecureOutputError("secure release output write did not progress")
        view = view[written:]


def create_exclusive_output(
    path: Path,
    body: bytes,
    *,
    expected_sha256: str,
    baseline: Path,
) -> str:
    if SHA256.fullmatch(expected_sha256) is None:
        raise SecureOutputError("expected release output SHA-256 is invalid")
    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != expected_sha256:
        raise SecureOutputError("final release output differs from its Actions authority")
    if path.name in {"", ".", ".."} or path.parent == path:
        raise SecureOutputError("secure release output path is invalid")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise SecureOutputError("secure release output parent is not a real directory") from exc
    output_fd = -1
    try:
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise SecureOutputError("secure release output parent is not private")
        try:
            baseline_stat = os.stat(baseline, follow_symlinks=False)
        except OSError as exc:
            raise SecureOutputError("release baseline output is unavailable") from exc
        if not stat.S_ISREG(baseline_stat.st_mode):
            raise SecureOutputError("release baseline output is not a regular file")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            output_fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            raise SecureOutputError("secure release output destination already exists") from exc
        opened = os.fstat(output_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
        ):
            raise SecureOutputError("secure release output inode is not trusted")
        if (opened.st_dev, opened.st_ino) == (
            baseline_stat.st_dev,
            baseline_stat.st_ino,
        ):
            raise SecureOutputError("final release output aliases its baseline inode")
        _write_all(output_fd, body)
        os.fsync(output_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
        ):
            raise SecureOutputError("secure release output path changed during capture")
        os.fsync(parent_fd)
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        os.close(parent_fd)
    return actual_sha256


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        digest = create_exclusive_output(
            args.output,
            sys.stdin.buffer.read(),
            expected_sha256=args.expected_sha256,
            baseline=args.baseline,
        )
    except (OSError, SecureOutputError) as exc:
        print(f"SECURE RELEASE OUTPUT BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"SECURE RELEASE OUTPUT PASS: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
