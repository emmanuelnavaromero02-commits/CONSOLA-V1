#!/usr/bin/python3 -I
"""Create and consume one provenance-bound OpenTofu plan transaction."""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _bootstrap_controller() -> None:
    """Re-exec the committed controller graph from an owner-private snapshot."""
    marker = os.environ.get("OMEGA_SEALED_CONTROLLER_ROOT")
    running = Path(__file__).resolve(strict=True)
    if marker is not None:
        sealed = Path(marker).resolve(strict=True)
        try:
            running.relative_to(sealed)
        except ValueError as error:
            raise RuntimeError("sealed controller marker/path differs") from error
        info = sealed.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o500:
            raise RuntimeError("sealed controller root identity differs")
        return
    source_root = running.parents[2]

    def git_read(arguments: list[str], maximum: int = 64 * 1024 * 1024) -> bytes:
        result = subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", *arguments],
            cwd=source_root,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if (
            result.returncode != 0
            or len(result.stdout) > maximum
            or len(result.stderr) > 1024 * 1024
        ):
            raise RuntimeError("controller bootstrap Git query failed")
        return result.stdout

    if git_read(["status", "--porcelain=v1", "--untracked-files=no"]):
        raise RuntimeError("controller bootstrap requires a clean tracked tree")
    head = git_read(["rev-parse", "--verify", "HEAD"], 128).decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise RuntimeError("controller bootstrap HEAD is invalid")
    relative_running = running.relative_to(source_root).as_posix()
    committed_running = git_read(["show", f"{head}:{relative_running}"])
    descriptor = os.open(
        running,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        observed = os.read(descriptor, info.st_size + 1)
    finally:
        os.close(descriptor)
    if observed != committed_running:
        raise RuntimeError("running controller differs from committed HEAD")
    listing = git_read(
        ["ls-files", "-z", "scripts/gcp", "infra/terraform-gcp"],
        8 * 1024 * 1024,
    )
    members = sorted(item for item in listing.rstrip(b"\0").split(b"\0") if item)
    if not members or len(members) > 10_000:
        raise RuntimeError("controller bootstrap inventory differs")
    root = Path(tempfile.mkdtemp(prefix="omega-controller.", dir="/tmp"))
    root.chmod(0o700)
    total = 0
    directories = {root}
    for raw_path in members:
        path = raw_path.decode("utf-8", errors="strict")
        pure = Path(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise RuntimeError("controller bootstrap path is unsafe")
        payload = git_read(["show", f"{head}:{path}"])
        total += len(payload)
        if total > 512 * 1024 * 1024:
            raise RuntimeError("controller bootstrap exceeds its bound")
        destination = root / pure
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        directories.add(destination.parent)
        mode_raw = git_read(["ls-tree", head, "--", path], 4096)
        mode = 0o500 if mode_raw.startswith(b"100755 ") else 0o400
        output = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(output, view)
                if written <= 0:
                    raise RuntimeError("controller bootstrap copy made no progress")
                view = view[written:]
            os.fchmod(output, mode)
            os.fsync(output)
        finally:
            os.close(output)
    for directory in sorted(
        directories, key=lambda item: len(item.parts), reverse=True
    ):
        directory.chmod(0o500)
    environment = {
        "HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OMEGA_SEALED_CONTROLLER_ROOT": str(root),
        "OMEGA_SOURCE_ROOT": str(source_root),
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }
    os.execve(
        "/usr/bin/python3",
        [
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            str(root / relative_running),
            *sys.argv[1:],
        ],
        environment,
    )


if __name__ == "__main__":
    _bootstrap_controller()

CONTROLLER_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("OMEGA_SOURCE_ROOT", str(CONTROLLER_ROOT))).resolve()
MODULE = ROOT / "infra/terraform-gcp"
BACKEND_CONFIG = MODULE / "envs/staging/backend.hcl"
GIT = Path("/usr/bin/git")
MAX_FILE = 64 * 1024 * 1024
MAX_PLAN = 512 * 1024 * 1024
MAX_PLAN_AGE_SECONDS = 6 * 60 * 60
TOFU_VERSION = "1.11.6"
TOFU_HASHES = {
    (
        "darwin",
        "arm64",
    ): "7eaaddb595e670d713edba20bdf3e5d8252c58a1752cda967bad4455dbf35a6d",
    (
        "linux",
        "x86_64",
    ): "866def654873882203d50deac4a38901b4c94818b9201b3090b5c49210cf9509",
}
HELPERS = (
    "scripts/gcp/terraform_transaction.py",
    "scripts/gcp/terraform_plan_contract.py",
    "scripts/gcp/verify_terraform_plan.py",
    "scripts/gcp/generate_release_authority.py",
    "scripts/gcp/iam_revoke_transaction.py",
    "scripts/gcp/verify_edge_tls.py",
    "scripts/gcp/startup_metadata_transaction.py",
    "scripts/gcp/bootstrap-runtime.sh",
    "scripts/gcp/safe_io.py",
    "scripts/gcp/metadata-firewall.sh",
    "infra/terraform-gcp/templates/omega-operation-gate",
    "scripts/gcp/operation-watchdog.sh",
    "scripts/gcp/reboot-runtime.sh",
    "scripts/gcp/runtime_contract.py",
)
MANIFEST_KEYS = {
    "schema_version",
    "profile",
    "git_head",
    "controller_ref",
    "startup_script_sha256",
    "startup_script",
    "created_at",
    "tool",
    "plan_tool_copy",
    "plan",
    "plan_json_sha256",
    "tfvars",
    "backend_config",
    "backend_projection",
    "release_authority",
    "release_authority_source_path",
    "authority_live_verification",
    "tracked_config_sha256",
    "helpers",
    "operator",
    "github_config",
    "gcloud",
    "control_bundle",
    "provider_bundle",
}
EXECUTABLE_MEMBER = "opentofu"
CONTROL_BUNDLE_MEMBER = "control-bundle"
PROVIDER_BUNDLE_MEMBER = "provider-bundle"
GCLOUD_PYTHON = Path(
    "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
)
GCLOUD_SDK_TREE_SHA256 = (
    "7cdcefef539887a3be501f8b14e8f8f74a7e6252cfa867a11ae789a87ac23805"
)


def _load_verifier() -> Any:
    spec = importlib.util.spec_from_file_location(
        "omega_gcp_verify_terraform_plan",
        Path(__file__).with_name("verify_terraform_plan.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("plan verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()
SECRET_ADOPTION_TARGETS = tuple(sorted(verifier.SECRET_ADOPTION_ADDRESSES))


def _load_edge_contract() -> Any:
    spec = importlib.util.spec_from_file_location(
        "omega_gcp_verify_edge_tls",
        Path(__file__).with_name("verify_edge_tls.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("edge verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier.edge_contract = _load_edge_contract()


def _load_startup_transaction() -> Any:
    spec = importlib.util.spec_from_file_location(
        "omega_gcp_startup_metadata_transaction",
        Path(__file__).with_name("startup_metadata_transaction.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("startup transaction cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


startup_transaction = _load_startup_transaction()


class TransactionError(RuntimeError):
    pass


class CommandError(TransactionError):
    """A bounded subprocess failed without retaining or exposing its output."""

    def __init__(self, *, returncode: int | None = None, timed_out: bool = False):
        self.returncode = returncode
        self.timed_out = timed_out
        outcome = "timeout" if timed_out else f"status {returncode}"
        super().__init__(f"bounded command failed ({outcome}); output suppressed")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _clean_env(config: Path, data_dir: Path) -> dict[str, str]:
    return {
        "HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "CLOUDSDK_CONFIG": str(config),
        "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
        "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1",
        "TF_CLI_CONFIG_FILE": "/dev/null",
        "TF_DATA_DIR": str(data_dir),
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
        "CHECKPOINT_DISABLE": "1",
    }


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    pass_fds: tuple[int, ...] = (),
    maximum: int = MAX_FILE,
) -> bytes:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            pass_fds=pass_fds,
        )
    except subprocess.TimeoutExpired as error:
        raise CommandError(timed_out=True) from error
    if len(result.stdout) > maximum or len(result.stderr) > MAX_FILE:
        raise TransactionError("bounded command output exceeded its limit")
    if result.returncode != 0:
        raise CommandError(returncode=result.returncode)
    return result.stdout


def _safe_path(
    path: Path, *, maximum: int, executable: bool = False
) -> tuple[int, os.stat_result, bytes]:
    if not path.is_absolute():
        raise TransactionError("transaction input paths must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid
            not in ({0, os.geteuid()} if executable else {os.geteuid()})
            or before.st_nlink != 1
            or mode & 0o022
            or (executable and not mode & 0o100)
            or not 1 <= before.st_size <= maximum
        ):
            raise TransactionError(f"unsafe transaction input: {path.name}")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(raw) != before.st_size or before_identity != after_identity:
            raise TransactionError(
                f"transaction input changed during read: {path.name}"
            )
        return descriptor, before, raw
    except Exception:
        os.close(descriptor)
        raise


def _open_transaction(
    path: Path, *, expected_modes: set[int]
) -> tuple[int, os.stat_result]:
    """Open and exclusively lock the exact transaction directory inode."""
    if not path.is_absolute() or path.is_symlink():
        raise TransactionError("transaction directory path is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in expected_modes
        ):
            raise TransactionError("transaction directory identity/mode differs")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _revalidate_transaction_path(path, info)
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _revalidate_transaction_path(path: Path, expected: os.stat_result) -> None:
    """Fail if the pathname no longer selects the held transaction directory."""
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        raise TransactionError("transaction directory pathname was substituted")


def _create_private_directory(
    transaction_fd: int,
    transaction_path: Path,
    transaction_info: os.stat_result,
    name: str,
) -> tuple[Path, int, os.stat_result]:
    if (
        name not in {"plan-data", CONTROL_BUNDLE_MEMBER}
        and re.fullmatch(r"apply-data-[0-9a-f]{16}", name) is None
    ):
        raise TransactionError("private transaction directory name is invalid")
    os.mkdir(name, 0o700, dir_fd=transaction_fd)
    os.fsync(transaction_fd)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=transaction_fd,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise TransactionError("private transaction directory identity differs")
        _revalidate_transaction_path(transaction_path, transaction_info)
        live = os.stat(transaction_path / name, follow_symlinks=False)
        if (live.st_dev, live.st_ino) != (info.st_dev, info.st_ino):
            raise TransactionError("private transaction directory pathname differs")
        return transaction_path / name, descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _revalidate_private_directory(path: Path, expected: os.stat_result) -> None:
    info = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise TransactionError("private transaction directory was substituted")


def _safe_member(
    directory_fd: int,
    name: str,
    *,
    maximum: int,
    executable: bool = False,
) -> tuple[int, os.stat_result, bytes]:
    """Read a simple transaction member relative to the held directory FD."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
        raise TransactionError("transaction member name is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or mode & 0o022
            or (executable and not mode & 0o100)
            or not 1 <= before.st_size <= maximum
        ):
            raise TransactionError(f"unsafe transaction member: {name}")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if len(raw) != before.st_size or identity(before) != identity(after):
            raise TransactionError(f"transaction member changed during read: {name}")
        return descriptor, before, raw
    except Exception:
        os.close(descriptor)
        raise


def _identity(info: os.stat_result, raw: bytes) -> dict[str, Any]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "sha256": _sha(raw),
    }


def _validate_backend(raw: bytes) -> dict[str, str]:
    expected = (
        b'bucket = "omega-gcp-tfstate-project-dd5ba7fa-374c-4554-ae6"\n'
        b'prefix = "infra/terraform-gcp/staging"\n'
    )
    if raw != expected:
        raise TransactionError(
            "backend config differs from the canonical state backend"
        )
    return {
        "bucket": "omega-gcp-tfstate-project-dd5ba7fa-374c-4554-ae6",
        "prefix": "infra/terraform-gcp/staging",
    }


def _git(args: list[str], *, maximum: int = MAX_FILE) -> bytes:
    return _run(
        [str(GIT), "-c", "core.hooksPath=/dev/null", *args],
        cwd=ROOT,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        timeout=30,
        maximum=maximum,
    )


def _source_identity() -> tuple[str, str, dict[str, str]]:
    status = _git(["status", "--porcelain=v1", "--untracked-files=all"])
    if status:
        raise TransactionError("Git worktree is not exactly clean")
    head = _git(["rev-parse", "--verify", "HEAD"]).decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise TransactionError("Git HEAD is not one full SHA")
    tracked = _git(["ls-files", "-z", "infra/terraform-gcp"])
    paths = tracked.rstrip(b"\0").split(b"\0") if tracked else []
    config_hash = hashlib.sha256()
    for raw_path in paths:
        path = raw_path.decode("utf-8", errors="strict")
        blob = _git(["show", f"{head}:{path}"])
        config_hash.update(path.encode() + b"\0" + len(blob).to_bytes(8, "big") + blob)
    helpers: dict[str, str] = {}
    for path in HELPERS:
        committed = _git(["show", f"{head}:{path}"])
        working = (ROOT / path).read_bytes()
        if working != committed:
            raise TransactionError(f"controller helper differs from Git HEAD: {path}")
        helpers[path] = _sha(committed)
    return head, config_hash.hexdigest(), helpers


def _seal_control_bundle(
    transaction_fd: int,
    transaction: Path,
    transaction_info: os.stat_result,
    *,
    head: str,
    config_sha: str,
    helpers: dict[str, str],
    gh_config: Path,
    gcloud_config: Path,
) -> tuple[Path, dict[str, Any]]:
    bundle, bundle_fd, bundle_info = _create_private_directory(
        transaction_fd,
        transaction,
        transaction_info,
        CONTROL_BUNDLE_MEMBER,
    )
    try:
        files = set(HELPERS)
        tracked = _git(["ls-files", "-z", "infra/terraform-gcp"])
        files.update(
            raw.decode("utf-8", errors="strict")
            for raw in tracked.rstrip(b"\0").split(b"\0")
            if raw
        )
        if not files or any(
            path.startswith("/") or ".." in Path(path).parts for path in files
        ):
            raise TransactionError("control bundle inventory is unsafe")
        for path in sorted(files):
            raw = _git(["show", f"{head}:{path}"])
            destination = bundle / path
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            mode_raw = _git(["ls-tree", head, "--", path], maximum=4096)
            mode = 0o500 if mode_raw.startswith(b"100755 ") else 0o400
            descriptor = os.open(
                destination,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise TransactionError("control bundle write made no progress")
                    view = view[written:]
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        runtime_root = bundle / "runtime"
        runtime_root.mkdir(mode=0o700)
        contract = verifier.plan_contract.release_authority_contract
        private_sdk = runtime_root / "google-cloud-sdk"
        sdk = contract._runtime_tree_snapshot(
            Path("/opt/homebrew/share/google-cloud-sdk"),
            private_sdk,
            expected_sha256=GCLOUD_SDK_TREE_SHA256,
            omit_python_bytecode=True,
        )
        python_root = runtime_root / "python-runtime"
        private_python = python_root / "Library/Frameworks/Python.framework"
        private_python.parent.mkdir(parents=True, mode=0o700)
        python_runtime = contract._runtime_tree_snapshot(
            contract.PYTHON_RUNTIME_ROOT,
            private_python,
            expected_sha256=contract.PYTHON_RUNTIME_TREE_SHA256,
            allow_writable_source=True,
        )
        gh_source, gh_identity, gh_raw = contract._verified_tool(
            contract.GH, "GitHub CLI"
        )
        del gh_source
        contract._write_private_file(runtime_root / "gh", gh_raw, 0o500)
        # Credentials are never retained in the transaction/evidence bundle.
        # Validate the operator inputs now; each later phase creates a fresh
        # same-FD private projection for execution.
        with tempfile.TemporaryDirectory(
            prefix="omega-config-seal.", dir="/tmp"
        ) as temp:
            temp_root = Path(temp)
            gcloud_projection = contract._private_gcloud_config(
                gcloud_config, temp_root / "gcloud-config"
            )
            gh_projection = contract._private_gh_config(
                gh_config, temp_root / "gh-config"
            )
        for directory in sorted(
            {path.parent for path in bundle.rglob("*") if path.is_dir()},
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            directory.chmod(0o500)
        os.fchmod(bundle_fd, 0o500)
        os.fsync(bundle_fd)
        try:
            inventory = verifier.plan_contract.release_authority_contract._runtime_tree_snapshot(
                bundle, None
            )
        except (
            verifier.plan_contract.release_authority_contract.AuthorityError
        ) as error:
            raise TransactionError(
                "sealed control bundle cannot be attested"
            ) from error
        return bundle, {
            "path": str(bundle),
            "device": bundle_info.st_dev,
            "inode": bundle_info.st_ino,
            "mode": 0o500,
            "git_head": head,
            "tracked_config_sha256": config_sha,
            "helpers": helpers,
            "inventory_sha256": inventory["sha256"],
            "files": inventory["files"],
            "sdk": sdk,
            "python": python_runtime,
            "gh": gh_identity,
            "gcloud_projection": gcloud_projection,
            "gh_projection": gh_projection,
        }
    finally:
        os.close(bundle_fd)


def _control_bundle(manifest: dict[str, Any]) -> Path:
    value = manifest.get("control_bundle")
    if not isinstance(value, dict) or set(value) != {
        "path",
        "device",
        "inode",
        "mode",
        "git_head",
        "tracked_config_sha256",
        "helpers",
        "inventory_sha256",
        "files",
        "sdk",
        "python",
        "gh",
        "gcloud_projection",
        "gh_projection",
    }:
        raise TransactionError("sealed control bundle shape differs")
    path = Path(value["path"])
    info = path.stat(follow_symlinks=False)
    if (
        path.name != CONTROL_BUNDLE_MEMBER
        or not path.is_absolute()
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (value["device"], value["inode"])
        or stat.S_IMODE(info.st_mode) != 0o500
        or value["mode"] != 0o500
        or value["git_head"] != manifest["git_head"]
        or value["tracked_config_sha256"] != manifest["tracked_config_sha256"]
        or value["helpers"] != manifest["helpers"]
        or type(value["files"]) is not int
        or value["files"] < len(HELPERS)
        or re.fullmatch(r"[0-9a-f]{64}", str(value["inventory_sha256"])) is None
        or value["sdk"].get("sha256") != GCLOUD_SDK_TREE_SHA256
        or value["python"].get("sha256")
        != verifier.plan_contract.release_authority_contract.PYTHON_RUNTIME_TREE_SHA256
        or value["gcloud_projection"]
        != {
            "account": verifier.plan_contract.release_authority_contract.OPERATOR_ACCOUNT,
            "project": verifier.plan_contract.PROJECT,
        }
        or value["gh_projection"].get("host") != "github.com"
    ):
        raise TransactionError("sealed control bundle identity differs")
    try:
        observed = (
            verifier.plan_contract.release_authority_contract._runtime_tree_snapshot(
                path, None
            )
        )
    except verifier.plan_contract.release_authority_contract.AuthorityError as error:
        raise TransactionError("sealed control bundle inventory differs") from error
    if (
        observed["files"] != value["files"]
        or observed["sha256"] != value["inventory_sha256"]
    ):
        raise TransactionError("sealed control bundle inventory differs")
    return path


def _bundle_module(manifest: dict[str, Any]) -> Path:
    module = _control_bundle(manifest) / "infra/terraform-gcp"
    info = module.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o500:
        raise TransactionError("sealed Terraform module identity differs")
    return module


def _seal_provider_bundle(
    transaction: Path, source: Path
) -> tuple[Path, dict[str, Any]]:
    destination = transaction / PROVIDER_BUNDLE_MEMBER
    contract = verifier.plan_contract.release_authority_contract
    try:
        inventory = contract._runtime_tree_snapshot(source, destination)
    except contract.AuthorityError as error:
        raise TransactionError("provider bundle cannot be sealed") from error
    info = destination.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o500
        or type(inventory["files"]) is not int
        or inventory["files"] < 1
    ):
        raise TransactionError("provider bundle identity differs")
    return destination, {
        "path": str(destination),
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": 0o500,
        "inventory_sha256": inventory["sha256"],
        "files": inventory["files"],
        "size": inventory["size"],
    }


def _provider_bundle(manifest: dict[str, Any]) -> Path:
    value = manifest.get("provider_bundle")
    if not isinstance(value, dict) or set(value) != {
        "path",
        "device",
        "inode",
        "mode",
        "inventory_sha256",
        "files",
        "size",
    }:
        raise TransactionError("provider bundle shape differs")
    path = Path(value["path"])
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as error:
        raise TransactionError("provider bundle is unavailable") from error
    if (
        not path.is_absolute()
        or path.name != PROVIDER_BUNDLE_MEMBER
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (value["device"], value["inode"])
        or stat.S_IMODE(info.st_mode) != 0o500
        or value["mode"] != 0o500
        or type(value["files"]) is not int
        or value["files"] < 1
        or type(value["size"]) is not int
        or value["size"] < 1
        or re.fullmatch(r"[0-9a-f]{64}", str(value["inventory_sha256"])) is None
    ):
        raise TransactionError("provider bundle identity differs")
    contract = verifier.plan_contract.release_authority_contract
    try:
        observed = contract._runtime_tree_snapshot(
            path, None, expected_sha256=value["inventory_sha256"]
        )
    except contract.AuthorityError as error:
        raise TransactionError("provider bundle inventory differs") from error
    if observed != {
        "sha256": value["inventory_sha256"],
        "files": value["files"],
        "size": value["size"],
    }:
        raise TransactionError("provider bundle inventory differs")
    return path


def _attest_provider_installation(data_dir: Path, identity: dict[str, Any]) -> None:
    contract = verifier.plan_contract.release_authority_contract
    try:
        observed = contract._runtime_tree_snapshot(
            data_dir / "providers",
            None,
            expected_sha256=identity["inventory_sha256"],
        )
    except (OSError, contract.AuthorityError) as error:
        raise TransactionError("installed provider closure differs") from error
    if observed["files"] != identity["files"] or observed["size"] != identity["size"]:
        raise TransactionError("installed provider closure differs")


def _bundle_runtime(manifest: dict[str, Any]) -> dict[str, Path]:
    root = _control_bundle(manifest) / "runtime"
    framework = root / "python-runtime/Library/Frameworks/Python.framework"
    values = {
        "gcloud": root / "google-cloud-sdk/lib/gcloud.py",
        "python": framework / "Versions/3.14/bin/python3.14",
        "python_framework": framework,
        "gh": root / "gh",
    }
    for name, path in values.items():
        if not path.exists() or path.is_symlink():
            raise TransactionError(f"sealed runtime member is absent: {name}")
    return values


@contextlib.contextmanager
def _runtime_session(
    manifest: dict[str, Any], gh_config: Path, gcloud_config: Path
) -> Any:
    """Project credentials into an ephemeral exact config, never evidence."""
    runtime = _bundle_runtime(manifest)
    contract = verifier.plan_contract.release_authority_contract
    with tempfile.TemporaryDirectory(
        prefix="omega-control-session.", dir="/tmp"
    ) as temp:
        root = Path(temp)
        root.chmod(0o700)
        private_gcloud = root / "gcloud-config"
        private_gh_root = root / "gh-config"
        gcloud_projection = contract._private_gcloud_config(
            gcloud_config, private_gcloud
        )
        gh_projection = contract._private_gh_config(gh_config, private_gh_root)
        if (
            gcloud_projection != manifest["control_bundle"]["gcloud_projection"]
            or gh_projection != manifest["control_bundle"]["gh_projection"]
        ):
            raise TransactionError("operator config projection differs")
        contract._private_python_canary(runtime["python"], runtime["python_framework"])
        contract._private_gcloud_canary(
            runtime["gcloud"],
            private_gcloud,
            runtime["python"],
            runtime["python_framework"],
        )
        yield runtime | {
            "gcloud_config": private_gcloud,
            "gh_config": private_gh_root / "home/.config/gh",
        }


def _runtime_env(
    config: Path,
    data_dir: Path,
    python: Path,
    python_framework: Path,
) -> dict[str, str]:
    value = _clean_env(config, data_dir)
    value.update(
        verifier.plan_contract.release_authority_contract._python_runtime_environment(
            python, python_framework
        )
    )
    return value


def _load_bundle_module(bundle: Path, relative: str, name: str) -> Any:
    path = bundle / relative
    descriptor, _info, raw = _safe_path(path, maximum=MAX_FILE)
    os.close(descriptor)
    namespace: dict[str, Any] = {
        "__name__": name,
        "__file__": str(path),
        "__package__": None,
        "__builtins__": __builtins__,
    }
    exec(compile(raw, str(path), "exec"), namespace)  # noqa: S102
    return argparse.Namespace(**namespace)


def _sealed_contracts(manifest: dict[str, Any]) -> tuple[Any, Any, Any]:
    bundle = _control_bundle(manifest)
    sealed_verifier = _load_bundle_module(
        bundle,
        "scripts/gcp/verify_terraform_plan.py",
        "omega_sealed_verify_terraform_plan",
    )
    sealed_edge = _load_bundle_module(
        bundle,
        "scripts/gcp/verify_edge_tls.py",
        "omega_sealed_verify_edge_tls",
    )
    sealed_startup = _load_bundle_module(
        bundle,
        "scripts/gcp/startup_metadata_transaction.py",
        "omega_sealed_startup_metadata_transaction",
    )
    return sealed_verifier, sealed_edge, sealed_startup


def _activate_contracts(bundle: Path) -> None:
    global verifier, startup_transaction
    sealed_verifier = _load_bundle_module(
        bundle,
        "scripts/gcp/verify_terraform_plan.py",
        "omega_sealed_verify_terraform_plan",
    )
    sealed_verifier.edge_contract = _load_bundle_module(
        bundle,
        "scripts/gcp/verify_edge_tls.py",
        "omega_sealed_verify_edge_tls",
    )
    startup_transaction = _load_bundle_module(
        bundle,
        "scripts/gcp/startup_metadata_transaction.py",
        "omega_sealed_startup_metadata_transaction",
    )
    verifier = sealed_verifier


def _tool(
    path: Path,
    config: Path,
    data_dir: Path,
    data_dir_fd: int,
    module: Path = MODULE,
) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, str]]:
    descriptor, info, raw = _safe_path(
        path.resolve(strict=True), maximum=MAX_PLAN, executable=True
    )
    platform = (sys.platform, os.uname().machine)
    expected = TOFU_HASHES.get(platform)
    if expected is None or _sha(raw) != expected:
        os.close(descriptor)
        raise TransactionError(
            "OpenTofu executable is not the reviewed official 1.11.6 binary"
        )
    environment = _clean_env(config, data_dir)
    executable, private_identity = _private_executable(
        descriptor, data_dir, data_dir_fd
    )
    version = json.loads(
        _run(
            [str(executable), "version", "-json"],
            cwd=module,
            env=environment,
            timeout=15,
            maximum=4096,
        )
    )
    if version.get("terraform_version") != TOFU_VERSION:
        os.close(descriptor)
        raise TransactionError("OpenTofu version differs")
    return (
        descriptor,
        _identity(info, raw) | {"path": str(path.resolve())},
        private_identity | {"path": str(executable)},
        environment,
    )


def _private_executable(
    source_fd: int, data_dir: Path, data_dir_fd: int
) -> tuple[Path, dict[str, Any]]:
    """Execute only a verified byte-for-byte private copy of the held binary FD.

    Darwin has neither fexecve(2) nor an executable /dev/fd entry.  The private
    directory is owner-only and already held by the transaction; copying from
    the verified descriptor removes the pathname revalidate-to-exec window.
    """
    destination = data_dir / EXECUTABLE_MEMBER
    descriptor = os.open(
        EXECUTABLE_MEMBER,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o500,
        dir_fd=data_dir_fd,
    )
    try:
        os.fchmod(descriptor, 0o500)
        os.lseek(source_fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            pending = memoryview(chunk)
            while pending:
                written = os.write(descriptor, pending)
                if written <= 0:
                    raise TransactionError("OpenTofu private copy made no progress")
                pending = pending[written:]
        os.fsync(descriptor)
        copied = os.fstat(descriptor)
        source = os.fstat(source_fd)
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if copied.st_size != source.st_size or digest.hexdigest() != TOFU_HASHES.get(
            (sys.platform, os.uname().machine)
        ):
            raise TransactionError("OpenTofu private executable copy differs")
    finally:
        os.close(descriptor)
    os.fsync(data_dir_fd)
    identity = {
        "device": copied.st_dev,
        "inode": copied.st_ino,
        "size": copied.st_size,
        "sha256": digest.hexdigest(),
    }
    return destination, identity


def _operator(
    gcloud: Path,
    config: Path,
    account: str,
    environment: dict[str, str],
    *,
    python: Path = GCLOUD_PYTHON,
) -> dict[str, Any]:
    if not config.is_absolute() or not config.is_dir() or config.is_symlink():
        raise TransactionError("gcloud config directory is unsafe")
    info = config.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise TransactionError("gcloud config directory ownership/mode differs")
    active = (
        _run(
            [
                str(python),
                "-I",
                "-S",
                "-B",
                str(gcloud),
                "auth",
                "list",
                "--filter=status:ACTIVE",
                "--format=value(account)",
            ],
            cwd=ROOT,
            env=environment,
            timeout=30,
            maximum=4096,
        )
        .decode()
        .splitlines()
    )
    project = (
        _run(
            [
                str(python),
                "-I",
                "-S",
                "-B",
                str(gcloud),
                "config",
                "get-value",
                "project",
            ],
            cwd=ROOT,
            env=environment,
            timeout=30,
            maximum=4096,
        )
        .decode()
        .strip()
    )
    if active != [account] or project != verifier.plan_contract.PROJECT:
        raise TransactionError("active gcloud operator/project differs")
    return {
        "account": account,
        "project": project,
    }


def _config_identity(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink():
        raise TransactionError(f"{label} config directory is unsafe")
    try:
        info = path.lstat()
    except OSError as error:
        raise TransactionError(f"{label} config directory is unavailable") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise TransactionError(f"{label} config directory ownership/mode differs")
    return {
        "path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def _verify_authority_live(
    authority: dict[str, Any],
    gh_config: Path,
    gcloud_config: Path,
    *,
    gh: Path | None = None,
    gcloud: Path | None = None,
    python: Path | None = None,
    python_framework: Path | None = None,
) -> dict[str, str]:
    contract = verifier.plan_contract.release_authority_contract
    try:
        namespace = argparse.Namespace(
            source_ref=authority["source_sha"],
            controller_ref=authority["controller_ref"],
            gh_config=gh_config,
            gcloud_config=gcloud_config,
        )
        runtime = None
        if (
            gh is not None
            and gcloud is not None
            and python is not None
            and python_framework is not None
        ):
            runtime = {
                "gh": gh,
                "gh_config": gh_config,
                "gh_identity": authority["provenance"]["tools"]["gh"],
                "gcloud": gcloud,
                "gcloud_config": gcloud_config,
                "gcloud_identity": authority["provenance"]["tools"]["gcloud"],
                "gcloud_sdk_identity": {
                    "sha256": GCLOUD_SDK_TREE_SHA256,
                },
                "python": python,
                "python_framework": python_framework,
                "python_identity": {
                    "sha256": contract.PYTHON_RUNTIME_TREE_SHA256,
                },
            }
        rebuilt = contract._build(namespace, runtime=runtime)
    except contract.AuthorityError as error:
        raise TransactionError("live release authority verification failed") from error
    if rebuilt != authority:
        raise TransactionError("release authority differs from current exact evidence")
    return {
        "authority_sha256": _sha(contract._canonical(authority)),
        "source_sha": authority["source_sha"],
        "controller_ref": authority["controller_ref"],
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def _require_fresh_manifest(manifest: dict[str, Any]) -> None:
    try:
        created = datetime.fromisoformat(str(manifest["created_at"]))
    except (TypeError, ValueError):
        raise TransactionError("transaction creation time is invalid") from None
    now = datetime.now(timezone.utc)
    if (
        created.tzinfo is None
        or created.utcoffset() != timezone.utc.utcoffset(created)
        or (now - created).total_seconds() < 0
        or (now - created).total_seconds() > MAX_PLAN_AGE_SECONDS
    ):
        raise TransactionError("sealed plan is outside the six-hour freshness window")


def _authority_verification_matches(
    value: Any, authority: dict[str, Any], authority_raw: bytes
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "authority_sha256",
        "source_sha",
        "controller_ref",
        "verified_at",
    }:
        return False
    try:
        verified = datetime.fromisoformat(str(value["verified_at"]))
    except (TypeError, ValueError):
        return False
    return (
        value["authority_sha256"] == _sha(authority_raw)
        and value["source_sha"] == authority["source_sha"]
        and value["controller_ref"] == authority["controller_ref"]
        and verified.tzinfo is not None
        and verified.utcoffset() == timezone.utc.utcoffset(verified)
    )


def _gcloud_identity() -> tuple[int, dict[str, Any]]:
    resolved = Path(
        "/opt/homebrew/share/google-cloud-sdk/lib/gcloud.py"
        if sys.platform == "darwin"
        else "/usr/lib/google-cloud-sdk/lib/gcloud.py"
    ).resolve(strict=True)
    descriptor, info, raw = _safe_path(resolved, maximum=MAX_PLAN)
    sdk_root = resolved.parent.parent
    try:
        sdk = verifier.plan_contract.release_authority_contract._runtime_tree_snapshot(
            sdk_root,
            None,
            expected_sha256=GCLOUD_SDK_TREE_SHA256,
            omit_python_bytecode=True,
        )
    except verifier.plan_contract.release_authority_contract.AuthorityError as error:
        os.close(descriptor)
        raise TransactionError("gcloud SDK tree identity differs") from error
    return descriptor, _identity(info, raw) | {"path": str(resolved), "sdk": sdk}


def _private_tool(identity: dict[str, Any]) -> Path:
    path = Path(identity.get("path", ""))
    descriptor, info, raw = _safe_path(path, maximum=MAX_PLAN, executable=True)
    os.close(descriptor)
    expected = {key: value for key, value in identity.items() if key != "path"}
    if _identity(info, raw) != expected or stat.S_IMODE(info.st_mode) != 0o500:
        raise TransactionError("private OpenTofu executable identity differs")
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise TransactionError("private OpenTofu directory identity differs")
    return path


def _revalidate_gcloud(identity: dict[str, Any]) -> Path:
    path = Path(identity.get("path", ""))
    descriptor, info, raw = _safe_path(path, maximum=MAX_PLAN)
    os.close(descriptor)
    sdk = verifier.plan_contract.release_authority_contract._runtime_tree_snapshot(
        path.parent.parent,
        None,
        expected_sha256=GCLOUD_SDK_TREE_SHA256,
        omit_python_bytecode=True,
    )
    if identity != _identity(info, raw) | {"path": str(path), "sdk": sdk}:
        raise TransactionError(
            "gcloud pathname no longer resolves to sealed inode/hash"
        )
    return path


def _edge(
    mode: str,
    account: str,
    config: Path,
    environment: dict[str, str],
    gcloud: Path,
) -> None:
    # In-process execution avoids passing an already-built CLOUDSDK_* mapping
    # into a helper whose own boundary intentionally rejects all ambient cloud
    # overrides before constructing its exact environment.
    try:
        verifier.plan_contract.release_authority_contract._config_dir(config, "gcloud")
        edge_args = [
            "--project-id",
            verifier.plan_contract.PROJECT,
            "--project-number",
            verifier.plan_contract.PROJECT_NUMBER,
            "--environment",
            "staging",
            "--expected-ip",
            "34.144.248.147",
            "--legacy-console-ip",
            "136.68.67.95",
            "--legacy-workspace-ip",
            "8.233.29.138",
            "--edge-mode",
            mode,
            "--expected-account",
            account,
            "--gcloud-config",
            str(config),
        ]
        runtime_keys = {
            "CLOUDSDK_CONFIG",
            "CLOUDSDK_CORE_DISABLE_FILE_LOGGING",
            "CLOUDSDK_CORE_DISABLE_PROMPTS",
            "CLOUDSDK_PYTHON",
            "CLOUDSDK_PYTHON_SITEPACKAGES",
            "DYLD_FRAMEWORK_PATH",
            "DYLD_LIBRARY_PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "OMEGA_SEALED_PYTHON",
            "OMEGA_SEALED_PYTHON_FRAMEWORK",
            "PATH",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONNOUSERSITE",
            "PYTHONSAFEPATH",
        }
        sealed_environment = {
            key: value for key, value in environment.items() if key in runtime_keys
        }
        python = sealed_environment.get("OMEGA_SEALED_PYTHON")
        if not python:
            raise TransactionError("sealed Python runtime is absent")
        with _without_ambient_cloud_overrides():
            if (
                verifier.edge_contract.main(
                    edge_args,
                    sealed_gcloud_command=[
                        python,
                        "-I",
                        "-S",
                        "-B",
                        str(gcloud),
                    ],
                    sealed_runtime_environment=sealed_environment,
                )
                != 0
            ):
                raise TransactionError("edge verifier failed")
    except verifier.edge_contract.GateError as error:
        raise TransactionError("edge verifier failed") from error


@contextlib.contextmanager
def _without_ambient_cloud_overrides() -> Any:
    removed = {
        key: value
        for key, value in os.environ.items()
        if verifier.edge_contract.FORBIDDEN_ENVIRONMENT.search(key)
    }
    try:
        for key in removed:
            os.environ.pop(key, None)
        yield
    finally:
        os.environ.update(removed)


def _show(
    tofu: Path, plan_fd: int, environment: dict[str, str], module: Path = MODULE
) -> bytes:
    os.lseek(plan_fd, 0, os.SEEK_SET)
    return _run(
        [
            str(tofu),
            "-chdir=" + str(module),
            "show",
            "-json",
            f"/dev/fd/{plan_fd}",
        ],
        cwd=ROOT,
        env=environment,
        timeout=120,
        pass_fds=(plan_fd,),
    )


def _terraform_state_identity(
    tofu: Path, environment: dict[str, str], module: Path = MODULE
) -> dict[str, Any]:
    raw = _run(
        [str(tofu), "-chdir=" + str(module), "state", "pull"],
        cwd=ROOT,
        env=environment,
        timeout=120,
        maximum=MAX_PLAN,
    )
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=verifier._no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError, verifier.PlanError) as error:
        raise TransactionError("Terraform state identity is malformed") from error
    serial = value.get("serial") if isinstance(value, dict) else None
    lineage = value.get("lineage") if isinstance(value, dict) else None
    if (
        type(serial) is not int
        or serial < 0
        or type(lineage) is not str
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            lineage,
        )
        is None
    ):
        raise TransactionError("Terraform state lineage/serial is invalid")
    return {
        "lineage": lineage,
        "serial": serial,
        "sha256": _sha(raw),
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }


def _startup_sha(payload: dict[str, Any]) -> str:
    planned = payload.get("planned_values")
    outputs = planned.get("outputs") if isinstance(planned, dict) else None
    entry = outputs.get("startup_script_sha256") if isinstance(outputs, dict) else None
    value = entry.get("value") if isinstance(entry, dict) else None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise TransactionError("planned startup script hash is absent")
    return value


def _startup_bytes(payload: dict[str, Any]) -> bytes:
    planned = payload.get("planned_values")
    outputs = planned.get("outputs") if isinstance(planned, dict) else None
    entry = outputs.get("startup_script_base64") if isinstance(outputs, dict) else None
    encoded = entry.get("value") if isinstance(entry, dict) else None
    if not isinstance(encoded, str) or entry.get("sensitive") is not True:
        raise TransactionError(
            "planned startup script bytes are absent or not sensitive"
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise TransactionError("planned startup script bytes are malformed") from error
    if (
        not 1 <= len(raw) <= 256 * 1024
        or not raw.startswith(b"#!/bin/bash -p\n")
        or not raw.endswith(b"\n")
        or _sha(raw) != _startup_sha(payload)
    ):
        raise TransactionError("planned startup script byte identity differs")
    return raw


def _plan_profile_args(profile: str) -> list[str]:
    if profile == "foundation":
        return []
    if profile == "iam-revoke":
        return ["-var=revoke_project_secret_accessor=true"]
    if profile == "secret-adoption":
        return [f"-target={address}" for address in SECRET_ADOPTION_TARGETS]
    raise TransactionError("Terraform transaction profile is unsupported")


def _profile_uses_runtime_transition(profile: str) -> bool:
    if profile not in {"foundation", "iam-revoke", "secret-adoption"}:
        raise TransactionError("Terraform transaction profile is unsupported")
    return profile == "foundation"


def _write_member(
    directory_fd: int, name: str, payload: bytes, mode: int
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
        raise TransactionError("transaction member name is invalid")
    descriptor = os.open(
        name,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=directory_fd,
    )
    try:
        os.fchmod(descriptor, mode)
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise TransactionError("durable transaction write made no progress")
            pending = pending[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = bytearray()
        while len(readback) < len(payload):
            chunk = os.read(descriptor, len(payload) - len(readback))
            if not chunk:
                break
            readback.extend(chunk)
        after = os.fstat(descriptor)
        if bytes(readback) != payload or (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise TransactionError("durable transaction write read-back differs")
        result = _identity(info, payload)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)
    return result


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic_candidate(directory_fd: int, name: str, expected: bytes) -> os.stat_result:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink not in {1, 2}
            or before.st_size != len(expected)
        ):
            raise TransactionError("atomic transaction state candidate is unsafe")
        raw = os.read(descriptor, len(expected) + 1)
        after = os.fstat(descriptor)
        if raw != expected or (
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
            raise TransactionError("atomic transaction state candidate differs")
        return after
    finally:
        os.close(descriptor)


def _write_state_member(
    directory_fd: int, name: str, value: dict[str, Any]
) -> tuple[bytes, dict[str, Any]]:
    """Publish one append-only state member without exposing partial bytes."""
    payload = _canonical_json(value)
    temporary = f".{name}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=directory_fd,
        )
    except FileExistsError:
        temporary_info = _atomic_candidate(directory_fd, temporary, payload)
    else:
        try:
            os.fchmod(descriptor, 0o400)
            pending = memoryview(payload)
            while pending:
                written = os.write(descriptor, pending)
                if written <= 0:
                    raise TransactionError(
                        "durable transaction state write made no progress"
                    )
                pending = pending[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary_info = _atomic_candidate(directory_fd, temporary, payload)
    if _member_exists(directory_fd, name):
        published_info = _atomic_candidate(directory_fd, name, payload)
        if (published_info.st_dev, published_info.st_ino) != (
            temporary_info.st_dev,
            temporary_info.st_ino,
        ):
            raise TransactionError(f"existing transaction state conflicts: {name}")
    else:
        os.link(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    os.unlink(temporary, dir_fd=directory_fd)
    os.fsync(directory_fd)
    raw, written = _read_json_member(directory_fd, name, keys=set(value))
    if raw != payload or written != value:
        raise TransactionError(f"transaction state read-back differs: {name}")
    descriptor, info, raw = _safe_member(directory_fd, name, maximum=MAX_FILE)
    os.close(descriptor)
    return raw, _identity(info, raw)


def _member_exists(directory_fd: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        raise TransactionError(f"reserved transaction member is unsafe: {name}")
    return True


def _read_json_member(
    directory_fd: int,
    name: str,
    *,
    keys: set[str],
) -> tuple[bytes, dict[str, Any]]:
    descriptor, info, raw = _safe_member(directory_fd, name, maximum=MAX_FILE)
    try:
        if stat.S_IMODE(info.st_mode) != 0o400:
            raise TransactionError(f"transaction state mode differs: {name}")
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=verifier._no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError, verifier.PlanError) as error:
        raise TransactionError(f"transaction state is malformed: {name}") from error
    finally:
        os.close(descriptor)
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or raw != _canonical_json(value)
    ):
        raise TransactionError(f"transaction state shape differs: {name}")
    return raw, value


APPLY_INTENT_KEYS = {
    "schema_version",
    "state",
    "profile",
    "git_head",
    "manifest_sha256",
    "plan_sha256",
    "plan_json_sha256",
    "startup_script_sha256",
    "release_authority_sha256",
    "operator",
    "tool_sha256",
    "authority_live_verification",
    "terraform_state_before",
    "created_at",
}
APPLY_RESULT_KEYS = {
    "schema_version",
    "state",
    "intent_sha256",
    "output_sha256",
    "output_size_bytes",
    "completed_at",
}
APPLY_FAILURE_KEYS = {
    "schema_version",
    "state",
    "outcome",
    "intent_sha256",
    "timed_out",
    "returncode",
    "failed_at",
}
APPLY_RECEIPT_KEYS = {
    "schema_version",
    "state",
    "profile",
    "git_head",
    "manifest_sha256",
    "plan_sha256",
    "plan_json_sha256",
    "startup_script_sha256",
    "startup_metadata_intent_sha256",
    "startup_metadata_receipt_sha256",
    "foundation_execution_intent_sha256",
    "foundation_handoff_receipt_sha256",
    "foundation_boot_id",
    "intent_sha256",
    "result_sha256",
    "authority_live_verification",
    "completed_at",
}


def _timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise TransactionError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise TransactionError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TransactionError(f"{label} timestamp is invalid")
    return parsed


def _validate_apply_intent(
    value: dict[str, Any], manifest: dict[str, Any], manifest_raw: bytes
) -> None:
    projection = value.get("authority_live_verification")
    state_before = value.get("terraform_state_before")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["state"] != "apply-started"
        or value["profile"] != manifest["profile"]
        or value["git_head"] != manifest["git_head"]
        or value["manifest_sha256"] != _sha(manifest_raw)
        or value["plan_sha256"] != manifest["plan"]["sha256"]
        or value["plan_json_sha256"] != manifest["plan_json_sha256"]
        or value["startup_script_sha256"] != manifest["startup_script_sha256"]
        or value["release_authority_sha256"] != manifest["release_authority"]["sha256"]
        or value["operator"] != manifest["operator"]
        or value["tool_sha256"] != manifest["tool"]["sha256"]
        or not isinstance(projection, dict)
        or set(projection)
        != {"authority_sha256", "source_sha", "controller_ref", "verified_at"}
        or projection.get("authority_sha256") != manifest["release_authority"]["sha256"]
        or projection.get("source_sha") != manifest["git_head"]
        or projection.get("controller_ref") != manifest["controller_ref"]
        or not isinstance(state_before, dict)
        or set(state_before) != {"lineage", "serial", "sha256", "pulled_at"}
        or type(state_before.get("lineage")) is not str
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            state_before.get("lineage", ""),
        )
        is None
        or type(state_before.get("serial")) is not int
        or state_before["serial"] < 0
        or type(state_before.get("sha256")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", state_before["sha256"]) is None
    ):
        raise TransactionError("apply intent differs from sealed transaction")
    _timestamp(value["created_at"], "apply intent")
    _timestamp(projection["verified_at"], "apply intent authority")
    _timestamp(state_before["pulled_at"], "apply intent Terraform state")


def _validate_apply_result(value: dict[str, Any], intent_sha256: str) -> None:
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["state"] != "apply-exited-zero"
        or value["intent_sha256"] != intent_sha256
        or type(value["output_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["output_sha256"]) is None
        or type(value["output_size_bytes"]) is not int
        or not 0 <= value["output_size_bytes"] <= MAX_FILE
    ):
        raise TransactionError("apply result identity differs")
    _timestamp(value["completed_at"], "apply result")


def _validate_apply_failure(value: dict[str, Any], intent_sha256: str) -> None:
    returncode = value["returncode"]
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["state"] != "apply-failed"
        or value["outcome"] != "INDETERMINATE"
        or value["intent_sha256"] != intent_sha256
        or type(value["timed_out"]) is not bool
        or (returncode is not None and type(returncode) is not int)
        or returncode == 0
        or (value["timed_out"] and returncode is not None)
    ):
        raise TransactionError("apply failure identity differs")
    _timestamp(value["failed_at"], "apply failure")


def _validate_apply_receipt(
    value: dict[str, Any],
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    intent: dict[str, Any],
    intent_sha256: str,
    result_sha256: str,
) -> None:
    startup = value["startup_script_sha256"]
    startup_intent = value["startup_metadata_intent_sha256"]
    startup_metadata = value["startup_metadata_receipt_sha256"]
    foundation_intent = value["foundation_execution_intent_sha256"]
    foundation_handoff = value["foundation_handoff_receipt_sha256"]
    foundation_boot_id = value["foundation_boot_id"]
    foundation = manifest["profile"] == "foundation"
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["state"] != "applied-and-postchecked"
        or value["profile"] != manifest["profile"]
        or value["git_head"] != manifest["git_head"]
        or value["manifest_sha256"] != manifest_sha256
        or value["plan_sha256"] != manifest["plan"]["sha256"]
        or value["plan_json_sha256"] != manifest["plan_json_sha256"]
        or startup != manifest["startup_script_sha256"]
        or (
            startup is not None
            and (
                type(startup) is not str
                or re.fullmatch(r"[0-9a-f]{64}", startup) is None
            )
        )
        or (
            foundation
            and (
                not isinstance(startup_metadata, str)
                or re.fullmatch(r"[0-9a-f]{64}", startup_metadata) is None
                or not isinstance(startup_intent, str)
                or re.fullmatch(r"[0-9a-f]{64}", startup_intent) is None
                or not isinstance(foundation_intent, str)
                or re.fullmatch(r"[0-9a-f]{64}", foundation_intent) is None
                or not isinstance(foundation_handoff, str)
                or re.fullmatch(r"[0-9a-f]{64}", foundation_handoff) is None
                or not isinstance(foundation_boot_id, str)
                or re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    foundation_boot_id,
                )
                is None
            )
        )
        or (
            not foundation
            and (
                startup_metadata is not None
                or startup_intent is not None
                or foundation_intent is not None
                or foundation_handoff is not None
                or foundation_boot_id is not None
            )
        )
        or value["intent_sha256"] != intent_sha256
        or value["result_sha256"] != result_sha256
        or value["authority_live_verification"] != intent["authority_live_verification"]
    ):
        raise TransactionError("final apply state identity differs")
    _timestamp(value["completed_at"], "apply receipt")


def _existing_apply_state(
    directory_fd: int, manifest: dict[str, Any], manifest_raw: bytes
) -> str:
    present = {
        name
        for name in (
            "apply-intent.json",
            "apply-result.json",
            "apply-failure.json",
            "apply-receipt.json",
        )
        if _member_exists(directory_fd, name)
    }
    if not present:
        return "new"
    if "apply-intent.json" not in present:
        raise TransactionError("apply state exists without durable intent")
    intent_raw, intent = _read_json_member(
        directory_fd, "apply-intent.json", keys=APPLY_INTENT_KEYS
    )
    _validate_apply_intent(intent, manifest, manifest_raw)
    if "apply-receipt.json" in present:
        if present != {"apply-intent.json", "apply-result.json", "apply-receipt.json"}:
            raise TransactionError("final apply state contains conflicting members")
        result_raw, result = _read_json_member(
            directory_fd, "apply-result.json", keys=APPLY_RESULT_KEYS
        )
        _receipt_raw, receipt = _read_json_member(
            directory_fd, "apply-receipt.json", keys=APPLY_RECEIPT_KEYS
        )
        _validate_apply_result(result, _sha(intent_raw))
        _validate_apply_receipt(
            receipt,
            manifest=manifest,
            manifest_sha256=_sha(manifest_raw),
            intent=intent,
            intent_sha256=_sha(intent_raw),
            result_sha256=_sha(result_raw),
        )
        if manifest["profile"] == "foundation":
            evidence = _startup_evidence(directory_fd, manifest, mode=0o400)
            if any(receipt.get(key) != value for key, value in evidence.items()):
                raise TransactionError("final receipt startup evidence differs")
        elif any(
            _member_exists(directory_fd, name)
            for name in STARTUP_EVIDENCE_MEMBERS.values()
        ):
            raise TransactionError("non-foundation apply contains startup evidence")
        return "complete"
    if "apply-result.json" in present:
        if present != {"apply-intent.json", "apply-result.json"}:
            raise TransactionError(
                "successful apply state contains conflicting members"
            )
        _result_raw, result = _read_json_member(
            directory_fd, "apply-result.json", keys=APPLY_RESULT_KEYS
        )
        _validate_apply_result(result, _sha(intent_raw))
        return "postcheck"
    if "apply-failure.json" in present:
        if present != {"apply-intent.json", "apply-failure.json"}:
            raise TransactionError("failed apply state contains conflicting members")
        _failure_raw, failure = _read_json_member(
            directory_fd, "apply-failure.json", keys=APPLY_FAILURE_KEYS
        )
        _validate_apply_failure(failure, _sha(intent_raw))
        return "indeterminate"
    return "indeterminate"


def _write_new(path: Path, payload: bytes, mode: int) -> dict[str, Any]:
    """Write an external receipt after anchoring its parent directory."""
    directory = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return _write_member(directory, path.name, payload, mode)
    finally:
        os.close(directory)


def _manifest(
    directory_fd: int,
) -> tuple[int, os.stat_result, bytes, dict[str, Any]]:
    descriptor, _, raw = _safe_member(directory_fd, "manifest.json", maximum=MAX_FILE)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=verifier._no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError, verifier.PlanError) as error:
        os.close(descriptor)
        raise TransactionError("transaction manifest is malformed") from error
    if (
        not isinstance(value, dict)
        or set(value) != MANIFEST_KEYS
        or raw != _canonical_json(value)
    ):
        os.close(descriptor)
        raise TransactionError("transaction manifest shape differs")
    profiles = {"foundation", "iam-revoke", "secret-adoption"}
    identities = (
        "tool",
        "plan_tool_copy",
        "plan",
        "tfvars",
        "backend_config",
        "release_authority",
        "gcloud",
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["profile"] not in profiles
        or type(value["git_head"]) is not str
        or re.fullmatch(r"[0-9a-f]{40}", value["git_head"]) is None
        or value["controller_ref"] != value["git_head"]
        or type(value["plan_json_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["plan_json_sha256"]) is None
        or type(value["tracked_config_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["tracked_config_sha256"]) is None
        or any(not isinstance(value[name], dict) for name in identities)
        or type(value["helpers"]) is not dict
        or type(value["operator"]) is not dict
        or type(value["github_config"]) is not dict
        or type(value["backend_projection"]) is not dict
        or type(value["release_authority_source_path"]) is not str
        or type(value["control_bundle"]) is not dict
        or type(value["provider_bundle"]) is not dict
    ):
        os.close(descriptor)
        raise TransactionError("transaction manifest value types differ")
    _timestamp(value["created_at"], "transaction manifest")
    _control_bundle(value)
    _provider_bundle(value)
    return descriptor, os.fstat(descriptor), raw, value


def _release_authority(path: Path) -> tuple[int, os.stat_result, bytes, dict[str, Any]]:
    descriptor, info, raw = _safe_path(path, maximum=MAX_FILE)
    if stat.S_IMODE(info.st_mode) != 0o400:
        os.close(descriptor)
        raise TransactionError("release authority must be mode 0400")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=verifier._no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError, verifier.PlanError) as error:
        os.close(descriptor)
        raise TransactionError("release authority is not strict JSON") from error
    if not isinstance(value, dict):
        os.close(descriptor)
        raise TransactionError("release authority is not an object")
    try:
        value = verifier.plan_contract.release_authority_contract.validate_authority_receipt(
            value
        )
    except verifier.plan_contract.release_authority_contract.AuthorityError as error:
        os.close(descriptor)
        raise TransactionError("release authority receipt is invalid") from error
    if raw != verifier.plan_contract.release_authority_contract._canonical(value):
        os.close(descriptor)
        raise TransactionError("release authority bytes are not exact canonical JSON")
    return descriptor, info, raw, value


def _sealed_authority(
    directory_fd: int,
) -> tuple[int, os.stat_result, bytes, dict[str, Any]]:
    descriptor, info, raw = _safe_member(
        directory_fd, "release-authority.json", maximum=MAX_FILE
    )
    if stat.S_IMODE(info.st_mode) != 0o400:
        os.close(descriptor)
        raise TransactionError("sealed release authority must be mode 0400")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=verifier._no_duplicates
        )
        value = verifier.plan_contract.release_authority_contract.validate_authority_receipt(
            value
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        verifier.PlanError,
        verifier.plan_contract.release_authority_contract.AuthorityError,
    ) as error:
        os.close(descriptor)
        raise TransactionError("sealed release authority is invalid") from error
    if raw != verifier.plan_contract.release_authority_contract._canonical(value):
        os.close(descriptor)
        raise TransactionError("sealed release authority is not exact canonical JSON")
    return descriptor, info, raw, value


def create(args: argparse.Namespace) -> int:
    transaction = args.transaction
    if not transaction.is_absolute() or transaction.parent.is_symlink():
        raise TransactionError("transaction directory path is unsafe")
    parent_fd = os.open(
        transaction.parent,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    parent_info = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid():
        os.close(parent_fd)
        raise TransactionError("transaction parent directory is unsafe")
    try:
        os.mkdir(transaction.name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        created_info = os.stat(
            transaction.name, dir_fd=parent_fd, follow_symlinks=False
        )
    except BaseException:
        os.close(parent_fd)
        raise
    if (
        not stat.S_ISDIR(created_info.st_mode)
        or stat.S_IMODE(created_info.st_mode) != 0o700
    ):
        os.close(parent_fd)
        raise TransactionError("transaction directory creation identity differs")
    descriptors: list[int] = [parent_fd]
    sessions = contextlib.ExitStack()
    transaction_fd = -1
    try:
        transaction_fd, transaction_info = _open_transaction(
            transaction, expected_modes={0o700}
        )
        plan_data, plan_data_fd, plan_data_info = _create_private_directory(
            transaction_fd, transaction, transaction_info, "plan-data"
        )
        descriptors.append(plan_data_fd)
        head, config_sha, helpers = _source_identity()
        bundle, control_bundle = _seal_control_bundle(
            transaction_fd,
            transaction,
            transaction_info,
            head=head,
            config_sha=config_sha,
            helpers=helpers,
            gh_config=args.gh_config,
            gcloud_config=args.gcloud_config,
        )
        _activate_contracts(bundle)
        module = bundle / "infra/terraform-gcp"
        runtime = sessions.enter_context(
            _runtime_session(
                {
                    "control_bundle": control_bundle,
                    "git_head": head,
                    "tracked_config_sha256": config_sha,
                    "helpers": helpers,
                },
                args.gh_config,
                args.gcloud_config,
            )
        )
        tfvars_fd, tfvars_info, tfvars_raw = _safe_path(args.tfvars, maximum=MAX_FILE)
        descriptors.append(tfvars_fd)
        backend_path = module / "envs/staging/backend.hcl"
        backend_fd, backend_info, backend_raw = _safe_path(
            backend_path, maximum=MAX_FILE
        )
        descriptors.append(backend_fd)
        backend_projection = _validate_backend(backend_raw)
        authority_fd, _authority_info, authority_raw, authority = _release_authority(
            args.release_authority
        )
        descriptors.append(authority_fd)
        tool_fd, tool_identity, private_tool_identity, environment = _tool(
            args.tofu, runtime["gcloud_config"], plan_data, plan_data_fd, module
        )
        environment = _runtime_env(
            runtime["gcloud_config"],
            plan_data,
            runtime["python"],
            runtime["python_framework"],
        )
        descriptors.append(tool_fd)
        gcloud_fd, gcloud_info, gcloud_raw = _safe_path(
            runtime["gcloud"], maximum=MAX_PLAN
        )
        descriptors.append(gcloud_fd)
        gcloud_identity = _identity(gcloud_info, gcloud_raw) | {
            "path": str(runtime["gcloud"]),
            "sdk": control_bundle["sdk"],
        }
        gcloud_path = runtime["gcloud"]
        github_config = control_bundle["gh_projection"]
        operator = _operator(
            gcloud_path,
            runtime["gcloud_config"],
            args.expected_account,
            environment,
            python=runtime["python"],
        )
        _write_member(transaction_fd, "release-authority.json", authority_raw, 0o400)
        authority_copy_fd, authority_copy_info, authority_copy_raw = _safe_member(
            transaction_fd, "release-authority.json", maximum=MAX_FILE
        )
        descriptors.append(authority_copy_fd)
        _verify_authority_live(
            authority,
            runtime["gh_config"],
            runtime["gcloud_config"],
            gh=runtime["gh"],
            gcloud=runtime["gcloud"],
            python=runtime["python"],
            python_framework=runtime["python_framework"],
        )
        _revalidate_gcloud(gcloud_identity)
        if _profile_uses_runtime_transition(args.profile):
            _edge(
                "pre-transition",
                args.expected_account,
                runtime["gcloud_config"],
                environment,
                runtime["gcloud"],
            )
        tofu_path = _private_tool(private_tool_identity)
        _revalidate_private_directory(plan_data, plan_data_info)
        os.lseek(backend_fd, 0, os.SEEK_SET)
        _run(
            [
                str(tofu_path),
                "-chdir=" + str(module),
                "init",
                "-input=false",
                "-lockfile=readonly",
                "-reconfigure",
                f"-backend-config=/dev/fd/{backend_fd}",
            ],
            cwd=ROOT,
            env=environment,
            timeout=300,
            pass_fds=(backend_fd,),
        )
        provider_path, provider_bundle = _seal_provider_bundle(
            transaction, plan_data / "providers"
        )
        del provider_path
        os.fsync(transaction_fd)
        _attest_provider_installation(plan_data, provider_bundle)
        os.lseek(tfvars_fd, 0, os.SEEK_SET)
        plan_path = plan_data / "plan.bin"
        _revalidate_private_directory(plan_data, plan_data_info)
        _run(
            [
                str(_private_tool(private_tool_identity)),
                "-chdir=" + str(module),
                "plan",
                "-input=false",
                "-lock=true",
                "-refresh=true",
                "-out=" + str(plan_path),
                f"-var-file=/dev/fd/{tfvars_fd}",
                *_plan_profile_args(args.profile),
            ],
            cwd=ROOT,
            env=environment,
            timeout=900,
            pass_fds=(tfvars_fd,),
        )
        _attest_provider_installation(plan_data, provider_bundle)
        temporary_plan_fd, _temporary_plan_info, plan_raw = _safe_member(
            plan_data_fd, "plan.bin", maximum=MAX_PLAN
        )
        os.fchmod(temporary_plan_fd, 0o400)
        os.close(temporary_plan_fd)
        _write_member(transaction_fd, "plan.bin", plan_raw, 0o400)
        plan_fd, plan_info, plan_raw = _safe_member(
            transaction_fd, "plan.bin", maximum=MAX_PLAN
        )
        descriptors.append(plan_fd)
        plan_json = _show(
            _private_tool(private_tool_identity), plan_fd, environment, module
        )
        _attest_provider_installation(plan_data, provider_bundle)
        _write_member(transaction_fd, "plan.json", plan_json, 0o400)
        payload = verifier.load_plan_bytes(plan_json)
        counts = verifier.validate_plan(payload, profile=args.profile)
        verifier.plan_contract.validate_authority_variables(
            payload,
            revoke_secret_accessor=args.profile == "iam-revoke",
            release_authority=authority,
        )
        startup_sha: str | None = None
        startup_identity: dict[str, Any] | None = None
        if _profile_uses_runtime_transition(args.profile):
            startup_raw = _startup_bytes(payload)
            _write_member(transaction_fd, "startup-script.sh", startup_raw, 0o400)
            startup_fd, startup_info, startup_readback = _safe_member(
                transaction_fd, "startup-script.sh", maximum=256 * 1024
            )
            descriptors.append(startup_fd)
            startup_sha = _startup_sha(payload)
            startup_identity = _identity(startup_info, startup_readback)
        controller_ref = verifier.plan_contract.validate_authority_variables(
            payload, revoke_secret_accessor=args.profile == "iam-revoke"
        )
        if controller_ref != head:
            raise TransactionError(
                "plan controller_ref differs from exact clean Git HEAD"
            )
        if _source_identity() != (head, config_sha, helpers):
            raise TransactionError("source identity changed during plan generation")
        authority_live_verification = _verify_authority_live(
            authority,
            runtime["gh_config"],
            runtime["gcloud_config"],
            gh=runtime["gh"],
            gcloud=runtime["gcloud"],
            python=runtime["python"],
            python_framework=runtime["python_framework"],
        )
        manifest = {
            "schema_version": 1,
            "profile": args.profile,
            "git_head": head,
            "controller_ref": controller_ref,
            "startup_script_sha256": startup_sha,
            "startup_script": startup_identity,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tool": tool_identity,
            "plan_tool_copy": private_tool_identity,
            "plan": _identity(plan_info, plan_raw),
            "plan_json_sha256": _sha(plan_json),
            "tfvars": _identity(tfvars_info, tfvars_raw) | {"path": str(args.tfvars)},
            "backend_config": _identity(backend_info, backend_raw)
            | {"path": "envs/staging/backend.hcl"},
            "backend_projection": backend_projection,
            "release_authority": _identity(authority_copy_info, authority_copy_raw),
            "release_authority_source_path": str(args.release_authority),
            "authority_live_verification": authority_live_verification,
            "tracked_config_sha256": config_sha,
            "helpers": helpers,
            "operator": operator,
            "github_config": github_config,
            "gcloud": gcloud_identity,
            "control_bundle": control_bundle,
            "provider_bundle": provider_bundle,
        }
        raw = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        _write_member(transaction_fd, "manifest.json", raw, 0o400)
        print(
            "OMEGA_GCP_TERRAFORM_TRANSACTION\tSEALED\t"
            f"profile={args.profile}\tcreate={counts['create']}\tupdate={counts['update']}"
        )
        return 0
    finally:
        sessions.close()
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if transaction_fd >= 0:
            try:
                _revalidate_transaction_path(transaction, transaction_info)
            finally:
                os.close(transaction_fd)


def _matches(identity: dict[str, Any], info: os.stat_result, raw: bytes) -> bool:
    return identity == _identity(info, raw)


STARTUP_EVIDENCE_MEMBERS = {
    "startup_metadata_intent": "startup-metadata-receipt.json.intent",
    "startup_metadata_receipt": "startup-metadata-receipt.json",
    "foundation_execution_intent": "foundation-handoff-receipt.json.intent",
    "foundation_handoff_receipt": "foundation-handoff-receipt.json",
}


def _startup_evidence(
    directory_fd: int,
    manifest: dict[str, Any],
    *,
    mode: int | set[int],
    seal: bool = False,
) -> dict[str, str]:
    raw: dict[str, bytes] = {}
    values: dict[str, dict[str, Any]] = {}
    for label, name in STARTUP_EVIDENCE_MEMBERS.items():
        descriptor, info, payload = _safe_member(directory_fd, name, maximum=65536)
        try:
            allowed_modes = {mode} if isinstance(mode, int) else mode
            if stat.S_IMODE(info.st_mode) not in allowed_modes:
                raise TransactionError("startup evidence mode differs")
            try:
                value = json.loads(
                    payload.decode("utf-8"),
                    object_pairs_hook=verifier._no_duplicates,
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                verifier.PlanError,
            ) as error:
                raise TransactionError("startup evidence is malformed") from error
            if not isinstance(value, dict) or payload != _canonical_json(value):
                raise TransactionError("startup evidence is not canonical JSON")
            if seal:
                os.fchmod(descriptor, 0o400)
                os.fsync(descriptor)
            raw[label] = payload
            values[label] = value
        finally:
            os.close(descriptor)

    metadata_intent = values["startup_metadata_intent"]
    metadata_receipt = values["startup_metadata_receipt"]
    foundation_intent = values["foundation_execution_intent"]
    foundation_receipt = values["foundation_handoff_receipt"]
    host_receipt = foundation_receipt.get("host_receipt")
    metadata_keys = set(startup_transaction.METADATA_RECEIPT_KEYS)
    execution_keys = set(startup_transaction.FOUNDATION_EXECUTION_RECEIPT_KEYS)
    metadata_intent_keys = metadata_keys - {
        "status",
        "operation_name",
        "post_metadata_fingerprint_sha256",
        "last_start_timestamp",
        "instance_reset_requested",
        "foundation_execution_state",
        "recovered",
    }
    execution_intent_keys = {
        "schema_version",
        "operation",
        "state",
        "project_id",
        "zone",
        "instance",
        "instance_id",
        "controller_ref",
        "source_ref",
        "new_startup_sha256",
        "startup_contract_sha256",
        "metadata_receipt_sha256",
        "pre_reset_last_start_timestamp",
        "secrets_included",
        "created_at",
    }
    startup_args = argparse.Namespace(
        controller_ref=metadata_intent.get("controller_ref"),
        source_ref=metadata_intent.get("source_ref"),
        old_sha256=metadata_intent.get("old_startup_sha256"),
        new_sha256=manifest["startup_script_sha256"],
    )
    try:
        startup_transaction._validate_metadata_receipt(
            metadata_receipt,
            args=startup_args,
            startup_contract_sha256=metadata_intent.get("startup_contract_sha256", ""),
        )
        startup_transaction._validate_execution_intent(
            foundation_intent,
            args=startup_args,
            metadata_receipt_sha256=_sha(raw["startup_metadata_receipt"]),
            startup_contract_sha256=metadata_intent.get("startup_contract_sha256", ""),
        )
        startup_transaction._validate_execution_receipt(
            foundation_receipt,
            args=startup_args,
            intent=foundation_intent,
            metadata_receipt_sha256=_sha(raw["startup_metadata_receipt"]),
            startup_contract_sha256=metadata_intent.get("startup_contract_sha256", ""),
        )
    except startup_transaction.TransactionError as error:
        raise TransactionError("startup evidence receipt validation failed") from error
    if (
        set(metadata_intent) != metadata_intent_keys
        or set(metadata_receipt) != metadata_keys
        or set(foundation_intent) != execution_intent_keys
        or set(foundation_receipt) != execution_keys
        or not isinstance(host_receipt, dict)
        or set(host_receipt) != set(startup_transaction.FOUNDATION_HOST_RECEIPT_KEYS)
        or any(
            metadata_receipt.get(key) != value for key, value in metadata_intent.items()
        )
        or metadata_receipt.get("status") != "PASS"
        or metadata_receipt.get("instance_reset_requested") is not False
        or metadata_receipt.get("foundation_execution_state")
        != "pending-controlled-reboot"
        or metadata_receipt.get("new_startup_sha256")
        != manifest["startup_script_sha256"]
        or foundation_intent.get("metadata_receipt_sha256")
        != _sha(raw["startup_metadata_receipt"])
        or foundation_intent.get("new_startup_sha256")
        != manifest["startup_script_sha256"]
        or any(
            foundation_receipt.get(key) != value
            for key, value in foundation_intent.items()
            if key not in {"state", "created_at"}
        )
        or foundation_receipt.get("status") != "PASS"
        or foundation_receipt.get("pre_reset_last_start_timestamp")
        == foundation_receipt.get("post_reset_last_start_timestamp")
        or foundation_receipt.get("host_receipt_sha256")
        != _sha(_canonical_json(host_receipt))
        or host_receipt.get("state") != "foundation-fenced"
        or host_receipt.get("live_startup_script_sha256")
        != manifest["startup_script_sha256"]
        or host_receipt.get("startup_contract_sha256")
        != metadata_receipt.get("startup_contract_sha256")
        or host_receipt.get("source_sha") != metadata_receipt.get("source_ref")
        or host_receipt.get("controller_ref") != metadata_receipt.get("controller_ref")
        or host_receipt.get("instance_id") != startup_transaction.INSTANCE_ID
        or host_receipt.get("zone") != startup_transaction.ZONE
        or not isinstance(host_receipt.get("boot_id"), str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            host_receipt["boot_id"],
        )
        is None
    ):
        raise TransactionError("startup foundation evidence chain differs")
    if seal:
        os.fsync(directory_fd)
    return {
        "startup_metadata_intent_sha256": _sha(raw["startup_metadata_intent"]),
        "startup_metadata_receipt_sha256": _sha(raw["startup_metadata_receipt"]),
        "foundation_execution_intent_sha256": _sha(raw["foundation_execution_intent"]),
        "foundation_handoff_receipt_sha256": _sha(raw["foundation_handoff_receipt"]),
        "foundation_boot_id": host_receipt["boot_id"],
    }


def _execute_startup_foundation(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    authority: dict[str, Any],
    runtime: dict[str, Path],
) -> dict[str, Any]:
    if (
        authority.get("controller_ref") != manifest["controller_ref"]
        or authority.get("old_live_startup_sha256") == manifest["startup_script_sha256"]
        or authority.get("provenance", {}).get("live_vm", {}).get("instance_id")
        != startup_transaction.INSTANCE_ID
    ):
        raise TransactionError("startup authority differs from sealed foundation")
    bundle = _control_bundle(manifest)
    startup_args = argparse.Namespace(
        repo=bundle,
        candidate_startup=args.transaction / "startup-script.sh",
        old_sha256=authority["old_live_startup_sha256"],
        new_sha256=manifest["startup_script_sha256"],
        controller_ref=authority["controller_ref"],
        source_ref=authority["source_sha"],
        receipt=args.transaction / "startup-metadata-receipt.json",
        foundation_receipt=args.transaction / "foundation-handoff-receipt.json",
        operator_account=args.expected_account,
        gcloud_config=runtime["gcloud_config"],
        gcloud=runtime["gcloud"],
        gcloud_python=runtime["python"],
        python_framework=runtime["python_framework"],
        timeout_seconds=180,
        foundation_timeout_seconds=11400,
        poll_seconds=15,
        confirm_exact_cas=True,
        confirm_exact_foundation_reset=True,
    )
    try:
        return startup_transaction.execute_foundation(startup_args)
    except startup_transaction.TransactionError as error:
        raise TransactionError(
            "startup foundation did not reach verified handoff"
        ) from error


def _finish_postcheck(
    args: argparse.Namespace,
    transaction_fd: int,
    manifest_raw: bytes,
    manifest: dict[str, Any],
) -> int:
    if _existing_apply_state(transaction_fd, manifest, manifest_raw) != "postcheck":
        raise TransactionError("postcheck recovery requires an exact successful result")
    intent_raw, intent = _read_json_member(
        transaction_fd, "apply-intent.json", keys=APPLY_INTENT_KEYS
    )
    result_raw, _result = _read_json_member(
        transaction_fd, "apply-result.json", keys=APPLY_RESULT_KEYS
    )
    authority_fd, authority_info, authority_raw, authority = _sealed_authority(
        transaction_fd
    )
    sessions = contextlib.ExitStack()
    try:
        if not _matches(
            manifest["release_authority"], authority_info, authority_raw
        ) or not _authority_verification_matches(
            intent["authority_live_verification"], authority, authority_raw
        ):
            raise TransactionError("postcheck sealed authority/tool identity differs")
        runtime = sessions.enter_context(
            _runtime_session(manifest, args.gh_config, args.gcloud_config)
        )
        environment = _runtime_env(
            runtime["gcloud_config"],
            args.transaction,
            runtime["python"],
            runtime["python_framework"],
        )
        operator = _operator(
            runtime["gcloud"],
            runtime["gcloud_config"],
            args.expected_account,
            environment,
            python=runtime["python"],
        )
        head, config_sha, helpers = _source_identity()
        if (
            operator != manifest["operator"]
            or head != manifest["git_head"]
            or config_sha != manifest["tracked_config_sha256"]
            or helpers != manifest["helpers"]
            or manifest["control_bundle"]["gh_projection"] != manifest["github_config"]
        ):
            raise TransactionError("postcheck controller/operator identity differs")
        _revalidate_gcloud(manifest["gcloud"])
        startup_evidence: dict[str, str | None] = {
            "startup_metadata_intent_sha256": None,
            "startup_metadata_receipt_sha256": None,
            "foundation_execution_intent_sha256": None,
            "foundation_handoff_receipt_sha256": None,
            "foundation_boot_id": None,
        }
        if _profile_uses_runtime_transition(args.profile):
            startup_receipt = _execute_startup_foundation(
                args, manifest, authority, runtime
            )
            startup_evidence = _startup_evidence(
                transaction_fd, manifest, mode={0o400, 0o600}
            )
            _edge(
                "post-transition",
                args.expected_account,
                runtime["gcloud_config"],
                environment,
                runtime["gcloud"],
            )
            if (
                _execute_startup_foundation(args, manifest, authority, runtime)
                != startup_receipt
            ):
                raise TransactionError("post-edge startup handoff read-back differs")
            if (
                _startup_evidence(
                    transaction_fd, manifest, mode={0o400, 0o600}, seal=True
                )
                != startup_evidence
                or _startup_evidence(transaction_fd, manifest, mode=0o400)
                != startup_evidence
            ):
                raise TransactionError("sealed startup evidence read-back differs")
        else:
            _verify_authority_live(
                authority,
                runtime["gh_config"],
                runtime["gcloud_config"],
                gh=runtime["gh"],
                gcloud=runtime["gcloud"],
                python=runtime["python"],
                python_framework=runtime["python_framework"],
            )
        receipt = {
            "schema_version": 1,
            "state": "applied-and-postchecked",
            "profile": args.profile,
            "git_head": head,
            "manifest_sha256": _sha(manifest_raw),
            "plan_sha256": manifest["plan"]["sha256"],
            "plan_json_sha256": manifest["plan_json_sha256"],
            "startup_script_sha256": manifest["startup_script_sha256"],
            **startup_evidence,
            "intent_sha256": _sha(intent_raw),
            "result_sha256": _sha(result_raw),
            "authority_live_verification": intent["authority_live_verification"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_state_member(transaction_fd, "apply-receipt.json", receipt)
        os.fchmod(transaction_fd, 0o500)
        os.fsync(transaction_fd)
        print("OMEGA_GCP_TERRAFORM_TRANSACTION\tAPPLIED_AND_POSTCHECKED")
        return 0
    finally:
        sessions.close()
        os.close(authority_fd)


def _new_apply_directory(
    transaction_fd: int,
    transaction: Path,
    transaction_info: os.stat_result,
) -> tuple[Path, int, os.stat_result]:
    for _attempt in range(8):
        name = f"apply-data-{secrets.token_hex(8)}"
        try:
            return _create_private_directory(
                transaction_fd, transaction, transaction_info, name
            )
        except FileExistsError:
            continue
    raise TransactionError("cannot allocate a fresh private apply directory")


IAM_CONTROLLER_MANIFEST_KEYS = {
    "schema_version",
    "profile",
    "state",
    "created_at",
    "project",
    "environment",
    "zone",
    "instance",
    "role",
    "member",
    "resource_grants",
    "forbidden_resource_count",
    "source",
    "operator",
    "inputs",
    "matrix_verifier",
    "terraform",
    "precheck",
}


def _require_active_iam_controller(
    controller: Path | None,
    transaction: Path,
    inner_manifest_info: os.stat_result,
    inner_manifest_raw: bytes,
    inner_manifest: dict[str, Any],
) -> int:
    if (
        controller is None
        or not controller.is_absolute()
        or controller.is_symlink()
        or transaction.name != "terraform"
        or transaction.parent != controller
    ):
        raise TransactionError("iam-revoke apply requires the reversible controller")
    descriptor = os.open(
        controller,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise TransactionError("IAM controller directory identity differs")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            raise TransactionError("IAM reversible controller is not actively locked")
        manifest_fd, manifest_info, outer_raw = _safe_member(
            descriptor, "manifest.json", maximum=MAX_FILE
        )
        try:
            if stat.S_IMODE(manifest_info.st_mode) != 0o400:
                raise TransactionError("IAM controller manifest mode differs")
            outer = json.loads(
                outer_raw.decode("utf-8"), object_pairs_hook=verifier._no_duplicates
            )
        except (UnicodeDecodeError, json.JSONDecodeError, verifier.PlanError) as error:
            raise TransactionError("IAM controller manifest is malformed") from error
        finally:
            os.close(manifest_fd)
        if (
            not isinstance(outer, dict)
            or set(outer) != IAM_CONTROLLER_MANIFEST_KEYS
            or outer_raw != _canonical_json(outer)
            or type(outer["schema_version"]) is not int
            or outer["schema_version"] != 1
            or outer["profile"] != "iam-revoke"
            or outer["state"] != "sealed"
            or not isinstance(outer["terraform"], dict)
            or set(outer["terraform"])
            != {
                "directory",
                "manifest_sha256",
                "manifest_inode",
                "plan_sha256",
                "plan_json_sha256",
            }
            or outer["terraform"]["directory"] != "terraform"
            or outer["terraform"]["manifest_sha256"] != _sha(inner_manifest_raw)
            or outer["terraform"]["manifest_inode"] != inner_manifest_info.st_ino
            or outer["terraform"]["plan_sha256"] != inner_manifest["plan"]["sha256"]
            or outer["terraform"]["plan_json_sha256"]
            != inner_manifest["plan_json_sha256"]
            or not isinstance(outer["source"], dict)
            or outer["source"].get("git_head") != inner_manifest["git_head"]
            or not isinstance(outer["operator"], dict)
            or outer["operator"].get("account")
            != inner_manifest["operator"].get("account")
        ):
            raise TransactionError("IAM controller authority differs from sealed plan")
        events_fd = os.open(
            "events",
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            events_info = os.fstat(events_fd)
            names = os.listdir(events_fd)
            if (
                events_info.st_uid != os.geteuid()
                or stat.S_IMODE(events_info.st_mode) != 0o700
                or names != ["00000001-mutation-intent.json"]
            ):
                raise TransactionError("IAM controller event inventory differs")
            event_fd, event_info, event_raw = _safe_member(
                events_fd, names[0], maximum=MAX_FILE
            )
            try:
                if stat.S_IMODE(event_info.st_mode) != 0o400:
                    raise TransactionError("IAM controller intent mode differs")
                event = json.loads(
                    event_raw.decode("utf-8"),
                    object_pairs_hook=verifier._no_duplicates,
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                verifier.PlanError,
            ) as error:
                raise TransactionError("IAM controller intent is malformed") from error
            finally:
                os.close(event_fd)
            if (
                not isinstance(event, dict)
                or set(event)
                != {
                    "schema_version",
                    "sequence",
                    "event",
                    "manifest_sha256",
                    "occurred_at",
                    "details",
                }
                or event_raw != _canonical_json(event)
                or type(event["schema_version"]) is not int
                or event["schema_version"] != 1
                or type(event["sequence"]) is not int
                or event["sequence"] != 1
                or event["event"] != "mutation-intent"
                or event["manifest_sha256"] != _sha(outer_raw)
                or event["details"] != {"state": "mutation-authorized"}
            ):
                raise TransactionError("IAM controller mutation intent differs")
            _timestamp(event["occurred_at"], "IAM controller intent")
        finally:
            os.close(events_fd)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def apply(args: argparse.Namespace) -> int:
    transaction_fd, transaction_info = _open_transaction(
        args.transaction, expected_modes={0o500, 0o700}
    )
    descriptors: list[int] = []
    sessions = contextlib.ExitStack()
    try:
        manifest_fd, manifest_info, manifest_raw, manifest = _manifest(transaction_fd)
        descriptors.append(manifest_fd)
        if not any(
            _member_exists(transaction_fd, name)
            for name in (
                "apply-intent.json",
                "apply-result.json",
                "apply-failure.json",
                "apply-receipt.json",
            )
        ):
            _require_fresh_manifest(manifest)
        bundle = _control_bundle(manifest)
        _activate_contracts(bundle)
        module = _bundle_module(manifest)
        if manifest["profile"] != args.profile:
            raise TransactionError("transaction profile differs")
        controller_path = getattr(args, "iam_controller_transaction", None)
        if args.profile == "iam-revoke":
            descriptors.append(
                _require_active_iam_controller(
                    controller_path,
                    args.transaction,
                    manifest_info,
                    manifest_raw,
                    manifest,
                )
            )
        elif controller_path is not None:
            raise TransactionError("IAM controller authority is profile-confused")
        state = _existing_apply_state(transaction_fd, manifest, manifest_raw)
        if state == "complete":
            print("OMEGA_GCP_TERRAFORM_TRANSACTION\tALREADY_APPLIED_AND_POSTCHECKED")
            return 0
        if state == "postcheck":
            return _finish_postcheck(args, transaction_fd, manifest_raw, manifest)
        if state == "indeterminate":
            raise TransactionError(
                "apply outcome is INDETERMINATE; never rerun the sealed plan"
            )
        _require_fresh_manifest(manifest)
        apply_data, apply_data_fd, apply_data_info = _new_apply_directory(
            transaction_fd, args.transaction, transaction_info
        )
        descriptors.append(apply_data_fd)
        runtime = sessions.enter_context(
            _runtime_session(manifest, args.gh_config, args.gcloud_config)
        )
        tool_fd, tool_identity, private_tool_identity, environment = _tool(
            args.tofu,
            runtime["gcloud_config"],
            apply_data,
            apply_data_fd,
            module,
        )
        environment = _runtime_env(
            runtime["gcloud_config"],
            apply_data,
            runtime["python"],
            runtime["python_framework"],
        )
        descriptors.append(tool_fd)
        gcloud_identity = manifest["gcloud"]
        tfvars_fd, tfvars_info, tfvars_raw = _safe_path(args.tfvars, maximum=MAX_FILE)
        descriptors.append(tfvars_fd)
        backend_path = module / "envs/staging/backend.hcl"
        backend_fd, backend_info, backend_raw = _safe_path(
            backend_path, maximum=MAX_FILE
        )
        descriptors.append(backend_fd)
        backend_projection = _validate_backend(backend_raw)
        authority_fd, authority_info, authority_raw, authority = _sealed_authority(
            transaction_fd
        )
        descriptors.append(authority_fd)
        provider_path = _provider_bundle(manifest)
        provider_identity = manifest["provider_bundle"]
        contract = verifier.plan_contract.release_authority_contract
        try:
            contract._runtime_tree_snapshot(
                provider_path,
                apply_data / "providers",
                expected_sha256=provider_identity["inventory_sha256"],
            )
        except contract.AuthorityError as error:
            raise TransactionError("provider closure cannot be installed") from error
        _attest_provider_installation(apply_data, provider_identity)
        if tool_identity != manifest["tool"]:
            raise TransactionError(
                "OpenTofu inode/hash differs from sealed transaction"
            )
        if gcloud_identity != manifest["gcloud"]:
            raise TransactionError("gcloud inode/hash differs from sealed transaction")
        operator = _operator(
            runtime["gcloud"],
            runtime["gcloud_config"],
            args.expected_account,
            environment,
            python=runtime["python"],
        )
        if operator != manifest["operator"]:
            raise TransactionError(
                "operator identity/config differs from sealed transaction"
            )
        head, config_sha, helpers = _source_identity()
        _control_bundle(manifest)
        if (
            head != manifest["git_head"]
            or head != manifest["controller_ref"]
            or config_sha != manifest["tracked_config_sha256"]
            or helpers != manifest["helpers"]
            or manifest["control_bundle"]["gh_projection"] != manifest["github_config"]
            or manifest["tfvars"].get("path") != str(args.tfvars)
            or not _matches(
                {
                    key: value
                    for key, value in manifest["tfvars"].items()
                    if key != "path"
                },
                tfvars_info,
                tfvars_raw,
            )
            or manifest["backend_config"].get("path") != "envs/staging/backend.hcl"
            or manifest["backend_projection"] != backend_projection
            or not _matches(
                {
                    key: value
                    for key, value in manifest["backend_config"].items()
                    if key != "path"
                },
                backend_info,
                backend_raw,
            )
            or not _matches(
                manifest["release_authority"], authority_info, authority_raw
            )
            or not _authority_verification_matches(
                manifest["authority_live_verification"], authority, authority_raw
            )
        ):
            raise TransactionError("sealed source/config identity differs")
        os.lseek(backend_fd, 0, os.SEEK_SET)
        _revalidate_private_directory(apply_data, apply_data_info)
        _run(
            [
                str(_private_tool(private_tool_identity)),
                "-chdir=" + str(module),
                "init",
                "-input=false",
                "-lockfile=readonly",
                "-reconfigure",
                "-plugin-dir=" + str(provider_path),
                f"-backend-config=/dev/fd/{backend_fd}",
            ],
            cwd=ROOT,
            env=environment,
            timeout=300,
            pass_fds=(backend_fd,),
        )
        _attest_provider_installation(apply_data, provider_identity)
        plan_fd, plan_info, plan_raw = _safe_member(
            transaction_fd, "plan.bin", maximum=MAX_PLAN
        )
        descriptors.append(plan_fd)
        if not _matches(manifest["plan"], plan_info, plan_raw):
            raise TransactionError("saved plan inode/hash differs")
        plan_json = _show(
            _private_tool(private_tool_identity), plan_fd, environment, module
        )
        _attest_provider_installation(apply_data, provider_identity)
        if _sha(plan_json) != manifest["plan_json_sha256"]:
            raise TransactionError("same-inode plan JSON differs from sealed JSON")
        payload = verifier.load_plan_bytes(plan_json)
        verifier.validate_plan(payload, profile=args.profile)
        verifier.plan_contract.validate_authority_variables(
            payload,
            revoke_secret_accessor=args.profile == "iam-revoke",
            release_authority=authority,
        )
        if _profile_uses_runtime_transition(args.profile):
            startup_fd, startup_info, startup_raw = _safe_member(
                transaction_fd, "startup-script.sh", maximum=256 * 1024
            )
            descriptors.append(startup_fd)
            if (
                _startup_sha(payload) != manifest["startup_script_sha256"]
                or _startup_bytes(payload) != startup_raw
                or not _matches(manifest["startup_script"], startup_info, startup_raw)
            ):
                raise TransactionError("startup render differs from sealed transaction")
        elif (
            manifest["startup_script_sha256"] is not None
            or manifest["startup_script"] is not None
        ):
            raise TransactionError("adoption transaction contains startup metadata")
        _revalidate_gcloud(gcloud_identity)
        if _profile_uses_runtime_transition(args.profile):
            _edge(
                "pre-transition",
                args.expected_account,
                runtime["gcloud_config"],
                environment,
                runtime["gcloud"],
            )
        authority_verification = _verify_authority_live(
            authority,
            runtime["gh_config"],
            runtime["gcloud_config"],
            gh=runtime["gh"],
            gcloud=runtime["gcloud"],
            python=runtime["python"],
            python_framework=runtime["python_framework"],
        )
        state_before = _terraform_state_identity(
            _private_tool(private_tool_identity), environment, module
        )
        _require_fresh_manifest(manifest)
        intent = {
            "schema_version": 1,
            "state": "apply-started",
            "profile": args.profile,
            "git_head": head,
            "manifest_sha256": _sha(manifest_raw),
            "plan_sha256": manifest["plan"]["sha256"],
            "plan_json_sha256": manifest["plan_json_sha256"],
            "startup_script_sha256": manifest["startup_script_sha256"],
            "release_authority_sha256": manifest["release_authority"]["sha256"],
            "operator": operator,
            "tool_sha256": tool_identity["sha256"],
            "authority_live_verification": authority_verification,
            "terraform_state_before": state_before,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        intent_raw, _intent_identity = _write_state_member(
            transaction_fd, "apply-intent.json", intent
        )
        os.lseek(plan_fd, 0, os.SEEK_SET)
        _attest_provider_installation(apply_data, provider_identity)
        try:
            output = _run(
                [
                    str(_private_tool(private_tool_identity)),
                    "-chdir=" + str(module),
                    "apply",
                    "-input=false",
                    "-auto-approve",
                    f"/dev/fd/{plan_fd}",
                ],
                cwd=ROOT,
                env=environment,
                timeout=1200,
                pass_fds=(plan_fd,),
            )
            _attest_provider_installation(apply_data, provider_identity)
        except (OSError, TransactionError) as error:
            failure = {
                "schema_version": 1,
                "state": "apply-failed",
                "outcome": "INDETERMINATE",
                "intent_sha256": _sha(intent_raw),
                "timed_out": isinstance(error, CommandError) and error.timed_out,
                "returncode": error.returncode
                if isinstance(error, CommandError)
                else None,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_state_member(transaction_fd, "apply-failure.json", failure)
            raise TransactionError(
                "apply outcome is INDETERMINATE; reconcile cloud state before a fresh plan"
            ) from None
        result = {
            "schema_version": 1,
            "state": "apply-exited-zero",
            "intent_sha256": _sha(intent_raw),
            "output_sha256": _sha(output),
            "output_size_bytes": len(output),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_state_member(transaction_fd, "apply-result.json", result)
        return _finish_postcheck(args, transaction_fd, manifest_raw, manifest)
    finally:
        sessions.close()
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            _revalidate_transaction_path(args.transaction, transaction_info)
        finally:
            os.close(transaction_fd)


def recover(args: argparse.Namespace) -> int:
    transaction_fd, transaction_info = _open_transaction(
        args.transaction, expected_modes={0o500, 0o700}
    )
    manifest_fd = -1
    try:
        manifest_fd, _info, manifest_raw, manifest = _manifest(transaction_fd)
        if manifest["profile"] != args.profile:
            raise TransactionError("transaction profile differs")
        state = _existing_apply_state(transaction_fd, manifest, manifest_raw)
        if state == "complete":
            print("OMEGA_GCP_TERRAFORM_TRANSACTION\tALREADY_APPLIED_AND_POSTCHECKED")
            return 0
        if state == "postcheck":
            return _finish_postcheck(args, transaction_fd, manifest_raw, manifest)
        if state == "new":
            raise TransactionError("transaction has no apply intent to recover")
        raise TransactionError(
            "apply outcome is INDETERMINATE; automated recovery is prohibited"
        )
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        try:
            _revalidate_transaction_path(args.transaction, transaction_info)
        finally:
            os.close(transaction_fd)


def status(args: argparse.Namespace) -> int:
    transaction_fd, transaction_info = _open_transaction(
        args.transaction, expected_modes={0o500, 0o700}
    )
    manifest_fd = -1
    try:
        manifest_fd, _info, manifest_raw, manifest = _manifest(transaction_fd)
        if manifest["profile"] != args.profile:
            raise TransactionError("transaction profile differs")
        state = _existing_apply_state(transaction_fd, manifest, manifest_raw)
        print(f"OMEGA_GCP_TERRAFORM_TRANSACTION\tSTATUS\t{state.upper()}")
        return 0
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        try:
            _revalidate_transaction_path(args.transaction, transaction_info)
        finally:
            os.close(transaction_fd)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="action", required=True)
    for action in ("plan", "apply"):
        command = sub.add_parser(action)
        command.add_argument("--transaction", type=Path, required=True)
        command.add_argument("--tfvars", type=Path, required=True)
        if action == "plan":
            command.add_argument("--release-authority", type=Path, required=True)
        command.add_argument("--tofu", type=Path, required=True)
        command.add_argument("--gh-config", type=Path, required=True)
        command.add_argument("--gcloud-config", type=Path, required=True)
        command.add_argument("--expected-account", required=True)
        if action == "apply":
            command.add_argument(
                "--iam-controller-transaction",
                type=Path,
                help=argparse.SUPPRESS,
            )
        command.add_argument(
            "--profile",
            choices=("foundation", "iam-revoke", "secret-adoption"),
            default="foundation",
        )
        command.set_defaults(handler=create if action == "plan" else apply)
    recover_command = sub.add_parser("recover")
    recover_command.add_argument("--transaction", type=Path, required=True)
    recover_command.add_argument("--gh-config", type=Path, required=True)
    recover_command.add_argument("--gcloud-config", type=Path, required=True)
    recover_command.add_argument("--expected-account", required=True)
    recover_command.add_argument(
        "--profile",
        choices=("foundation", "secret-adoption"),
        default="foundation",
    )
    recover_command.set_defaults(handler=recover)
    status_command = sub.add_parser("status")
    status_command.add_argument("--transaction", type=Path, required=True)
    status_command.add_argument(
        "--profile",
        choices=("foundation", "iam-revoke", "secret-adoption"),
        default="foundation",
    )
    status_command.set_defaults(handler=status)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    expected_account = getattr(args, "expected_account", None)
    if expected_account is not None and not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", expected_account
    ):
        raise TransactionError("expected operator account is invalid")
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TransactionError, verifier.PlanError) as error:
        print(f"OMEGA_GCP_TERRAFORM_TRANSACTION\tFAIL\t{error}", file=sys.stderr)
        raise SystemExit(1) from None
