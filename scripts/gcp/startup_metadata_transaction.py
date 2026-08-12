#!/usr/bin/python3 -I
"""Install and execute the reviewed GCP startup foundation transaction.

The metadata phase binds candidate bytes to an immutable Git controller commit
and uses the live Compute fingerprint as its only compare-and-swap authority.
The foundation phase records an append-only reset intent, resets at most once,
and accepts success only from the exact server-owned fail-closed handoff
receipt.  Both phases are recoverable and secret-free.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable
import urllib.error
import urllib.request


PROJECT = "project-dd5ba7fa-374c-4554-ae6"
GCLOUD_PYTHON = Path(
    "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
)
ZONE = "us-central1-a"
INSTANCE = "omega-staging-app"
INSTANCE_ID = "4767392334132429161"
SERVICE_ACCOUNT = f"omega-staging-app@{PROJECT}.iam.gserviceaccount.com"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
COMPUTE_ROOT = "https://compute.googleapis.com/compute/v1"
MAX_STARTUP_BYTES = 256 * 1024
MAX_API_BYTES = 4 * 1024 * 1024
TOKEN_REFRESH_SECONDS = 1200
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{20,8192}$")
FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9_-]{4,256}={0,2}$")
ASSIGNMENTS = {
    "STARTUP_CONFIG_BASE64": "startup_config_base64",
    "RUNTIME_BASE64": "bootstrap_runtime_base64",
    "SAFE_IO_BASE64": "safe_io_base64",
    "METADATA_FIREWALL_BASE64": "metadata_firewall_base64",
    "OPERATION_GUARD_BASE64": "operation_guard_base64",
    "OPERATION_WATCHDOG_BASE64": "operation_watchdog_base64",
    "REBOOT_RUNTIME_BASE64": "reboot_runtime_base64",
    "RUNTIME_CONTRACT_BASE64": "runtime_contract_base64",
}
HELPERS = {
    "RUNTIME_BASE64": "scripts/gcp/bootstrap-runtime.sh",
    "SAFE_IO_BASE64": "scripts/gcp/safe_io.py",
    "METADATA_FIREWALL_BASE64": "scripts/gcp/metadata-firewall.sh",
    "OPERATION_GUARD_BASE64": "infra/terraform-gcp/templates/omega-operation-gate",
    "OPERATION_WATCHDOG_BASE64": "scripts/gcp/operation-watchdog.sh",
    "REBOOT_RUNTIME_BASE64": "scripts/gcp/reboot-runtime.sh",
    "RUNTIME_CONTRACT_BASE64": "scripts/gcp/runtime_contract.py",
}
TEMPLATE = "infra/terraform-gcp/templates/startup.sh.tftpl"
FOUNDATION_RECEIPT_ROOT = Path("/opt/modecissions/shared/foundation-receipts")
METADATA_RECEIPT_KEYS = {
    "schema_version",
    "operation",
    "project_id",
    "zone",
    "instance",
    "instance_id",
    "service_account",
    "controller_ref",
    "source_ref",
    "old_startup_sha256",
    "new_startup_sha256",
    "startup_contract_sha256",
    "metadata_keys",
    "secrets_included",
    "status",
    "operation_name",
    "post_metadata_fingerprint_sha256",
    "last_start_timestamp",
    "instance_reset_requested",
    "foundation_execution_state",
    "recovered",
}
FOUNDATION_HOST_RECEIPT_KEYS = {
    "schema_version",
    "state",
    "deploy_ref",
    "source_sha",
    "helper_ref",
    "controller_ref",
    "startup_contract_sha256",
    "live_startup_script_sha256",
    "foundation_marker_sha256",
    "foundation_watchdog_state_sha256",
    "terminal_receipt_sha256",
    "instance_id",
    "zone",
    "boot_id",
    "boot_started_epoch",
    "completed_at",
}
FOUNDATION_EXECUTION_RECEIPT_KEYS = {
    "schema_version",
    "operation",
    "status",
    "project_id",
    "zone",
    "instance",
    "instance_id",
    "controller_ref",
    "source_ref",
    "new_startup_sha256",
    "startup_contract_sha256",
    "metadata_receipt_sha256",
    "reset_operation_name",
    "pre_reset_last_start_timestamp",
    "post_reset_last_start_timestamp",
    "host_receipt_sha256",
    "host_receipt",
    "secrets_included",
    "recovered",
}


class TransactionError(RuntimeError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise TransactionError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TransactionError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise TransactionError(f"{label} timestamp is invalid")
    return parsed


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TransactionError("JSON contains duplicate keys")
        result[key] = value
    return result


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransactionError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise TransactionError(f"{label} is not a JSON object")
    return value


def _read_canonical_json(
    path: Path,
    *,
    label: str,
    maximum: int,
    mode: int | frozenset[int] = frozenset({0o400, 0o600}),
) -> tuple[bytes, dict[str, Any]]:
    raw = _read_exact(path, maximum=maximum, mode=mode)
    value = _strict_json(raw, label)
    if raw != _canonical(value):
        raise TransactionError(f"{label} is not exact canonical JSON")
    return raw, value


def _read_exact(
    path: Path,
    *,
    maximum: int,
    mode: int | frozenset[int] | None = None,
) -> bytes:
    if not path.is_absolute():
        raise TransactionError("authoritative file path must be absolute")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or (
                mode is not None
                and stat.S_IMODE(before.st_mode)
                not in ({mode} if isinstance(mode, int) else mode)
            )
            or not 1 <= before.st_size <= maximum
        ):
            raise TransactionError("authoritative file descriptor is unsafe")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)

        def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if len(raw) != before.st_size or identity(before) != identity(after):
            raise TransactionError("authoritative file changed during read")
        return raw
    finally:
        os.close(descriptor)


def _sealed_blob(repo: Path, commit: str, name: str) -> bytes:
    if not repo.is_absolute() or not repo.is_dir() or not COMMIT_RE.fullmatch(commit):
        raise TransactionError("sealed controller authority is invalid")
    path = repo / name
    try:
        raw = _read_exact(path, maximum=4 * 1024 * 1024)
    except (OSError, TransactionError) as exc:
        raise TransactionError(
            f"sealed controller blob is unavailable: {name}"
        ) from exc
    return raw


def _assignment(candidate: bytes, name: str) -> str:
    try:
        text = candidate.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TransactionError("candidate startup script is not UTF-8") from exc
    matches = re.findall(rf"(?m)^{re.escape(name)}='([A-Za-z0-9+/=]+)'$", text)
    if len(matches) != 1:
        raise TransactionError(f"candidate startup assignment differs: {name}")
    return matches[0]


def _startup_contract(candidate: bytes) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = base64.b64decode(
            _assignment(candidate, "STARTUP_CONFIG_BASE64"), validate=True
        )
    except ValueError as exc:
        raise TransactionError("candidate startup config encoding is invalid") from exc
    return raw, _strict_json(raw, "candidate startup config")


def validate_candidate(
    raw: bytes,
    *,
    expected_sha256: str,
    controller_ref: str,
    source_ref: str,
    repo: Path,
    blob_reader: Callable[[Path, str, str], bytes] | None = None,
) -> dict[str, Any]:
    read_blob = blob_reader or _sealed_blob
    if (
        not SHA256_RE.fullmatch(expected_sha256)
        or _sha256(raw) != expected_sha256
        or not raw.startswith(b"#!/bin/bash -p\n")
        or not raw.endswith(b"\n")
        or len(raw) > MAX_STARTUP_BYTES
        or not COMMIT_RE.fullmatch(controller_ref)
        or not COMMIT_RE.fullmatch(source_ref)
    ):
        raise TransactionError("candidate startup byte identity differs")
    values = {name: _assignment(raw, name) for name in ASSIGNMENTS}
    _config_raw, config = _startup_contract(raw)
    source = config.get("source")
    host_identity = config.get("host_identity")
    if (
        config.get("schema_version") != 1
        or config.get("project_id") != PROJECT
        or config.get("environment") != "staging"
        or config.get("controller_ref") != controller_ref
        or not isinstance(source, dict)
        or source.get("ref") != source_ref
        or host_identity
        != {
            "project_id": PROJECT,
            "instance_id": INSTANCE_ID,
            "instance_name": INSTANCE,
            "zone": ZONE,
            "service_account_email": SERVICE_ACCOUNT,
        }
        or config.get("canonical_writer") is not True
        or config.get("enable_airflow_scheduler") is not True
        or config.get("exact_runtime_contract_ready") is not False
    ):
        raise TransactionError("candidate startup authority differs")
    for variable, path in HELPERS.items():
        try:
            decoded = gzip.decompress(base64.b64decode(values[variable], validate=True))
        except (ValueError, gzip.BadGzipFile, EOFError) as exc:
            raise TransactionError(
                f"candidate helper encoding is invalid: {path}"
            ) from exc
        if len(decoded) > 4 * 1024 * 1024 or decoded != read_blob(
            repo, controller_ref, path
        ):
            raise TransactionError(f"candidate helper differs from controller: {path}")
    template = read_blob(repo, controller_ref, TEMPLATE).decode(
        "utf-8", errors="strict"
    )
    rendered = template
    for name, placeholder in ASSIGNMENTS.items():
        token = "${" + placeholder + "}"
        if rendered.count(token) != 1:
            raise TransactionError("reviewed startup template placeholder differs")
        rendered = rendered.replace(token, values[name])
    if rendered.encode() != raw:
        raise TransactionError("candidate startup differs from reviewed template")
    return config


def _metadata(payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {"fingerprint", "items"}:
        raise TransactionError("live metadata shape differs")
    fingerprint = metadata.get("fingerprint")
    items = metadata.get("items")
    if not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(fingerprint):
        raise TransactionError("live metadata fingerprint is invalid")
    if not isinstance(items, list) or len(items) != 2:
        raise TransactionError("live metadata key inventory differs")
    values: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"key", "value"}:
            raise TransactionError("live metadata item shape differs")
        key, value = item.get("key"), item.get("value")
        if not isinstance(key, str) or not isinstance(value, str) or key in values:
            raise TransactionError("live metadata item is invalid")
        values[key] = value
    if set(values) != {"enable-oslogin", "startup-script"}:
        raise TransactionError("live metadata key inventory differs")
    if values["enable-oslogin"] != "TRUE":
        raise TransactionError("live OS Login metadata differs")
    return fingerprint, values


def validate_instance(
    payload: dict[str, Any],
    *,
    expected_startup_sha256: str,
    expected_statuses: frozenset[str] = frozenset({"RUNNING"}),
) -> tuple[str, dict[str, str], str]:
    expected_zone = f"{COMPUTE_ROOT}/projects/{PROJECT}/zones/{ZONE}"
    accounts = payload.get("serviceAccounts")
    if (
        payload.get("id") != INSTANCE_ID
        or payload.get("name") != INSTANCE
        or payload.get("zone") != expected_zone
        or payload.get("status") not in expected_statuses
        or not isinstance(accounts, list)
        or len(accounts) != 1
        or not isinstance(accounts[0], dict)
        or accounts[0].get("email") != SERVICE_ACCOUNT
        or accounts[0].get("scopes") != [SCOPE]
        or not isinstance(payload.get("lastStartTimestamp"), str)
        or not SHA256_RE.fullmatch(expected_startup_sha256)
    ):
        raise TransactionError("live canonical instance identity differs")
    fingerprint, values = _metadata(payload)
    if _sha256(values["startup-script"].encode()) != expected_startup_sha256:
        raise TransactionError("live startup script checksum differs")
    return fingerprint, values, payload["lastStartTimestamp"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise TransactionError("authenticated redirect is forbidden")


class Compute:
    def __init__(
        self,
        token: str,
        *,
        deadline: float,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        if not TOKEN_RE.fullmatch(token):
            raise TransactionError("operator access token is invalid")
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )
        self.token = token
        self.deadline = deadline
        self.token_provider = token_provider
        self.token_refresh_at = time.monotonic() + TOKEN_REFRESH_SECONDS

    def request(
        self, url: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not url.startswith(COMPUTE_ROOT + "/"):
            raise TransactionError("Compute API deadline/authority is invalid")
        if (
            self.token_provider is not None
            and time.monotonic() >= self.token_refresh_at
        ):
            token = self.token_provider()
            if not TOKEN_RE.fullmatch(token):
                raise TransactionError("refreshed operator access token is invalid")
            self.token = token
            self.token_refresh_at = time.monotonic() + TOKEN_REFRESH_SECONDS
        data = (
            None
            if body is None
            else json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        )
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data else "GET"
        )
        try:
            with self.opener.open(request, timeout=min(30, remaining)) as response:
                raw = response.read(MAX_API_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise TransactionError(
                f"Compute API rejected request with HTTP {exc.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TransactionError("Compute API request failed") from exc
        if not 1 <= len(raw) <= MAX_API_BYTES:
            raise TransactionError("Compute API response exceeds bound")
        return _strict_json(raw, "Compute API response")


def _gcloud_process_env(
    config: Path, python: Path, framework: Path | None
) -> dict[str, str]:
    if framework is None:
        raise TransactionError("sealed Python framework authority is required")
    version_root = framework / "Versions/3.14"
    if not all(value.is_absolute() for value in (config, python, framework)):
        raise TransactionError("sealed gcloud runtime paths must be absolute")
    return {
        "CLOUDSDK_CONFIG": str(config),
        "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1",
        "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
        "CLOUDSDK_PYTHON": str(python),
        "CLOUDSDK_PYTHON_SITEPACKAGES": "0",
        "DYLD_FRAMEWORK_PATH": str(framework.parent),
        "DYLD_LIBRARY_PATH": str(version_root / "lib"),
        "HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }


def _token(
    gcloud: Path,
    config: Path,
    account: str,
    *,
    python: Path = GCLOUD_PYTHON,
    python_framework: Path | None = None,
) -> str:
    if not gcloud.is_absolute() or not config.is_absolute() or not account:
        raise TransactionError("operator gcloud authority is incomplete")
    command = [
        str(python),
        "-I",
        "-S",
        "-B",
        str(gcloud),
        "auth",
        "print-access-token",
        f"--account={account}",
        "--configuration=default",
        "--quiet",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
            env=_gcloud_process_env(config, python, python_framework),
        )
    except subprocess.TimeoutExpired as exc:
        raise TransactionError("operator token command timed out") from exc
    try:
        value = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise TransactionError("operator token output is invalid") from exc
    if result.returncode != 0 or not TOKEN_RE.fullmatch(value):
        raise TransactionError("operator token command failed")
    return value


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise TransactionError("receipt destination is unsafe")
    parent = path.parent
    # Path.stat(follow_symlinks=...) is unavailable on the canonical macOS
    # Python 3.9 operator runtime.  lstat() preserves the no-follow check and
    # keeps this controller executable on that declared interpreter.
    info = parent.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
        or path.exists()
        or path.is_symlink()
    ):
        raise TransactionError("receipt destination is unsafe")
    raw = _canonical(payload)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise TransactionError("receipt write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _instance_url() -> str:
    return f"{COMPUTE_ROOT}/projects/{PROJECT}/zones/{ZONE}/instances/{INSTANCE}"


def _metadata_intent(
    args: argparse.Namespace, startup_contract_sha256: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": "startup-metadata-cas",
        "project_id": PROJECT,
        "zone": ZONE,
        "instance": INSTANCE,
        "instance_id": INSTANCE_ID,
        "service_account": SERVICE_ACCOUNT,
        "controller_ref": args.controller_ref,
        "source_ref": args.source_ref,
        "old_startup_sha256": args.old_sha256,
        "new_startup_sha256": args.new_sha256,
        "startup_contract_sha256": startup_contract_sha256,
        "metadata_keys": ["enable-oslogin", "startup-script"],
        "secrets_included": False,
    }


def _validate_metadata_receipt(
    value: dict[str, Any],
    *,
    args: argparse.Namespace,
    startup_contract_sha256: str,
) -> dict[str, Any]:
    if set(value) != METADATA_RECEIPT_KEYS:
        raise TransactionError("startup metadata receipt shape differs")
    intent = _metadata_intent(args, startup_contract_sha256)
    if (
        {key: value.get(key) for key in intent} != intent
        or value.get("status") != "PASS"
        or not isinstance(value.get("operation_name"), str)
        or re.fullmatch(
            r"(?:operation-[0-9]+|recovered-after-cas)", value["operation_name"]
        )
        is None
        or not isinstance(value.get("post_metadata_fingerprint_sha256"), str)
        or SHA256_RE.fullmatch(value["post_metadata_fingerprint_sha256"]) is None
        or value.get("instance_reset_requested") is not False
        or value.get("foundation_execution_state") != "pending-controlled-reboot"
        or not isinstance(value.get("recovered"), bool)
    ):
        raise TransactionError("startup metadata receipt identity differs")
    _timestamp(value.get("last_start_timestamp"), "startup metadata receipt")
    return value


def _wait_operation(
    compute: Any,
    operation: dict[str, Any],
    *,
    expected_type: str,
) -> str:
    name = operation.get("name")
    if not isinstance(name, str) or re.fullmatch(r"operation-[0-9]+", name) is None:
        raise TransactionError(f"{expected_type} operation identity is invalid")
    operation_url = f"{COMPUTE_ROOT}/projects/{PROJECT}/zones/{ZONE}/operations/{name}"
    while operation.get("status") != "DONE":
        if operation.get("status") not in {"PENDING", "RUNNING"}:
            raise TransactionError(f"{expected_type} operation status is invalid")
        operation = compute.request(operation_url)
    if (
        operation.get("error") not in (None, {})
        or operation.get("operationType") != expected_type
    ):
        raise TransactionError(f"{expected_type} operation failed")
    return name


def _foundation_host_receipt(
    value: Any,
    *,
    args: argparse.Namespace,
    startup_contract_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FOUNDATION_HOST_RECEIPT_KEYS:
        raise TransactionError("foundation host receipt shape differs")
    sha_fields = (
        "startup_contract_sha256",
        "live_startup_script_sha256",
        "foundation_marker_sha256",
        "foundation_watchdog_state_sha256",
        "terminal_receipt_sha256",
    )
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("state") != "foundation-fenced"
        or value.get("deploy_ref") != args.source_ref
        or value.get("source_sha") != args.source_ref
        or value.get("helper_ref") != args.controller_ref
        or value.get("controller_ref") != args.controller_ref
        or value.get("startup_contract_sha256") != startup_contract_sha256
        or value.get("live_startup_script_sha256") != args.new_sha256
        or value.get("instance_id") != INSTANCE_ID
        or value.get("zone") != ZONE
        or any(
            not isinstance(value.get(name), str)
            or SHA256_RE.fullmatch(value[name]) is None
            for name in sha_fields
        )
        or not isinstance(value.get("boot_id"), str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value["boot_id"],
        )
        is None
        or type(value.get("boot_started_epoch")) is not int
        or value["boot_started_epoch"] < 1
    ):
        raise TransactionError("foundation host receipt identity differs")
    _timestamp(value.get("completed_at"), "foundation host receipt")
    return value


def _collect_foundation_receipt(
    args: argparse.Namespace,
    *,
    startup_contract_sha256: str,
    timeout_seconds: float,
) -> bytes | None:
    path = FOUNDATION_RECEIPT_ROOT / f"{args.controller_ref}.json"
    python = Path(getattr(args, "gcloud_python", GCLOUD_PYTHON))
    command = [
        str(python),
        "-I",
        "-S",
        "-B",
        str(args.gcloud),
        f"--account={args.operator_account}",
        f"--project={PROJECT}",
        "compute",
        "ssh",
        INSTANCE,
        f"--zone={ZONE}",
        "--tunnel-through-iap",
        "--quiet",
        "--ssh-flag=-oBatchMode=yes",
        "--ssh-flag=-oConnectTimeout=20",
        "--command=sudo -n /usr/local/sbin/omega-safe-io "
        f"foundation-receipt-collect --path {path} "
        f"--deploy-ref {args.source_ref} --helper-ref {args.controller_ref} "
        f"--startup-contract-sha256 {startup_contract_sha256}",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(1, min(180, timeout_seconds)),
            env=_gcloud_process_env(
                args.gcloud_config,
                python,
                getattr(args, "python_framework", None),
            ),
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    if not 1 <= len(result.stdout) <= 32768:
        raise TransactionError("foundation receipt collection output exceeds bound")
    return result.stdout


def _validated_foundation_collection(
    raw: bytes | None,
    *,
    args: argparse.Namespace,
    startup_contract_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if raw is None:
        raise TransactionError(
            "foundation handoff receipt is unavailable; runtime remains fail-closed"
        )
    projection = _strict_json(raw, "foundation receipt collection")
    if raw != _canonical(projection) or set(projection) != {"receipt", "sha256"}:
        raise TransactionError("foundation receipt collection is not canonical")
    host = _foundation_host_receipt(
        projection["receipt"],
        args=args,
        startup_contract_sha256=startup_contract_sha256,
    )
    if projection["sha256"] != _sha256(_canonical(host)):
        raise TransactionError("foundation host receipt checksum differs")
    return projection, host


def _execution_intent(
    args: argparse.Namespace,
    *,
    metadata_receipt_sha256: str,
    startup_contract_sha256: str,
    pre_reset_last_start_timestamp: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": "startup-foundation-execution",
        "state": "reset-may-have-started",
        "project_id": PROJECT,
        "zone": ZONE,
        "instance": INSTANCE,
        "instance_id": INSTANCE_ID,
        "controller_ref": args.controller_ref,
        "source_ref": args.source_ref,
        "new_startup_sha256": args.new_sha256,
        "startup_contract_sha256": startup_contract_sha256,
        "metadata_receipt_sha256": metadata_receipt_sha256,
        "pre_reset_last_start_timestamp": pre_reset_last_start_timestamp,
        "secrets_included": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_execution_intent(
    value: dict[str, Any],
    *,
    args: argparse.Namespace,
    metadata_receipt_sha256: str,
    startup_contract_sha256: str,
) -> dict[str, Any]:
    expected_keys = set(
        _execution_intent(
            args,
            metadata_receipt_sha256=metadata_receipt_sha256,
            startup_contract_sha256=startup_contract_sha256,
            pre_reset_last_start_timestamp="placeholder",
        )
    )
    if set(value) != expected_keys:
        raise TransactionError("foundation execution intent shape differs")
    expected = _execution_intent(
        args,
        metadata_receipt_sha256=metadata_receipt_sha256,
        startup_contract_sha256=startup_contract_sha256,
        pre_reset_last_start_timestamp=value.get("pre_reset_last_start_timestamp", ""),
    )
    expected["created_at"] = value.get("created_at")
    if value != expected:
        raise TransactionError("foundation execution intent identity differs")
    _timestamp(value["pre_reset_last_start_timestamp"], "foundation pre-reset")
    _timestamp(value["created_at"], "foundation execution intent")
    return value


def _validate_execution_receipt(
    value: dict[str, Any],
    *,
    args: argparse.Namespace,
    intent: dict[str, Any],
    metadata_receipt_sha256: str,
    startup_contract_sha256: str,
) -> dict[str, Any]:
    if set(value) != FOUNDATION_EXECUTION_RECEIPT_KEYS:
        raise TransactionError("foundation execution receipt shape differs")
    host = _foundation_host_receipt(
        value.get("host_receipt"),
        args=args,
        startup_contract_sha256=startup_contract_sha256,
    )
    host_raw = _canonical(host)
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("operation") != "startup-foundation-execution"
        or value.get("status") != "PASS"
        or value.get("project_id") != PROJECT
        or value.get("zone") != ZONE
        or value.get("instance") != INSTANCE
        or value.get("instance_id") != INSTANCE_ID
        or value.get("controller_ref") != args.controller_ref
        or value.get("source_ref") != args.source_ref
        or value.get("new_startup_sha256") != args.new_sha256
        or value.get("startup_contract_sha256") != startup_contract_sha256
        or value.get("metadata_receipt_sha256") != metadata_receipt_sha256
        or value.get("pre_reset_last_start_timestamp")
        != intent["pre_reset_last_start_timestamp"]
        or value.get("post_reset_last_start_timestamp")
        == intent["pre_reset_last_start_timestamp"]
        or not isinstance(value.get("reset_operation_name"), str)
        or re.fullmatch(
            r"(?:operation-[0-9]+|recovered-after-reset-intent)",
            value["reset_operation_name"],
        )
        is None
        or value.get("host_receipt_sha256") != _sha256(host_raw)
        or value.get("secrets_included") is not False
        or not isinstance(value.get("recovered"), bool)
    ):
        raise TransactionError("foundation execution receipt identity differs")
    pre_reset = _timestamp(
        value.get("pre_reset_last_start_timestamp"), "foundation pre-reset"
    )
    post_reset = _timestamp(
        value.get("post_reset_last_start_timestamp"), "foundation post-reset"
    )
    completed = _timestamp(host.get("completed_at"), "foundation host receipt")
    if post_reset <= pre_reset or completed < post_reset:
        raise TransactionError("foundation execution receipt timestamp order differs")
    return value


def transact(args: argparse.Namespace, *, compute: Any | None = None) -> dict[str, Any]:
    if not getattr(args, "confirm_exact_cas", False):
        raise TransactionError("--confirm-exact-cas is required")
    candidate = _read_exact(args.candidate_startup, maximum=MAX_STARTUP_BYTES)
    validate_candidate(
        candidate,
        expected_sha256=args.new_sha256,
        controller_ref=args.controller_ref,
        source_ref=args.source_ref,
        repo=args.repo,
    )
    startup_contract_raw, _config = _startup_contract(candidate)
    startup_contract_sha256 = _sha256(startup_contract_raw)
    if args.old_sha256 == args.new_sha256 or not SHA256_RE.fullmatch(args.old_sha256):
        raise TransactionError("startup transition checksums are invalid")
    intent_path = Path(str(args.receipt) + ".intent")
    if compute is None:
        token = _token(
            args.gcloud,
            args.gcloud_config,
            args.operator_account,
            python=args.gcloud_python,
            python_framework=args.python_framework,
        )
        compute = Compute(token, deadline=time.monotonic() + args.timeout_seconds)
    before = compute.request(_instance_url())
    current_sha = _sha256(_metadata(before)[1]["startup-script"].encode())
    recovering = intent_path.exists()
    if args.receipt.exists() or args.receipt.is_symlink():
        if not recovering or args.receipt.is_symlink():
            raise TransactionError(
                "startup metadata receipt exists without exact intent"
            )
        _intent_raw, intent = _read_canonical_json(
            intent_path, label="CAS intent", maximum=16384
        )
        if intent != _metadata_intent(args, startup_contract_sha256):
            raise TransactionError("existing CAS intent differs")
        _receipt_raw, receipt = _read_canonical_json(
            args.receipt, label="startup metadata receipt", maximum=32768
        )
        _validate_metadata_receipt(
            receipt,
            args=args,
            startup_contract_sha256=startup_contract_sha256,
        )
        fingerprint, values, started = validate_instance(
            before, expected_startup_sha256=args.new_sha256
        )
        if (
            values["startup-script"].encode() != candidate
            or started != receipt["last_start_timestamp"]
            or _sha256(fingerprint.encode())
            != receipt["post_metadata_fingerprint_sha256"]
        ):
            raise TransactionError(
                "completed startup metadata receipt is no longer live"
            )
        return receipt
    if current_sha not in {args.old_sha256, args.new_sha256}:
        raise TransactionError(
            "live startup is neither the authorized predecessor nor recoverable candidate"
        )
    if current_sha == args.new_sha256 and not recovering:
        raise TransactionError(
            "live startup is neither the authorized predecessor nor recoverable candidate"
        )
    if recovering:
        _intent_raw, intent = _read_canonical_json(
            intent_path, label="CAS intent", maximum=16384
        )
        if intent != _metadata_intent(args, startup_contract_sha256):
            raise TransactionError("existing CAS intent differs")
    else:
        intent = _metadata_intent(args, startup_contract_sha256)
        _write_new(intent_path, intent)
    operation_name = "recovered-after-cas"
    if current_sha == args.old_sha256:
        fingerprint, values, started = validate_instance(
            before, expected_startup_sha256=args.old_sha256
        )
        body = {
            "fingerprint": fingerprint,
            "items": [
                {"key": "enable-oslogin", "value": values["enable-oslogin"]},
                {"key": "startup-script", "value": candidate.decode("utf-8")},
            ],
        }
        operation = compute.request(_instance_url() + "/setMetadata", body=body)
        operation_name = _wait_operation(
            compute, operation, expected_type="setMetadata"
        )
    elif current_sha == args.new_sha256 and recovering:
        _fingerprint, _values, started = validate_instance(
            before, expected_startup_sha256=args.new_sha256
        )
    after = compute.request(_instance_url())
    post_fingerprint, post_values, post_started = validate_instance(
        after, expected_startup_sha256=args.new_sha256
    )
    if post_values["startup-script"].encode() != candidate or post_started != started:
        raise TransactionError("post-CAS read-back or VM start identity differs")
    receipt = {
        **intent,
        "status": "PASS",
        "operation_name": operation_name,
        "post_metadata_fingerprint_sha256": _sha256(post_fingerprint.encode()),
        "last_start_timestamp": post_started,
        "instance_reset_requested": False,
        "foundation_execution_state": "pending-controlled-reboot",
        "recovered": recovering,
    }
    _write_new(args.receipt, receipt)
    directory = os.open(args.receipt.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return receipt


def execute_foundation(
    args: argparse.Namespace,
    *,
    compute: Any | None = None,
    collector: Callable[..., bytes | None] | None = None,
) -> dict[str, Any]:
    """Install, boot once, and collect the exact server-owned foundation handoff."""
    if not getattr(args, "confirm_exact_foundation_reset", False):
        raise TransactionError("--confirm-exact-foundation-reset is required")
    if args.foundation_receipt is None or not args.foundation_receipt.is_absolute():
        raise TransactionError("foundation receipt destination is required")
    candidate = _read_exact(args.candidate_startup, maximum=MAX_STARTUP_BYTES)
    validate_candidate(
        candidate,
        expected_sha256=args.new_sha256,
        controller_ref=args.controller_ref,
        source_ref=args.source_ref,
        repo=args.repo,
    )
    startup_contract_raw, _config = _startup_contract(candidate)
    startup_contract_sha256 = _sha256(startup_contract_raw)
    if compute is None:
        token = _token(
            args.gcloud,
            args.gcloud_config,
            args.operator_account,
            python=args.gcloud_python,
            python_framework=args.python_framework,
        )
        compute = Compute(
            token,
            deadline=time.monotonic() + args.foundation_timeout_seconds + 300,
            token_provider=lambda: _token(
                args.gcloud,
                args.gcloud_config,
                args.operator_account,
                python=args.gcloud_python,
                python_framework=args.python_framework,
            ),
        )
    if args.receipt.exists() and not args.receipt.is_symlink():
        metadata_intent_path = Path(str(args.receipt) + ".intent")
        _metadata_intent_raw, metadata_intent = _read_canonical_json(
            metadata_intent_path, label="CAS intent", maximum=16384
        )
        if metadata_intent != _metadata_intent(args, startup_contract_sha256):
            raise TransactionError("existing CAS intent differs")
        metadata_raw, metadata_receipt = _read_canonical_json(
            args.receipt, label="startup metadata receipt", maximum=32768
        )
        _validate_metadata_receipt(
            metadata_receipt,
            args=args,
            startup_contract_sha256=startup_contract_sha256,
        )
        current = compute.request(_instance_url())
        current_fingerprint, current_values, _current_started = validate_instance(
            current, expected_startup_sha256=args.new_sha256
        )
        if (
            current_values["startup-script"].encode() != candidate
            or _sha256(current_fingerprint.encode())
            != metadata_receipt["post_metadata_fingerprint_sha256"]
        ):
            raise TransactionError("completed startup metadata is no longer live")
    else:
        metadata_receipt = transact(args, compute=compute)
        metadata_raw, persisted_metadata = _read_canonical_json(
            args.receipt, label="startup metadata receipt", maximum=32768
        )
        if persisted_metadata != metadata_receipt:
            raise TransactionError("startup metadata receipt read-back differs")
    metadata_receipt_sha256 = _sha256(metadata_raw)
    intent_path = Path(str(args.foundation_receipt) + ".intent")
    recovering = intent_path.exists()

    if args.foundation_receipt.exists() or args.foundation_receipt.is_symlink():
        if not recovering or args.foundation_receipt.is_symlink():
            raise TransactionError("foundation receipt exists without exact intent")
        _intent_raw, intent = _read_canonical_json(
            intent_path, label="foundation execution intent", maximum=32768
        )
        _validate_execution_intent(
            intent,
            args=args,
            metadata_receipt_sha256=metadata_receipt_sha256,
            startup_contract_sha256=startup_contract_sha256,
        )
        _receipt_raw, receipt = _read_canonical_json(
            args.foundation_receipt,
            label="foundation execution receipt",
            maximum=65536,
        )
        _validate_execution_receipt(
            receipt,
            args=args,
            intent=intent,
            metadata_receipt_sha256=metadata_receipt_sha256,
            startup_contract_sha256=startup_contract_sha256,
        )
        current = compute.request(_instance_url())
        fingerprint, values, started = validate_instance(
            current, expected_startup_sha256=args.new_sha256
        )
        if (
            values["startup-script"].encode() != candidate
            or started != receipt["post_reset_last_start_timestamp"]
            or _sha256(fingerprint.encode())
            != metadata_receipt["post_metadata_fingerprint_sha256"]
        ):
            raise TransactionError("completed foundation receipt is no longer live")
        collect = collector or _collect_foundation_receipt
        projection, host = _validated_foundation_collection(
            collect(
                args,
                startup_contract_sha256=startup_contract_sha256,
                timeout_seconds=max(
                    1.0, min(180.0, float(args.foundation_timeout_seconds))
                ),
            ),
            args=args,
            startup_contract_sha256=startup_contract_sha256,
        )
        if (
            projection["sha256"] != receipt["host_receipt_sha256"]
            or host != receipt["host_receipt"]
        ):
            raise TransactionError(
                "completed foundation host receipt is no longer live"
            )
        return receipt

    before = compute.request(_instance_url())
    before_fingerprint, values, before_started = validate_instance(
        before, expected_startup_sha256=args.new_sha256
    )
    if (
        values["startup-script"].encode() != candidate
        or _sha256(before_fingerprint.encode())
        != metadata_receipt["post_metadata_fingerprint_sha256"]
    ):
        raise TransactionError("live startup bytes differ before foundation execution")
    reset_operation_name = "recovered-after-reset-intent"
    if recovering:
        _intent_raw, intent = _read_canonical_json(
            intent_path, label="foundation execution intent", maximum=32768
        )
        _validate_execution_intent(
            intent,
            args=args,
            metadata_receipt_sha256=metadata_receipt_sha256,
            startup_contract_sha256=startup_contract_sha256,
        )
    else:
        if before_started != metadata_receipt["last_start_timestamp"]:
            raise TransactionError("VM boot changed before foundation reset intent")
        intent = _execution_intent(
            args,
            metadata_receipt_sha256=metadata_receipt_sha256,
            startup_contract_sha256=startup_contract_sha256,
            pre_reset_last_start_timestamp=before_started,
        )
        _write_new(intent_path, intent)
        reset_operation_name = _wait_operation(
            compute,
            compute.request(_instance_url() + "/reset", body={}),
            expected_type="reset",
        )

    deadline = time.monotonic() + args.foundation_timeout_seconds
    post_started: str | None = None
    allowed_statuses = frozenset(
        {"PROVISIONING", "STAGING", "RUNNING", "STOPPING", "REPAIRING"}
    )
    while time.monotonic() < deadline:
        current = compute.request(_instance_url())
        current_fingerprint, current_values, current_started = validate_instance(
            current,
            expected_startup_sha256=args.new_sha256,
            expected_statuses=allowed_statuses,
        )
        if current_values["startup-script"].encode() != candidate:
            raise TransactionError("startup metadata changed during foundation boot")
        if (
            _sha256(current_fingerprint.encode())
            != metadata_receipt["post_metadata_fingerprint_sha256"]
        ):
            raise TransactionError("startup metadata fingerprint changed during boot")
        if (
            current.get("status") == "RUNNING"
            and current_started != intent["pre_reset_last_start_timestamp"]
        ):
            post_started = current_started
            break
        time.sleep(min(args.poll_seconds, max(0.0, deadline - time.monotonic())))
    if post_started is None:
        raise TransactionError(
            "foundation reset outcome is indeterminate; recover without another reset"
        )

    collect = collector or _collect_foundation_receipt
    projection: dict[str, Any] | None = None
    host: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        raw = collect(
            args,
            startup_contract_sha256=startup_contract_sha256,
            timeout_seconds=max(1.0, deadline - time.monotonic()),
        )
        if raw is None:
            time.sleep(min(args.poll_seconds, max(0.0, deadline - time.monotonic())))
            continue
        projection, host = _validated_foundation_collection(
            raw,
            args=args,
            startup_contract_sha256=startup_contract_sha256,
        )
        break
    if projection is None or host is None:
        raise TransactionError(
            "foundation handoff receipt is unavailable; runtime remains fail-closed"
        )

    after = compute.request(_instance_url())
    final_fingerprint, final_values, final_started = validate_instance(
        after, expected_startup_sha256=args.new_sha256
    )
    if (
        final_values["startup-script"].encode() != candidate
        or final_started != post_started
        or _sha256(final_fingerprint.encode())
        != metadata_receipt["post_metadata_fingerprint_sha256"]
    ):
        raise TransactionError("VM identity changed after foundation handoff")
    receipt = {
        "schema_version": 1,
        "operation": "startup-foundation-execution",
        "status": "PASS",
        "project_id": PROJECT,
        "zone": ZONE,
        "instance": INSTANCE,
        "instance_id": INSTANCE_ID,
        "controller_ref": args.controller_ref,
        "source_ref": args.source_ref,
        "new_startup_sha256": args.new_sha256,
        "startup_contract_sha256": startup_contract_sha256,
        "metadata_receipt_sha256": metadata_receipt_sha256,
        "reset_operation_name": reset_operation_name,
        "pre_reset_last_start_timestamp": intent["pre_reset_last_start_timestamp"],
        "post_reset_last_start_timestamp": post_started,
        "host_receipt_sha256": projection["sha256"],
        "host_receipt": host,
        "secrets_included": False,
        "recovered": recovering,
    }
    _validate_execution_receipt(
        receipt,
        args=args,
        intent=intent,
        metadata_receipt_sha256=metadata_receipt_sha256,
        startup_contract_sha256=startup_contract_sha256,
    )
    _write_new(args.foundation_receipt, receipt)
    persisted_raw, persisted = _read_canonical_json(
        args.foundation_receipt,
        label="foundation execution receipt",
        maximum=65536,
    )
    if persisted != receipt or persisted_raw != _canonical(receipt):
        raise TransactionError("foundation execution receipt read-back differs")
    return receipt


def main() -> int:
    raise TransactionError(
        "internal-only helper; use the canonical gcp-foundation transaction"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, TransactionError) as error:
        print(f"OMEGA_GCP_STARTUP_METADATA\tFAIL\t{error}", file=sys.stderr)
        raise SystemExit(1) from None
