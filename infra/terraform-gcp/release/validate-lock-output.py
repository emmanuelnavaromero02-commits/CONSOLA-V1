#!/usr/bin/env python3
"""Validate the only host path where an authenticated image lock may appear."""

from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path
from typing import Iterable


CANONICAL_LOCK_ROOT = Path("/opt/modecissions/shared/image-locks")
CANONICAL_MANAGED_PARENTS = (
    Path("/opt"),
    Path("/opt/modecissions"),
    Path("/opt/modecissions/shared"),
    CANONICAL_LOCK_ROOT,
)
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?")


def _validate_directory(
    path: Path, *, uid: int, gid: int, exact_mode: int | None = None
) -> None:
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or mode & 0o022
        or (exact_mode is not None and mode != exact_mode)
        or path.resolve(strict=True) != path
    ):
        raise ValueError(f"unsafe managed image-lock directory: {path}")


def validate_lock_output(
    lock_file: Path,
    target_revision: str,
    *,
    authority_mode: str = "candidate",
    image_tag: str | None = None,
    version: str | None = None,
    lock_root: Path = CANONICAL_LOCK_ROOT,
    managed_parents: Iterable[Path] = CANONICAL_MANAGED_PARENTS,
    uid: int = 0,
    gid: int = 0,
) -> None:
    if version is None:
        version = "1.45.207-beta"
    if image_tag is None and authority_mode == "candidate":
        image_tag = f"candidate-{target_revision}"
    if (
        os.geteuid() != uid
        or SHA.fullmatch(target_revision) is None
        or authority_mode not in {"candidate", "published", "legacy-rollback"}
        or version is None
        or VERSION.fullmatch(version) is None
        or image_tag
        != (
            f"candidate-{target_revision}"
            if authority_mode == "candidate"
            else f"v{version}"
        )
    ):
        raise ValueError("image-lock validation requires root and one exact revision")
    if (
        not lock_file.is_absolute()
        or str(lock_file) != os.path.abspath(lock_file)
        or lock_file.name != "release-images.env"
        or lock_file.parent.parent != lock_root
    ):
        raise ValueError("image lock is outside the canonical release hierarchy")
    directory_name = lock_file.parent.name
    if re.fullmatch(rf"\.{target_revision}\.tmp\.[1-9][0-9]*", directory_name) is None:
        raise ValueError("image-lock output must remain in revision-bound staging")
    for parent in managed_parents:
        _validate_directory(parent, uid=uid, gid=gid)
    _validate_directory(lock_file.parent, uid=uid, gid=gid, exact_mode=0o700)
    authority_file = Path(f"{lock_file}.authority.json")
    commit_file = Path(f"{lock_file}.commit.json")
    allowed = {
        lock_file.name,
        authority_file.name,
        commit_file.name,
        f"{lock_file.name}.pending",
        f"{authority_file.name}.pending",
        f"{commit_file.name}.pending",
    }
    actual = {entry.name for entry in lock_file.parent.iterdir()}
    if not actual <= allowed:
        raise ValueError("image-lock staging contains an unexpected file")

    def inspect_pair(path: Path, expected_mode: int) -> tuple[bool, bool]:
        pending = Path(f"{path}.pending")
        destination_info = path.lstat() if os.path.lexists(path) else None
        pending_info = pending.lstat() if os.path.lexists(pending) else None
        if (
            destination_info
            and pending_info
            and (
                destination_info.st_dev,
                destination_info.st_ino,
            )
            != (pending_info.st_dev, pending_info.st_ino)
        ):
            raise ValueError("image-lock destination and pending names are ambiguous")
        expected_links = 2 if destination_info and pending_info else 1
        for info in (destination_info, pending_info):
            if info is None:
                continue
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != uid
                or info.st_gid != gid
                or info.st_nlink != expected_links
                or stat.S_IMODE(info.st_mode) != expected_mode
            ):
                raise ValueError("interrupted image-lock staging file is unsafe")
        return destination_info is not None, pending_info is not None

    authority_state = inspect_pair(authority_file, 0o600)
    lock_state = inspect_pair(lock_file, 0o600)
    commit_state = inspect_pair(commit_file, 0o400)

    # These are the only states reachable when authority, lock, and commit are
    # published in that order. In particular, destination+pending is accepted
    # only when both names are the same two-link inode: it is the precise crash
    # state after link(2), not a second authority or an ambiguous replacement.
    if (lock_state[0] or lock_state[1]) and authority_state != (True, False):
        raise ValueError("lock publication exists before durable authority")
    if (commit_state[0] or commit_state[1]) and not (
        authority_state == (True, False) and lock_state == (True, False)
    ):
        raise ValueError("commit publication exists before durable data outputs")
    if commit_state == (True, False) and actual != {
        lock_file.name,
        authority_file.name,
        commit_file.name,
    }:
        raise ValueError("committed image-lock staging is structurally incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument(
        "--authority-mode",
        choices=("candidate", "published", "legacy-rollback"),
        required=True,
    )
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        validate_lock_output(
            args.lock_file,
            args.target_revision,
            authority_mode=args.authority_mode,
            image_tag=args.image_tag,
            version=args.version,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid image-lock output path: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
