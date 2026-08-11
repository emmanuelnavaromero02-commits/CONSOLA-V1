#!/usr/bin/python3 -I
"""Launch the database migration runner with a closed, isolated environment."""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


ALLOWED_INPUTS = {
    "APP_ENV",
    "OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER",
    "OMEGA_MIGRATION_BASELINE_MANIFEST",
    "OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256",
    "OMEGA_MIGRATION_BOOTSTRAP_MODE",
    "OMEGA_MIGRATION_CANDIDATE_REF",
    "OMEGA_MIGRATION_COMPOSE_FILE",
    "OMEGA_MIGRATION_COMPOSE_PROJECT_NAME",
    "OMEGA_MIGRATION_ENVIRONMENT",
    "OMEGA_MIGRATION_ENV_FILE",
    "OMEGA_MIGRATION_OLD_REF",
    "OMEGA_MIGRATION_RELEASE_ATTESTATION",
    "OMEGA_MIGRATION_RELEASE_MANIFEST",
    "OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256",
    "OMEGA_MIGRATION_RELEASE_VERSION",
    "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT",
    "OMEGA_MIGRATION_RUN_ID",
}
MAX_VALUE_BYTES = 4096
TIMEOUT_SECONDS = 1800
TERMINATION_GRACE_SECONDS = 60
CANONICAL_RECEIPT_ROOT = Path("/opt/modecissions/shared/operation-receipts")
CANONICAL_DAY2_LOCK = Path("/var/lock/omega-gcp-day2.lock")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _fail(message: str) -> None:
    raise SystemExit(f"migration launcher: {message}")


def _snapshot(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _fail(f"cannot open runner safely: {exc}")
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= 1024 * 1024
        ):
            _fail("runner file ownership/mode/size is unsafe")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                _fail("runner file read was short")
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
            _fail("runner file changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_private(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o500,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("private runner write made no progress")
            offset += written
        os.fchmod(descriptor, 0o500)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _closed_environment(
    root: Path, receipt_directory: Path | None, run_id: str | None = None
) -> dict[str, str]:
    explicit = os.environ.get("OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT") == "1"
    tool_path = "/usr/sbin:/usr/bin:/sbin:/bin"
    if not explicit:
        tool_path += ":/usr/local/bin:/opt/homebrew/bin"
    environment = {
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
        "OMEGA_MIGRATION_HERMETIC": "1",
        "OMEGA_MIGRATION_RELEASE_ROOT": str(root),
        "PATH": tool_path,
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8:strict",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }
    for name in sorted(ALLOWED_INPUTS):
        value = os.environ.get(name)
        if value is None:
            continue
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError:
            _fail(f"{name} is not strict UTF-8")
        if (
            not encoded
            or len(encoded) > MAX_VALUE_BYTES
            or "\n" in value
            or "\r" in value
        ):
            _fail(f"{name} is empty, multiline, or oversized")
        environment[name] = value
    if receipt_directory is not None:
        environment["OMEGA_MIGRATION_RECEIPT_DIR"] = str(receipt_directory)
    if run_id is not None:
        environment["OMEGA_MIGRATION_RUN_ID"] = run_id
    if not explicit:
        docker_host = _local_docker_host()
        if docker_host is not None:
            environment["DOCKER_HOST"] = docker_host
    return environment


def _local_docker_host() -> str | None:
    """Select one safe local desktop socket without trusting host variables."""
    system_socket = Path("/var/run/docker.sock")
    try:
        if stat.S_ISSOCK(system_socket.lstat().st_mode):
            return None
    except OSError:
        pass
    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        if (
            not home.is_absolute()
            or home.resolve(strict=True) != home
            or not stat.S_ISDIR(home.lstat().st_mode)
            or home.lstat().st_uid != os.geteuid()
            or stat.S_IMODE(home.lstat().st_mode) & 0o022
        ):
            return None
    except (KeyError, OSError, RuntimeError):
        return None
    safe: list[Path] = []
    for relative in (
        Path(".colima/default/docker.sock"),
        Path(".docker/run/docker.sock"),
    ):
        candidate = home / relative
        try:
            parents = (candidate.parent, candidate.parent.parent)
            if any(
                parent.resolve(strict=True) != parent
                or not stat.S_ISDIR(parent.lstat().st_mode)
                or parent.lstat().st_uid != os.geteuid()
                or stat.S_IMODE(parent.lstat().st_mode) & 0o022
                for parent in parents
            ):
                continue
            info = candidate.lstat()
            if (
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and candidate.resolve(strict=True) == candidate
            ):
                safe.append(candidate)
        except (OSError, RuntimeError):
            continue
    if len(safe) > 1:
        _fail("multiple safe local Docker sockets are active")
    return f"unix://{safe[0]}" if safe else None


def _verify_directory(descriptor: int, *, mode: int, label: str) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != mode
    ):
        _fail(f"{label} ownership or mode is unsafe")


def _open_directory(parent: int, name: str, *, mode: int, label: str) -> int:
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        _fail(f"cannot open {label} safely: {exc}")
    _verify_directory(descriptor, mode=mode, label=label)
    return descriptor


def _write_receipt_intent(
    directory: int,
    *,
    candidate: str,
    version: str,
    manifest_sha: str,
    run_id: str,
) -> None:
    payload = {
        "candidate_ref": candidate,
        "database_commit_state": "none",
        "exit_code": 0,
        "operation": "gcp_day2_database_migration",
        "phase": "launcher_intent",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "release_manifest_sha256": manifest_sha,
        "release_version": version,
        "run_id": run_id,
        "schema_version": 1,
        "status": "IN_PROGRESS",
    }
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(
            "00-launcher-intent.json", flags, mode=0o400, dir_fd=directory
        )
    except OSError as exc:
        _fail(f"cannot create the migration receipt intent: {exc}")
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("migration receipt intent write made no progress")
            offset += written
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory)


def _assert_candidate_receipt_absent(directory: int, candidate: str) -> None:
    """Persistently fence retries after any prior attempt for this candidate."""
    prefix = f"migration-{candidate}-"
    try:
        names = os.listdir(directory)
    except OSError as exc:
        _fail(f"cannot inspect prior migration receipts: {exc}")
    if any(name.startswith(prefix) for name in names):
        _fail(
            "a prior migration attempt for this candidate requires canonical "
            "reconciliation before retry"
        )


def _lock_info_is_exact(
    info: os.stat_result, *, expected_uid: int, expected_gid: int
) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == expected_uid
        and info.st_gid == expected_gid
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_nlink == 1
    )


def _acquire_day2_lock(
    path: Path = CANONICAL_DAY2_LOCK,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
    descriptor: int = 9,
    timeout_seconds: float = 30,
) -> int:
    """Return validated FD 9 holding the canonical host-wide day-2 lock."""
    try:
        inherited = os.fstat(descriptor)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            _fail(f"cannot inspect inherited day-2 lock: {exc}")
        inherited = None

    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        canonical = os.open(path, flags, 0o600)
    except OSError as exc:
        _fail(f"cannot open the canonical day-2 lock safely: {exc}")
    try:
        canonical_info = os.fstat(canonical)
        if not _lock_info_is_exact(
            canonical_info, expected_uid=expected_uid, expected_gid=expected_gid
        ):
            _fail("canonical day-2 lock ownership or mode is unsafe")
        if inherited is not None:
            if not _lock_info_is_exact(
                inherited, expected_uid=expected_uid, expected_gid=expected_gid
            ) or (
                inherited.st_dev,
                inherited.st_ino,
            ) != (canonical_info.st_dev, canonical_info.st_ino):
                _fail("inherited FD 9 is not the canonical day-2 lock")
        else:
            os.dup2(canonical, descriptor, inheritable=True)
    finally:
        if canonical != descriptor:
            os.close(canonical)

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                _fail("timed out waiting for the exclusive day-2 lock")
            time.sleep(0.1)
    os.set_inheritable(descriptor, True)
    return descriptor


def _prepare_receipt_directory(run_id: str) -> Path | None:
    if os.environ.get("OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT") != "1":
        return None
    # Production reaches this launcher as root. Non-root explicit invocations
    # remain available to exercise the downstream fail-closed contract in
    # portable tests, but can never create canonical host evidence.
    if os.geteuid() != 0:
        return None
    candidate = os.environ.get("OMEGA_MIGRATION_CANDIDATE_REF", "")
    version = os.environ.get("OMEGA_MIGRATION_RELEASE_VERSION", "")
    manifest_sha = os.environ.get("OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256", "")
    if _FULL_SHA.fullmatch(candidate) is None:
        _fail("cannot create a receipt for an invalid candidate ref")
    if version != "1.45.207-beta":
        _fail("cannot create a receipt for an invalid release version")
    if _SHA256.fullmatch(manifest_sha) is None:
        _fail("cannot create a receipt for an invalid release manifest hash")

    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        root = os.open("/", flags)
        descriptors.append(root)
        _verify_directory(root, mode=0o755, label="filesystem root")
        opt = _open_directory(root, "opt", mode=0o755, label="/opt")
        descriptors.append(opt)
        app = _open_directory(
            opt, "modecissions", mode=0o755, label="/opt/modecissions"
        )
        descriptors.append(app)
        shared = _open_directory(
            app, "shared", mode=0o755, label="canonical shared directory"
        )
        descriptors.append(shared)
        try:
            os.mkdir("operation-receipts", mode=0o700, dir_fd=shared)
            os.fsync(shared)
        except FileExistsError:
            pass
        receipts = _open_directory(
            shared,
            "operation-receipts",
            mode=0o700,
            label="operation receipt root",
        )
        descriptors.append(receipts)
        _assert_candidate_receipt_absent(receipts, candidate)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_name = f"migration-{candidate}-{timestamp}-{os.getpid()}"
        try:
            os.mkdir(run_name, mode=0o700, dir_fd=receipts)
        except FileExistsError:
            _fail("operation receipt directory already exists")
        os.fsync(receipts)
        run = _open_directory(
            receipts, run_name, mode=0o700, label="migration receipt directory"
        )
        descriptors.append(run)
        _write_receipt_intent(
            run,
            candidate=candidate,
            version=version,
            manifest_sha=manifest_sha,
            run_id=run_id,
        )
        return CANONICAL_RECEIPT_ROOT / run_name
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _terminate_process_group(process: subprocess.Popen[bytes], signum: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return


def _wait_after_termination(process: subprocess.Popen[bytes]) -> int:
    try:
        return process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process, signal.SIGKILL)
        return process.wait()


def _run_bounded(command: list[str], *, pass_fds: tuple[int, ...]) -> int:
    """Spawn only after forwarding handlers cover every termination signal."""
    received_signal: int | None = None
    process: subprocess.Popen[bytes] | None = None

    def forward_signal(signum: int, _frame: object) -> None:
        nonlocal received_signal
        if received_signal is None:
            received_signal = signum
        if process is not None:
            _terminate_process_group(process, signum)

    managed_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, managed_signals)
    try:
        previous_handlers = {
            signum: signal.signal(signum, forward_signal) for signum in managed_signals
        }
    finally:
        # The child must inherit the caller's original mask, not the temporary
        # block used to make handler installation atomic.
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    try:
        process = subprocess.Popen(
            command,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        # A signal delivered inside Popen was recorded while process was None.
        # Forward it before entering the wait loop so the new session cannot
        # outlive a terminated launcher.
        if received_signal is not None:
            _terminate_process_group(process, received_signal)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while True:
            try:
                result = process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if received_signal is not None:
                    _wait_after_termination(process)
                    return 128 + received_signal
                if time.monotonic() < deadline:
                    continue
                _terminate_process_group(process, signal.SIGTERM)
                _wait_after_termination(process)
                print(
                    "migration launcher: bounded execution timed out",
                    file=sys.stderr,
                )
                return 124
            if received_signal is not None:
                return 128 + received_signal
            return result
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _validate_success_receipt(
    root: Path,
    receipt_directory: Path,
    *,
    candidate: str,
    version: str,
    manifest_sha: str,
    run_id: str,
) -> str:
    guard = root / "scripts" / "migration_guard.py"
    environment = {
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8:strict",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }
    try:
        validation = subprocess.run(
            [
                sys.executable,
                "-I",
                str(guard),
                "validate-success-receipt",
                "--receipt-dir",
                str(receipt_directory),
                "--candidate-ref",
                candidate,
                "--release-version",
                version,
                "--release-manifest-sha256",
                manifest_sha,
                "--run-id",
                run_id,
            ],
            env=environment,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("successful migration receipt validation could not execute")
    if validation.returncode != 0:
        _fail("successful migration receipt did not validate")
    try:
        marker = validation.stdout.decode("utf-8", errors="strict").strip()
        parsed = json.loads(marker)
    except (UnicodeError, json.JSONDecodeError):
        _fail("successful migration receipt marker is malformed")
    if (
        not isinstance(parsed, dict)
        or parsed.get("status") != "PASS"
        or parsed.get("receipt_dir") != str(receipt_directory)
        or parsed.get("run_id") != run_id
        or "\n" in marker
        or "\r" in marker
    ):
        _fail("successful migration receipt marker differs")
    return marker


def main() -> int:
    if not sys.flags.isolated:
        _fail("launcher must run through its isolated shebang")
    if len(sys.argv) != 1:
        _fail("positional arguments are forbidden")
    launcher = Path(__file__).resolve(strict=True)
    root = launcher.parents[1]
    runner = root / "scripts" / "apply_db_migrations.sh"
    raw = _snapshot(runner)
    explicit_root = (
        os.environ.get("OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT") == "1"
        and os.geteuid() == 0
    )
    run_id = f"omega_migration_{os.getpid()}_{time.monotonic_ns()}"
    lock_descriptor = _acquire_day2_lock() if explicit_root else None
    receipt_directory = _prepare_receipt_directory(run_id)
    temporary = Path(tempfile.mkdtemp(prefix="omega-migration-launch.", dir="/tmp"))
    try:
        os.chmod(temporary, 0o700)
        private_runner = temporary / "apply_db_migrations.sh"
        _write_private(private_runner, raw)
        command = [
            "/usr/bin/env",
            "-i",
            *(
                f"{name}={value}"
                for name, value in sorted(
                    _closed_environment(root, receipt_directory, run_id).items()
                )
            ),
            "/bin/bash",
            "--noprofile",
            "--norc",
            str(private_runner),
        ]
        result = _run_bounded(
            command,
            pass_fds=(() if lock_descriptor is None else (lock_descriptor,)),
        )
        if result != 0 or receipt_directory is None:
            return result
        marker = _validate_success_receipt(
            root,
            receipt_directory,
            candidate=os.environ.get("OMEGA_MIGRATION_CANDIDATE_REF", ""),
            version=os.environ.get("OMEGA_MIGRATION_RELEASE_VERSION", ""),
            manifest_sha=os.environ.get("OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256", ""),
            run_id=run_id,
        )
        print(f"OMEGA_MIGRATION_RECEIPT\t{marker}", flush=True)
        return 0
    finally:
        shutil.rmtree(temporary)
        if lock_descriptor is not None:
            os.close(lock_descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
