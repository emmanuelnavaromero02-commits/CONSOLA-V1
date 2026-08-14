#!/usr/bin/env python3
"""Reclaim a narrowly reviewed GitHub-hosted runner disk, fail closed.

The release gate pulls every immutable application digest before it starts the
full compose stack.  Standard hosted runners can run out of disk while doing
that.  This module removes only five reviewed, disposable directories and
stops as soon as the pre-pull budget is met.  It never follows symlinks while
inventorying or deleting a cleanup tree.

The command-line entry point is deliberately stricter than the injectable
library API used by unit tests: it accepts only GitHub-hosted Ubuntu 24.04,
fixed runner paths, fixed budgets, and fixed cleanup targets.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


GIB: Final = 1024**3
PRE_PULL_MINIMUM_FREE_BYTES: Final = 17 * GIB
PRE_INFRA_MINIMUM_FREE_BYTES: Final = 5 * GIB
PRE_COMPOSE_MINIMUM_FREE_BYTES: Final = 2 * GIB
MINIMUM_FREE_INODES: Final = 100_000
DISK_BUDGET_RATIONALE: Final = (
    "v1.45.213-beta run 31831837077, digest-gate job 94872875740, "
    "successfully pulled all 15 immutable application digests but exhausted "
    "the hosted-runner filesystem while pulling the MailHog infrastructure "
    "layer ('no space left on device'). The 17-GiB pre-pull floor reserves "
    "capacity for those 15 digests, the 5-GiB pre-infra floor catches growth "
    "before infrastructure pulls, and the 2-GiB pre-compose floor preserves "
    "working space for the complete 26-service startup."
)

PHASE_BUDGETS: Final[Mapping[str, int]] = {
    "pre-pull": PRE_PULL_MINIMUM_FREE_BYTES,
    "pre-infra": PRE_INFRA_MINIMUM_FREE_BYTES,
    "pre-compose": PRE_COMPOSE_MINIMUM_FREE_BYTES,
}

EXPECTED_RUNNER_ENVIRONMENT: Final = "github-hosted"
EXPECTED_RUNNER_OS: Final = "Linux"
EXPECTED_IMAGE_OS: Final = "ubuntu24"
EXPECTED_OS_RELEASE_ID: Final = "ubuntu"
EXPECTED_OS_RELEASE_VERSION: Final = "24.04"

FIXED_RUNNER_TEMP: Final = Path("/home/runner/work/_temp")
FIXED_PIP_CACHE: Final = Path("/home/runner/.cache/pip")
FIXED_NPM_CACHE: Final = Path("/home/runner/.npm/_cacache")
FIXED_ANDROID_TOOLCHAIN: Final = Path("/usr/local/lib/android")
FIXED_CODEQL_TOOLCHAIN: Final = Path("/opt/hostedtoolcache/CodeQL")
FIXED_DOCKER_ROOT: Final = Path("/var/lib/docker")
FIXED_WORK_ROOT: Final = Path("/home/runner/work")
FIXED_PROTECTED_EXECUTABLES: Final = (
    Path("/usr/bin/python3"),
    Path("/opt/omega-release-runtime/bin/node"),
    Path("/usr/bin/docker"),
    Path("/opt/omega-release-runtime/bin/docker"),
)


class RunnerDiskError(RuntimeError):
    """Runner identity, cleanup safety, or disk budget is invalid."""


@dataclass(frozen=True)
class CleanupTarget:
    """One exact, reviewed directory that may be removed."""

    name: str
    path: Path


@dataclass(frozen=True)
class DiskSnapshot:
    """Relevant filesystem capacity at one point in time."""

    path: Path
    device: int
    total_bytes: int
    used_bytes: int
    free_bytes: int
    free_inodes: int


@dataclass(frozen=True)
class ReclaimReport:
    """Auditable result of an adaptive cleanup."""

    before: DiskSnapshot
    after: DiskSnapshot
    removed: tuple[str, ...]


@dataclass(frozen=True)
class _EntryIdentity:
    relative_path: str
    kind: str
    device: int
    inode: int
    mode: int
    allocated_bytes: int


@dataclass(frozen=True)
class _TargetInventory:
    target: CleanupTarget
    exists: bool
    entries: tuple[_EntryIdentity, ...]
    allocated_bytes: int


@dataclass(frozen=True)
class _ProtectedIdentity:
    requested_path: Path
    requested_device: int
    requested_inode: int
    requested_mode: int
    resolved_path: Path
    resolved_device: int
    resolved_inode: int
    resolved_mode: int
    sha256: str


Snapshotter = Callable[[Path], DiskSnapshot]
Emitter = Callable[[str], None]


def default_cleanup_targets() -> tuple[CleanupTarget, ...]:
    """Return the production allowlist in its mandatory cleanup order."""

    return (
        CleanupTarget(
            "duplicate-chromium-installer",
            FIXED_RUNNER_TEMP / "omega-playwright-browsers.install",
        ),
        CleanupTarget("pip-cache", FIXED_PIP_CACHE),
        CleanupTarget("npm-cacache", FIXED_NPM_CACHE),
        CleanupTarget("android-toolchain", FIXED_ANDROID_TOOLCHAIN),
        CleanupTarget("codeql-toolchain", FIXED_CODEQL_TOOLCHAIN),
    )


def snapshot_disk(path: Path) -> DiskSnapshot:
    """Read byte and inode availability for ``path`` without changing it."""

    checked = _strict_absolute_path(path, label="disk path")
    try:
        path_stat = os.lstat(checked)
        values = os.statvfs(checked)
    except OSError as exc:
        raise RunnerDiskError(f"cannot inspect disk path {checked}: {exc}") from exc
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise RunnerDiskError(f"disk path must be a real directory: {checked}")

    total_bytes = values.f_blocks * values.f_frsize
    free_bytes = values.f_bavail * values.f_frsize
    return DiskSnapshot(
        path=checked,
        device=path_stat.st_dev,
        total_bytes=total_bytes,
        used_bytes=total_bytes - free_bytes,
        free_bytes=free_bytes,
        free_inodes=values.f_favail,
    )


def assert_disk_budget(
    disk_path: Path,
    *,
    minimum_free_bytes: int,
    phase: str,
    minimum_free_inodes: int = 0,
    snapshotter: Snapshotter = snapshot_disk,
    emit: Emitter = print,
) -> DiskSnapshot:
    """Log and enforce an exact byte/inode budget for a release phase."""

    _validate_budget(minimum_free_bytes, minimum_free_inodes)
    if not phase or any(character.isspace() for character in phase):
        raise RunnerDiskError("disk-budget phase must be a non-empty token")

    snapshot = snapshotter(Path(disk_path))
    line = (
        f"RELEASE RUNNER DISK BUDGET phase={phase} "
        f"device={snapshot.device} total_bytes={snapshot.total_bytes} "
        f"used_bytes={snapshot.used_bytes} free_bytes={snapshot.free_bytes} "
        f"free_inodes={snapshot.free_inodes} "
        f"required_free_bytes={minimum_free_bytes}"
    )
    if minimum_free_inodes:
        line += f" required_free_inodes={minimum_free_inodes}"
    emit(line)

    failures: list[str] = []
    if snapshot.free_bytes < minimum_free_bytes:
        failures.append(
            f"free_bytes={snapshot.free_bytes} required={minimum_free_bytes}"
        )
    if snapshot.free_inodes < minimum_free_inodes:
        failures.append(
            f"free_inodes={snapshot.free_inodes} required={minimum_free_inodes}"
        )
    if failures:
        raise RunnerDiskError(f"disk budget failed for {phase}: " + ", ".join(failures))
    return snapshot


def assert_release_phase_budget(
    phase: str,
    *,
    disk_path: Path = FIXED_DOCKER_ROOT,
    snapshotter: Snapshotter = snapshot_disk,
    emit: Emitter = print,
) -> DiskSnapshot:
    """Enforce one of the three non-overridable production budgets."""

    try:
        minimum_free_bytes = PHASE_BUDGETS[phase]
    except KeyError as exc:
        raise RunnerDiskError(f"unknown release disk phase: {phase}") from exc
    return assert_disk_budget(
        disk_path,
        minimum_free_bytes=minimum_free_bytes,
        minimum_free_inodes=MINIMUM_FREE_INODES,
        phase=phase,
        snapshotter=snapshotter,
        emit=emit,
    )


def reclaim_hosted_runner_disk(
    *,
    runner_environment: str,
    runner_os: str,
    github_actions: str,
    runner_temp: Path,
    workspace: Path,
    disk_path: Path,
    minimum_free_bytes: int,
    protected_executables: Sequence[Path],
    cache_targets: Sequence[CleanupTarget],
    toolchain_targets: Sequence[CleanupTarget],
    minimum_free_inodes: int = 0,
    image_os: str = EXPECTED_IMAGE_OS,
    os_release_id: str = EXPECTED_OS_RELEASE_ID,
    os_release_version: str = EXPECTED_OS_RELEASE_VERSION,
    snapshotter: Snapshotter = snapshot_disk,
    syncer: Callable[[], None] = os.sync,
    emit: Emitter = print,
) -> ReclaimReport:
    """Remove reviewed targets adaptively after a complete safe inventory.

    ``cache_targets`` followed by ``toolchain_targets`` is the exact allowlist
    and order for this invocation.  Production callers use
    :func:`default_cleanup_targets`; injectable paths exist only so the safety
    properties can be exercised in temporary test directories.
    """

    _validate_host_context(
        runner_environment=runner_environment,
        runner_os=runner_os,
        github_actions=github_actions,
        image_os=image_os,
        os_release_id=os_release_id,
        os_release_version=os_release_version,
    )
    _validate_budget(minimum_free_bytes, minimum_free_inodes)

    disk_path = _strict_absolute_path(disk_path, label="disk path")
    runner_temp = _strict_absolute_path(runner_temp, label="runner temp")
    workspace = _strict_absolute_path(workspace, label="workspace")
    targets = tuple(cache_targets) + tuple(toolchain_targets)
    _validate_target_set(targets)
    _validate_production_scope(
        disk_path=disk_path,
        runner_temp=runner_temp,
        targets=targets,
        protected_executables=protected_executables,
    )

    before = snapshotter(disk_path)
    disk_device = _disk_device(disk_path)
    protected = tuple(_capture_protected(path) for path in protected_executables)
    _validate_cleanup_boundaries(
        targets=targets,
        disk_path=disk_path,
        runner_temp=runner_temp,
        workspace=workspace,
        protected=protected,
    )

    # This loop must finish for every allowlisted root before deletion starts.
    inventories = tuple(
        _inventory_target(target, disk_device=disk_device) for target in targets
    )
    _verify_protected(protected)

    emit(_snapshot_line("BEFORE", before))
    for inventory in inventories:
        emit(
            "RELEASE RUNNER DISK INVENTORY "
            f"target={inventory.target.name} path={inventory.target.path} "
            f"exists={str(inventory.exists).lower()} "
            f"entries={len(inventory.entries)} "
            f"allocated_bytes={inventory.allocated_bytes}"
        )

    current = before
    removed: list[str] = []
    if not _budget_met(current, minimum_free_bytes, minimum_free_inodes):
        for inventory in inventories:
            if not inventory.exists:
                _assert_absent_inventory_unchanged(inventory)
                emit(
                    "RELEASE RUNNER DISK CLEANUP "
                    f"target={inventory.target.name} status=absent "
                    "reclaimed_bytes=0 reclaimed_inodes=0"
                )
                continue

            previous = current
            _remove_inventory(inventory, disk_device=disk_device)
            syncer()
            current = snapshotter(disk_path)
            if current.device != before.device:
                raise RunnerDiskError("disk snapshot device changed during cleanup")
            _verify_protected(protected)
            reclaimed_bytes = max(0, current.free_bytes - previous.free_bytes)
            reclaimed_inodes = max(0, current.free_inodes - previous.free_inodes)
            removed.append(inventory.target.name)
            emit(
                "RELEASE RUNNER DISK CLEANUP "
                f"target={inventory.target.name} status=removed "
                f"reclaimed_bytes={reclaimed_bytes} "
                f"reclaimed_inodes={reclaimed_inodes}"
            )
            if _budget_met(current, minimum_free_bytes, minimum_free_inodes):
                break

    _verify_protected(protected)
    emit(
        _snapshot_line("AFTER", current)
        + f" freed_bytes={current.free_bytes - before.free_bytes}"
        + f" freed_inodes={current.free_inodes - before.free_inodes}"
    )
    if not _budget_met(current, minimum_free_bytes, minimum_free_inodes):
        raise RunnerDiskError(
            "allowlisted cleanup exhausted without meeting minimum free-byte budget: "
            f"free_bytes={current.free_bytes} required={minimum_free_bytes}; "
            f"free_inodes={current.free_inodes} required={minimum_free_inodes}"
        )
    return ReclaimReport(before=before, after=current, removed=tuple(removed))


def _validate_budget(minimum_free_bytes: int, minimum_free_inodes: int) -> None:
    for value, label in (
        (minimum_free_bytes, "minimum free bytes"),
        (minimum_free_inodes, "minimum free inodes"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RunnerDiskError(f"{label} must be a non-negative integer")


def _budget_met(
    snapshot: DiskSnapshot, minimum_free_bytes: int, minimum_free_inodes: int
) -> bool:
    return (
        snapshot.free_bytes >= minimum_free_bytes
        and snapshot.free_inodes >= minimum_free_inodes
    )


def _snapshot_line(label: str, snapshot: DiskSnapshot) -> str:
    return (
        f"RELEASE RUNNER DISK {label} path={snapshot.path} "
        f"device={snapshot.device} total_bytes={snapshot.total_bytes} "
        f"used_bytes={snapshot.used_bytes} free_bytes={snapshot.free_bytes} "
        f"free_inodes={snapshot.free_inodes}"
    )


def _validate_host_context(
    *,
    runner_environment: str,
    runner_os: str,
    github_actions: str,
    image_os: str,
    os_release_id: str,
    os_release_version: str,
) -> None:
    if (
        runner_environment != EXPECTED_RUNNER_ENVIRONMENT
        or runner_os != EXPECTED_RUNNER_OS
        or github_actions != "true"
    ):
        raise RunnerDiskError(
            "cleanup is allowed only on GitHub-hosted Linux Actions runners"
        )
    if (
        image_os != EXPECTED_IMAGE_OS
        or os_release_id != EXPECTED_OS_RELEASE_ID
        or os_release_version != EXPECTED_OS_RELEASE_VERSION
    ):
        raise RunnerDiskError(
            "cleanup is allowed only on the exact GitHub-hosted Ubuntu 24.04 image"
        )


def _strict_absolute_path(path: Path, *, label: str) -> Path:
    candidate = Path(path)
    raw = os.fspath(candidate)
    if not candidate.is_absolute() or os.path.normpath(raw) != raw:
        raise RunnerDiskError(f"{label} must be a normalized absolute path: {raw}")
    if candidate == Path("/") and label != "disk path":
        raise RunnerDiskError(f"{label} cannot be the filesystem root")
    return candidate


def _disk_device(disk_path: Path) -> int:
    _validate_real_directory_chain(disk_path, expected_device=None)
    try:
        disk_stat = os.lstat(disk_path)
    except OSError as exc:
        raise RunnerDiskError(f"cannot lstat disk path {disk_path}: {exc}") from exc
    if stat.S_ISLNK(disk_stat.st_mode) or not stat.S_ISDIR(disk_stat.st_mode):
        raise RunnerDiskError(f"disk path must be a real directory: {disk_path}")
    return disk_stat.st_dev


def _validate_target_set(targets: Sequence[CleanupTarget]) -> None:
    names: set[str] = set()
    paths: list[Path] = []
    for target in targets:
        if not target.name or any(character.isspace() for character in target.name):
            raise RunnerDiskError("cleanup target names must be non-empty tokens")
        if target.name in names:
            raise RunnerDiskError(f"duplicate cleanup target name: {target.name}")
        names.add(target.name)
        path = _strict_absolute_path(target.path, label=f"target {target.name}")
        if len(path.parts) < 3:
            raise RunnerDiskError(f"cleanup target is too broad: {path}")
        for previous in paths:
            if path == previous or path in previous.parents or previous in path.parents:
                raise RunnerDiskError(f"cleanup targets overlap: {previous} and {path}")
        paths.append(path)


def _validate_production_scope(
    *,
    disk_path: Path,
    runner_temp: Path,
    targets: Sequence[CleanupTarget],
    protected_executables: Sequence[Path],
) -> None:
    if disk_path != FIXED_DOCKER_ROOT:
        return
    if runner_temp != FIXED_RUNNER_TEMP:
        raise RunnerDiskError("production runner temp is not the fixed reviewed path")
    if tuple(targets) != default_cleanup_targets():
        raise RunnerDiskError("production cleanup allowlist or order changed")
    if (
        tuple(Path(path) for path in protected_executables)
        != FIXED_PROTECTED_EXECUTABLES
    ):
        raise RunnerDiskError("production protected-executable set or order changed")


def _validate_cleanup_boundaries(
    *,
    targets: Sequence[CleanupTarget],
    disk_path: Path,
    runner_temp: Path,
    workspace: Path,
    protected: Sequence[_ProtectedIdentity],
) -> None:
    for target in targets:
        path = target.path
        if path == disk_path or path in disk_path.parents:
            raise RunnerDiskError(f"cleanup target contains Docker storage: {path}")
        if path == workspace or path in workspace.parents:
            raise RunnerDiskError(f"cleanup target contains the workspace: {path}")
        if path == runner_temp:
            raise RunnerDiskError("cleanup target cannot be the entire runner temp")
        for identity in protected:
            requested = identity.requested_path
            resolved = identity.resolved_path
            if (
                requested == path
                or path in requested.parents
                or resolved == path
                or path in resolved.parents
            ):
                raise RunnerDiskError(
                    f"protected executable is inside cleanup root {path}: {requested}"
                )


def _capture_protected(path: Path) -> _ProtectedIdentity:
    requested = _strict_absolute_path(path, label="protected executable")
    try:
        requested_stat = os.lstat(requested)
        resolved = requested.resolve(strict=True)
        resolved_stat = os.lstat(resolved)
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot inspect protected executable {requested}: {exc}"
        ) from exc
    if not stat.S_ISREG(resolved_stat.st_mode):
        raise RunnerDiskError(
            f"protected executable is not a regular file: {requested}"
        )
    if resolved_stat.st_mode & 0o111 == 0:
        raise RunnerDiskError(f"protected executable is not executable: {requested}")
    return _ProtectedIdentity(
        requested_path=requested,
        requested_device=requested_stat.st_dev,
        requested_inode=requested_stat.st_ino,
        requested_mode=requested_stat.st_mode,
        resolved_path=resolved,
        resolved_device=resolved_stat.st_dev,
        resolved_inode=resolved_stat.st_ino,
        resolved_mode=resolved_stat.st_mode,
        sha256=_sha256_file(resolved),
    )


def _verify_protected(identities: Sequence[_ProtectedIdentity]) -> None:
    for identity in identities:
        try:
            requested_stat = os.lstat(identity.requested_path)
            resolved = identity.requested_path.resolve(strict=True)
            resolved_stat = os.lstat(resolved)
        except OSError as exc:
            raise RunnerDiskError(
                f"protected executable disappeared: {identity.requested_path}"
            ) from exc
        if (
            requested_stat.st_dev != identity.requested_device
            or requested_stat.st_ino != identity.requested_inode
            or requested_stat.st_mode != identity.requested_mode
            or resolved != identity.resolved_path
            or resolved_stat.st_dev != identity.resolved_device
            or resolved_stat.st_ino != identity.resolved_inode
            or resolved_stat.st_mode != identity.resolved_mode
            or _sha256_file(resolved) != identity.sha256
        ):
            raise RunnerDiskError(
                f"protected executable identity changed: {identity.requested_path}"
            )


def _sha256_file(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot hash protected executable {path}: {exc}"
        ) from exc


def _validate_real_directory_chain(path: Path, *, expected_device: int | None) -> None:
    current = Path(path.anchor)
    components = path.parts[1:]
    for index, component in enumerate((path.anchor, *components)):
        if index:
            current /= component
        try:
            identity = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RunnerDiskError(
                f"cannot lstat path component {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(identity.st_mode):
            raise RunnerDiskError(f"symlink path component is forbidden: {current}")
        if not stat.S_ISDIR(identity.st_mode):
            raise RunnerDiskError(f"path component is not a directory: {current}")
        if expected_device is not None and identity.st_dev != expected_device:
            raise RunnerDiskError(f"filesystem device changed at {current}")
        if current != Path(path.anchor) and os.path.ismount(current):
            raise RunnerDiskError(
                f"mount point inside cleanup path is forbidden: {current}"
            )


def _inventory_target(target: CleanupTarget, *, disk_device: int) -> _TargetInventory:
    path = _strict_absolute_path(target.path, label=f"target {target.name}")
    _validate_real_directory_chain(path.parent, expected_device=disk_device)
    try:
        root_stat = os.lstat(path)
    except FileNotFoundError:
        return _TargetInventory(
            target=CleanupTarget(target.name, path),
            exists=False,
            entries=(),
            allocated_bytes=0,
        )
    except OSError as exc:
        raise RunnerDiskError(f"cannot lstat cleanup target {path}: {exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise RunnerDiskError(f"cleanup target root cannot be a symlink: {path}")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise RunnerDiskError(f"cleanup target root must be a directory: {path}")
    if root_stat.st_dev != disk_device:
        raise RunnerDiskError(f"cleanup target changes filesystem device: {path}")
    if os.path.ismount(path):
        raise RunnerDiskError(f"cleanup target cannot be a mount point: {path}")

    entries: list[_EntryIdentity] = [
        _entry_identity(".", root_stat, expected_kind="directory")
    ]
    _inventory_directory(
        path,
        relative_prefix="",
        disk_device=disk_device,
        entries=entries,
    )
    entries.sort(key=lambda item: item.relative_path)
    return _TargetInventory(
        target=CleanupTarget(target.name, path),
        exists=True,
        entries=tuple(entries),
        allocated_bytes=sum(item.allocated_bytes for item in entries),
    )


def _inventory_directory(
    directory: Path,
    *,
    relative_prefix: str,
    disk_device: int,
    entries: list[_EntryIdentity],
) -> None:
    try:
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda entry: entry.name)
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot inventory cleanup directory {directory}: {exc}"
        ) from exc
    for child in children:
        relative = f"{relative_prefix}/{child.name}" if relative_prefix else child.name
        try:
            identity = child.stat(follow_symlinks=False)
        except OSError as exc:
            raise RunnerDiskError(
                f"cannot lstat cleanup entry {child.path}: {exc}"
            ) from exc
        kind = _entry_kind(identity.st_mode)
        if kind == "special":
            raise RunnerDiskError(
                f"special file type inside cleanup target: {child.path}"
            )
        if kind != "symlink" and identity.st_dev != disk_device:
            raise RunnerDiskError(f"filesystem device changed at {child.path}")
        if kind == "directory" and os.path.ismount(child.path):
            raise RunnerDiskError(
                f"mount point inside cleanup target is forbidden: {child.path}"
            )
        entries.append(_entry_identity(relative, identity, expected_kind=kind))
        if kind == "directory":
            _inventory_directory(
                Path(child.path),
                relative_prefix=relative,
                disk_device=disk_device,
                entries=entries,
            )


def _entry_kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _entry_identity(
    relative_path: str, identity: os.stat_result, *, expected_kind: str
) -> _EntryIdentity:
    kind = _entry_kind(identity.st_mode)
    if kind != expected_kind:
        raise RunnerDiskError(f"cleanup entry type changed: {relative_path}")
    return _EntryIdentity(
        relative_path=relative_path,
        kind=kind,
        device=identity.st_dev,
        inode=identity.st_ino,
        mode=identity.st_mode,
        allocated_bytes=getattr(identity, "st_blocks", 0) * 512,
    )


def _assert_absent_inventory_unchanged(inventory: _TargetInventory) -> None:
    if os.path.lexists(inventory.target.path):
        raise RunnerDiskError(
            f"absent cleanup target appeared after inventory: {inventory.target.path}"
        )


def _remove_inventory(inventory: _TargetInventory, *, disk_device: int) -> None:
    # Validate the complete tree a second time immediately before the first
    # unlink.  Deletion below also uses no-follow directory descriptors.
    current = _inventory_target(inventory.target, disk_device=disk_device)
    if current != inventory:
        raise RunnerDiskError(
            f"cleanup target changed after inventory: {inventory.target.path}"
        )

    expected = {entry.relative_path: entry for entry in inventory.entries}
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(inventory.target.path, flags)
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot safely open cleanup target {inventory.target.path}: {exc}"
        ) from exc
    try:
        _assert_open_identity(root_fd, expected["."], inventory.target.path)
        _delete_directory_contents(
            root_fd,
            relative_prefix="",
            display_path=inventory.target.path,
            expected=expected,
            disk_device=disk_device,
        )
    finally:
        os.close(root_fd)

    try:
        final_stat = os.lstat(inventory.target.path)
    except OSError as exc:
        raise RunnerDiskError(
            f"cleanup target changed before root removal: {inventory.target.path}"
        ) from exc
    _assert_stat_identity(final_stat, expected["."], inventory.target.path)
    try:
        os.rmdir(inventory.target.path)
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot remove cleanup target {inventory.target.path}: {exc}"
        ) from exc


def _delete_directory_contents(
    directory_fd: int,
    *,
    relative_prefix: str,
    display_path: Path,
    expected: Mapping[str, _EntryIdentity],
    disk_device: int,
) -> None:
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot list cleanup directory {display_path}: {exc}"
        ) from exc

    expected_names = {
        relative.split("/")[-1]
        for relative in expected
        if relative != "."
        and (relative.rsplit("/", 1)[0] if "/" in relative else "") == relative_prefix
    }
    if set(names) != expected_names:
        raise RunnerDiskError(
            f"cleanup directory changed after inventory: {display_path}"
        )

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    for name in names:
        relative = f"{relative_prefix}/{name}" if relative_prefix else name
        expected_identity = expected[relative]
        child_display = display_path / name
        try:
            identity = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise RunnerDiskError(
                f"cannot lstat cleanup entry {child_display}: {exc}"
            ) from exc
        _assert_stat_identity(identity, expected_identity, child_display)
        kind = _entry_kind(identity.st_mode)
        if kind == "directory":
            if identity.st_dev != disk_device or os.path.ismount(child_display):
                raise RunnerDiskError(
                    f"mount/device boundary appeared at {child_display}"
                )
            try:
                child_fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise RunnerDiskError(
                    f"cannot safely open cleanup directory {child_display}: {exc}"
                ) from exc
            try:
                _assert_open_identity(child_fd, expected_identity, child_display)
                _delete_directory_contents(
                    child_fd,
                    relative_prefix=relative,
                    display_path=child_display,
                    expected=expected,
                    disk_device=disk_device,
                )
            finally:
                os.close(child_fd)
            try:
                final_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise RunnerDiskError(
                    f"cleanup directory changed before removal: {child_display}"
                ) from exc
            _assert_stat_identity(final_stat, expected_identity, child_display)
            try:
                os.rmdir(name, dir_fd=directory_fd)
            except OSError as exc:
                raise RunnerDiskError(
                    f"cannot remove cleanup directory {child_display}: {exc}"
                ) from exc
        elif kind in {"file", "symlink"}:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError as exc:
                raise RunnerDiskError(
                    f"cannot unlink cleanup entry {child_display}: {exc}"
                ) from exc
        else:
            raise RunnerDiskError(f"special file appeared at {child_display}")


def _assert_open_identity(
    descriptor: int, expected: _EntryIdentity, display_path: Path
) -> None:
    try:
        identity = os.fstat(descriptor)
    except OSError as exc:
        raise RunnerDiskError(
            f"cannot fstat cleanup directory {display_path}: {exc}"
        ) from exc
    _assert_stat_identity(identity, expected, display_path)


def _assert_stat_identity(
    identity: os.stat_result, expected: _EntryIdentity, display_path: Path
) -> None:
    if (
        identity.st_dev != expected.device
        or identity.st_ino != expected.inode
        or identity.st_mode != expected.mode
        or _entry_kind(identity.st_mode) != expected.kind
    ):
        raise RunnerDiskError(f"cleanup entry identity changed: {display_path}")


def _read_os_release(path: Path = Path("/etc/os-release")) -> tuple[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RunnerDiskError(f"cannot read OS release identity: {exc}") from exc
    if len(raw.encode("utf-8")) > 16_384:
        raise RunnerDiskError("OS release identity is oversized")
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        values[key] = value
    return values.get("ID", ""), values.get("VERSION_ID", "")


def _production_context(*, require_root: bool) -> tuple[Path, Path, tuple[Path, ...]]:
    runner_temp_raw = os.environ.get("RUNNER_TEMP", "")
    if runner_temp_raw != os.fspath(FIXED_RUNNER_TEMP):
        raise RunnerDiskError(
            f"RUNNER_TEMP must be the fixed hosted-runner path {FIXED_RUNNER_TEMP}"
        )
    workspace_raw = os.environ.get("GITHUB_WORKSPACE", "")
    workspace = _strict_absolute_path(Path(workspace_raw), label="workspace")
    if workspace == FIXED_WORK_ROOT or FIXED_WORK_ROOT not in workspace.parents:
        raise RunnerDiskError(f"workspace must be below {FIXED_WORK_ROOT}")

    os_release_id, os_release_version = _read_os_release()
    _validate_host_context(
        runner_environment=os.environ.get("RUNNER_ENVIRONMENT", ""),
        runner_os=os.environ.get("RUNNER_OS", ""),
        github_actions=os.environ.get("GITHUB_ACTIONS", ""),
        image_os=os.environ.get("ImageOS", ""),
        os_release_id=os_release_id,
        os_release_version=os_release_version,
    )
    protected: tuple[Path, ...] = ()
    if require_root:
        if os.geteuid() != 0:
            raise RunnerDiskError("production cleanup must run as root")
        protected = FIXED_PROTECTED_EXECUTABLES
    return FIXED_RUNNER_TEMP, workspace, protected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "reclaim",
        help="adaptively remove only reviewed roots until the 17-GiB budget",
    )
    check = subparsers.add_parser("check", help="enforce a fixed phase budget")
    check.add_argument("--phase", required=True, choices=tuple(PHASE_BUDGETS))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "reclaim":
            runner_temp, workspace, protected = _production_context(require_root=True)
            targets = default_cleanup_targets()
            reclaim_hosted_runner_disk(
                runner_environment=os.environ["RUNNER_ENVIRONMENT"],
                runner_os=os.environ["RUNNER_OS"],
                github_actions=os.environ["GITHUB_ACTIONS"],
                image_os=os.environ["ImageOS"],
                os_release_id=EXPECTED_OS_RELEASE_ID,
                os_release_version=EXPECTED_OS_RELEASE_VERSION,
                runner_temp=runner_temp,
                workspace=workspace,
                disk_path=FIXED_DOCKER_ROOT,
                minimum_free_bytes=PRE_PULL_MINIMUM_FREE_BYTES,
                minimum_free_inodes=MINIMUM_FREE_INODES,
                protected_executables=protected,
                cache_targets=targets[:3],
                toolchain_targets=targets[3:],
            )
        else:
            _production_context(require_root=False)
            assert_release_phase_budget(args.phase)
    except RunnerDiskError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
