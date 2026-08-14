from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

import scripts.prepare_release_runner_disk as runner_disk
from scripts.prepare_release_runner_disk import (
    CleanupTarget,
    DiskSnapshot,
    RunnerDiskError,
    assert_disk_budget,
    assert_release_phase_budget,
    reclaim_hosted_runner_disk,
)


def _target(name: str, path: Path) -> CleanupTarget:
    path.mkdir(parents=True)
    (path / "payload.bin").write_bytes(name.encode("utf-8"))
    return CleanupTarget(name=name, path=path)


def _executable(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o755)
    return path


def _snapshotter(
    disk_path: Path,
    contributions: dict[Path, int],
    *,
    base: int = 100,
    free_inodes: int = 5_000,
):
    canonical = {
        Path(os.path.normpath(path)): value for path, value in contributions.items()
    }

    def snapshot(path: Path) -> DiskSnapshot:
        assert path == disk_path
        free = base + sum(
            value for target, value in canonical.items() if not os.path.lexists(target)
        )
        return DiskSnapshot(
            path=path,
            device=7,
            total_bytes=1_000,
            used_bytes=1_000 - free,
            free_bytes=free,
            free_inodes=free_inodes,
        )

    return snapshot


def _reclaim(
    tmp_path: Path,
    *,
    minimum_free_bytes: int,
    cache_targets: tuple[CleanupTarget, ...],
    toolchain_targets: tuple[CleanupTarget, ...] = (),
    protected_executables: tuple[Path, ...] = (),
    snapshotter,
    syncer=lambda: None,
    emit=lambda _line: None,
    **context: str,
):
    disk = tmp_path / "docker"
    disk.mkdir(exist_ok=True)
    runner_temp = tmp_path / "_temp"
    runner_temp.mkdir(exist_ok=True)
    return reclaim_hosted_runner_disk(
        runner_environment=context.get("runner_environment", "github-hosted"),
        runner_os=context.get("runner_os", "Linux"),
        github_actions=context.get("github_actions", "true"),
        image_os=context.get("image_os", "ubuntu24"),
        os_release_id=context.get("os_release_id", "ubuntu"),
        os_release_version=context.get("os_release_version", "24.04"),
        runner_temp=runner_temp,
        workspace=tmp_path / "workspace",
        disk_path=disk,
        minimum_free_bytes=minimum_free_bytes,
        protected_executables=protected_executables,
        cache_targets=cache_targets,
        toolchain_targets=toolchain_targets,
        snapshotter=snapshotter,
        syncer=syncer,
        emit=emit,
    )


def test_reclaim_is_adaptive_in_exact_order_and_preserves_executables(
    tmp_path: Path,
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    runner_temp = tmp_path / "_temp"
    runner_temp.mkdir()
    duplicate = _target(
        "duplicate-chromium-installer",
        runner_temp / "omega-playwright-browsers.install",
    )
    pip_cache = _target("pip-cache", tmp_path / "runner" / ".cache" / "pip")
    npm_cache = _target("npm-cacache", tmp_path / "runner" / ".npm" / "_cacache")
    android = _target("android-toolchain", tmp_path / "usr" / "local" / "android")
    codeql = _target("codeql-toolchain", tmp_path / "opt" / "CodeQL")
    python = _executable(tmp_path / "protected" / "python", b"python")
    node = _executable(tmp_path / "protected" / "node", b"node")
    docker = _executable(tmp_path / "protected" / "docker", b"docker")
    protected = (python, node, docker)
    before_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected
    }
    snapshotter = _snapshotter(
        disk,
        {
            duplicate.path: 50,
            pip_cache.path: 50,
            npm_cache.path: 50,
            android.path: 250,
            codeql.path: 250,
        },
    )
    emitted: list[str] = []

    report = reclaim_hosted_runner_disk(
        runner_environment="github-hosted",
        runner_os="Linux",
        github_actions="true",
        runner_temp=runner_temp,
        workspace=tmp_path / "workspace",
        disk_path=disk,
        minimum_free_bytes=450,
        protected_executables=protected,
        cache_targets=(duplicate, pip_cache, npm_cache),
        toolchain_targets=(android, codeql),
        snapshotter=snapshotter,
        syncer=lambda: None,
        emit=emitted.append,
    )

    assert report.before.free_bytes == 100
    assert report.after.free_bytes == 500
    assert report.removed == (
        "duplicate-chromium-installer",
        "pip-cache",
        "npm-cacache",
        "android-toolchain",
    )
    assert codeql.path.is_dir(), "cleanup must stop as soon as the budget is met"
    cleanup_lines = [line for line in emitted if "status=removed" in line]
    assert [line.split("target=", 1)[1].split(" ", 1)[0] for line in cleanup_lines] == [
        "duplicate-chromium-installer",
        "pip-cache",
        "npm-cacache",
        "android-toolchain",
    ]
    assert any("reclaimed_bytes=250" in line for line in cleanup_lines)
    assert any("freed_bytes=400" in line for line in emitted)
    for path, expected in before_hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runner_environment", "self-hosted"),
        ("runner_os", "Windows"),
        ("github_actions", "false"),
        ("image_os", "ubuntu22"),
        ("os_release_id", "debian"),
        ("os_release_version", "22.04"),
    ],
)
def test_reclaim_rejects_any_non_exact_host_before_deletion(
    tmp_path: Path, field: str, value: str
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    target = _target("cache", tmp_path / "cache")
    context = {field: value}

    with pytest.raises(RunnerDiskError, match="GitHub-hosted|Ubuntu 24.04"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=1,
            cache_targets=(target,),
            snapshotter=_snapshotter(disk, {target.path: 1}),
            **context,
        )

    assert target.path.is_dir()


def test_complete_preinventory_rejects_late_special_file_before_any_deletion(
    tmp_path: Path,
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    first = _target("first", tmp_path / "first")
    later = _target("later", tmp_path / "later")
    os.mkfifo(later.path / "forbidden.fifo")

    with pytest.raises(RunnerDiskError, match="special file"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=900,
            cache_targets=(first, later),
            snapshotter=_snapshotter(disk, {first.path: 50, later.path: 50}),
        )

    assert first.path.is_dir(), "all roots must be inventoried before the first unlink"
    assert later.path.is_dir()


def test_cleanup_rejects_root_or_ancestor_symlink_before_mutation(
    tmp_path: Path,
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    first = _target("first", tmp_path / "first")
    outside = _target("outside", tmp_path / "outside")
    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(outside.path, target_is_directory=True)

    with pytest.raises(RunnerDiskError, match="symlink"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=900,
            cache_targets=(first, CleanupTarget("escape", symlink_root)),
            snapshotter=_snapshotter(disk, {first.path: 50}),
        )

    assert first.path.is_dir()
    assert outside.path.is_dir()
    assert symlink_root.is_symlink()

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(outside.path, target_is_directory=True)
    with pytest.raises(RunnerDiskError, match="symlink path component"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=900,
            cache_targets=(CleanupTarget("nested-escape", parent_link / "child"),),
            snapshotter=_snapshotter(disk, {}),
        )


def test_nested_symlink_is_unlinked_without_following_target(tmp_path: Path) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    outside = _target("outside", tmp_path / "outside")
    cleanup = _target("cleanup", tmp_path / "cleanup")
    (cleanup.path / "nested-link").symlink_to(outside.path, target_is_directory=True)

    report = _reclaim(
        tmp_path,
        minimum_free_bytes=150,
        cache_targets=(cleanup,),
        snapshotter=_snapshotter(disk, {cleanup.path: 50}),
    )

    assert report.removed == ("cleanup",)
    assert not cleanup.path.exists()
    assert (outside.path / "payload.bin").read_bytes() == b"outside"


def test_nested_hardlink_is_unlinked_without_mutating_external_inode(
    tmp_path: Path,
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"preserve")
    cleanup = _target("cleanup", tmp_path / "cleanup")
    os.link(outside, cleanup.path / "reviewed-hardlink")

    report = _reclaim(
        tmp_path,
        minimum_free_bytes=150,
        cache_targets=(cleanup,),
        snapshotter=_snapshotter(disk, {cleanup.path: 50}),
    )

    assert report.removed == ("cleanup",)
    assert not cleanup.path.exists()
    assert outside.read_bytes() == b"preserve"
    assert outside.stat().st_nlink == 1


def test_reclaim_treats_st_blocks_as_telemetry_not_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    cleanup = _target("cleanup", tmp_path / "cleanup")
    real_inventory = runner_disk._inventory_target
    inventory_calls = 0

    def inventory_with_settled_allocation(
        target: CleanupTarget, *, disk_device: int
    ) -> runner_disk._TargetInventory:
        nonlocal inventory_calls
        inventory_calls += 1
        inventory = real_inventory(target, disk_device=disk_device)
        if inventory_calls != 2:
            return inventory
        entries = tuple(
            replace(entry, allocated_bytes=entry.allocated_bytes + 4096)
            for entry in inventory.entries
        )
        return replace(
            inventory,
            entries=entries,
            allocated_bytes=inventory.allocated_bytes + 4096 * len(entries),
        )

    monkeypatch.setattr(
        runner_disk, "_inventory_target", inventory_with_settled_allocation
    )
    report = _reclaim(
        tmp_path,
        minimum_free_bytes=150,
        cache_targets=(cleanup,),
        snapshotter=_snapshotter(disk, {cleanup.path: 50}),
    )

    assert inventory_calls == 2
    assert report.removed == ("cleanup",)
    assert not cleanup.path.exists()


@pytest.mark.parametrize("field", ["kind", "device", "inode", "mode"])
def test_reclaim_still_rejects_security_identity_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    cleanup = _target("cleanup", tmp_path / "cleanup")
    real_inventory = runner_disk._inventory_target
    inventory_calls = 0

    def inventory_with_changed_identity(
        target: CleanupTarget, *, disk_device: int
    ) -> runner_disk._TargetInventory:
        nonlocal inventory_calls
        inventory_calls += 1
        inventory = real_inventory(target, disk_device=disk_device)
        if inventory_calls != 2:
            return inventory
        payload = next(
            entry for entry in inventory.entries if entry.relative_path == "payload.bin"
        )
        replacements: dict[str, object] = {
            "kind": "symlink",
            "device": payload.device + 1,
            "inode": payload.inode + 1,
            "mode": payload.mode ^ 0o100,
        }
        changed_entry = replace(payload, **{field: replacements[field]})
        return replace(
            inventory,
            entries=tuple(
                changed_entry if entry is payload else entry
                for entry in inventory.entries
            ),
        )

    monkeypatch.setattr(
        runner_disk, "_inventory_target", inventory_with_changed_identity
    )
    with pytest.raises(RunnerDiskError, match="changed after inventory"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=150,
            cache_targets=(cleanup,),
            snapshotter=_snapshotter(disk, {cleanup.path: 50}),
        )

    assert inventory_calls == 2
    assert cleanup.path.is_dir()
    assert (cleanup.path / "payload.bin").is_file()


def test_reclaim_builds_one_child_index_for_a_wide_nested_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    cleanup = _target("cleanup", tmp_path / "cleanup")
    for index in range(64):
        leaf = cleanup.path / f"sdk-{index:03d}" / "nested"
        leaf.mkdir(parents=True)
        (leaf / "payload.bin").write_bytes(b"payload")

    index_calls: list[int] = []
    real_index = runner_disk._index_expected_children

    def count_index_calls(
        expected: Mapping[str, runner_disk._EntryIdentity],
    ) -> Mapping[str, frozenset[str]]:
        index_calls.append(len(expected))
        return real_index(expected)

    monkeypatch.setattr(runner_disk, "_index_expected_children", count_index_calls)
    report = _reclaim(
        tmp_path,
        minimum_free_bytes=150,
        cache_targets=(cleanup,),
        snapshotter=_snapshotter(disk, {cleanup.path: 50}),
    )

    assert report.removed == ("cleanup",)
    assert not cleanup.path.exists()
    assert index_calls == [1 + 1 + 64 * 3]


def test_delete_uses_immutable_child_index_with_bounded_point_lookups(
    tmp_path: Path,
) -> None:
    cleanup_path = tmp_path / "cleanup"
    cleanup_path.mkdir()
    (cleanup_path / "empty").mkdir()
    (cleanup_path / "root-file").write_bytes(b"payload")
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    (cleanup_path / "outside-link").symlink_to(outside)
    for index in range(32):
        leaf = cleanup_path / f"tool-{index:03d}" / "bin"
        leaf.mkdir(parents=True)
        (leaf / "executable").write_bytes(b"payload")

    target = CleanupTarget("cleanup", cleanup_path)
    disk_device = os.lstat(tmp_path).st_dev
    inventory = runner_disk._inventory_target(target, disk_device=disk_device)
    identities = {entry.relative_path: entry for entry in inventory.entries}
    indexed_children = runner_disk._index_expected_children(identities)

    assert indexed_children[""] == frozenset(
        {
            "empty",
            "outside-link",
            "root-file",
            *(f"tool-{index:03d}" for index in range(32)),
        }
    )
    assert indexed_children["empty"] == frozenset()
    with pytest.raises(TypeError):
        indexed_children[""] = frozenset()

    class PointLookupOnly(Mapping[str, object]):
        def __init__(self, values: Mapping[str, object]) -> None:
            self.values = values
            self.lookups = 0

        def __getitem__(self, key: str) -> object:
            self.lookups += 1
            return self.values[key]

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("deletion must not rescan a global mapping")

        def __len__(self) -> int:
            return len(self.values)

    point_identities = PointLookupOnly(identities)
    point_children = PointLookupOnly(indexed_children)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(cleanup_path, flags)
    try:
        runner_disk._delete_directory_contents(
            root_fd,
            relative_prefix="",
            display_path=cleanup_path,
            expected=point_identities,
            expected_children=point_children,
            disk_device=disk_device,
        )
    finally:
        os.close(root_fd)

    directory_count = sum(entry.kind == "directory" for entry in inventory.entries)
    assert point_identities.lookups == len(inventory.entries) - 1
    assert point_children.lookups == directory_count
    assert not any(cleanup_path.iterdir())
    assert outside.read_bytes() == b"preserve"


def test_delete_fails_closed_if_child_index_omits_an_empty_directory(
    tmp_path: Path,
) -> None:
    cleanup_path = tmp_path / "cleanup"
    empty = cleanup_path / "empty"
    empty.mkdir(parents=True)
    target = CleanupTarget("cleanup", cleanup_path)
    disk_device = os.lstat(tmp_path).st_dev
    inventory = runner_disk._inventory_target(target, disk_device=disk_device)
    identities = {entry.relative_path: entry for entry in inventory.entries}
    indexed_children = dict(runner_disk._index_expected_children(identities))
    del indexed_children["empty"]
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(cleanup_path, flags)
    try:
        with pytest.raises(RunnerDiskError, match="child index is missing"):
            runner_disk._delete_directory_contents(
                root_fd,
                relative_prefix="",
                display_path=cleanup_path,
                expected=identities,
                expected_children=indexed_children,
                disk_device=disk_device,
            )
    finally:
        os.close(root_fd)

    assert empty.is_dir()


def test_mount_boundary_is_rejected_during_preinventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    first = _target("first", tmp_path / "first")
    mounted = _target("mounted", tmp_path / "mounted")
    real_ismount = runner_disk.os.path.ismount

    def fake_ismount(path: str | os.PathLike[str]) -> bool:
        return Path(path) == mounted.path or real_ismount(path)

    monkeypatch.setattr(runner_disk.os.path, "ismount", fake_ismount)
    with pytest.raises(RunnerDiskError, match="mount point"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=900,
            cache_targets=(first, mounted),
            snapshotter=_snapshotter(disk, {first.path: 50, mounted.path: 50}),
        )

    assert first.path.is_dir()
    assert mounted.path.is_dir()


def test_protected_executable_inside_cleanup_root_is_rejected(tmp_path: Path) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    cleanup = _target("cache", tmp_path / "cache")
    protected = _executable(cleanup.path / "bin" / "python", b"protected")

    with pytest.raises(RunnerDiskError, match="protected executable"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=1,
            cache_targets=(cleanup,),
            protected_executables=(protected,),
            snapshotter=_snapshotter(disk, {cleanup.path: 1}),
        )

    assert protected.is_file()


def test_protected_hash_is_verified_after_each_removal(tmp_path: Path) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    cleanup = _target("cache", tmp_path / "cache")
    protected = _executable(tmp_path / "protected" / "node", b"before")

    def mutate_protected() -> None:
        protected.write_bytes(b"after")
        protected.chmod(0o755)

    with pytest.raises(RunnerDiskError, match="identity changed"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=150,
            cache_targets=(cleanup,),
            protected_executables=(protected,),
            snapshotter=_snapshotter(disk, {cleanup.path: 50}),
            syncer=mutate_protected,
        )


def test_reclaim_fails_closed_when_allowlist_cannot_meet_budget(
    tmp_path: Path,
) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    cache = _target("cache", tmp_path / "cache")
    toolchain = _target("toolchain", tmp_path / "toolchain")

    with pytest.raises(RunnerDiskError, match="minimum free-byte budget"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=900,
            cache_targets=(cache,),
            toolchain_targets=(toolchain,),
            snapshotter=_snapshotter(
                disk, {cache.path: 50, toolchain.path: 50}, base=100
            ),
        )

    assert not cache.path.exists()
    assert not toolchain.path.exists()


def test_overlapping_targets_fail_before_deletion(tmp_path: Path) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    outer = _target("outer", tmp_path / "outer")
    inner = _target("inner", outer.path / "inner")

    with pytest.raises(RunnerDiskError, match="overlap"):
        _reclaim(
            tmp_path,
            minimum_free_bytes=900,
            cache_targets=(outer, inner),
            snapshotter=_snapshotter(disk, {outer.path: 50}),
        )

    assert outer.path.is_dir()
    assert inner.path.is_dir()


def test_budget_assertion_is_exact_and_reports_bytes(tmp_path: Path) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    snapshot = DiskSnapshot(
        path=disk,
        device=42,
        total_bytes=1_000,
        used_bytes=600,
        free_bytes=400,
        free_inodes=123,
    )
    emitted: list[str] = []

    assert_disk_budget(
        disk,
        minimum_free_bytes=400,
        phase="exact-digest-pulls",
        snapshotter=lambda _path: snapshot,
        emit=emitted.append,
    )
    assert emitted == [
        "RELEASE RUNNER DISK BUDGET phase=exact-digest-pulls "
        "device=42 total_bytes=1000 used_bytes=600 free_bytes=400 "
        "free_inodes=123 required_free_bytes=400"
    ]

    with pytest.raises(RunnerDiskError, match="exact-digest-pulls"):
        assert_disk_budget(
            disk,
            minimum_free_bytes=401,
            phase="exact-digest-pulls",
            snapshotter=lambda _path: snapshot,
        )


def test_budget_assertion_enforces_explicit_inode_floor(tmp_path: Path) -> None:
    disk = tmp_path / "docker"
    disk.mkdir()
    snapshot = DiskSnapshot(
        path=disk,
        device=42,
        total_bytes=10_000,
        used_bytes=1,
        free_bytes=9_999,
        free_inodes=99,
    )

    with pytest.raises(RunnerDiskError, match="free_inodes=99 required=100"):
        assert_disk_budget(
            disk,
            minimum_free_bytes=1,
            minimum_free_inodes=100,
            phase="pre-compose",
            snapshotter=lambda _path: snapshot,
        )


def test_production_targets_budgets_and_phase_checks_are_fixed(tmp_path: Path) -> None:
    targets = runner_disk.default_cleanup_targets()
    assert [(target.name, str(target.path)) for target in targets] == [
        (
            "duplicate-chromium-installer",
            "/home/runner/work/_temp/omega-playwright-browsers.install",
        ),
        ("pip-cache", "/home/runner/.cache/pip"),
        ("npm-cacache", "/home/runner/.npm/_cacache"),
        ("android-toolchain", "/usr/local/lib/android"),
        ("codeql-toolchain", "/opt/hostedtoolcache/CodeQL"),
    ]
    assert runner_disk.PRE_PULL_MINIMUM_FREE_BYTES == 17 * 1024**3
    assert runner_disk.PRE_INFRA_MINIMUM_FREE_BYTES == 5 * 1024**3
    assert runner_disk.PRE_COMPOSE_MINIMUM_FREE_BYTES == 2 * 1024**3
    assert runner_disk.MINIMUM_FREE_INODES == 100_000
    assert runner_disk.FIXED_PROTECTED_EXECUTABLES == (
        Path("/usr/bin/python3"),
        Path("/opt/omega-release-runtime/bin/node"),
        Path("/usr/bin/docker"),
        Path("/opt/omega-release-runtime/bin/docker"),
    )
    assert "v1.45.213-beta run 31831837077" in runner_disk.DISK_BUDGET_RATIONALE
    assert "job 94872875740" in runner_disk.DISK_BUDGET_RATIONALE
    assert "15 immutable application digests" in runner_disk.DISK_BUDGET_RATIONALE
    assert "26-service startup" in runner_disk.DISK_BUDGET_RATIONALE

    disk = tmp_path / "docker"
    disk.mkdir()
    at_budget = DiskSnapshot(
        path=disk,
        device=1,
        total_bytes=20 * 1024**3,
        used_bytes=3 * 1024**3,
        free_bytes=17 * 1024**3,
        free_inodes=100_000,
    )
    assert_release_phase_budget(
        "pre-pull",
        disk_path=disk,
        snapshotter=lambda _path: at_budget,
        emit=lambda _line: None,
    )

    with pytest.raises(RunnerDiskError, match="unknown release disk phase"):
        assert_release_phase_budget("arbitrary", disk_path=disk)


def test_production_context_works_with_an_explicit_env_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in tuple(os.environ):
        monkeypatch.delenv(key, raising=False)
    explicit = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "ImageOS": "ubuntu24",
        "RUNNER_TEMP": "/home/runner/work/_temp",
        "GITHUB_WORKSPACE": "/home/runner/work/CONSOLA-V1/CONSOLA-V1",
    }
    for key, value in explicit.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runner_disk, "_read_os_release", lambda: ("ubuntu", "24.04"))
    monkeypatch.setattr(runner_disk.os, "geteuid", lambda: 0)

    runner_temp, workspace, protected = runner_disk._production_context(
        require_root=True
    )

    assert runner_temp == Path(explicit["RUNNER_TEMP"])
    assert workspace == Path(explicit["GITHUB_WORKSPACE"])
    assert protected == runner_disk.FIXED_PROTECTED_EXECUTABLES
    assert "PATH" not in os.environ


def test_production_disk_path_cannot_use_an_injected_allowlist(tmp_path: Path) -> None:
    unexpected = _target("unexpected", tmp_path / "unexpected")

    with pytest.raises(RunnerDiskError, match="allowlist or order changed"):
        reclaim_hosted_runner_disk(
            runner_environment="github-hosted",
            runner_os="Linux",
            github_actions="true",
            runner_temp=runner_disk.FIXED_RUNNER_TEMP,
            workspace=Path("/home/runner/work/CONSOLA-V1/CONSOLA-V1"),
            disk_path=runner_disk.FIXED_DOCKER_ROOT,
            minimum_free_bytes=1,
            protected_executables=runner_disk.FIXED_PROTECTED_EXECUTABLES,
            cache_targets=(unexpected,),
            toolchain_targets=(),
        )

    assert unexpected.path.is_dir()


def test_cli_reclaim_uses_only_fixed_production_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "ImageOS": "ubuntu24",
    }
    for key, value in explicit.items():
        monkeypatch.setenv(key, value)
    targets = runner_disk.default_cleanup_targets()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        runner_disk,
        "_production_context",
        lambda *, require_root: (
            runner_disk.FIXED_RUNNER_TEMP,
            Path("/home/runner/work/CONSOLA-V1/CONSOLA-V1"),
            runner_disk.FIXED_PROTECTED_EXECUTABLES,
        ),
    )

    def fake_reclaim(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(runner_disk, "reclaim_hosted_runner_disk", fake_reclaim)

    assert runner_disk.main(["reclaim"]) == 0
    assert captured["disk_path"] == runner_disk.FIXED_DOCKER_ROOT
    assert captured["minimum_free_bytes"] == 17 * 1024**3
    assert captured["minimum_free_inodes"] == 100_000
    assert captured["protected_executables"] == (
        runner_disk.FIXED_PROTECTED_EXECUTABLES
    )
    assert captured["cache_targets"] == targets[:3]
    assert captured["toolchain_targets"] == targets[3:]


def test_cli_check_uses_non_overridable_phase_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases: list[str] = []
    monkeypatch.setattr(
        runner_disk,
        "_production_context",
        lambda *, require_root: (
            runner_disk.FIXED_RUNNER_TEMP,
            Path("/home/runner/work/CONSOLA-V1/CONSOLA-V1"),
            (),
        ),
    )
    monkeypatch.setattr(
        runner_disk,
        "assert_release_phase_budget",
        lambda phase: phases.append(phase),
    )

    assert runner_disk.main(["check", "--phase", "pre-compose"]) == 0
    assert phases == ["pre-compose"]
