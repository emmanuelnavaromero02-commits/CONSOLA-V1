#!/usr/bin/env python3
"""Open stable GHCR lock/auth descriptors and execute the exact child boundary."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path


PARENT_FD = 4
ROOT_FD = 5
CONTEXT_FD = 6
CONFIG_FD = 7
CONFIG_DIRECTORY_FD = 8
LOCK_FD = 9
BUNDLE_DIRECTORY_FD = 10
BUNDLE_FILE_FDS = {
    "ghcr-auth-run.sh": 11,
    "secure-ghcr-session.py": 12,
    "preflight-release-images.sh": 13,
    "validate-image-authority.py": 14,
    "validate-lock-output.py": 15,
    "publish-image-lock.py": 16,
    "release_images.py": 17,
    "bundle-manifest.json": 18,
}
BUNDLE_FILE_MODES = {
    "ghcr-auth-run.sh": 0o500,
    "secure-ghcr-session.py": 0o400,
    "preflight-release-images.sh": 0o500,
    "validate-image-authority.py": 0o400,
    "validate-lock-output.py": 0o400,
    "publish-image-lock.py": 0o400,
    "release_images.py": 0o400,
    "bundle-manifest.json": 0o400,
}
BUNDLE_MAX_FILE_BYTES = 8 * 1024 * 1024
HOST_LOCK_NAME = ".omega-gcp-ghcr-release.lock"
RUNNER_NAME = "ghcr-auth-run.sh"
PREFLIGHT_NAME = "preflight-release-images.sh"
CANONICAL_OWNER = "emmanuelnavaromero02-commits"
RELEASE_SERVICES = frozenset(
    {
        "airflow",
        "banxico",
        "console",
        "hubspot",
        "inegi",
        "mcp-infra",
        "refinement",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "sec_edgar",
        "vault",
        "workspace",
    }
)
SCRIPT_MODES = frozenset({0o500, 0o555, 0o700, 0o755})
DOCKER_DEADLINES = {
    "login": 60,
    "logout": 30,
    "info": 30,
    "pull": 600,
    "inspect-repo-digests": 30,
    "inspect-oci-identity": 30,
    "inspect-image-id": 30,
}
PREFLIGHT_DEADLINE = 2 * 60 * 60
TERM_GRACE_SECONDS = 5
MAX_SAFE_OUTPUT = 1024 * 1024
CANONICAL_DOCKER_HOST = "unix:///run/docker.sock"
PROCESS_GROUP_SUPERVISOR = (
    "trap 'trap \"\" TERM; while :; do sleep 1; done' TERM\n"
    "exec 3<&0\n"
    '(trap - TERM; exec "$@" <&3 3<&-) &\n'
    "worker=$!\n"
    "exec 3<&-\n"
    'wait "$worker"'
)
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
IMAGE_REFERENCE_PATTERN = re.compile(
    r"ghcr\.io/emmanuelnavaromero02-commits/"
    r"([a-z0-9][a-z0-9_-]*)@sha256:[0-9a-f]{64}"
)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _bundle_environment() -> dict[str, str]:
    return {
        "OMEGA_GHCR_BUNDLE_BOUND": "1",
        "OMEGA_GHCR_BUNDLE_DIRECTORY_FD": str(BUNDLE_DIRECTORY_FD),
        "OMEGA_GHCR_BUNDLE_RUNNER_FD": str(BUNDLE_FILE_FDS[RUNNER_NAME]),
        "OMEGA_GHCR_BUNDLE_HELPER_FD": str(BUNDLE_FILE_FDS["secure-ghcr-session.py"]),
        "OMEGA_GHCR_BUNDLE_PREFLIGHT_FD": str(BUNDLE_FILE_FDS[PREFLIGHT_NAME]),
        "OMEGA_GHCR_BUNDLE_AUTHORITY_VALIDATOR_FD": str(
            BUNDLE_FILE_FDS["validate-image-authority.py"]
        ),
        "OMEGA_GHCR_BUNDLE_LOCK_VALIDATOR_FD": str(
            BUNDLE_FILE_FDS["validate-lock-output.py"]
        ),
        "OMEGA_GHCR_BUNDLE_PUBLISHER_FD": str(BUNDLE_FILE_FDS["publish-image-lock.py"]),
        "OMEGA_GHCR_BUNDLE_RELEASE_IMAGES_FD": str(
            BUNDLE_FILE_FDS["release_images.py"]
        ),
        "OMEGA_GHCR_BUNDLE_MANIFEST_FD": str(BUNDLE_FILE_FDS["bundle-manifest.json"]),
    }


def _read_descriptor(descriptor: int, maximum: int) -> bytes:
    info = os.fstat(descriptor)
    raw = os.pread(descriptor, maximum + 1, 0)
    if not raw or len(raw) != info.st_size or len(raw) > maximum:
        raise ValueError("sealed bundle descriptor changed or exceeds its limit")
    return raw


def _validate_bundle_descriptors() -> None:
    expected_environment = _bundle_environment()
    if any(os.environ.get(key) != value for key, value in expected_environment.items()):
        raise ValueError("sealed bundle descriptor environment differs")
    directory = os.fstat(BUNDLE_DIRECTORY_FD)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or directory.st_gid != os.getegid()
        or stat.S_IMODE(directory.st_mode) != 0o500
        or set(os.listdir(BUNDLE_DIRECTORY_FD)) != set(BUNDLE_FILE_FDS)
    ):
        raise ValueError("sealed bundle directory differs")
    manifest_fd = BUNDLE_FILE_FDS["bundle-manifest.json"]
    manifest_info = os.fstat(manifest_fd)
    if (
        not stat.S_ISREG(manifest_info.st_mode)
        or manifest_info.st_uid != os.geteuid()
        or manifest_info.st_gid != os.getegid()
        or stat.S_IMODE(manifest_info.st_mode) != 0o400
        or manifest_info.st_nlink != 1
    ):
        raise ValueError("sealed bundle manifest descriptor differs")
    manifest_raw = _read_descriptor(manifest_fd, 128 * 1024)
    document = json.loads(manifest_raw, object_pairs_hook=_strict_object)
    canonical = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if (
        manifest_raw != canonical
        or not isinstance(document, dict)
        or set(document) != {"files", "schema_version", "source_sha"}
        or document.get("schema_version") != 1
        or SHA_PATTERN.fullmatch(str(document.get("source_sha", ""))) is None
        or not isinstance(document.get("files"), dict)
        or set(document["files"]) != set(BUNDLE_FILE_FDS) - {"bundle-manifest.json"}
    ):
        raise ValueError("sealed bundle manifest contract differs")
    files = document["files"]
    assert isinstance(files, dict)
    for name, descriptor in BUNDLE_FILE_FDS.items():
        info = os.fstat(descriptor)
        named = os.stat(name, dir_fd=BUNDLE_DIRECTORY_FD, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != BUNDLE_FILE_MODES[name]
            or info.st_nlink != 1
            or not 1 <= info.st_size <= BUNDLE_MAX_FILE_BYTES
            or not _same(info, named)
        ):
            raise ValueError(f"sealed bundle descriptor differs: {name}")
        if name == "bundle-manifest.json":
            continue
        row = files.get(name)
        raw = _read_descriptor(descriptor, BUNDLE_MAX_FILE_BYTES)
        if (
            not isinstance(row, dict)
            or set(row) != {"mode", "sha256", "size"}
            or row.get("mode") != f"{BUNDLE_FILE_MODES[name]:04o}"
            or row.get("size") != len(raw)
            or row.get("sha256") != hashlib.sha256(raw).hexdigest()
        ):
            raise ValueError(f"sealed bundle digest differs: {name}")


def _bind_bundle(bundle_dir: Path) -> None:
    if (
        not bundle_dir.is_absolute()
        or bundle_dir.resolve(strict=True) != bundle_dir
        or SHA_PATTERN.fullmatch(bundle_dir.name) is None
        or bundle_dir.parent.name != "ghcr-release-bundles"
    ):
        raise ValueError("GHCR bundle path is not revision-bound")
    parent_info = bundle_dir.parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or parent_info.st_gid != os.getegid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
        or bundle_dir.parent.resolve(strict=True) != bundle_dir.parent
    ):
        raise ValueError("GHCR bundle parent is unsafe")
    directory_fd = os.open(
        bundle_dir,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    opened: list[int] = []
    try:
        for name in BUNDLE_FILE_FDS:
            opened.append(
                os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
            )
        _duplicate_many(
            ((directory_fd, BUNDLE_DIRECTORY_FD),)
            + tuple(
                (descriptor, BUNDLE_FILE_FDS[name])
                for name, descriptor in zip(BUNDLE_FILE_FDS, opened, strict=True)
            )
        )
    finally:
        targets = {BUNDLE_DIRECTORY_FD, *BUNDLE_FILE_FDS.values()}
        for descriptor in opened:
            if descriptor in targets:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass
        if directory_fd not in targets:
            try:
                os.close(directory_fd)
            except OSError:
                pass
    os.environ.update(_bundle_environment())
    for descriptor in (BUNDLE_DIRECTORY_FD, *BUNDLE_FILE_FDS.values()):
        os.set_inheritable(descriptor, True)
    _validate_bundle_descriptors()


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_directory(info: os.stat_result, *, mode: int) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise ValueError("unsafe GHCR session directory")


def _validate_regular(
    info: os.stat_result, *, mode: int, minimum: int, maximum: int
) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_nlink != 1
        or not minimum <= info.st_size <= maximum
    ):
        raise ValueError("unsafe GHCR session file")


def _validate_executable(
    path: Path,
    *,
    expected_name: str | None = None,
    maximum_size: int = 1024 * 1024,
) -> Path:
    """Bind one absolute, non-link executable owned by the current authority."""

    if (
        not path.is_absolute()
        or path.name in {"", ".", ".."}
        or (expected_name is not None and path.name != expected_name)
        or path.is_symlink()
    ):
        raise ValueError("executable path is outside the exact allowlist")
    canonical = path.resolve(strict=True)
    if canonical != path:
        raise ValueError("executable path is not its canonical realpath")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) not in SCRIPT_MODES
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum_size
            or not _same(before, named)
        ):
            raise ValueError("executable inode/owner/mode is not allowlisted")
        after = os.fstat(descriptor)
        if (
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
            raise ValueError("executable changed while its identity was bound")
    finally:
        os.close(descriptor)
    return canonical


def _open_executable(
    path: Path, *, expected_name: str, maximum_size: int
) -> tuple[int, Path]:
    canonical = _validate_executable(
        path, expected_name=expected_name, maximum_size=maximum_size
    )
    descriptor = os.open(
        canonical,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        named = os.stat(canonical, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) not in SCRIPT_MODES
            or info.st_nlink != 1
            or not 1 <= info.st_size <= maximum_size
            or not _same(info, named)
        ):
            raise ValueError("executable descriptor changed before binding")
        os.set_inheritable(descriptor, True)
        return descriptor, Path(f"/proc/self/fd/{descriptor}")
    except Exception:
        os.close(descriptor)
        raise


def _validate_release_authority(
    *,
    runner: Path,
    preflight: Path,
    owner: str,
    image_tag: str,
    source_sha: str,
    version: str,
    lock_file: Path,
) -> tuple[Path, Path]:
    if os.environ.get("OMEGA_GHCR_BUNDLE_BOUND") == "1":
        _validate_bundle_descriptors()
        expected_runner = Path(f"/proc/self/fd/{BUNDLE_FILE_FDS[RUNNER_NAME]}")
        expected_preflight = Path(f"/proc/self/fd/{BUNDLE_FILE_FDS[PREFLIGHT_NAME]}")
        if runner != expected_runner or preflight != expected_preflight:
            raise ValueError("GHCR runner/preflight descriptors differ")
    else:
        runner = _validate_executable(runner, expected_name=RUNNER_NAME)
        preflight = _validate_executable(preflight, expected_name=PREFLIGHT_NAME)
        helper = Path(__file__).resolve(strict=True)
        if runner.parent != preflight.parent or helper.parent != runner.parent:
            raise ValueError("GHCR runner/preflight/helper are not exact siblings")
    if (
        owner != CANONICAL_OWNER
        or SHA_PATTERN.fullmatch(source_sha) is None
        or VERSION_PATTERN.fullmatch(version) is None
        or image_tag not in {f"candidate-{source_sha}", f"v{version}"}
        or not lock_file.is_absolute()
        or os.fspath(lock_file) != os.path.abspath(lock_file)
        or lock_file.name != "release-images.env"
        or any(part in {"", ".", ".."} for part in lock_file.parts)
    ):
        raise ValueError("release preflight five-argument authority differs")
    return runner, preflight


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(TERM_GRACE_SECONDS)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=TERM_GRACE_SECONDS)


def _group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _drain_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Kill a surviving descendant group and prove that it is gone.

    The session leader may exit while a daemonised grandchild still owns the
    Docker-config descriptors.  A successful direct child is therefore not a
    successful boundary until its complete process group has disappeared.
    """

    group_id = process.pid
    if not _group_exists(group_id):
        return False
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _group_exists(group_id):
            return True
        time.sleep(0.02)
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _group_exists(group_id):
            return True
        time.sleep(0.02)
    raise ValueError("bounded child process group could not be fenced")


def _run_bounded(
    command: list[str],
    *,
    environment: dict[str, str],
    deadline: int | float,
    pass_fds: tuple[int, ...] = (),
    inherit_stdin: bool = False,
    maximum_output: int = MAX_SAFE_OUTPUT,
) -> tuple[int, bytes]:
    """Run one prevalidated operation with a hard process-group deadline."""

    process = subprocess.Popen(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-p",
            "-c",
            PROCESS_GROUP_SUPERVISOR,
            "omega-bounded-supervisor",
            *command,
        ],
        env=environment,
        stdin=None if inherit_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        pass_fds=pass_fds,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, stdout)
    selector.register(process.stderr, selectors.EVENT_READ, stderr)
    expires = time.monotonic() + deadline
    try:
        while selector.get_map():
            remaining = expires - time.monotonic()
            if remaining <= 0:
                _terminate_group(process)
                raise ValueError("bounded child exceeded its hard deadline")
            events = selector.select(min(remaining, 0.25))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ) for key in selector.get_map().values()
                ]
            for key, _mask in events:
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = key.data
                target.extend(chunk)
                if len(stdout) + len(stderr) > maximum_output:
                    _terminate_group(process)
                    raise ValueError("bounded child output exceeded its limit")
        try:
            returncode = process.wait(timeout=max(0.01, expires - time.monotonic()))
        except subprocess.TimeoutExpired:
            _terminate_group(process)
            raise ValueError("bounded child exceeded its hard deadline") from None
        leftovers = _drain_process_group(process)
        if leftovers:
            raise ValueError("bounded child left a surviving process group")
        return returncode, bytes(stdout)
    except BaseException:
        if process.poll() is None:
            _terminate_group(process)
        else:
            _drain_process_group(process)
        raise
    finally:
        selector.close()


def _duplicate_many(mappings: tuple[tuple[int, int], ...]) -> None:
    """Remap descriptors without a low-number target clobbering a later source."""

    minimum = max(target for _source, target in mappings) + 1
    temporary = [
        (
            fcntl.fcntl(
                source,
                getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD),
                minimum,
            ),
            target,
        )
        for source, target in mappings
    ]
    try:
        for descriptor, target in temporary:
            os.dup2(descriptor, target, inheritable=True)
    finally:
        for descriptor, _target in temporary:
            os.close(descriptor)
        for source, _target in mappings:
            if source not in {target for _item, target in mappings}:
                os.close(source)


def _open_lock(parent_fd: int) -> int:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    created = False
    try:
        descriptor = os.open(HOST_LOCK_NAME, flags, 0o600, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        descriptor = os.open(
            HOST_LOCK_NAME,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
    try:
        if created:
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, os.geteuid(), os.getegid())
        os.fsync(descriptor)
        if created:
            os.fsync(parent_fd)
        current = os.fstat(descriptor)
        path_info = os.stat(HOST_LOCK_NAME, dir_fd=parent_fd, follow_symlinks=False)
        _validate_regular(current, mode=0o600, minimum=0, maximum=4096)
        if not _same(current, path_info):
            raise ValueError("GHCR host lock pathname changed during open")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another canonical GHCR release operation holds the host lock"
            ) from exc
        # Revalidate the parent-relative name after the lock is held. The same
        # open file description is retained through exec; no pathname reopen,
        # truncation, or symlink-following operation is used.
        if not _same(
            os.fstat(descriptor),
            os.stat(HOST_LOCK_NAME, dir_fd=parent_fd, follow_symlinks=False),
        ):
            raise ValueError("GHCR host lock pathname changed after flock")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _require_lock_held(parent_fd: int, lock_fd: int) -> None:
    """Probe from a separate process after closing its inherited lock copy."""

    child = os.fork()
    if child == 0:
        try:
            os.close(lock_fd)
            probe = os.open(
                HOST_LOCK_NAME,
                os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os._exit(0)
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
                os._exit(1)
        except OSError:
            os._exit(2)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise ValueError("inherited GHCR lock is not held")


def lock_exec(args: argparse.Namespace) -> int:
    if (
        args.script != args.bundle_dir / RUNNER_NAME
        or args.preflight != args.bundle_dir / PREFLIGHT_NAME
    ):
        raise ValueError("initial GHCR bundle entrypoints differ")
    _bind_bundle(args.bundle_dir)
    runner_path = Path(f"/proc/self/fd/{BUNDLE_FILE_FDS[RUNNER_NAME]}")
    preflight_path = Path(f"/proc/self/fd/{BUNDLE_FILE_FDS[PREFLIGHT_NAME]}")
    runner, preflight = _validate_release_authority(
        runner=runner_path,
        preflight=preflight_path,
        owner=args.owner,
        image_tag=args.image_tag,
        source_sha=args.source_sha,
        version=args.version,
        lock_file=args.lock_file,
    )
    auth_root = args.auth_root
    if (
        not auth_root.is_absolute()
        or os.fspath(auth_root) != os.path.abspath(auth_root)
        or auth_root.name in {"", ".", ".."}
    ):
        raise ValueError("GHCR auth root must be one absolute direct child")
    parent_fd = os.open(
        auth_root.parent,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        parent_info = os.fstat(parent_fd)
        path_info = os.lstat(auth_root.parent)
        if not stat.S_ISDIR(parent_info.st_mode) or not _same(parent_info, path_info):
            raise ValueError("GHCR auth parent changed during open")
        lock_fd = _open_lock(parent_fd)
        try:
            os.mkdir(auth_root.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        root_fd = os.open(
            auth_root.name,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        root_info = os.fstat(root_fd)
        named_info = os.stat(auth_root.name, dir_fd=parent_fd, follow_symlinks=False)
        _validate_directory(root_info, mode=0o700)
        if not _same(root_info, named_info):
            raise ValueError("GHCR auth root changed during open")
    except Exception:
        os.close(parent_fd)
        raise

    _duplicate_many(((parent_fd, PARENT_FD), (root_fd, ROOT_FD), (lock_fd, LOCK_FD)))
    environment = dict(os.environ)
    environment["OMEGA_GHCR_LOCK_BOOTSTRAPPED"] = "1"
    environment["OMEGA_GHCR_AUTH_PARENT_FD"] = str(PARENT_FD)
    environment["OMEGA_GHCR_AUTH_ROOT_FD"] = str(ROOT_FD)
    environment["OMEGA_GHCR_AUTH_ROOT_NAME"] = auth_root.name
    environment["OMEGA_GHCR_AUTH_LOCK_FD"] = str(LOCK_FD)
    environment.update(_bundle_environment())
    os.execve(
        "/bin/bash",
        [
            "/bin/bash",
            "-p",
            os.fspath(runner),
            os.fspath(preflight),
            args.owner,
            args.image_tag,
            args.source_sha,
            args.version,
            os.fspath(args.lock_file),
        ],
        environment,
    )
    raise AssertionError("unreachable")


def _read_exact(descriptor: int, maximum: int) -> bytes:
    info = os.fstat(descriptor)
    raw = os.pread(descriptor, maximum + 1, 0)
    if len(raw) != info.st_size or not raw or len(raw) > maximum:
        raise ValueError("GHCR auth file changed while bound")
    return raw


def _exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _parse_child_environment(values: list[str]) -> dict[str, str]:
    allowed = {
        "PATH",
        "LANG",
        "PYTHONSAFEPATH",
        "PYTHONNOUSERSITE",
        "DOCKER_HOST",
        "OMEGA_GCP_IMAGE_AUTHORITY_MODE",
        "OMEGA_RELEASE_CANDIDATE_MANIFEST_SHA256",
        "OMEGA_RELEASE_CANDIDATE_RUN_ID",
        "OMEGA_RELEASE_CANDIDATE_RUN_ATTEMPT",
        "OMEGA_RELEASE_TAG_MANIFEST_SHA256",
        "OMEGA_RELEASE_TAG_OBJECT_SHA",
        "OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE",
        "OMEGA_GCP_ROLLBACK_RUNTIME_IMAGES",
        "OMEGA_GCP_LEGACY_TAG_COMMIT",
    }
    environment: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if (
            not separator
            or not name
            or name not in allowed
            or name in environment
            or "\x00" in content
            or "\n" in content
            or "\r" in content
            or len(content) > 4096
        ):
            raise ValueError("invalid exact child environment")
        environment[name] = content
    required = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONSAFEPATH": "1",
        "PYTHONNOUSERSITE": "1",
        "DOCKER_HOST": CANONICAL_DOCKER_HOST,
    }
    if any(environment.get(name) != value for name, value in required.items()):
        raise ValueError("required child environment differs")
    return environment


def auth_exec(args: argparse.Namespace) -> int:
    _validate_bundle_descriptors()
    _runner, preflight = _validate_release_authority(
        runner=args.runner_script,
        preflight=args.preflight,
        owner=args.owner,
        image_tag=args.image_tag,
        source_sha=args.source_sha,
        version=args.version,
        lock_file=args.lock_file,
    )
    parent_fd = args.parent_fd
    root_fd = args.root_fd
    lock_fd = args.lock_fd
    parent_info = os.fstat(parent_fd)
    root_info = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or parent_info.st_gid != os.getegid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.auth_root_name)
        is None
        or re.fullmatch(r"omega-gcp-ghcr-auth\.[A-Za-z0-9]{6}", args.config_directory)
        is None
        or not _same(
            root_info,
            os.stat(args.auth_root_name, dir_fd=parent_fd, follow_symlinks=False),
        )
    ):
        raise ValueError("inherited GHCR auth parent/root binding changed")
    _validate_directory(root_info, mode=0o700)
    lock_info = os.fstat(lock_fd)
    _validate_regular(lock_info, mode=0o600, minimum=0, maximum=4096)
    if not _same(
        lock_info,
        os.stat(HOST_LOCK_NAME, dir_fd=parent_fd, follow_symlinks=False),
    ):
        raise ValueError("inherited GHCR lock no longer has its exact parent name")
    _require_lock_held(parent_fd, lock_fd)

    directory_fd = os.open(
        args.config_directory,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_fd,
    )
    directory_info = os.fstat(directory_fd)
    named_directory = os.stat(
        args.config_directory, dir_fd=root_fd, follow_symlinks=False
    )
    _validate_directory(directory_info, mode=0o700)
    expected_identity = args.expected_directory_identity
    if (
        re.fullmatch(r"[0-9]+:[0-9]+", expected_identity) is None
        or expected_identity != f"{directory_info.st_dev}:{directory_info.st_ino}"
        or not _same(directory_info, named_directory)
    ):
        raise ValueError("GHCR config directory changed during open")

    config_fd = os.open(
        "config.json",
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    context_fd = os.open(
        "auth-context.json",
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    config_info = os.fstat(config_fd)
    context_info = os.fstat(context_fd)
    _validate_regular(config_info, mode=0o600, minimum=2, maximum=65536)
    _validate_regular(context_info, mode=0o400, minimum=2, maximum=4096)
    if not _same(
        config_info,
        os.stat("config.json", dir_fd=directory_fd, follow_symlinks=False),
    ) or not _same(
        context_info,
        os.stat("auth-context.json", dir_fd=directory_fd, follow_symlinks=False),
    ):
        raise ValueError("GHCR auth files changed during stable descriptor binding")
    config = _read_exact(config_fd, 65536)
    raw_context = _read_exact(context_fd, 4096)
    context = json.loads(raw_context, object_pairs_hook=_exact_object)
    secret_version_resource = context.get("secret_version_resource")
    if (
        not isinstance(secret_version_resource, str)
        or re.fullmatch(
            r"projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/secrets/"
            r"omega-staging-ghcr_pull_credentials/versions/[1-9][0-9]*",
            secret_version_resource,
        )
        is None
    ):
        raise ValueError("GHCR secret-version receipt differs")
    expected_context = {
        "config_sha256": hashlib.sha256(config).hexdigest(),
        "owner": "emmanuelnavaromero02-commits",
        "private_packages": ["banxico", "inegi", "sec_edgar"],
        "registry": "ghcr.io",
        "schema_version": 1,
        "secret_version_resource": secret_version_resource,
    }
    canonical_context = (
        json.dumps(context, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if context != expected_context or raw_context != canonical_context:
        raise ValueError("GHCR auth context does not bind the exact config bytes")

    _duplicate_many(
        (
            (directory_fd, CONFIG_DIRECTORY_FD),
            (config_fd, CONFIG_FD),
            (context_fd, CONTEXT_FD),
        )
    )
    for descriptor in (
        PARENT_FD,
        ROOT_FD,
        CONTEXT_FD,
        CONFIG_FD,
        CONFIG_DIRECTORY_FD,
        LOCK_FD,
    ):
        os.set_inheritable(descriptor, True)
    environment = _parse_child_environment(args.environment)
    environment.update(
        {
            "DOCKER_CONFIG": f"{args.descriptor_root}/{CONFIG_DIRECTORY_FD}",
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
            "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
            "OMEGA_GHCR_AUTH_PARENT_FD": str(PARENT_FD),
            "OMEGA_GHCR_AUTH_ROOT_FD": str(ROOT_FD),
            "OMEGA_GHCR_AUTH_ROOT_NAME": args.auth_root_name,
            "OMEGA_GHCR_AUTH_CONTEXT_FD": str(CONTEXT_FD),
            "OMEGA_GHCR_AUTH_CONFIG_FD": str(CONFIG_FD),
            "OMEGA_GHCR_AUTH_DIRECTORY_FD": str(CONFIG_DIRECTORY_FD),
            "OMEGA_GHCR_AUTH_DIRECTORY_NAME": args.config_directory,
            "OMEGA_GHCR_AUTH_LOCK_FD": str(LOCK_FD),
        }
    )
    environment.update(_bundle_environment())
    returncode, stdout = _run_bounded(
        [
            "/bin/bash",
            "-p",
            os.fspath(preflight),
            args.owner,
            args.image_tag,
            args.source_sha,
            args.version,
            os.fspath(args.lock_file),
        ],
        environment=environment,
        deadline=PREFLIGHT_DEADLINE,
        pass_fds=(
            PARENT_FD,
            ROOT_FD,
            CONTEXT_FD,
            CONFIG_FD,
            CONFIG_DIRECTORY_FD,
            LOCK_FD,
            BUNDLE_DIRECTORY_FD,
            *BUNDLE_FILE_FDS.values(),
        ),
    )
    if returncode == 0:
        sys.stdout.buffer.write(stdout)
    return returncode


def _docker_environment() -> tuple[dict[str, str], tuple[int, ...]]:
    docker_host = os.environ.get("DOCKER_HOST")
    docker_config = os.environ.get("DOCKER_CONFIG")
    if docker_host != CANONICAL_DOCKER_HOST or not docker_config:
        raise ValueError("Docker authority is not pinned to the canonical host")
    pass_fds: tuple[int, ...] = ()
    descriptor_match = re.fullmatch(r"/(?:proc/self|dev)/fd/([0-9]+)", docker_config)
    if descriptor_match is not None:
        descriptor = int(descriptor_match.group(1))
        if descriptor != CONFIG_DIRECTORY_FD:
            raise ValueError("Docker config descriptor differs")
        info = os.fstat(descriptor)
        _validate_directory(info, mode=0o700)
        pass_fds = (descriptor,)
    elif not Path(docker_config).is_absolute():
        raise ValueError("Docker config path is not absolute")
    return (
        {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "DOCKER_HOST": docker_host,
            "DOCKER_CONFIG": docker_config,
        },
        pass_fds,
    )


def docker_exec(args: argparse.Namespace) -> int:
    docker_fd, docker = _open_executable(
        args.docker,
        expected_name="docker",
        maximum_size=256 * 1024 * 1024,
    )
    try:
        operation = args.docker_operation
        deadline = DOCKER_DEADLINES[operation]
        inherit_stdin = False
        if operation == "login":
            if (
                args.reference is not None
                or not isinstance(args.username, str)
                or re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?",
                    args.username,
                )
                is None
            ):
                raise ValueError("Docker login authority differs")
            command = [
                os.fspath(docker),
                "login",
                "ghcr.io",
                "--username",
                args.username,
                "--password-stdin",
            ]
            inherit_stdin = True
        elif operation == "logout":
            if args.username is not None or args.reference is not None:
                raise ValueError("Docker logout authority differs")
            command = [os.fspath(docker), "logout", "ghcr.io"]
        elif operation == "info":
            if args.username is not None or args.reference is not None:
                raise ValueError("Docker info authority differs")
            command = [
                os.fspath(docker),
                "info",
                "--format",
                "{{.DockerRootDir}}|{{.ID}}",
            ]
        else:
            match = (
                IMAGE_REFERENCE_PATTERN.fullmatch(args.reference or "")
                if args.username is None
                else None
            )
            if match is None or match.group(1) not in RELEASE_SERVICES:
                raise ValueError(
                    "Docker image reference is outside the release allowlist"
                )
            assert args.reference is not None
            if operation == "pull":
                command = [os.fspath(docker), "pull", "--quiet", args.reference]
            elif operation == "inspect-repo-digests":
                command = [
                    os.fspath(docker),
                    "image",
                    "inspect",
                    "--format",
                    "{{json .RepoDigests}}",
                    args.reference,
                ]
            elif operation == "inspect-oci-identity":
                command = [
                    os.fspath(docker),
                    "image",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}'
                    '{{println}}{{index .Config.Labels "org.opencontainers.image.version"}}'
                    '{{println}}{{index .Config.Labels "org.opencontainers.image.source"}}',
                    args.reference,
                ]
            elif operation == "inspect-image-id":
                command = [
                    os.fspath(docker),
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    args.reference,
                ]
            else:
                raise ValueError("Docker operation is not allowlisted")
        environment, pass_fds = _docker_environment()
        returncode, stdout = _run_bounded(
            command,
            environment=environment,
            deadline=deadline,
            pass_fds=(*pass_fds, docker_fd),
            inherit_stdin=inherit_stdin,
        )
        if returncode == 0 and operation not in {"login", "logout", "pull"}:
            sys.stdout.buffer.write(stdout)
        return returncode
    finally:
        os.close(docker_fd)


def _release_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--preflight", type=Path, required=True)
    command.add_argument("--owner", required=True)
    command.add_argument("--image-tag", required=True)
    command.add_argument("--source-sha", required=True)
    command.add_argument("--version", required=True)
    command.add_argument("--lock-file", type=Path, required=True)


def _interrupt(signum: int, _frame: object) -> None:
    raise InterruptedError(f"secure GHCR session interrupted by signal {signum}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    lock = commands.add_parser("lock-exec")
    lock.add_argument("--auth-root", type=Path, required=True)
    lock.add_argument("--bundle-dir", type=Path, required=True)
    lock.add_argument("--script", type=Path, required=True)
    _release_arguments(lock)
    auth = commands.add_parser("auth-exec")
    auth.add_argument("--parent-fd", type=int, default=PARENT_FD)
    auth.add_argument("--root-fd", type=int, default=ROOT_FD)
    auth.add_argument("--lock-fd", type=int, default=LOCK_FD)
    auth.add_argument("--auth-root-name", required=True)
    auth.add_argument("--config-directory", required=True)
    auth.add_argument("--expected-directory-identity", required=True)
    auth.add_argument("--runner-script", type=Path, required=True)
    auth.add_argument(
        "--descriptor-root",
        choices=("/proc/self/fd", "/dev/fd"),
        default="/proc/self/fd",
    )
    auth.add_argument("--environment", action="append", default=[])
    _release_arguments(auth)
    docker = commands.add_parser("docker-exec")
    docker.add_argument("--docker", type=Path, required=True)
    docker.add_argument(
        "--operation",
        dest="docker_operation",
        choices=tuple(DOCKER_DEADLINES),
        required=True,
    )
    docker.add_argument("--username")
    docker.add_argument("--reference")
    validate = commands.add_parser("validate-bundle")
    validate.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main() -> int:
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _interrupt)
    args = build_parser().parse_args()
    try:
        if args.operation == "lock-exec":
            return lock_exec(args)
        if args.operation == "validate-bundle":
            _bind_bundle(args.bundle_dir)
            return 0
        _validate_bundle_descriptors()
        if args.operation == "auth-exec":
            return auth_exec(args)
        return docker_exec(args)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(
            "secure GHCR session failed at " f"{frame.name}:{frame.lineno}: {exc}",
            file=sys.stderr,
        )
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
