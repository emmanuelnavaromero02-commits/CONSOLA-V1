#!/usr/bin/python3 -I
"""Reversibly remove the legacy project-wide Secret Manager accessor grant.

This controller deliberately wraps, rather than weakens, the sealed Terraform
transaction.  It adds the effective-IAM proof and the compensating transaction
that a bare Terraform delete cannot provide.  It never reads a secret value.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_TRANSACTION = Path(__file__).with_name("terraform_transaction.py")
VERIFY_SECRET_ACCESS = Path(__file__).with_name("verify-secret-access.sh")
PROJECT = "project-dd5ba7fa-374c-4554-ae6"
ENVIRONMENT = "staging"
ZONE = "us-central1-a"
INSTANCE = "omega-staging-app"
ROLE = "roles/secretmanager.secretAccessor"
SERVICE_ACCOUNT_EMAIL = f"omega-staging-app@{PROJECT}.iam.gserviceaccount.com"
MEMBER = f"serviceAccount:{SERVICE_ACCOUNT_EMAIL}"
RESOURCE_GRANTS = (
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
    "ghcr_pull_credentials",
)
FORBIDDEN_RESOURCE_COUNT = 64
MAX_FILE = 64 * 1024 * 1024
MAX_POLICY = 4 * 1024 * 1024
MAX_PLAN_AGE_SECONDS = 6 * 60 * 60
EVENT_PATTERN = re.compile(r"([0-9]{8})-([a-z][a-z0-9-]{0,63})\.json")
EVENT_TEMP_PATTERN = re.compile(
    r"\.event-tmp-([0-9]{8})-([a-z][a-z0-9-]{0,63})-([0-9a-f]{16})"
)
ACCOUNT_PATTERN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SOURCE_MEMBERS = (
    "scripts/gcp/iam_revoke_transaction.py",
    "scripts/gcp/terraform_transaction.py",
    "scripts/gcp/terraform_plan_contract.py",
    "scripts/gcp/verify_terraform_plan.py",
    "scripts/gcp/verify-secret-access.sh",
    "infra/terraform-gcp/iam.tf",
    "infra/terraform-gcp/locals.tf",
    "infra/terraform-gcp/variables.tf",
)
MANIFEST_KEYS = {
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
EVENT_KEYS = {
    "schema_version",
    "sequence",
    "event",
    "manifest_sha256",
    "occurred_at",
    "details",
}
TERMINAL_STATES = {
    "applied-and-postchecked",
    "failed-restored",
}


class IAMTransactionError(RuntimeError):
    """A closed, non-sensitive transaction failure."""


class IAMTransactionInterrupted(IAMTransactionError):
    """A catchable interruption that must trigger compensation."""

    def __init__(self, signal_name: str) -> None:
        super().__init__("IAM revoke transaction was interrupted")
        self.signal_name = signal_name


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise IAMTransactionError("JSON contains duplicate object keys")
        value[key] = item
    return value


def _strict_json(raw: bytes, label: str) -> Any:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IAMTransactionError(f"{label} is not strict JSON") from error
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("+00:00"):
        raise IAMTransactionError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise IAMTransactionError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo != timezone.utc:
        raise IAMTransactionError(f"{label} timestamp is not UTC")
    return parsed


def _require_fresh_manifest(manifest: dict[str, Any]) -> None:
    age = (
        datetime.now(timezone.utc) - _parse_time(manifest.get("created_at"), "manifest")
    ).total_seconds()
    if age < 0 or age > MAX_PLAN_AGE_SECONDS:
        raise IAMTransactionError("sealed IAM revoke plan is stale")


def _identity(
    info: os.stat_result, raw: bytes, *, path: Path | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "sha256": _sha256(raw),
    }
    if path is not None:
        result["path"] = str(path)
    return result


def _directory_identity(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink():
        raise IAMTransactionError(f"{label} directory path is unsafe")
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
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise IAMTransactionError(f"{label} directory ownership/mode differs")
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise IAMTransactionError(f"{label} directory pathname was substituted")
        return {
            "path": str(path),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid,
        }
    finally:
        os.close(descriptor)


def _safe_file(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_FILE,
    executable: bool = False,
) -> tuple[int, os.stat_result, bytes]:
    if not path.is_absolute() or path.is_symlink():
        raise IAMTransactionError(f"{label} path is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        allowed_owners = {0, os.geteuid()} if executable else {os.geteuid()}
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in allowed_owners
            or before.st_nlink != 1
            or mode & 0o022
            or (executable and not mode & 0o100)
            or not 1 <= before.st_size <= maximum
        ):
            raise IAMTransactionError(f"{label} identity/mode is unsafe")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        before_id = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_id = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(raw) != before.st_size or before_id != after_id:
            raise IAMTransactionError(f"{label} changed during read")
        return descriptor, before, bytes(raw)
    except Exception:
        os.close(descriptor)
        raise


def _open_transaction(path: Path, modes: set[int]) -> tuple[int, os.stat_result]:
    if not path.is_absolute() or path.is_symlink():
        raise IAMTransactionError("transaction directory path is unsafe")
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
            or stat.S_IMODE(info.st_mode) not in modes
        ):
            raise IAMTransactionError("transaction directory identity/mode differs")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _revalidate_transaction(path, info)
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _revalidate_transaction(path: Path, expected: os.stat_result) -> None:
    current = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        raise IAMTransactionError("transaction directory pathname was substituted")


def _write_member(
    directory_fd: int, name: str, raw: bytes, mode: int = 0o400
) -> dict[str, Any]:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is None:
        raise IAMTransactionError("transaction member name is invalid")
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
        pending = memoryview(raw)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise IAMTransactionError("durable receipt write made no progress")
            pending = pending[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = bytearray()
        while len(readback) < len(raw):
            chunk = os.read(descriptor, len(raw) - len(readback))
            if not chunk:
                break
            readback.extend(chunk)
        after = os.fstat(descriptor)
        if bytes(readback) != raw or (
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
            raise IAMTransactionError("durable receipt read-back differs")
        return _identity(info, raw)
    finally:
        os.close(descriptor)
        os.fsync(directory_fd)


def _read_member(
    directory_fd: int, name: str, *, maximum: int = MAX_FILE
) -> tuple[os.stat_result, bytes]:
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
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o222
            or not 1 <= before.st_size <= maximum
        ):
            raise IAMTransactionError("sealed transaction member identity differs")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
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
            raise IAMTransactionError("sealed transaction member changed during read")
        return before, raw
    finally:
        os.close(descriptor)


def _create_directory(parent_fd: int, name: str) -> tuple[int, os.stat_result]:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) is None:
        raise IAMTransactionError("private directory name is invalid")
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise IAMTransactionError("private transaction directory identity differs")
    os.fsync(parent_fd)
    return descriptor, info


def _require_transaction_members(transaction_fd: int) -> None:
    if set(os.listdir(transaction_fd)) != {
        "events",
        "manifest.json",
        "matrix-verifier.sh",
        "terraform",
    }:
        raise IAMTransactionError("IAM revoke transaction member inventory differs")


def _open_directory(
    parent_fd: int, name: str, modes: set[int]
) -> tuple[int, os.stat_result]:
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in modes:
            raise IAMTransactionError("private transaction directory identity differs")
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _clean_env(config: Path) -> dict[str, str]:
    return {
        "HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "CLOUDSDK_CONFIG": str(config),
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
        "CHECKPOINT_DISABLE": "1",
    }


_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None


def _terminate_active_process() -> None:
    process = _ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    input_bytes: bytes | None = None,
    maximum: int = MAX_FILE,
    label: str,
) -> bytes:
    global _ACTIVE_PROCESS
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        _ACTIVE_PROCESS = process
        try:
            stdout, stderr = process.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_active_process()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                process.wait(timeout=10)
            raise IAMTransactionError(f"{label} timed out") from None
        except BaseException:
            _terminate_active_process()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                process.wait(timeout=10)
            raise
        if len(stdout) > maximum or len(stderr) > maximum:
            raise IAMTransactionError(f"{label} output exceeded its bound")
        if process.returncode != 0:
            raise IAMTransactionError(f"{label} failed")
        return stdout
    finally:
        _ACTIVE_PROCESS = None


def _executable_identity(name: str) -> tuple[Path, dict[str, Any]]:
    selected = shutil.which(name, path=_clean_env(Path("/var/empty"))["PATH"])
    if selected is None:
        raise IAMTransactionError(f"required executable is unavailable: {name}")
    path = Path(selected).resolve(strict=True)
    descriptor, info, raw = _safe_file(path, label=name, executable=True)
    os.close(descriptor)
    return path, _identity(info, raw, path=path)


def _matches_file_identity(path: Path, expected: dict[str, Any], label: str) -> None:
    descriptor, info, raw = _safe_file(path, label=label, executable=True)
    os.close(descriptor)
    if _identity(info, raw, path=path) != expected:
        raise IAMTransactionError(f"{label} identity differs from sealed transaction")


def _gcloud(
    binary: Path,
    identity: dict[str, Any],
    config: Path,
    arguments: list[str],
    *,
    label: str,
    timeout: int = 60,
    input_bytes: bytes | None = None,
    maximum: int = MAX_POLICY,
) -> bytes:
    _matches_file_identity(binary, identity, "gcloud")
    return _run(
        [str(binary), *arguments],
        cwd=ROOT,
        env=_clean_env(config),
        timeout=timeout,
        input_bytes=input_bytes,
        maximum=maximum,
        label=label,
    )


def _operator(
    binary: Path, identity: dict[str, Any], config: Path, expected_account: str
) -> dict[str, Any]:
    active_raw = _gcloud(
        binary,
        identity,
        config,
        ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        label="active gcloud identity query",
        maximum=4096,
    )
    try:
        active = active_raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise IAMTransactionError("active gcloud identity is malformed") from error
    if active != [expected_account]:
        raise IAMTransactionError(
            "active gcloud identity differs from expected operator"
        )
    project_raw = _gcloud(
        binary,
        identity,
        config,
        ["config", "get-value", "project", "--quiet"],
        label="active gcloud project query",
        maximum=4096,
    )
    try:
        project = project_raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise IAMTransactionError("active gcloud project is malformed") from error
    if project != PROJECT:
        raise IAMTransactionError(
            "active gcloud project differs from canonical project"
        )
    return {"account": expected_account, "project": PROJECT}


def _policy(
    binary: Path,
    identity: dict[str, Any],
    config: Path,
    arguments: list[str],
    label: str,
) -> dict[str, Any]:
    raw = _gcloud(
        binary,
        identity,
        config,
        [*arguments, "--format=json"],
        label=label,
        maximum=MAX_POLICY,
    )
    value = _strict_json(raw, label)
    if not isinstance(value, dict):
        raise IAMTransactionError(f"{label} shape differs")
    bindings = value.get("bindings")
    if not isinstance(bindings, list) or any(
        not isinstance(item, dict) for item in bindings
    ):
        raise IAMTransactionError(f"{label} binding shape differs")
    return value


def _unconditional_binding_count(policy: dict[str, Any], role: str, member: str) -> int:
    count = 0
    for binding in policy["bindings"]:
        members = binding.get("members")
        if not isinstance(members, list) or any(
            not isinstance(item, str) for item in members
        ):
            raise IAMTransactionError("IAM policy member shape differs")
        if binding.get("role") == role and member in members:
            if set(binding) != {"members", "role"}:
                raise IAMTransactionError(
                    "matching IAM grant is conditional or ambiguous"
                )
            count += 1
    return count


def _broad_binding(binary: Path, identity: dict[str, Any], config: Path) -> bool:
    policy = _policy(
        binary,
        identity,
        config,
        ["projects", "get-iam-policy", PROJECT],
        "project IAM policy query",
    )
    count = _unconditional_binding_count(policy, ROLE, MEMBER)
    if count > 1:
        raise IAMTransactionError("project IAM grant is duplicated")
    return count == 1


def _resource_grants(binary: Path, identity: dict[str, Any], config: Path) -> list[str]:
    proven: list[str] = []
    for suffix in RESOURCE_GRANTS:
        secret_id = f"omega-{ENVIRONMENT}-{suffix}"
        policy = _policy(
            binary,
            identity,
            config,
            ["secrets", "get-iam-policy", secret_id, f"--project={PROJECT}"],
            "resource IAM policy query",
        )
        if _unconditional_binding_count(policy, ROLE, MEMBER) != 1:
            raise IAMTransactionError("required resource IAM grant differs")
        proven.append(suffix)
    if tuple(proven) != RESOURCE_GRANTS:
        raise IAMTransactionError("resource IAM grant inventory differs")
    return proven


def _matrix(
    binary: Path,
    identity: dict[str, Any],
    config: Path,
    verifier_raw: bytes,
    stage: str,
) -> dict[str, Any]:
    if stage not in {"grants", "revoke"}:
        raise IAMTransactionError("effective-IAM matrix stage is invalid")
    raw = _gcloud(
        binary,
        identity,
        config,
        [
            "compute",
            "ssh",
            INSTANCE,
            f"--project={PROJECT}",
            f"--zone={ZONE}",
            "--tunnel-through-iap",
            "--quiet",
            "--ssh-flag=-oBatchMode=yes",
            "--ssh-flag=-oConnectTimeout=20",
            "--command=sudo -n /bin/bash -p -s -- " f"{PROJECT} {ENVIRONMENT} {stage}",
        ],
        label="effective-IAM matrix probe",
        timeout=300,
        input_bytes=verifier_raw,
        maximum=16384,
    )
    value = _strict_json(raw, "effective-IAM matrix result")
    expected = {
        "status": "PASS",
        "stage": stage,
        "resource_grants": len(RESOURCE_GRANTS),
        "forbidden_resources_checked": FORBIDDEN_RESOURCE_COUNT
        if stage == "revoke"
        else 0,
        "forbidden_access": 0,
        "canary_denied": stage == "revoke",
        "secret_values_read": False,
    }
    if value != expected:
        raise IAMTransactionError("effective-IAM matrix result differs")
    return value


def _precheck(
    binary: Path,
    identity: dict[str, Any],
    config: Path,
    verifier_raw: bytes,
) -> dict[str, Any]:
    if not _broad_binding(binary, identity, config):
        raise IAMTransactionError("legacy project IAM grant is absent before revoke")
    grants = _resource_grants(binary, identity, config)
    matrix = _matrix(binary, identity, config, verifier_raw, "grants")
    if not _broad_binding(binary, identity, config):
        raise IAMTransactionError("legacy project IAM grant changed during precheck")
    return {
        "broad_project_grant": True,
        "resource_grants": grants,
        "matrix": matrix,
    }


def _postcheck(
    binary: Path,
    identity: dict[str, Any],
    config: Path,
    verifier_raw: bytes,
) -> dict[str, Any]:
    if _broad_binding(binary, identity, config):
        raise IAMTransactionError("legacy project IAM grant remains after revoke")
    grants = _resource_grants(binary, identity, config)
    matrix = _matrix(binary, identity, config, verifier_raw, "revoke")
    if _broad_binding(binary, identity, config):
        raise IAMTransactionError("legacy project IAM grant changed during postcheck")
    return {
        "broad_project_grant": False,
        "resource_grants": grants,
        "matrix": matrix,
    }


def _restore(
    binary: Path,
    identity: dict[str, Any],
    config: Path,
    verifier_raw: bytes,
    expected_account: str,
) -> dict[str, Any]:
    _operator(binary, identity, config, expected_account)
    _gcloud(
        binary,
        identity,
        config,
        [
            f"--account={expected_account}",
            f"--project={PROJECT}",
            "projects",
            "add-iam-policy-binding",
            PROJECT,
            f"--member={MEMBER}",
            f"--role={ROLE}",
            "--condition=None",
            "--quiet",
            "--format=none",
        ],
        label="project IAM compensation",
        timeout=180,
        maximum=4096,
    )
    if not _broad_binding(binary, identity, config):
        raise IAMTransactionError("compensating project IAM grant was not restored")
    grants = _resource_grants(binary, identity, config)
    matrix = _matrix(binary, identity, config, verifier_raw, "grants")
    if not _broad_binding(binary, identity, config):
        raise IAMTransactionError(
            "compensating project IAM grant did not remain stable"
        )
    return {
        "broad_project_grant": True,
        "resource_grants": grants,
        "matrix": matrix,
        "terraform_state": "requires-new-sealed-reconciliation",
    }


def _git_output(arguments: list[str], maximum: int = MAX_FILE) -> bytes:
    return _run(
        ["/usr/bin/git", *arguments],
        cwd=ROOT,
        env={
            "HOME": "/var/empty",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        },
        timeout=60,
        maximum=maximum,
        label="Git source identity query",
    )


def _source_identity() -> dict[str, Any]:
    head_raw = _git_output(["rev-parse", "--verify", "HEAD"], 4096)
    try:
        head = head_raw.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise IAMTransactionError("Git HEAD is malformed") from error
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise IAMTransactionError("Git HEAD is malformed")
    status = _git_output(["status", "--porcelain=v1", "--untracked-files=no"])
    if status:
        raise IAMTransactionError("tracked controller source is not clean")
    files: dict[str, str] = {}
    for member in SOURCE_MEMBERS:
        committed = _git_output(["show", f"{head}:{member}"])
        path = ROOT / member
        descriptor, _info, working = _safe_file(path, label="controller source")
        os.close(descriptor)
        if working != committed:
            raise IAMTransactionError("controller source differs from Git HEAD")
        files[member] = _sha256(committed)
    return {"git_head": head, "files": files}


def _terraform_summary(terraform_fd: int) -> dict[str, Any]:
    manifest_info, manifest_raw = _read_member(terraform_fd, "manifest.json")
    manifest = _strict_json(manifest_raw, "sealed Terraform manifest")
    if not isinstance(manifest, dict) or manifest.get("profile") != "iam-revoke":
        raise IAMTransactionError("sealed Terraform profile is not iam-revoke")
    plan = manifest.get("plan")
    plan_json_sha = manifest.get("plan_json_sha256")
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("sha256"), str)
        or SHA256_PATTERN.fullmatch(plan["sha256"]) is None
        or not isinstance(plan_json_sha, str)
        or SHA256_PATTERN.fullmatch(plan_json_sha) is None
    ):
        raise IAMTransactionError("sealed Terraform plan identity is malformed")
    _plan_info, plan_raw = _read_member(
        terraform_fd, "plan.bin", maximum=512 * 1024 * 1024
    )
    if _sha256(plan_raw) != plan["sha256"]:
        raise IAMTransactionError("sealed Terraform plan bytes differ")
    return {
        "directory": "terraform",
        "manifest_sha256": _sha256(manifest_raw),
        "manifest_inode": manifest_info.st_ino,
        "plan_sha256": plan["sha256"],
        "plan_json_sha256": plan_json_sha,
    }


def _run_terraform_plan(args: argparse.Namespace, path: Path) -> None:
    command = [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        str(TERRAFORM_TRANSACTION),
        "plan",
        "--profile",
        "iam-revoke",
        "--transaction",
        str(path),
        "--tfvars",
        str(args.tfvars),
        "--release-authority",
        str(args.release_authority),
        "--tofu",
        str(args.tofu),
        "--gh-config",
        str(args.gh_config),
        "--gcloud-config",
        str(args.gcloud_config),
        "--expected-account",
        args.expected_account,
    ]
    _run(
        command,
        cwd=ROOT,
        env=_clean_env(args.gcloud_config),
        timeout=900,
        label="sealed Terraform iam-revoke plan",
    )


def _run_terraform_apply(args: argparse.Namespace, path: Path) -> None:
    controller_root = path / "control-bundle"
    controller = controller_root / "scripts/gcp/terraform_transaction.py"
    command = [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        str(controller),
        "apply",
        "--profile",
        "iam-revoke",
        "--iam-controller-transaction",
        str(args.transaction),
        "--transaction",
        str(path),
        "--tfvars",
        str(args.tfvars),
        "--tofu",
        str(args.tofu),
        "--gh-config",
        str(args.gh_config),
        "--gcloud-config",
        str(args.gcloud_config),
        "--expected-account",
        args.expected_account,
    ]
    environment = _clean_env(args.gcloud_config)
    environment.update(
        {
            "OMEGA_SEALED_CONTROLLER_ROOT": str(controller_root),
            "OMEGA_SOURCE_ROOT": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        }
    )
    _run(
        command,
        cwd=ROOT,
        env=environment,
        timeout=1500,
        label="sealed Terraform iam-revoke apply",
    )


def _input_identity(
    path: Path, label: str, *, executable: bool = False
) -> dict[str, Any]:
    descriptor, info, raw = _safe_file(path, label=label, executable=executable)
    os.close(descriptor)
    return _identity(info, raw, path=path)


def _validate_manifest(value: Any, raw: bytes) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS:
        raise IAMTransactionError("IAM revoke manifest shape differs")
    if raw != _canonical(value):
        raise IAMTransactionError("IAM revoke manifest is not canonical JSON")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["profile"] != "iam-revoke"
        or value["state"] != "sealed"
        or value["project"] != PROJECT
        or value["environment"] != ENVIRONMENT
        or value["zone"] != ZONE
        or value["instance"] != INSTANCE
        or value["role"] != ROLE
        or value["member"] != MEMBER
        or value["resource_grants"] != list(RESOURCE_GRANTS)
        or type(value["forbidden_resource_count"]) is not int
        or value["forbidden_resource_count"] != FORBIDDEN_RESOURCE_COUNT
    ):
        raise IAMTransactionError("IAM revoke manifest authority differs")
    _parse_time(value["created_at"], "manifest")
    if not isinstance(value["source"], dict) or set(value["source"]) != {
        "git_head",
        "files",
    }:
        raise IAMTransactionError("IAM revoke manifest source shape differs")
    source = value["source"]
    if (
        not isinstance(source["git_head"], str)
        or re.fullmatch(r"[0-9a-f]{40}", source["git_head"]) is None
        or not isinstance(source["files"], dict)
        or set(source["files"]) != set(SOURCE_MEMBERS)
        or any(
            not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None
            for digest in source["files"].values()
        )
    ):
        raise IAMTransactionError("IAM revoke manifest source identity differs")
    if not isinstance(value["operator"], dict) or set(value["operator"]) != {
        "account",
        "project",
        "gcloud",
        "gcloud_config",
    }:
        raise IAMTransactionError("IAM revoke manifest operator shape differs")
    if not isinstance(value["inputs"], dict) or set(value["inputs"]) != {
        "tfvars",
        "release_authority",
        "tofu",
        "gh_config",
    }:
        raise IAMTransactionError("IAM revoke manifest input shape differs")
    operator = value["operator"]
    if (
        not isinstance(operator["account"], str)
        or ACCOUNT_PATTERN.fullmatch(operator["account"]) is None
        or operator["project"] != PROJECT
    ):
        raise IAMTransactionError("IAM revoke manifest operator authority differs")
    _validate_file_identity(operator["gcloud"], executable=True)
    _validate_directory_identity(operator["gcloud_config"])
    inputs = value["inputs"]
    _validate_file_identity(inputs["tfvars"], executable=False)
    _validate_file_identity(inputs["release_authority"], executable=False)
    _validate_file_identity(inputs["tofu"], executable=True)
    _validate_directory_identity(inputs["gh_config"])
    if not isinstance(value["matrix_verifier"], dict):
        raise IAMTransactionError("IAM revoke verifier identity differs")
    _validate_member_identity(value["matrix_verifier"], "matrix-verifier.sh")
    if not isinstance(value["terraform"], dict) or set(value["terraform"]) != {
        "directory",
        "manifest_sha256",
        "manifest_inode",
        "plan_sha256",
        "plan_json_sha256",
    }:
        raise IAMTransactionError("IAM revoke Terraform identity differs")
    terraform = value["terraform"]
    if (
        terraform["directory"] != "terraform"
        or type(terraform["manifest_inode"]) is not int
        or terraform["manifest_inode"] <= 0
        or any(
            not isinstance(terraform[name], str)
            or SHA256_PATTERN.fullmatch(terraform[name]) is None
            for name in ("manifest_sha256", "plan_sha256", "plan_json_sha256")
        )
    ):
        raise IAMTransactionError("IAM revoke Terraform authority differs")
    if not isinstance(value["precheck"], dict) or value["precheck"] != {
        "broad_project_grant": True,
        "resource_grants": list(RESOURCE_GRANTS),
        "matrix": {
            "status": "PASS",
            "stage": "grants",
            "resource_grants": len(RESOURCE_GRANTS),
            "forbidden_resources_checked": 0,
            "forbidden_access": 0,
            "canary_denied": False,
            "secret_values_read": False,
        },
    }:
        raise IAMTransactionError("IAM revoke precheck differs")
    return value


def _validate_file_identity(value: Any, *, executable: bool) -> None:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "device",
        "inode",
        "mode",
        "size",
        "mtime_ns",
        "sha256",
    }:
        raise IAMTransactionError("sealed file identity shape differs")
    if (
        not isinstance(value["path"], str)
        or not Path(value["path"]).is_absolute()
        or any(
            type(value[name]) is not int or value[name] < 0
            for name in ("device", "inode", "mode", "size", "mtime_ns")
        )
        or value["inode"] == 0
        or value["size"] == 0
        or value["mode"] & 0o022
        or (executable and not value["mode"] & 0o100)
        or not isinstance(value["sha256"], str)
        or SHA256_PATTERN.fullmatch(value["sha256"]) is None
    ):
        raise IAMTransactionError("sealed file identity differs")


def _validate_directory_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "device",
        "inode",
        "mode",
        "uid",
    }:
        raise IAMTransactionError("sealed directory identity shape differs")
    if (
        not isinstance(value["path"], str)
        or not Path(value["path"]).is_absolute()
        or any(
            type(value[name]) is not int or value[name] < 0
            for name in ("device", "inode", "mode", "uid")
        )
        or value["inode"] == 0
        or value["mode"] & 0o022
    ):
        raise IAMTransactionError("sealed directory identity differs")


def _validate_member_identity(value: Any, member: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "member",
        "device",
        "inode",
        "mode",
        "size",
        "mtime_ns",
        "sha256",
    }:
        raise IAMTransactionError("sealed member identity shape differs")
    if (
        value["member"] != member
        or any(
            type(value[name]) is not int or value[name] < 0
            for name in ("device", "inode", "mode", "size", "mtime_ns")
        )
        or value["inode"] == 0
        or value["size"] == 0
        or value["mode"] != 0o400
        or not isinstance(value["sha256"], str)
        or SHA256_PATTERN.fullmatch(value["sha256"]) is None
    ):
        raise IAMTransactionError("sealed member identity differs")


def _load_manifest(transaction_fd: int) -> tuple[os.stat_result, bytes, dict[str, Any]]:
    info, raw = _read_member(transaction_fd, "manifest.json")
    return info, raw, _validate_manifest(_strict_json(raw, "IAM revoke manifest"), raw)


def _validate_event(value: Any, raw: bytes, expected_sequence: int) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != EVENT_KEYS
        or raw != _canonical(value)
    ):
        raise IAMTransactionError("IAM revoke event shape/canonical form differs")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["sequence"]) is not int
        or value["sequence"] != expected_sequence
        or not isinstance(value["event"], str)
        or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["event"]) is None
        or not isinstance(value["manifest_sha256"], str)
        or SHA256_PATTERN.fullmatch(value["manifest_sha256"]) is None
        or not isinstance(value["details"], dict)
    ):
        raise IAMTransactionError("IAM revoke event authority differs")
    _parse_time(value["occurred_at"], "event")
    event = value["event"]
    details = value["details"]
    allowed: dict[str, set[str]] = {
        "mutation-intent": {"state"},
        "delete-applied": {"state"},
        "postcheck-passed": {"result"},
        "compensation-started": {"trigger"},
        "compensation-verified": {"trigger", "result"},
        "compensation-failed": {"trigger", "state"},
        "terminal": {"state"},
    }
    if event not in allowed or set(details) != allowed[event]:
        raise IAMTransactionError("IAM revoke event detail shape differs")
    if event == "mutation-intent" and details["state"] != "mutation-authorized":
        raise IAMTransactionError("IAM revoke intent differs")
    if event == "delete-applied" and details["state"] != "delete-command-complete":
        raise IAMTransactionError("IAM revoke delete receipt differs")
    if event in {"compensation-started", "compensation-verified"} and details[
        "trigger"
    ] not in {
        "apply-failure",
        "postcheck-failure",
        "signal",
        "interrupted-recovery",
    }:
        raise IAMTransactionError("IAM revoke compensation trigger differs")
    if event == "compensation-failed" and (
        details["trigger"]
        not in {
            "apply-failure",
            "postcheck-failure",
            "signal",
            "interrupted-recovery",
        }
        or details["state"] != "restore-unverified"
    ):
        raise IAMTransactionError("IAM revoke compensation failure differs")
    if event == "terminal" and details["state"] not in TERMINAL_STATES:
        raise IAMTransactionError("IAM revoke terminal state differs")
    if event == "postcheck-passed":
        _validate_check_result(details["result"], stage="revoke", broad=False)
    if event == "compensation-verified":
        result = details["result"]
        _validate_check_result(result, stage="grants", broad=True, compensated=True)
    return value


def _validate_check_result(
    value: Any,
    *,
    stage: str,
    broad: bool,
    compensated: bool = False,
) -> None:
    expected_keys = {"broad_project_grant", "resource_grants", "matrix"}
    if compensated:
        expected_keys.add("terraform_state")
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise IAMTransactionError("IAM matrix receipt shape differs")
    if value["broad_project_grant"] is not broad or value["resource_grants"] != list(
        RESOURCE_GRANTS
    ):
        raise IAMTransactionError("IAM matrix receipt authority differs")
    if compensated and value["terraform_state"] != "requires-new-sealed-reconciliation":
        raise IAMTransactionError("IAM compensation state receipt differs")
    expected_matrix = {
        "status": "PASS",
        "stage": stage,
        "resource_grants": len(RESOURCE_GRANTS),
        "forbidden_resources_checked": (
            FORBIDDEN_RESOURCE_COUNT if stage == "revoke" else 0
        ),
        "forbidden_access": 0,
        "canary_denied": stage == "revoke",
        "secret_values_read": False,
    }
    if value["matrix"] != expected_matrix:
        raise IAMTransactionError("effective-IAM matrix receipt differs")


def _events(
    events_fd: int, expected_manifest_sha256: str | None = None
) -> list[dict[str, Any]]:
    _clean_event_residues(events_fd)
    names = sorted(os.listdir(events_fd))
    values: list[dict[str, Any]] = []
    for expected, name in enumerate(names, start=1):
        match = EVENT_PATTERN.fullmatch(name)
        if match is None or int(match.group(1)) != expected:
            raise IAMTransactionError("IAM revoke event sequence differs")
        _info, raw = _read_member(events_fd, name)
        value = _validate_event(_strict_json(raw, "IAM revoke event"), raw, expected)
        if value["event"] != match.group(2):
            raise IAMTransactionError("IAM revoke event filename differs")
        if (
            expected_manifest_sha256 is not None
            and value["manifest_sha256"] != expected_manifest_sha256
        ):
            raise IAMTransactionError("IAM revoke event belongs to another manifest")
        values.append(value)
    terminals = [item for item in values if item["event"] == "terminal"]
    if len(terminals) > 1 or (terminals and values[-1] != terminals[0]):
        raise IAMTransactionError("IAM revoke terminal event ordering differs")
    _validate_event_history(values)
    return values


def _validate_event_history(values: list[dict[str, Any]]) -> None:
    if not values:
        return
    names = [item["event"] for item in values]
    if names[0] != "mutation-intent" or names.count("mutation-intent") != 1:
        raise IAMTransactionError("IAM revoke intent ordering differs")
    if names.count("delete-applied") > 1 or names.count("postcheck-passed") > 1:
        raise IAMTransactionError("IAM revoke mutation receipt is duplicated")
    if "postcheck-passed" in names and (
        "delete-applied" not in names
        or names.index("postcheck-passed") != names.index("delete-applied") + 1
    ):
        raise IAMTransactionError("IAM revoke postcheck ordering differs")
    if "terminal" in names:
        terminal = values[-1]["details"]["state"]
        if terminal == "applied-and-postchecked" and names != [
            "mutation-intent",
            "delete-applied",
            "postcheck-passed",
            "terminal",
        ]:
            raise IAMTransactionError("IAM revoke success history differs")
        if terminal == "failed-restored" and names[-2] != "compensation-verified":
            raise IAMTransactionError("IAM revoke compensation history differs")
    compensation_open = False
    for name in names:
        if name == "compensation-started":
            if compensation_open:
                raise IAMTransactionError("IAM compensation attempt overlaps")
            compensation_open = True
        elif name in {"compensation-verified", "compensation-failed"}:
            if not compensation_open:
                raise IAMTransactionError("IAM compensation result lacks an attempt")
            compensation_open = False
    if names[-1] == "terminal" and compensation_open:
        raise IAMTransactionError("IAM compensation terminal is incomplete")


def _append_event(
    events_fd: int,
    existing: list[dict[str, Any]],
    manifest_sha: str,
    event: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    sequence = len(existing) + 1
    value = {
        "schema_version": 1,
        "sequence": sequence,
        "event": event,
        "manifest_sha256": manifest_sha,
        "occurred_at": _utc_now(),
        "details": details,
    }
    raw = _canonical(value)
    _validate_event(value, raw, sequence)
    try:
        _write_atomic_event(events_fd, f"{sequence:08d}-{event}.json", raw)
    except (OSError, IAMTransactionError):
        recovered = _events(events_fd, manifest_sha)
        if len(recovered) != sequence or recovered[-1] != value:
            raise
        existing[:] = recovered
        return value
    existing.append(value)
    return value


def _terminal_state(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event["event"] == "terminal":
            return event["details"]["state"]
    return None


def _seal_terminal(transaction_fd: int, events_fd: int) -> None:
    os.fchmod(events_fd, 0o500)
    os.fsync(events_fd)
    os.fchmod(transaction_fd, 0o500)
    os.fsync(transaction_fd)


def _has_intent(events: list[dict[str, Any]]) -> bool:
    return any(event["event"] == "mutation-intent" for event in events)


def _validate_runtime_identity(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    transaction_fd: int,
    terraform_fd: int,
) -> tuple[Path, dict[str, Any], bytes]:
    source = _source_identity()
    if source != manifest["source"]:
        raise IAMTransactionError("controller source differs from sealed transaction")
    gcloud, gcloud_identity = _executable_identity("gcloud")
    if gcloud_identity != manifest["operator"]["gcloud"]:
        raise IAMTransactionError("gcloud differs from sealed transaction")
    if (
        _directory_identity(args.gcloud_config, "gcloud config")
        != manifest["operator"]["gcloud_config"]
    ):
        raise IAMTransactionError("gcloud config differs from sealed transaction")
    if (
        _directory_identity(args.gh_config, "GitHub CLI config")
        != manifest["inputs"]["gh_config"]
    ):
        raise IAMTransactionError("GitHub CLI config differs from sealed transaction")
    if _input_identity(args.tfvars, "tfvars") != manifest["inputs"]["tfvars"]:
        raise IAMTransactionError("tfvars differs from sealed transaction")
    if (
        _input_identity(args.tofu, "OpenTofu", executable=True)
        != manifest["inputs"]["tofu"]
    ):
        raise IAMTransactionError("OpenTofu differs from sealed transaction")
    if _operator(
        gcloud, gcloud_identity, args.gcloud_config, args.expected_account
    ) != {
        "account": manifest["operator"]["account"],
        "project": PROJECT,
    }:
        raise IAMTransactionError("operator differs from sealed transaction")
    verifier_raw = _sealed_verifier(transaction_fd, manifest)
    if _terraform_summary(terraform_fd) != manifest["terraform"]:
        raise IAMTransactionError(
            "Terraform transaction differs from sealed transaction"
        )
    _require_fresh_manifest(manifest)
    return gcloud, gcloud_identity, verifier_raw


def _recovery_runtime_identity(
    args: argparse.Namespace, manifest: dict[str, Any], transaction_fd: int
) -> tuple[Path, dict[str, Any], bytes]:
    """Validate only the immutable authority needed to restore live access.

    Recovery intentionally does not depend on plan freshness, current Git, the
    Terraform state, or the OpenTofu binary.  A crash must not turn an expired
    plan or a subsequently updated checkout into a barrier to compensation.
    """
    gcloud, gcloud_identity = _executable_identity("gcloud")
    if gcloud_identity != manifest["operator"]["gcloud"]:
        raise IAMTransactionError("gcloud differs from sealed recovery authority")
    if (
        _directory_identity(args.gcloud_config, "gcloud config")
        != manifest["operator"]["gcloud_config"]
    ):
        raise IAMTransactionError(
            "gcloud config differs from sealed recovery authority"
        )
    operator = _operator(
        gcloud, gcloud_identity, args.gcloud_config, args.expected_account
    )
    if operator != {
        "account": manifest["operator"]["account"],
        "project": PROJECT,
    }:
        raise IAMTransactionError("operator differs from sealed recovery authority")
    verifier_raw = _sealed_verifier(transaction_fd, manifest)
    return gcloud, gcloud_identity, verifier_raw


def _sealed_verifier(transaction_fd: int, manifest: dict[str, Any]) -> bytes:
    info, raw = _read_member(transaction_fd, "matrix-verifier.sh")
    identity = _identity(info, raw) | {"member": "matrix-verifier.sh"}
    if identity != manifest["matrix_verifier"]:
        raise IAMTransactionError(
            "effective-IAM verifier differs from sealed authority"
        )
    return raw


def plan(args: argparse.Namespace) -> int:
    transaction_fd, transaction_info = _open_transaction(args.transaction, {0o700})
    terraform_fd = -1
    events_fd = -1
    verifier_fd = -1
    try:
        if os.listdir(transaction_fd):
            raise IAMTransactionError("IAM revoke transaction directory is not empty")
        events_fd, _events_info = _create_directory(transaction_fd, "events")
        source = _source_identity()
        gcloud, gcloud_identity = _executable_identity("gcloud")
        gcloud_config = _directory_identity(args.gcloud_config, "gcloud config")
        github_config = _directory_identity(args.gh_config, "GitHub CLI config")
        operator = _operator(
            gcloud, gcloud_identity, args.gcloud_config, args.expected_account
        )
        verifier_fd, verifier_info, verifier_raw = _safe_file(
            VERIFY_SECRET_ACCESS, label="effective-IAM verifier", executable=True
        )
        del verifier_info
        matrix_verifier = _write_member(
            transaction_fd, "matrix-verifier.sh", verifier_raw
        ) | {"member": "matrix-verifier.sh"}
        tfvars = _input_identity(args.tfvars, "tfvars")
        authority = _input_identity(args.release_authority, "release authority")
        tofu = _input_identity(args.tofu, "OpenTofu", executable=True)
        precheck = _precheck(gcloud, gcloud_identity, args.gcloud_config, verifier_raw)
        _run_terraform_plan(args, args.transaction / "terraform")
        terraform_fd, _terraform_info = _open_directory(
            transaction_fd, "terraform", {0o700}
        )
        if source != _source_identity():
            raise IAMTransactionError(
                "controller source changed during IAM revoke plan"
            )
        if operator != _operator(
            gcloud, gcloud_identity, args.gcloud_config, args.expected_account
        ):
            raise IAMTransactionError("operator changed during IAM revoke plan")
        precheck = _precheck(gcloud, gcloud_identity, args.gcloud_config, verifier_raw)
        terraform = _terraform_summary(terraform_fd)
        manifest = {
            "schema_version": 1,
            "profile": "iam-revoke",
            "state": "sealed",
            "created_at": _utc_now(),
            "project": PROJECT,
            "environment": ENVIRONMENT,
            "zone": ZONE,
            "instance": INSTANCE,
            "role": ROLE,
            "member": MEMBER,
            "resource_grants": list(RESOURCE_GRANTS),
            "forbidden_resource_count": FORBIDDEN_RESOURCE_COUNT,
            "source": source,
            "operator": operator
            | {"gcloud": gcloud_identity, "gcloud_config": gcloud_config},
            "inputs": {
                "tfvars": tfvars,
                "release_authority": authority,
                "tofu": tofu,
                "gh_config": github_config,
            },
            "matrix_verifier": matrix_verifier,
            "terraform": terraform,
            "precheck": precheck,
        }
        raw = _canonical(manifest)
        _validate_manifest(manifest, raw)
        _write_member(transaction_fd, "manifest.json", raw)
        print(
            "OMEGA_GCP_IAM_REVOKE_TRANSACTION\tSEALED\t"
            f"resource_grants={len(RESOURCE_GRANTS)}"
        )
        return 0
    finally:
        if verifier_fd >= 0:
            os.close(verifier_fd)
        if events_fd >= 0:
            os.close(events_fd)
        if terraform_fd >= 0:
            os.close(terraform_fd)
        _revalidate_transaction(args.transaction, transaction_info)
        os.close(transaction_fd)


def _trigger_for(error: BaseException, delete_completed: bool) -> str:
    if isinstance(error, IAMTransactionInterrupted):
        return "signal"
    return "postcheck-failure" if delete_completed else "apply-failure"


def _compensate(
    *,
    events_fd: int,
    events: list[dict[str, Any]],
    manifest_sha: str,
    trigger: str,
    gcloud: Path,
    gcloud_identity: dict[str, Any],
    config: Path,
    verifier_raw: bytes,
    expected_account: str,
) -> bool:
    try:
        _append_event(
            events_fd,
            events,
            manifest_sha,
            "compensation-started",
            {"trigger": trigger},
        )
    except (OSError, IAMTransactionError):
        # Receipt I/O must never prevent the safety action itself.
        pass
    try:
        result = _restore(
            gcloud,
            gcloud_identity,
            config,
            verifier_raw,
            expected_account,
        )
    except (OSError, IAMTransactionError):
        try:
            _append_event(
                events_fd,
                events,
                manifest_sha,
                "compensation-failed",
                {"trigger": trigger, "state": "restore-unverified"},
            )
        except (OSError, IAMTransactionError):
            pass
        return False
    try:
        _append_event(
            events_fd,
            events,
            manifest_sha,
            "compensation-verified",
            {"trigger": trigger, "result": result},
        )
        _append_event(
            events_fd,
            events,
            manifest_sha,
            "terminal",
            {"state": "failed-restored"},
        )
    except (OSError, IAMTransactionError):
        # The live grant is already verified even if the local receipt volume failed.
        pass
    return True


class _SignalGuard:
    def __init__(self) -> None:
        self._previous: dict[signal.Signals, Any] = {}

    def __enter__(self) -> "_SignalGuard":
        for selected in (
            signal.SIGHUP,
            signal.SIGINT,
            signal.SIGQUIT,
            signal.SIGTERM,
        ):
            self._previous[selected] = signal.getsignal(selected)

            def handler(signum: int, _frame: Any) -> None:
                _terminate_active_process()
                name = signal.Signals(signum).name
                raise IAMTransactionInterrupted(name)

            signal.signal(selected, handler)
        return self

    def protect_compensation(self) -> None:
        for selected in self._previous:
            signal.signal(selected, signal.SIG_IGN)

    def __exit__(self, _kind: Any, _value: Any, _traceback: Any) -> None:
        for selected, previous in self._previous.items():
            signal.signal(selected, previous)


def _auto_recover_dangling(
    *,
    transaction_fd: int,
    events_fd: int,
    events: list[dict[str, Any]],
    manifest_sha: str,
    gcloud: Path,
    gcloud_identity: dict[str, Any],
    config: Path,
    verifier_raw: bytes,
    expected_account: str,
) -> None:
    if _has_intent(events) and _terminal_state(events) is None:
        restored = _compensate(
            events_fd=events_fd,
            events=events,
            manifest_sha=manifest_sha,
            trigger="interrupted-recovery",
            gcloud=gcloud,
            gcloud_identity=gcloud_identity,
            config=config,
            verifier_raw=verifier_raw,
            expected_account=expected_account,
        )
        if restored:
            if _terminal_state(events) == "failed-restored":
                _seal_terminal(transaction_fd, events_fd)
            raise IAMTransactionError(
                "interrupted IAM revoke was restored; create a new sealed transaction"
            )
        raise IAMTransactionError("interrupted IAM revoke restoration is unverified")


def apply(args: argparse.Namespace) -> int:
    transaction_fd, transaction_info = _open_transaction(
        args.transaction, {0o500, 0o700}
    )
    terraform_fd = -1
    events_fd = -1
    mutation_armed = False
    delete_completed = False
    try:
        _require_transaction_members(transaction_fd)
        _manifest_info, manifest_raw, manifest = _load_manifest(transaction_fd)
        manifest_sha = _sha256(manifest_raw)
        terraform_fd, _terraform_info = _open_directory(
            transaction_fd, "terraform", {0o500, 0o700}
        )
        events_fd, _events_info = _open_directory(
            transaction_fd, "events", {0o500, 0o700}
        )
        events = _events(events_fd, manifest_sha)
        terminal = _terminal_state(events)
        if terminal == "applied-and-postchecked":
            _seal_terminal(transaction_fd, events_fd)
            print("OMEGA_GCP_IAM_REVOKE_TRANSACTION\tALREADY_APPLIED_AND_POSTCHECKED")
            return 0
        if terminal == "failed-restored":
            _seal_terminal(transaction_fd, events_fd)
            raise IAMTransactionError(
                "IAM revoke transaction already failed-restored; create a new plan"
            )
        if stat.S_IMODE(transaction_info.st_mode) != 0o700:
            raise IAMTransactionError(
                "read-only IAM revoke transaction lacks a terminal receipt"
            )
        if _has_intent(events):
            gcloud, gcloud_identity, verifier_raw = _recovery_runtime_identity(
                args, manifest, transaction_fd
            )
            _auto_recover_dangling(
                transaction_fd=transaction_fd,
                events_fd=events_fd,
                events=events,
                manifest_sha=manifest_sha,
                gcloud=gcloud,
                gcloud_identity=gcloud_identity,
                config=args.gcloud_config,
                verifier_raw=verifier_raw,
                expected_account=args.expected_account,
            )
        gcloud, gcloud_identity, verifier_raw = _validate_runtime_identity(
            args, manifest, transaction_fd, terraform_fd
        )
        if (
            _precheck(gcloud, gcloud_identity, args.gcloud_config, verifier_raw)
            != manifest["precheck"]
        ):
            raise IAMTransactionError(
                "live IAM precheck differs from sealed transaction"
            )
        with _SignalGuard() as signal_guard:
            try:
                _append_event(
                    events_fd,
                    events,
                    manifest_sha,
                    "mutation-intent",
                    {"state": "mutation-authorized"},
                )
                mutation_armed = True
                _run_terraform_apply(args, args.transaction / "terraform")
                delete_completed = True
                _append_event(
                    events_fd,
                    events,
                    manifest_sha,
                    "delete-applied",
                    {"state": "delete-command-complete"},
                )
                result = _postcheck(
                    gcloud, gcloud_identity, args.gcloud_config, verifier_raw
                )
                _append_event(
                    events_fd,
                    events,
                    manifest_sha,
                    "postcheck-passed",
                    {"result": result},
                )
                _append_event(
                    events_fd,
                    events,
                    manifest_sha,
                    "terminal",
                    {"state": "applied-and-postchecked"},
                )
            except BaseException as error:
                if _terminal_state(events) == "applied-and-postchecked":
                    signal_guard.protect_compensation()
                    _seal_terminal(transaction_fd, events_fd)
                    print(
                        "OMEGA_GCP_IAM_REVOKE_TRANSACTION\t" "APPLIED_AND_POSTCHECKED"
                    )
                    return 0
                if not _has_intent(events):
                    raise
                mutation_armed = True
                signal_guard.protect_compensation()
                trigger = _trigger_for(error, delete_completed)
                restored = _compensate(
                    events_fd=events_fd,
                    events=events,
                    manifest_sha=manifest_sha,
                    trigger=trigger,
                    gcloud=gcloud,
                    gcloud_identity=gcloud_identity,
                    config=args.gcloud_config,
                    verifier_raw=verifier_raw,
                    expected_account=args.expected_account,
                )
                if restored:
                    if _terminal_state(events) == "failed-restored":
                        _seal_terminal(transaction_fd, events_fd)
                    raise IAMTransactionError(
                        "IAM revoke failed; legacy project grant was restored and verified"
                    ) from None
                raise IAMTransactionError(
                    "IAM revoke failed and legacy project grant restoration is unverified"
                ) from None
        _seal_terminal(transaction_fd, events_fd)
        print(
            "OMEGA_GCP_IAM_REVOKE_TRANSACTION\tAPPLIED_AND_POSTCHECKED\t"
            f"resource_grants={len(RESOURCE_GRANTS)}\t"
            f"forbidden_resources={FORBIDDEN_RESOURCE_COUNT}"
        )
        return 0
    finally:
        if events_fd >= 0:
            os.close(events_fd)
        if terraform_fd >= 0:
            os.close(terraform_fd)
        if (
            not mutation_armed
            or stat.S_IMODE(os.fstat(transaction_fd).st_mode) == 0o700
        ):
            _revalidate_transaction(args.transaction, transaction_info)
        os.close(transaction_fd)


def recover(args: argparse.Namespace) -> int:
    transaction_fd, transaction_info = _open_transaction(
        args.transaction, {0o700, 0o500}
    )
    events_fd = -1
    try:
        _require_transaction_members(transaction_fd)
        _manifest_info, manifest_raw, manifest = _load_manifest(transaction_fd)
        manifest_sha = _sha256(manifest_raw)
        events_fd, _events_info = _open_directory(
            transaction_fd, "events", {0o700, 0o500}
        )
        events = _events(events_fd, manifest_sha)
        terminal = _terminal_state(events)
        if terminal == "applied-and-postchecked":
            _seal_terminal(transaction_fd, events_fd)
            print("OMEGA_GCP_IAM_REVOKE_TRANSACTION\tRECOVERY_NOT_REQUIRED_APPLIED")
            return 0
        gcloud, gcloud_identity, verifier_raw = _recovery_runtime_identity(
            args, manifest, transaction_fd
        )
        if not _has_intent(events):
            raise IAMTransactionError("IAM revoke transaction has no mutation intent")
        if terminal == "failed-restored":
            if (
                _precheck(
                    gcloud,
                    gcloud_identity,
                    args.gcloud_config,
                    verifier_raw,
                )
                != manifest["precheck"]
            ):
                raise IAMTransactionError(
                    "previously restored IAM authority is no longer exact"
                )
            _seal_terminal(transaction_fd, events_fd)
            print("OMEGA_GCP_IAM_REVOKE_TRANSACTION\tRECOVERY_ALREADY_VERIFIED")
            return 0
        restored = _compensate(
            events_fd=events_fd,
            events=events,
            manifest_sha=manifest_sha,
            trigger="interrupted-recovery",
            gcloud=gcloud,
            gcloud_identity=gcloud_identity,
            config=args.gcloud_config,
            verifier_raw=verifier_raw,
            expected_account=args.expected_account,
        )
        if not restored:
            raise IAMTransactionError(
                "interrupted IAM revoke restoration is unverified"
            )
        if _terminal_state(events) == "failed-restored":
            _seal_terminal(transaction_fd, events_fd)
        print("OMEGA_GCP_IAM_REVOKE_TRANSACTION\tRECOVERED_AND_VERIFIED")
        return 0
    finally:
        if events_fd >= 0:
            os.close(events_fd)
        _revalidate_transaction(args.transaction, transaction_info)
        os.close(transaction_fd)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="action", required=True)
    for action in ("plan", "apply", "recover"):
        command = subparsers.add_parser(action)
        command.add_argument("--transaction", type=Path, required=True)
        command.add_argument("--gcloud-config", type=Path, required=True)
        command.add_argument("--expected-account", required=True)
        if action in {"plan", "apply"}:
            command.add_argument("--tfvars", type=Path, required=True)
            command.add_argument("--tofu", type=Path, required=True)
            command.add_argument("--gh-config", type=Path, required=True)
        if action == "plan":
            command.add_argument("--release-authority", type=Path, required=True)
        command.set_defaults(
            handler={"plan": plan, "apply": apply, "recover": recover}[action]
        )
    return result


def _write_atomic_event(directory_fd: int, name: str, raw: bytes) -> dict[str, Any]:
    """Publish a complete event without ever exposing partial canonical bytes."""
    match = EVENT_PATTERN.fullmatch(name)
    if match is None:
        raise IAMTransactionError("atomic event member name is invalid")
    temporary = f".event-tmp-{match.group(1)}-{match.group(2)}-{secrets.token_hex(8)}"
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
    published = False
    try:
        os.fchmod(descriptor, 0o400)
        pending = memoryview(raw)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise IAMTransactionError("atomic event write made no progress")
            pending = pending[written:]
        os.fsync(descriptor)
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = bytearray()
        while len(readback) < len(raw):
            chunk = os.read(descriptor, len(raw) - len(readback))
            if not chunk:
                break
            readback.extend(chunk)
        after = os.fstat(descriptor)
        if (
            bytes(readback) != raw
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise IAMTransactionError("atomic event temporary read-back differs")
        os.link(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        published = True
        os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
        info, canonical = _read_member(directory_fd, name)
        if canonical != raw or info.st_nlink != 1:
            raise IAMTransactionError("atomic event publication differs")
        return _identity(info, raw)
    finally:
        os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            if not published:
                raise


def _clean_event_residues(events_fd: int) -> None:
    """Remove only recognized, owner-only atomic publication residues."""
    changed = False
    for name in sorted(os.listdir(events_fd)):
        match = EVENT_TEMP_PATTERN.fullmatch(name)
        if match is None:
            continue
        info = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
        canonical_name = f"{match.group(1)}-{match.group(2)}.json"
        try:
            canonical = os.stat(canonical_name, dir_fd=events_fd, follow_symlinks=False)
        except FileNotFoundError:
            canonical = None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in {0o000, 0o400}
            or not 0 <= info.st_size <= MAX_FILE
            or (canonical is None and info.st_nlink != 1)
            or (
                canonical is not None
                and (
                    not stat.S_ISREG(canonical.st_mode)
                    or canonical.st_uid != os.geteuid()
                    or (canonical.st_dev, canonical.st_ino)
                    != (info.st_dev, info.st_ino)
                    or info.st_nlink != 2
                )
            )
        ):
            raise IAMTransactionError("atomic event residue identity differs")
        os.unlink(name, dir_fd=events_fd)
        changed = True
    if changed:
        os.fsync(events_fd)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    if ACCOUNT_PATTERN.fullmatch(args.expected_account) is None:
        raise IAMTransactionError("expected operator account is invalid")
    for name in (
        "transaction",
        "gcloud_config",
        "tfvars",
        "tofu",
        "gh_config",
        "release_authority",
    ):
        value = getattr(args, name, None)
        if value is not None and not value.is_absolute():
            raise IAMTransactionError(f"{name} path must be absolute")
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, IAMTransactionError) as error:
        print(f"OMEGA_GCP_IAM_REVOKE_TRANSACTION\tFAIL\t{error}", file=sys.stderr)
        raise SystemExit(1) from None
