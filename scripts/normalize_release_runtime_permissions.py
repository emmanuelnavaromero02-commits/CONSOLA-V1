#!/usr/bin/env python3
"""Normalize a copied release runtime to immutable, universally readable modes."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


class RuntimePermissionError(RuntimeError):
    """The runtime tree cannot be normalized safely."""


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _inventory(root: Path) -> tuple[list[tuple[Path, int]], int]:
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise RuntimePermissionError(f"runtime root is unavailable: {root}") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise RuntimePermissionError("runtime root must be a real directory")

    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimePermissionError("runtime root cannot be resolved safely") from exc

    modes: list[tuple[Path, int]] = []
    symlinks = 0
    pending = [root]
    while pending:
        path = pending.pop()
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimePermissionError(
                f"runtime entry is unavailable: {path}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            symlinks += 1
            try:
                target = os.readlink(path)
                resolved_target = (path.parent / target).resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise RuntimePermissionError(
                    f"runtime symlink cannot be resolved safely: {path}"
                ) from exc
            if os.path.isabs(target) or not _inside(resolved_root, resolved_target):
                raise RuntimePermissionError(
                    f"runtime symlink escapes the runtime root: {path}"
                )
            continue
        if stat.S_ISDIR(metadata.st_mode):
            modes.append((path, 0o555))
            try:
                children = sorted(
                    (Path(entry.path) for entry in os.scandir(path)),
                    key=lambda item: item.name,
                    reverse=True,
                )
            except OSError as exc:
                raise RuntimePermissionError(
                    f"runtime directory cannot be inventoried: {path}"
                ) from exc
            pending.extend(children)
            continue
        if stat.S_ISREG(metadata.st_mode):
            target_mode = 0o555 if metadata.st_mode & 0o111 else 0o444
            modes.append((path, target_mode))
            continue
        raise RuntimePermissionError(f"runtime contains a special file: {path}")
    return modes, symlinks


def normalize_runtime_permissions(root: Path) -> tuple[int, int, int]:
    """Make dirs 0555 and files 0444/0555 without following symlinks."""

    if not root.is_absolute():
        raise RuntimePermissionError("runtime root must be absolute")
    modes, symlinks = _inventory(root)
    # Normalize leaves before their parents.  The complete tree is validated
    # first, so a special file or escaping symlink cannot leave a partial freeze.
    for path, target_mode in sorted(
        modes, key=lambda item: len(item[0].parts), reverse=True
    ):
        try:
            os.chmod(path, target_mode, follow_symlinks=False)
        except OSError as exc:
            raise RuntimePermissionError(
                f"runtime permissions cannot be normalized: {path}"
            ) from exc
    directories = 0
    regular_files = 0
    for path, expected_mode in modes:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimePermissionError(
                f"normalized runtime entry is unavailable: {path}"
            ) from exc
        if stat.S_ISDIR(metadata.st_mode):
            directories += 1
        elif stat.S_ISREG(metadata.st_mode):
            regular_files += 1
        else:
            raise RuntimePermissionError(
                f"runtime entry changed type during normalization: {path}"
            )
        if stat.S_IMODE(metadata.st_mode) != expected_mode:
            raise RuntimePermissionError(
                f"runtime entry has an unexpected normalized mode: {path}"
            )
    return directories, regular_files, symlinks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        directories, regular_files, symlinks = normalize_runtime_permissions(args.root)
    except RuntimePermissionError as exc:
        print(f"RELEASE RUNTIME PERMISSIONS BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(
        "RELEASE RUNTIME PERMISSIONS PASS: "
        f"{directories} directories, {regular_files} files, {symlinks} symlinks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
