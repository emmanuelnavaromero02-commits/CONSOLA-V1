#!/usr/bin/python3 -I
"""Validate the exact GCP Compose runtime against immutable provenance."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMAGE_KEYS = {
    "console": ("OMEGA_GCP_IMAGE_CONSOLE", "console"),
    "workspace": ("OMEGA_GCP_IMAGE_WORKSPACE", "workspace"),
    "refinement": ("OMEGA_GCP_IMAGE_REFINEMENT", "refinement"),
    "vault": ("OMEGA_GCP_IMAGE_VAULT", "vault"),
    "mcp-infra": ("OMEGA_GCP_IMAGE_MCP_INFRA", "mcp-infra"),
    "airflow": ("OMEGA_GCP_IMAGE_AIRFLOW", "airflow"),
    "replicon": ("OMEGA_GCP_IMAGE_REPLICON", "replicon"),
    "hubspot": ("OMEGA_GCP_IMAGE_HUBSPOT", "hubspot"),
    "salesforce": ("OMEGA_GCP_IMAGE_SALESFORCE", "salesforce"),
    "banxico": ("OMEGA_GCP_IMAGE_BANXICO", "banxico"),
    "inegi": ("OMEGA_GCP_IMAGE_INEGI", "inegi"),
    "sec-edgar": ("OMEGA_GCP_IMAGE_SEC_EDGAR", "sec_edgar"),
    "sap-hcm": ("OMEGA_GCP_IMAGE_SAP_HCM", "sap_hcm"),
    "sap-successfactors": (
        "OMEGA_GCP_IMAGE_SAP_SUCCESSFACTORS",
        "sap_successfactors",
    ),
    "sap-s4hana": ("OMEGA_GCP_IMAGE_SAP_S4HANA", "sap_s4hana"),
}
SCHEDULER_SERVICE = "airflow-scheduler"
PROPRIETARY_INIT_SERVICE = "airflow-init"
ONE_SHOT_MUTATORS = (
    "airflow-init",
    "minio-init",
    "postgres_dev_seed",
    "superset-init",
)
INFRASTRUCTURE_SERVICES = (
    "postgres",
    "postgres_gold",
    "redis",
    "minio",
    "mailhog",
    "superset",
)
GLOBAL_SINGLETON_SERVICES = ("superset",)
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
GHCR_OWNER = "emmanuelnavaromero02-commits"
CANONICAL_APP_ROOT = Path("/opt/modecissions")
RUNTIME_INPUT_KEYS = {
    "shared_env",
    "base_compose",
    "gcp_compose",
    "legacy_image_compose",
    "release_compose",
}
REPOSITORY_TO_SERVICES = {
    repository: {service} for service, (_key, repository) in IMAGE_KEYS.items()
}
REPOSITORY_TO_SERVICES["airflow"].add(SCHEDULER_SERVICE)
MUTATING_SERVICES = (
    *IMAGE_KEYS,
    SCHEDULER_SERVICE,
    *ONE_SHOT_MUTATORS,
    *GLOBAL_SINGLETON_SERVICES,
)
RESTART_POLICY_NAMES = {"no", "always", "unless-stopped", "on-failure"}
ALL_CONTAINER_SERVICES = tuple(
    dict.fromkeys(
        (
            *IMAGE_KEYS,
            *INFRASTRUCTURE_SERVICES,
            SCHEDULER_SERVICE,
            *ONE_SHOT_MUTATORS,
        )
    )
)
RESTART_POLICY_SERVICES = ALL_CONTAINER_SERVICES
PROVENANCE_SERVICE_NAMES = frozenset(
    (
        *IMAGE_KEYS,
        *INFRASTRUCTURE_SERVICES,
        SCHEDULER_SERVICE,
        *(f"one-shot:{service}" for service in ONE_SHOT_MUTATORS),
    )
)
COMMAND_TIMEOUT_SECONDS = 15
MAX_RUNTIME_INPUT_BYTES = 2 * 1024 * 1024
MAX_PROVENANCE_BYTES = 256 * 1024
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024
DOCKER_CLIENT = "/usr/bin/docker"
DOCKER_COMMAND_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "DOCKER_HOST": "unix:///run/docker.sock",
    "LC_ALL": "C.UTF-8",
}

CARTRIDGE_DAG_MOUNTS = {
    (
        f"cartridges/{name}/dags",
        f"/opt/airflow/dags/{destination}",
        False,
    )
    for name, destination in (
        ("sap_successfactors", "sap_successfactors"),
        ("sap_hcm", "sap_hcm"),
        ("sap_s4hana", "sap_s4hana"),
        ("replicon", "replicon"),
        ("hubspot", "hubspot"),
        ("salesforce", "salesforce"),
        ("banxico", "banxico"),
        ("inegi", "inegi"),
        ("sec_edgar", "sec_edgar"),
    )
}
ALLOWED_BIND_MOUNTS = {
    "postgres": {("infra/init", "/docker-entrypoint-initdb.d", False)},
    "postgres_dev_seed": {("infra/init_dev", "/dev-seeds", False)},
    "postgres_gold": {("infra/init_gold", "/docker-entrypoint-initdb.d", False)},
    "console": {
        ("cartridges", "/registry/cartridges", False),
        ("airflow/dags", "/opt/airflow/dags", False),
        ("console/app/static", "/app/app/static", False),
        ("VERSION", "/app/VERSION", False),
    },
    "refinement": {
        (
            "infra/.secrets/gold_verifier_database_url",
            "/run/secrets/gold_verifier_database_url",
            False,
        )
    },
    "sap-successfactors": {
        (
            "/dev/null",
            "/run/secrets/sf_epiuse_iaappliance_connector.pem",
            False,
        )
    },
    "mcp-infra": {
        ("airflow/dags", "/opt/airflow/dags", False),
        ("cartridges", "/registry/cartridges", False),
    },
    "superset-init": {
        (
            "infra/terraform/deploy/superset_config/superset_config.py",
            "/app/pythonpath/superset_config.py",
            False,
        )
    },
    "superset": {
        (
            "infra/terraform/deploy/superset_config/superset_config.py",
            "/app/pythonpath/superset_config.py",
            False,
        )
    },
    "airflow-init": CARTRIDGE_DAG_MOUNTS,
    "airflow": {
        ("airflow/dags", "/opt/airflow/dags", False),
        ("airflow/plugins", "/opt/airflow/plugins", False),
        ("cartridges", "/registry/cartridges", False),
        *CARTRIDGE_DAG_MOUNTS,
    },
    "airflow-scheduler": {
        ("airflow/dags", "/opt/airflow/dags", False),
        ("airflow/plugins", "/opt/airflow/plugins", False),
        ("cartridges", "/registry/cartridges", False),
        *CARTRIDGE_DAG_MOUNTS,
    },
}
STATIC_NAMED_VOLUME_MOUNTS = {
    "postgres": {("postgres_data", "/var/lib/postgresql/data")},
    "postgres_gold": {("postgres_gold_data", "/var/lib/postgresql/data")},
    "minio": {("minio_data", "/data")},
    "airflow": {("airflow_logs", "/opt/airflow/logs")},
    "airflow-scheduler": {("airflow_logs", "/opt/airflow/logs")},
}
RUNTIME_NAMED_VOLUME_MOUNTS = {
    service: {(f"infra_{name}", destination) for name, destination in values}
    for service, values in STATIC_NAMED_VOLUME_MOUNTS.items()
}
PUBLISHED_TCP_PORTS = {
    "postgres": (("15432", 5432),),
    "postgres_gold": (("15433", 5433),),
    "minio": (("9000", 9000), ("9001", 9001)),
    "console": (("8000", 8000),),
    "workspace": (("8001", 8001),),
    "refinement": (("8500", 8500),),
    "vault": (("8300", 8300),),
    "mailhog": (("1025", 1025), ("8025", 8025)),
    "replicon": (("8201", 8201),),
    "salesforce": (("8205", 8205),),
    "hubspot": (("8210", 8210),),
    "banxico": (("8215", 8215),),
    "inegi": (("8216", 8216),),
    "sec-edgar": (("8217", 8217),),
    "sap-successfactors": (("8203", 8203),),
    "sap-hcm": (("8202", 8202),),
    "sap-s4hana": (("8204", 8204),),
    "mcp-infra": (("8010", 8010),),
    "superset": (("8088", 8088),),
    "airflow": (("8082", 8080),),
}
EXTRA_HOSTS = {"hubspot": ("host.docker.internal:host-gateway",)}
BUILD_SPECS = {
    "console": (".", "console/Dockerfile"),
    "workspace": ("workspace", "Dockerfile"),
    "refinement": (".", "refinement/Dockerfile"),
    "vault": ("vault", "Dockerfile"),
    "replicon": ("cartridges/replicon", "Dockerfile"),
    "salesforce": ("cartridges/salesforce", "Dockerfile"),
    "hubspot": ("cartridges/hubspot", "Dockerfile"),
    "banxico": (".", "cartridges/banxico/Dockerfile"),
    "inegi": (".", "cartridges/inegi/Dockerfile"),
    "sec-edgar": (".", "cartridges/sec_edgar/Dockerfile"),
    "sap-successfactors": ("cartridges/sap_successfactors", "Dockerfile"),
    "sap-hcm": ("cartridges/sap_hcm", "Dockerfile"),
    "sap-s4hana": ("cartridges/sap_s4hana", "Dockerfile"),
    "mcp-infra": (".", "mcp-infra/Dockerfile"),
    "airflow-init": ("infra/airflow", "Dockerfile"),
    "airflow": ("infra/airflow", "Dockerfile"),
    "airflow-scheduler": ("infra/airflow", "Dockerfile"),
}


def _validate_provenance_payload(
    payload: object,
    *,
    schema_version: int,
    project: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("runtime provenance is not an object")
    mode = payload.get("mode")
    base_keys = {
        "schema_version",
        "mode",
        "compose_project",
        "deploy_ref",
        "version",
        "runtime_input_sha256",
        "services",
    }
    expected_keys = base_keys | ({"image_lock_sha256"} if mode == "day2" else set())
    if set(payload) != expected_keys or mode not in {"bootstrap", "day2"}:
        raise ValueError("runtime provenance top-level shape is not exact")
    if (
        payload.get("schema_version") != schema_version
        or payload.get("compose_project") != project
        or not isinstance(payload.get("deploy_ref"), str)
        or FULL_SHA_RE.fullmatch(str(payload.get("deploy_ref"))) is None
        or not isinstance(payload.get("version"), str)
        or re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?",
            str(payload.get("version")),
        )
        is None
    ):
        raise ValueError("runtime provenance identity is invalid")
    if (
        mode == "day2"
        and re.fullmatch(r"[0-9a-f]{64}", str(payload.get("image_lock_sha256", "")))
        is None
    ):
        raise ValueError("runtime provenance image-lock hash is invalid")
    runtime_inputs = payload.get("runtime_input_sha256")
    expected_inputs = {"shared_env", "base_compose", "gcp_compose"}
    if mode == "day2":
        expected_inputs.add("release_compose")
    elif isinstance(runtime_inputs, dict) and "legacy_image_compose" in runtime_inputs:
        expected_inputs.add("legacy_image_compose")
    if (
        not isinstance(runtime_inputs, dict)
        or set(runtime_inputs) != expected_inputs
        or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in runtime_inputs.values()
        )
    ):
        raise ValueError("runtime provenance input hashes are not exact")
    services = payload.get("services")
    value_keys = {
        "configured_ref",
        "image_id",
        "runtime_config_sha256",
    }
    if schema_version == 2:
        value_keys.add("container_id")
    if not isinstance(services, dict) or set(services) != PROVENANCE_SERVICE_NAMES:
        raise ValueError("runtime provenance service inventory is not exact")
    for service, value in services.items():
        if not isinstance(value, dict) or set(value) != value_keys:
            raise ValueError(f"runtime provenance service shape differs: {service}")
        if (
            not isinstance(value.get("configured_ref"), str)
            or not value["configured_ref"]
            or SHA256_RE.fullmatch(str(value.get("image_id", ""))) is None
            or re.fullmatch(
                r"[0-9a-f]{64}", str(value.get("runtime_config_sha256", ""))
            )
            is None
            or (
                schema_version == 2
                and re.fullmatch(r"[0-9a-f]{64}", str(value.get("container_id", "")))
                is None
            )
        ):
            raise ValueError(f"runtime provenance service values differ: {service}")
    return payload


def _run(*command: str) -> str:
    if not command or command[0] != "docker":
        raise RuntimeError("runtime contract only permits the absolute Docker client")
    exact_command = [DOCKER_CLIENT, *command[1:]]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            result = subprocess.run(
                exact_command,
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
                env=DOCKER_COMMAND_ENV,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"command timed out after {COMMAND_TIMEOUT_SECONDS}s: "
                f"{' '.join(command[:2])}"
            ) from exc
        stdout_size = stdout.tell()
        stderr_size = stderr.tell()
        if (
            stdout_size > MAX_COMMAND_OUTPUT_BYTES
            or stderr_size > MAX_COMMAND_OUTPUT_BYTES
        ):
            raise RuntimeError("Docker command output exceeded the bounded contract")
        stdout.seek(0)
        stderr.seek(0)
        stdout_raw = stdout.read()
        stderr_raw = stderr.read()
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(command[:2])}")
    if stderr_raw:
        raise RuntimeError(
            f"command emitted unexpected stderr: {' '.join(command[:2])}"
        )
    try:
        return stdout_raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("Docker command output is not UTF-8") from exc


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON contains duplicate keys")
        value[key] = item
    return value


def _strict_json_loads(raw: str) -> Any:
    return json.loads(raw, object_pairs_hook=_json_no_duplicates)


def _read_owned_bytes(
    path: Path,
    *,
    mode: int,
    maximum: int,
    label: str,
    uid: int | None = None,
    gid: int | None = None,
    allow_empty: bool = False,
) -> bytes:
    """Read one authoritative descriptor after exact identity validation."""
    expected_uid = os.geteuid() if uid is None else uid
    expected_gid = os.getegid() if gid is None else gid
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or before.st_gid != expected_gid
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or before.st_size > maximum
            or (not allow_empty and before.st_size < 1)
        ):
            raise ValueError(f"{label} descriptor identity is invalid")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError(f"{label} changed during its authoritative read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError(f"{label} grew during its authoritative read")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_after != identity_before:
            raise ValueError(f"{label} changed during its authoritative read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_owned_json(
    path: Path,
    *,
    mode: int,
    maximum: int,
    label: str,
    uid: int | None = None,
    gid: int | None = None,
) -> tuple[bytes, object]:
    raw = _read_owned_bytes(
        path,
        mode=mode,
        maximum=maximum,
        label=label,
        uid=uid,
        gid=gid,
    )
    try:
        return raw, _strict_json_loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not canonical UTF-8 JSON") from exc


def _runtime_input_contract(key: str, path: Path) -> int:
    if not path.is_absolute():
        raise ValueError(f"runtime input path is not absolute: {key}")
    resolved = path.resolve(strict=True)
    parts = resolved.parts
    expected_tail: tuple[str, ...]
    mode: int
    if key == "shared_env":
        expected_tail = ("shared", "infra.env")
        mode = 0o600
    elif key == "gcp_compose":
        expected_tail = ("shared", "docker-compose.gcp.yml")
        mode = 0o600
    elif key == "legacy_image_compose":
        expected_tail = ("shared", "docker-compose.legacy-images.gcp.yml")
        mode = 0o600
    elif key == "base_compose":
        expected_tail = ("infra", "docker-compose.yml")
        mode = 0o644
    elif key == "release_compose":
        expected_tail = (
            "infra",
            "terraform-gcp",
            "release",
            "docker-compose.release.yml",
        )
        mode = 0o644
    else:  # guarded by the caller; keeps the helper fail-closed in isolation.
        raise ValueError(f"runtime input key is unsupported: {key}")
    if tuple(parts[-len(expected_tail) :]) != expected_tail:
        raise ValueError(f"runtime input path is not canonical: {key}")
    if key in {"base_compose", "release_compose"}:
        try:
            releases_index = len(parts) - len(expected_tail) - 2
            if (
                parts[releases_index] != "releases"
                or FULL_SHA_RE.fullmatch(parts[releases_index + 1]) is None
            ):
                raise ValueError
        except (IndexError, ValueError) as exc:
            raise ValueError(
                f"runtime release input is not one reviewed release: {key}"
            ) from exc
    return mode


def _runtime_input_hashes(values: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        if not separator or key not in RUNTIME_INPUT_KEYS or key in hashes:
            raise ValueError("runtime input inventory is invalid or duplicated")
        path = Path(raw_path)
        mode = _runtime_input_contract(key, path)
        raw = _read_owned_bytes(
            path,
            mode=mode,
            maximum=MAX_RUNTIME_INPUT_BYTES,
            label=f"runtime input {key}",
        )
        hashes[key] = hashlib.sha256(raw).hexdigest()
    base = {"shared_env", "base_compose", "gcp_compose"}
    if not base.issubset(hashes) or not set(hashes).issubset(RUNTIME_INPUT_KEYS):
        raise ValueError("runtime input inventory lacks its canonical base")
    if "legacy_image_compose" in hashes and "release_compose" in hashes:
        raise ValueError("legacy and day-2 image overlays cannot be active together")
    return hashes


def load_lock(
    path: Path,
    expected_version: str,
    *,
    uid: int = 0,
    gid: int = 0,
) -> tuple[dict[str, str], str]:
    raw = _read_owned_bytes(
        path,
        mode=0o600,
        maximum=65536,
        label="image lock",
        uid=uid,
        gid=gid,
    )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("image lock is not UTF-8") from exc
    expected_keys = {value[0] for value in IMAGE_KEYS.values()}
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("image lock contains a non-assignment line")
        key, value = line.split("=", 1)
        if key not in expected_keys or key in values:
            raise ValueError("image lock key inventory is not exact")
        values[key] = value
    if set(values) != expected_keys or len(values) != 15:
        raise ValueError("image lock must contain exactly 15 expected images")

    expected_tag = f"v{expected_version}"
    for service, (key, repository) in IMAGE_KEYS.items():
        match = re.fullmatch(
            rf"ghcr\.io/([a-z0-9][a-z0-9-]*)/{re.escape(repository)}:"
            r"(v[0-9][0-9A-Za-z._-]*)@(sha256:[0-9a-f]{64})",
            values[key],
        )
        if not match:
            raise ValueError(f"invalid locked reference for {service}")
        if match.group(1) != GHCR_OWNER or match.group(2) != expected_tag:
            raise ValueError("image lock owner or release tag is not canonical")
    return values, hashlib.sha256(raw).hexdigest()


def expected_service_refs(lock: dict[str, str]) -> dict[str, str]:
    refs = {service: lock[key] for service, (key, _) in IMAGE_KEYS.items()}
    refs[SCHEDULER_SERVICE] = lock[IMAGE_KEYS["airflow"][0]]
    refs[PROPRIETARY_INIT_SERVICE] = lock[IMAGE_KEYS["airflow"][0]]
    return refs


def _container_ids(
    project: str, service: str, *, running_only: bool = False
) -> list[str]:
    command = ["docker", "ps"] if running_only else ["docker", "ps", "-a"]
    output = _run(
        *command,
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        f"label=com.docker.compose.service={service}",
        "--format",
        "{{.ID}}",
    )
    return [line for line in output.splitlines() if line]


def _global_running_ids(service: str) -> list[str]:
    output = _run(
        "docker",
        "ps",
        "--filter",
        f"label=com.docker.compose.service={service}",
        "--format",
        "{{.ID}}",
    )
    return [line for line in output.splitlines() if line]


def _all_running_ids() -> list[str]:
    output = _run("docker", "ps", "--format", "{{.ID}}")
    return [line for line in output.splitlines() if line]


def _all_container_ids() -> list[str]:
    output = _run("docker", "ps", "-a", "--format", "{{.ID}}")
    return [line for line in output.splitlines() if line]


def _project_network_rows(project: str) -> list[tuple[str, str]]:
    output = _run(
        "docker",
        "network",
        "ls",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.ID}}\t{{.Name}}",
    )
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        network_id, separator, name = line.partition("\t")
        if not separator:
            raise RuntimeError("Docker network inventory output is invalid")
        rows.append((network_id, name))
    return rows


def _network_inspect(network_id: str) -> dict[str, Any]:
    payload = _strict_json_loads(_run("docker", "network", "inspect", network_id))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("docker network inspect did not return one network")
    return payload[0]


def _volume_inspect(volume_name: str) -> dict[str, Any]:
    payload = _strict_json_loads(_run("docker", "volume", "inspect", volume_name))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("docker volume inspect did not return one volume")
    volume = payload[0]
    if not isinstance(volume, dict):
        raise RuntimeError("docker volume inspect returned a malformed volume")
    return volume


def _validate_named_volume(
    *,
    project: str,
    volume_name: str,
    mount_source: str,
) -> None:
    prefix = f"{project}_"
    if not volume_name.startswith(prefix):
        raise RuntimeError("named volume does not belong to the Compose project")
    compose_volume = volume_name[len(prefix) :]
    expected_mountpoint = f"/var/lib/docker/volumes/{volume_name}/_data"
    if mount_source != expected_mountpoint:
        raise RuntimeError("named-volume mount source is not canonical")

    volume = _volume_inspect(volume_name)
    options = volume.get("Options")
    if options is None:
        options = {}
    labels = volume.get("Labels")
    if not isinstance(labels, dict):
        raise RuntimeError("named-volume Compose labels are missing")
    expected_label_keys = {
        "com.docker.compose.config-hash",
        "com.docker.compose.project",
        "com.docker.compose.version",
        "com.docker.compose.volume",
    }
    config_hash = labels.get("com.docker.compose.config-hash")
    compose_version = labels.get("com.docker.compose.version")
    if (
        volume.get("Name") != volume_name
        or volume.get("Driver") != "local"
        or volume.get("Scope") != "local"
        or options != {}
        or volume.get("Mountpoint") != expected_mountpoint
        or set(labels) != expected_label_keys
        or not isinstance(config_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", config_hash) is None
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.volume") != compose_volume
        or not isinstance(compose_version, str)
        or re.fullmatch(
            r"[1-9][0-9]*\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", compose_version
        )
        is None
    ):
        raise RuntimeError("named volume is not an exact local Compose volume")


def _validate_project_network(project: str, canonical: dict[str, str]) -> None:
    expected_name = f"{project}_default"
    observed_network_ids: set[str] = set()
    inspected: dict[str, dict[str, Any]] = {}
    attachments: dict[str, dict[str, Any]] = {}
    for service, container_id in canonical.items():
        info = _inspect(container_id)
        inspected[service] = info
        networks = (info.get("NetworkSettings") or {}).get("Networks")
        if not isinstance(networks, dict) or set(networks) != {expected_name}:
            raise RuntimeError(f"service={service} network inventory is not exact")
        attachment = networks[expected_name]
        if not isinstance(attachment, dict):
            raise RuntimeError(f"service={service} network attachment is malformed")
        container_name = info.get("Name")
        aliases = attachment.get("Aliases")
        expected_attachment_keys = {
            "Aliases",
            "DNSNames",
            "DriverOpts",
            "EndpointID",
            "Gateway",
            "GlobalIPv6Address",
            "GlobalIPv6PrefixLen",
            "GwPriority",
            "IPAddress",
            "IPAMConfig",
            "IPPrefixLen",
            "IPv6Gateway",
            "Links",
            "MacAddress",
            "NetworkID",
        }
        if (
            not isinstance(container_name, str)
            or re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9_.-]*", container_name) is None
            or not isinstance(aliases, list)
            or any(not isinstance(alias, str) for alias in aliases)
            or len(aliases) != 2
            or set(aliases) != {container_name[1:], service}
            or attachment.get("Links") is not None
            or attachment.get("DriverOpts") is not None
            or attachment.get("IPAMConfig") is not None
            or set(attachment) != expected_attachment_keys
            or attachment.get("GwPriority") != 0
            or attachment.get("IPv6Gateway") != ""
            or attachment.get("GlobalIPv6Address") != ""
            or attachment.get("GlobalIPv6PrefixLen") != 0
        ):
            raise RuntimeError(f"service={service} network attachment is not exact")
        network_id = attachment.get("NetworkID")
        if (
            not isinstance(network_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", network_id) is None
        ):
            raise RuntimeError(f"service={service} network identity is invalid")
        observed_network_ids.add(network_id)
        attachments[service] = attachment
    if len(observed_network_ids) != 1:
        raise RuntimeError("canonical containers do not share one exact network")
    network_id = next(iter(observed_network_ids))
    rows = _project_network_rows(project)
    if rows != [(network_id[:12], expected_name)] and rows != [
        (network_id, expected_name)
    ]:
        raise RuntimeError("Compose project network inventory is not exact")
    network = _network_inspect(network_id)
    labels = network.get("Labels")
    ipam = network.get("IPAM")
    config = ipam.get("Config") if isinstance(ipam, dict) else None
    try:
        subnet = ipaddress.ip_network(config[0]["Subnet"], strict=True)
        gateway = ipaddress.ip_address(config[0]["Gateway"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise RuntimeError("canonical Docker network IPAM is malformed") from exc
    if (
        not isinstance(subnet, ipaddress.IPv4Network)
        or subnet.prefixlen != 16
        or not subnet.is_private
        or gateway != subnet.network_address + 1
    ):
        raise RuntimeError("canonical Docker network IPAM is not exact")
    expected_label_keys = {
        "com.docker.compose.config-hash",
        "com.docker.compose.network",
        "com.docker.compose.project",
        "com.docker.compose.version",
    }
    config_hash = (
        labels.get("com.docker.compose.config-hash")
        if isinstance(labels, dict)
        else None
    )
    compose_version = (
        labels.get("com.docker.compose.version") if isinstance(labels, dict) else None
    )
    if (
        network.get("Id") != network_id
        or network.get("Name") != expected_name
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or network.get("Internal") is not False
        or network.get("Attachable") is not False
        or network.get("Ingress") is not False
        or network.get("ConfigOnly") is not False
        or network.get("EnableIPv4") is not True
        or network.get("EnableIPv6") is not False
        or network.get("Options") != {}
        or network.get("ConfigFrom") != {"Network": ""}
        or not isinstance(ipam, dict)
        or set(ipam) != {"Driver", "Options", "Config"}
        or ipam.get("Driver") != "default"
        or ipam.get("Options") not in (None, {})
        or not isinstance(config, list)
        or len(config) != 1
        or not isinstance(config[0], dict)
        or set(config[0]) != {"Subnet", "Gateway"}
        or not isinstance(labels, dict)
        or set(labels) != expected_label_keys
        or not isinstance(config_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", config_hash) is None
        or labels.get("com.docker.compose.network") != "default"
        or labels.get("com.docker.compose.project") != project
        or not isinstance(compose_version, str)
        or re.fullmatch(
            r"[1-9][0-9]*\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", compose_version
        )
        is None
    ):
        raise RuntimeError("canonical Docker network is not a local bridge")

    observed_containers = network.get("Containers")
    if not isinstance(observed_containers, dict):
        raise RuntimeError("canonical Docker network container map is malformed")
    expected_running_ids = {
        canonical[service]
        for service, info in inspected.items()
        if bool((info.get("State") or {}).get("Running"))
    }
    if set(observed_containers) != expected_running_ids:
        raise RuntimeError("canonical Docker network container inventory is not exact")
    for service, info in inspected.items():
        attachment = attachments[service]
        running = bool((info.get("State") or {}).get("Running"))
        endpoint_id = attachment.get("EndpointID")
        ip_address = attachment.get("IPAddress")
        prefix_length = attachment.get("IPPrefixLen")
        mac_address = attachment.get("MacAddress")
        expected_dns_names = {
            str(info["Name"])[1:],
            service,
            canonical[service][:12],
        }
        dns_names = attachment.get("DNSNames")
        if (
            not isinstance(dns_names, list)
            or any(not isinstance(name, str) for name in dns_names)
            or len(dns_names) != 3
            or set(dns_names) != expected_dns_names
        ):
            raise RuntimeError(f"service={service} network DNS names are not exact")
        if running:
            if (
                not isinstance(endpoint_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", endpoint_id) is None
                or not isinstance(ip_address, str)
                or ipaddress.ip_address(ip_address) not in subnet
                or prefix_length != subnet.prefixlen
                or attachment.get("Gateway") != str(gateway)
                or not isinstance(mac_address, str)
                or re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac_address) is None
            ):
                raise RuntimeError(
                    f"service={service} live network endpoint is invalid"
                )
            entry = observed_containers[canonical[service]]
            if not isinstance(entry, dict) or entry != {
                "Name": str(info["Name"])[1:],
                "EndpointID": endpoint_id,
                "MacAddress": mac_address,
                "IPv4Address": f"{ip_address}/{prefix_length}",
                "IPv6Address": "",
            }:
                raise RuntimeError(f"service={service} network endpoint map differs")
        elif (
            endpoint_id != ""
            or ip_address != ""
            or prefix_length != 0
            or mac_address != ""
        ):
            raise RuntimeError(
                f"service={service} stopped network endpoint is not empty"
            )


def _exact_all_container_ids(
    project: str, *, expected_release_ref: str | None = None
) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for service in ALL_CONTAINER_SERVICES:
        ids = _container_ids(project, service)
        if len(ids) != 1:
            raise RuntimeError(
                f"global container service={service} count={len(ids)} expected=1"
            )
        canonical[service] = ids[0]
        _validate_container_security(
            _inspect(ids[0]),
            service=service,
            project=project,
            expected_release_ref=expected_release_ref,
        )
    if len(set(canonical.values())) != len(canonical):
        raise RuntimeError("canonical services share a container identity")
    if set(_all_container_ids()) != set(canonical.values()):
        raise RuntimeError(
            "global all-container inventory differs from the exact 26 canonical containers"
        )
    _validate_project_network(project, canonical)
    return canonical


def _proprietary_repository(configured_ref: str) -> str | None:
    match = re.match(
        rf"^ghcr\.io/{re.escape(GHCR_OWNER)}/([a-z0-9_-]+)(?::|@)",
        configured_ref,
    )
    if match and match.group(1) in REPOSITORY_TO_SERVICES:
        return match.group(1)
    return None


def _verify_global_writer_inventory(
    project: str, canonical_ids: dict[str, str], *, scheduler: str
) -> None:
    expected_services = set(IMAGE_KEYS)
    if scheduler == "required":
        expected_services.add(SCHEDULER_SERVICE)
    for service in (*IMAGE_KEYS, SCHEDULER_SERVICE):
        global_ids = set(_global_running_ids(service))
        expected_ids = (
            {canonical_ids[service]} if service in expected_services else set()
        )
        if global_ids != expected_ids:
            raise RuntimeError(
                f"global service={service} ids={len(global_ids)} expected={len(expected_ids)}"
            )
    for service in GLOBAL_SINGLETON_SERVICES:
        global_ids = set(_global_running_ids(service))
        expected_ids = {canonical_ids[service]}
        if global_ids != expected_ids:
            raise RuntimeError(
                f"global service={service} ids={len(global_ids)} expected=1"
            )

    singleton_images = {
        str((_inspect(canonical_ids[service]).get("Config") or {}).get("Image", "")): (
            service,
            canonical_ids[service],
        )
        for service in GLOBAL_SINGLETON_SERVICES
    }

    for container_id in _all_running_ids():
        info = _inspect(container_id)
        config = info.get("Config") or {}
        repository = _proprietary_repository(str(config.get("Image", "")))
        labels = config.get("Labels") or {}
        configured_ref = str(config.get("Image", ""))
        if configured_ref in singleton_images:
            singleton_service, expected_id = singleton_images[configured_ref]
            if container_id != expected_id:
                raise RuntimeError(
                    f"foreign or unlabeled singleton image detected: {singleton_service}"
                )
        if repository is None:
            continue
        service = labels.get("com.docker.compose.service")
        allowed = REPOSITORY_TO_SERVICES[repository]
        if (
            labels.get("com.docker.compose.project") != project
            or service not in allowed
            or canonical_ids.get(str(service)) != container_id
        ):
            raise RuntimeError(
                f"foreign or unlabeled proprietary writer image detected: {repository}"
            )


def verify_global_fence(project: str) -> None:
    canonical_ids: set[str] = set()
    non_mutating_infrastructure = tuple(
        service
        for service in INFRASTRUCTURE_SERVICES
        if service not in MUTATING_SERVICES
    )
    for service in non_mutating_infrastructure:
        _require_exact_container(
            project,
            service,
            expected_ref=None,
            running=True,
            healthy=True,
        )
        ids = _container_ids(project, service)
        canonical_ids.add(ids[0])
        if set(_global_running_ids(service)) != {ids[0]}:
            raise RuntimeError(f"global infrastructure service drift: {service}")
    if set(_all_running_ids()) != canonical_ids:
        raise RuntimeError(
            "writer fence permits only the exact five non-mutating infrastructure containers"
        )


def _inspect(container_id: str) -> dict[str, Any]:
    payload = _strict_json_loads(_run("docker", "inspect", container_id))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("docker inspect did not return one container")
    return payload[0]


def _restart_policy(info: dict[str, Any]) -> tuple[str, int]:
    policy = (info.get("HostConfig") or {}).get("RestartPolicy")
    if not isinstance(policy, dict):
        raise RuntimeError("container restart policy is missing")
    name = policy.get("Name")
    maximum_retry_count = policy.get("MaximumRetryCount")
    if (
        name not in RESTART_POLICY_NAMES
        or not isinstance(maximum_retry_count, int)
        or isinstance(maximum_retry_count, bool)
        or maximum_retry_count < 0
        or (name != "on-failure" and maximum_retry_count != 0)
    ):
        raise RuntimeError("container restart policy is invalid")
    return name, maximum_retry_count


def _restart_policy_inventory(project: str) -> dict[str, dict[str, str | int]]:
    inventory: dict[str, dict[str, str | int]] = {}
    canonical = _exact_all_container_ids(project)
    for service in RESTART_POLICY_SERVICES:
        info = _inspect(canonical[service])
        container_id = info.get("Id")
        labels = (info.get("Config") or {}).get("Labels") or {}
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            raise RuntimeError(f"restart-policy service identity drift: {service}")
        name, maximum_retry_count = _restart_policy(info)
        inventory[service] = {
            "container_id": container_id,
            "restart_policy": name,
            "maximum_retry_count": maximum_retry_count,
        }
    return inventory


def _validate_restart_policy_payload(
    payload: object,
    *,
    project: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "state",
        "compose_project",
        "containers",
        "updated_at",
    }:
        raise ValueError("restart-policy fence state shape is invalid")
    if (
        payload.get("schema_version") != 1
        or payload.get("state") not in {"preparing", "fenced", "restoring", "restored"}
        or payload.get("compose_project") != project
        or not isinstance(payload.get("updated_at"), str)
    ):
        raise ValueError("restart-policy fence state identity is invalid")
    containers = payload.get("containers")
    if not isinstance(containers, dict) or set(containers) != set(
        RESTART_POLICY_SERVICES
    ):
        raise ValueError("restart-policy container inventory is not exact")
    for service, value in containers.items():
        if not isinstance(value, dict) or set(value) != {
            "container_id",
            "restart_policy",
            "maximum_retry_count",
        }:
            raise ValueError(f"restart-policy contract is invalid: {service}")
        container_id = value.get("container_id")
        name = value.get("restart_policy")
        maximum_retry_count = value.get("maximum_retry_count")
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or name not in RESTART_POLICY_NAMES
            or not isinstance(maximum_retry_count, int)
            or isinstance(maximum_retry_count, bool)
            or maximum_retry_count < 0
            or (name != "on-failure" and maximum_retry_count != 0)
        ):
            raise ValueError(f"restart-policy values are invalid: {service}")
    return payload


def _load_restart_policy_state(path: Path, *, project: str) -> dict[str, Any]:
    _validate_restart_policy_parent(path)
    _raw, payload = _read_owned_json(
        path,
        mode=0o600,
        maximum=16384,
        label="restart-policy fence state",
        uid=0,
        gid=0,
    )
    return _validate_restart_policy_payload(
        payload,
        project=project,
    )


def _validate_restart_policy_parent(path: Path) -> None:
    info = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not path.parent.is_dir()
        or info.st_uid != 0
        or info.st_gid != 0
        or (info.st_mode & 0o777) != 0o755
    ):
        raise ValueError("restart-policy fence parent is unsafe")


def _restart_policy_state(
    *,
    state: str,
    project: str,
    containers: dict[str, dict[str, str | int]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": state,
        "compose_project": project,
        "containers": containers,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _assert_restart_inventory(
    current: dict[str, dict[str, str | int]],
    recorded: dict[str, dict[str, str | int]],
    *,
    fenced: bool,
) -> None:
    if set(current) != set(recorded):
        raise RuntimeError("restart-policy service inventory changed")
    for service in RESTART_POLICY_SERVICES:
        if current[service]["container_id"] != recorded[service]["container_id"]:
            raise RuntimeError(f"restart-policy container changed: {service}")
        if fenced:
            if (
                current[service]["restart_policy"] != "no"
                or current[service]["maximum_retry_count"] != 0
            ):
                raise RuntimeError(f"restart-policy fence is not active: {service}")
        elif current[service] != recorded[service]:
            raise RuntimeError(f"restart-policy restore differs: {service}")


def _set_restart_policy(container_id: str, name: str, retries: int) -> None:
    value = f"on-failure:{retries}" if name == "on-failure" and retries else name
    _run("docker", "update", f"--restart={value}", container_id)


def _image_id(reference: str) -> str:
    payload = _strict_json_loads(_run("docker", "image", "inspect", reference))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("docker image inspect did not return one image")
    image_id = payload[0].get("Id", "")
    if not SHA256_RE.fullmatch(image_id):
        raise RuntimeError("locked image ID is invalid")
    return image_id


def _container_id(info: dict[str, Any], *, service: str) -> str:
    container_id = info.get("Id")
    if (
        not isinstance(container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise RuntimeError(f"service={service} container ID is invalid")
    return container_id


RUNTIME_SOCKET_NAMES = {
    "docker.sock",
    "containerd.sock",
    "podman.sock",
    "crio.sock",
    "cri-dockerd.sock",
    "dockershim.sock",
    "buildkit.sock",
    "buildkitd.sock",
    "lxd.sock",
    "incus.sock",
    "libvirt-sock",
    "libvirt-sock-ro",
    "libvirt-sock-admin",
}


def _has_runtime_socket(value: object) -> bool:
    if isinstance(value, str):
        return any(
            component.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            in RUNTIME_SOCKET_NAMES
            for component in value.split(":")
        )
    if isinstance(value, dict):
        return any(_has_runtime_socket(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_runtime_socket(item) for item in value)
    return False


def _release_mount_source(
    source: str,
    *,
    app_root: Path = CANONICAL_APP_ROOT,
) -> tuple[str, str]:
    """Return an immutable release SHA and release-relative source path."""
    prefix = f"{app_root}/releases/"
    if not source.startswith(prefix):
        raise RuntimeError("bind source is outside one immutable release")
    match = re.fullmatch(r"([0-9a-f]{40})/(.+)", source[len(prefix) :])
    if match is None:
        raise RuntimeError("bind source is outside one immutable release")
    relative = match.group(2)
    if (
        relative.startswith("/")
        or "//" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise RuntimeError("bind source release-relative path is invalid")
    path = Path(source)
    try:
        if path.resolve(strict=True) != path:
            raise RuntimeError("bind source traverses a symlink")
    except OSError as exc:
        raise RuntimeError("bind source is missing or unreadable") from exc
    return match.group(1), relative


def _validate_runtime_mounts(
    mounts: list[object],
    *,
    service: str,
    project: str = "infra",
    expected_release_ref: str | None = None,
) -> None:
    allowed_binds = ALLOWED_BIND_MOUNTS.get(service, set())
    allowed_volumes = RUNTIME_NAMED_VOLUME_MOUNTS.get(service, set())
    release_shas: set[str] = set()
    observed_binds: set[tuple[str, str, bool]] = set()
    observed_volumes: set[tuple[str, str]] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise RuntimeError(f"service={service} runtime mount is malformed")
        mount_type = mount.get("Type")
        destination = mount.get("Destination")
        source = mount.get("Source")
        if not isinstance(source, str) or not isinstance(destination, str):
            raise RuntimeError(f"service={service} runtime mount identity is malformed")
        if mount_type == "bind":
            read_write = mount.get("RW")
            if not isinstance(read_write, bool):
                raise RuntimeError(f"service={service} bind access mode is malformed")
            if mount.get("Propagation") not in (None, "", "rprivate"):
                raise RuntimeError(f"service={service} bind propagation is forbidden")
            if source == "/dev/null":
                relative = source
                try:
                    info = Path(source).lstat()
                except OSError as exc:
                    raise RuntimeError(
                        "optional null bind source is unreadable"
                    ) from exc
                if not stat.S_ISCHR(info.st_mode) or Path(source).resolve() != Path(
                    source
                ):
                    raise RuntimeError("optional null bind source identity is invalid")
            else:
                release_sha, relative = _release_mount_source(source)
                release_shas.add(release_sha)
            candidate = (relative, destination, read_write)
            if candidate not in allowed_binds or candidate in observed_binds:
                raise RuntimeError(
                    f"service={service} bind mount is not in the exact host allowlist"
                )
            observed_binds.add(candidate)
        elif mount_type == "volume":
            name = mount.get("Name")
            if (
                not isinstance(name, str)
                or (name, destination) not in allowed_volumes
                or (name, destination) in observed_volumes
                or mount.get("RW") is not True
                or mount.get("Driver") not in (None, "", "local")
            ):
                raise RuntimeError(
                    f"service={service} named-volume mount is not allowlisted"
                )
            _validate_named_volume(
                project=project,
                volume_name=name,
                mount_source=source,
            )
            observed_volumes.add((name, destination))
        else:
            raise RuntimeError(f"service={service} mount type is forbidden")
    if observed_binds != allowed_binds or observed_volumes != allowed_volumes:
        raise RuntimeError(f"service={service} runtime mount inventory is not exact")
    if len(release_shas) > 1:
        raise RuntimeError(f"service={service} bind sources mix release identities")
    if expected_release_ref is not None and release_shas not in (
        set(),
        {expected_release_ref},
    ):
        raise RuntimeError(
            f"service={service} bind source differs from authoritative release"
        )


def _validate_container_security(
    info: dict[str, Any],
    *,
    service: str,
    project: str = "infra",
    expected_release_ref: str | None = None,
) -> None:
    host = info.get("HostConfig")
    mounts = info.get("Mounts")
    if not isinstance(host, dict) or not isinstance(mounts, list):
        raise RuntimeError(f"service={service} runtime security config is missing")
    network_mode = host.get("NetworkMode")
    if (
        not isinstance(network_mode, str)
        or network_mode in {"", "host", "none"}
        or network_mode.startswith("container:")
    ):
        raise RuntimeError(
            f"canonical service has forbidden network namespace: {service}"
        )
    for field in ("PidMode", "UTSMode", "CgroupnsMode", "UsernsMode"):
        value = host.get(field, "")
        if value not in {"", "private"}:
            raise RuntimeError(f"service={service} forbidden {field}")
    ipc_mode = host.get("IpcMode", "")
    if ipc_mode not in {"", "private", "shareable"}:
        raise RuntimeError(f"service={service} forbidden IpcMode")
    if host.get("Privileged") is not False:
        raise RuntimeError(f"service={service} privileged mode is not explicitly false")
    for field in ("AutoRemove", "PublishAllPorts"):
        if host.get(field) is not False:
            raise RuntimeError(f"service={service} {field} is not explicitly false")
    for field in ("Devices", "DeviceRequests", "DeviceCgroupRules"):
        if host.get(field) not in (None, []):
            raise RuntimeError(f"service={service} host device access is forbidden")
    if host.get("Sysctls") not in (None, {}):
        raise RuntimeError(f"service={service} host sysctls are forbidden")
    if host.get("CgroupParent") not in (None, ""):
        raise RuntimeError(f"service={service} cgroup parent override is forbidden")
    if host.get("Runtime") not in (None, "", "runc"):
        raise RuntimeError(f"service={service} nonstandard OCI runtime is forbidden")
    if host.get("GroupAdd") not in (None, []):
        raise RuntimeError(f"service={service} supplemental host groups are forbidden")
    if host.get("VolumesFrom") not in (None, []):
        raise RuntimeError(
            f"service={service} inherited container volumes are forbidden"
        )
    if host.get("CapAdd") not in (None, []):
        raise RuntimeError(f"service={service} added Linux capabilities are forbidden")
    expected_bindings = {
        f"{target}/tcp": [{"HostIp": "", "HostPort": published}]
        for published, target in PUBLISHED_TCP_PORTS.get(service, ())
    }
    observed_bindings = host.get("PortBindings")
    if observed_bindings != expected_bindings and not (
        not expected_bindings and observed_bindings is None
    ):
        raise RuntimeError(f"service={service} published port inventory is not exact")
    expected_extra_hosts = list(EXTRA_HOSTS.get(service, ()))
    if (host.get("ExtraHosts") or []) != expected_extra_hosts:
        raise RuntimeError(f"service={service} extra host inventory is not exact")
    for field in ("Dns", "DnsOptions", "DnsSearch", "Links"):
        if host.get(field) not in (None, []):
            raise RuntimeError(f"service={service} host {field} is forbidden")
    if network_mode != f"{project}_default":
        raise RuntimeError(f"service={service} Compose network identity differs")
    security_options = host.get("SecurityOpt")
    if security_options not in (None, []):
        if (
            not isinstance(security_options, list)
            or {
                value.lower().replace(":", "=")
                for value in security_options
                if isinstance(value, str)
            }
            != {"no-new-privileges=true"}
            or any(not isinstance(value, str) for value in security_options)
        ):
            raise RuntimeError(
                f"service={service} security options are not allowlisted"
            )
    if _has_runtime_socket(host.get("Binds")) or _has_runtime_socket(mounts):
        raise RuntimeError(f"service={service} Docker socket access is forbidden")
    _validate_runtime_mounts(
        mounts,
        service=service,
        project=project,
        expected_release_ref=expected_release_ref,
    )


def _runtime_config_sha256(
    info: dict[str, Any],
    *,
    restart_policy_override: dict[str, Any] | None = None,
) -> str:
    """Hash deterministic runtime configuration without emitting secret values."""
    config = info.get("Config")
    host = info.get("HostConfig")
    mounts = info.get("Mounts")
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    if (
        not isinstance(config, dict)
        or not isinstance(host, dict)
        or not isinstance(mounts, list)
        or not isinstance(networks, dict)
    ):
        raise RuntimeError("container runtime configuration shape is invalid")
    normalized_host = dict(host)
    if restart_policy_override is not None:
        normalized_host["RestartPolicy"] = restart_policy_override
    payload = {
        "path": info.get("Path"),
        "args": info.get("Args") or [],
        "config": config,
        "host_config": normalized_host,
        "mounts": sorted(
            mounts,
            key=lambda item: json.dumps(item, separators=(",", ":"), sort_keys=True),
        ),
        "networks": {
            str(name): {
                key: value.get(key)
                for key in (
                    "Aliases",
                    "DriverOpts",
                    "IPAMConfig",
                    "Links",
                    "NetworkID",
                )
            }
            for name, value in sorted(networks.items())
            if isinstance(value, dict)
        },
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _legacy_runtime_config_sha256(
    info: dict[str, Any],
    *,
    restart_policy_override: dict[str, Any] | None = None,
) -> str:
    """Reproduce provenance schema v1 exactly for one-time adoption only."""
    config = info.get("Config") or {}
    host = info.get("HostConfig") or {}
    mounts = info.get("Mounts") or []
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    payload = {
        "path": info.get("Path"),
        "args": info.get("Args") or [],
        "config": {
            key: config.get(key)
            for key in (
                "Image",
                "Env",
                "Entrypoint",
                "Cmd",
                "WorkingDir",
                "User",
                "Labels",
                "Healthcheck",
                "ExposedPorts",
                "Volumes",
                "StopSignal",
            )
        },
        "host_config": {
            key: (
                restart_policy_override
                if key == "RestartPolicy" and restart_policy_override is not None
                else host.get(key)
            )
            for key in (
                "Binds",
                "CapAdd",
                "CapDrop",
                "CgroupnsMode",
                "CpuQuota",
                "CpuPeriod",
                "CpusetCpus",
                "Dns",
                "ExtraHosts",
                "IpcMode",
                "LogConfig",
                "Memory",
                "MemorySwap",
                "NanoCpus",
                "NetworkMode",
                "PidsLimit",
                "PortBindings",
                "Privileged",
                "ReadonlyRootfs",
                "RestartPolicy",
                "SecurityOpt",
                "ShmSize",
                "Ulimits",
                "UsernsMode",
            )
        },
        "mounts": sorted(
            (
                {
                    key: mount.get(key)
                    for key in (
                        "Type",
                        "Name",
                        "Source",
                        "Destination",
                        "Driver",
                        "Mode",
                        "RW",
                        "Propagation",
                    )
                }
                for mount in mounts
                if isinstance(mount, dict)
            ),
            key=lambda item: (str(item.get("Destination")), str(item.get("Source"))),
        ),
        "networks": sorted(str(name) for name in networks),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_exact_container(
    project: str,
    service: str,
    *,
    expected_ref: str | None,
    running: bool,
    healthy: bool,
    restart_policy_override: dict[str, Any] | None = None,
    legacy_config_hash: bool = False,
    expected_release_ref: str | None = None,
) -> dict[str, str]:
    ids = _container_ids(project, service)
    if len(ids) != 1:
        raise RuntimeError(f"service={service} containers={len(ids)} expected=1")
    info = _inspect(ids[0])
    _validate_container_security(
        info,
        service=service,
        project=project,
        expected_release_ref=expected_release_ref,
    )
    labels = info.get("Config", {}).get("Labels") or {}
    state = info.get("State") or {}
    if labels.get("com.docker.compose.project") != project:
        raise RuntimeError(f"service={service} project label mismatch")
    if labels.get("com.docker.compose.service") != service:
        raise RuntimeError(f"service={service} service label mismatch")
    if bool(state.get("Running")) is not running:
        raise RuntimeError(f"service={service} running state mismatch")
    if healthy and (state.get("Health") or {}).get("Status") != "healthy":
        raise RuntimeError(f"service={service} is not healthy")
    configured_ref = str((info.get("Config") or {}).get("Image", ""))
    image_id = str(info.get("Image", ""))
    if expected_ref is not None:
        if configured_ref != expected_ref:
            raise RuntimeError(f"service={service} configured digest mismatch")
        if image_id != _image_id(expected_ref):
            raise RuntimeError(f"service={service} local image ID mismatch")
    if not SHA256_RE.fullmatch(image_id):
        raise RuntimeError(f"service={service} image ID is invalid")
    config_hash = (
        _legacy_runtime_config_sha256 if legacy_config_hash else _runtime_config_sha256
    )
    return {
        "container_id": _container_id(info, service=service),
        "configured_ref": configured_ref,
        "image_id": image_id,
        "runtime_config_sha256": config_hash(
            info,
            restart_policy_override=restart_policy_override,
        ),
    }


def verify_runtime(
    project: str,
    *,
    expected_refs: dict[str, str] | None,
    scheduler: str,
    check_one_shots: bool,
    restart_policy_overrides: dict[str, dict[str, Any]] | None = None,
    legacy_config_hash: bool = False,
    expected_release_ref: str | None = None,
) -> dict[str, dict[str, str]]:
    if expected_refs is not None and not set(IMAGE_KEYS).issubset(expected_refs):
        raise ValueError("runtime image inventory must contain exactly 15 services")
    _exact_all_container_ids(project, expected_release_ref=expected_release_ref)
    observed: dict[str, dict[str, str]] = {}
    canonical_ids: dict[str, str] = {}
    for service in IMAGE_KEYS:
        observed[service] = _require_exact_container(
            project,
            service,
            expected_ref=(expected_refs or {}).get(service),
            running=True,
            healthy=True,
            restart_policy_override=(restart_policy_overrides or {}).get(service),
            legacy_config_hash=legacy_config_hash,
            expected_release_ref=expected_release_ref,
        )
        canonical_ids[service] = _container_ids(project, service)[0]

    # These services are not part of the 15 proprietary GHCR lock, but they
    # are still mandatory production runtime. In particular Superset is the
    # Analytics surface, and both databases/object/cache dependencies must be
    # healthy for a release to be considered exact. Their configured refs and
    # local image IDs are recorded in runtime provenance and checked on every
    # later recovery.
    for service in INFRASTRUCTURE_SERVICES:
        observed[service] = _require_exact_container(
            project,
            service,
            expected_ref=(expected_refs or {}).get(service),
            running=True,
            healthy=True,
            restart_policy_override=(restart_policy_overrides or {}).get(service),
            legacy_config_hash=legacy_config_hash,
            expected_release_ref=expected_release_ref,
        )
        canonical_ids[service] = _container_ids(project, service)[0]

    globally_running = _global_running_ids(SCHEDULER_SERVICE)
    if scheduler == "stopped":
        if globally_running:
            raise RuntimeError("scheduler must remain globally fenced")
    elif scheduler == "required":
        if len(globally_running) != 1:
            raise RuntimeError(
                f"global scheduler cardinality={len(globally_running)} expected=1"
            )
        observed[SCHEDULER_SERVICE] = _require_exact_container(
            project,
            SCHEDULER_SERVICE,
            expected_ref=(expected_refs or {}).get(SCHEDULER_SERVICE),
            running=True,
            healthy=True,
            restart_policy_override=(restart_policy_overrides or {}).get(
                SCHEDULER_SERVICE
            ),
            legacy_config_hash=legacy_config_hash,
            expected_release_ref=expected_release_ref,
        )
        canonical_ids[SCHEDULER_SERVICE] = _container_ids(project, SCHEDULER_SERVICE)[0]
    else:
        raise ValueError("scheduler policy must be stopped or required")

    if check_one_shots:
        for service in ONE_SHOT_MUTATORS:
            ids = _container_ids(project, service)
            if len(ids) != 1:
                raise RuntimeError(
                    f"one-shot service={service} containers={len(ids)} expected=1"
                )
            info = _inspect(ids[0])
            _validate_container_security(
                info,
                service=service,
                project=project,
                expected_release_ref=expected_release_ref,
            )
            labels = info.get("Config", {}).get("Labels") or {}
            state = info.get("State") or {}
            if labels.get("com.docker.compose.project") != project:
                raise RuntimeError(f"one-shot service={service} project mismatch")
            if labels.get("com.docker.compose.service") != service:
                raise RuntimeError(f"one-shot service={service} label mismatch")
            if state.get("Status") != "exited" or int(state.get("ExitCode", -1)) != 0:
                raise RuntimeError(
                    f"one-shot service={service} did not exit successfully"
                )
            expected = (expected_refs or {}).get(f"one-shot:{service}")
            if service == PROPRIETARY_INIT_SERVICE:
                expected = expected or (expected_refs or {}).get(service)
                configured_ref = str((info.get("Config") or {}).get("Image", ""))
                if expected is not None and configured_ref != expected:
                    raise RuntimeError("airflow-init configured digest mismatch")
                if expected is not None and info.get("Image") != _image_id(expected):
                    raise RuntimeError("airflow-init local image ID mismatch")
            configured_ref = str((info.get("Config") or {}).get("Image", ""))
            image_id = str(info.get("Image", ""))
            if expected is not None and configured_ref != expected:
                raise RuntimeError(f"one-shot service={service} configured ref drift")
            if expected is not None and image_id != _image_id(expected):
                raise RuntimeError(f"one-shot service={service} image ID drift")
            if not SHA256_RE.fullmatch(image_id):
                raise RuntimeError(f"one-shot service={service} image ID is invalid")
            config_hash = (
                _legacy_runtime_config_sha256
                if legacy_config_hash
                else _runtime_config_sha256
            )
            observed[f"one-shot:{service}"] = {
                "container_id": _container_id(info, service=service),
                "configured_ref": configured_ref,
                "image_id": image_id,
                "runtime_config_sha256": config_hash(
                    info,
                    restart_policy_override=(restart_policy_overrides or {}).get(
                        service
                    ),
                ),
            }
    if set(_all_running_ids()) != set(canonical_ids.values()):
        raise RuntimeError(
            "global running-container inventory differs from the exact canonical runtime"
        )
    _verify_global_writer_inventory(project, canonical_ids, scheduler=scheduler)
    return observed


def verify_prestart_runtime(
    project: str,
    *,
    expected_refs: dict[str, str],
    restart_policy_overrides: dict[str, dict[str, Any]],
    legacy_config_hash: bool,
    expected_release_ref: str | None = None,
) -> dict[str, dict[str, str]]:
    """Verify all persisted containers before Compose may start any service."""
    canonical = _exact_all_container_ids(
        project, expected_release_ref=expected_release_ref
    )
    observed: dict[str, dict[str, str]] = {}

    for service in IMAGE_KEYS:
        state = _inspect(canonical[service]).get("State") or {}
        if bool(state.get("Running")) or state.get("Status") != "exited":
            raise RuntimeError(
                f"prestart mutator service={service} is not cleanly stopped"
            )
        observed[service] = _require_exact_container(
            project,
            service,
            expected_ref=expected_refs.get(service),
            running=False,
            healthy=False,
            restart_policy_override=restart_policy_overrides.get(service),
            legacy_config_hash=legacy_config_hash,
            expected_release_ref=expected_release_ref,
        )

    for service in INFRASTRUCTURE_SERVICES:
        info = _inspect(canonical[service])
        state = info.get("State") or {}
        if bool(state.get("Running")) or state.get("Status") != "exited":
            raise RuntimeError(
                f"prestart infrastructure service={service} is not cleanly stopped"
            )
        observed[service] = _require_exact_container(
            project,
            service,
            expected_ref=None,
            running=False,
            healthy=False,
            restart_policy_override=restart_policy_overrides.get(service),
            legacy_config_hash=legacy_config_hash,
            expected_release_ref=expected_release_ref,
        )

    scheduler_info = _inspect(canonical[SCHEDULER_SERVICE])
    scheduler_state = scheduler_info.get("State") or {}
    if (
        bool(scheduler_state.get("Running"))
        or scheduler_state.get("Status") != "exited"
    ):
        raise RuntimeError("prestart scheduler is not cleanly stopped")
    observed[SCHEDULER_SERVICE] = _require_exact_container(
        project,
        SCHEDULER_SERVICE,
        expected_ref=expected_refs.get(SCHEDULER_SERVICE),
        running=False,
        healthy=False,
        restart_policy_override=restart_policy_overrides.get(SCHEDULER_SERVICE),
        legacy_config_hash=legacy_config_hash,
        expected_release_ref=expected_release_ref,
    )

    for service in ONE_SHOT_MUTATORS:
        info = _inspect(canonical[service])
        state = info.get("State") or {}
        if (
            bool(state.get("Running"))
            or state.get("Status") != "exited"
            or int(state.get("ExitCode", -1)) != 0
        ):
            raise RuntimeError(
                f"prestart one-shot service={service} did not remain exited 0"
            )
        key = f"one-shot:{service}"
        expected_ref = expected_refs.get(key)
        if service == PROPRIETARY_INIT_SERVICE:
            expected_ref = expected_ref or expected_refs.get(service)
        observed[key] = _require_exact_container(
            project,
            service,
            expected_ref=expected_ref,
            running=False,
            healthy=False,
            restart_policy_override=restart_policy_overrides.get(service),
            legacy_config_hash=legacy_config_hash,
            expected_release_ref=expected_release_ref,
        )

    if _all_running_ids():
        raise RuntimeError("prestart requires all 26 canonical containers stopped")
    return observed


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_owned_path(
    path: Path,
    *,
    kind: str,
    mode: int,
    uid: int = 0,
    gid: int = 0,
) -> os.stat_result:
    info = path.lstat()
    kind_matches = {
        "directory": stat.S_ISDIR(info.st_mode),
        "file": stat.S_ISREG(info.st_mode),
        "symlink": stat.S_ISLNK(info.st_mode),
    }
    if (
        not kind_matches.get(kind, False)
        or info.st_uid != uid
        or info.st_gid != gid
        or (kind != "symlink" and stat.S_IMODE(info.st_mode) != mode)
    ):
        raise ValueError(f"unsafe runtime-state {kind}: {path.name}")
    return info


def _load_state_bundle(
    app_root: Path,
    *,
    uid: int = 0,
    gid: int = 0,
) -> tuple[Path, str, Path, dict[str, Any], dict[str, Any]]:
    shared = app_root / "shared"
    bundles = shared / "state-bundles"
    state_link = shared / "runtime-state"
    _require_owned_path(shared, kind="directory", mode=0o755, uid=uid, gid=gid)
    _require_owned_path(bundles, kind="directory", mode=0o700, uid=uid, gid=gid)
    state_link_info = _require_owned_path(
        state_link, kind="symlink", mode=0, uid=uid, gid=gid
    )
    raw_target = os.readlink(state_link)
    if not os.path.isabs(raw_target):
        raise ValueError("runtime-state link target is not absolute")
    resolved_bundles = bundles.resolve(strict=True)
    state_dir = state_link.resolve(strict=True)
    if state_dir.parent != resolved_bundles:
        raise ValueError("runtime-state bundle is not one confined direct child")
    state_dir_info = _require_owned_path(
        state_dir, kind="directory", mode=0o700, uid=uid, gid=gid
    )
    names = {entry.name for entry in state_dir.iterdir()}
    if names != {"bootstrap-state.json", "runtime-provenance.json"}:
        raise ValueError("runtime-state bundle file inventory is not exact")
    bootstrap_path = state_dir / "bootstrap-state.json"
    provenance_path = state_dir / "runtime-provenance.json"
    bootstrap_raw, bootstrap = _read_owned_json(
        bootstrap_path,
        mode=0o600,
        maximum=32768,
        label="bootstrap state",
        uid=uid,
        gid=gid,
    )
    provenance_raw, provenance = _read_owned_json(
        provenance_path,
        mode=0o600,
        maximum=MAX_PROVENANCE_BYTES,
        label="runtime provenance",
        uid=uid,
        gid=gid,
    )
    if not isinstance(bootstrap, dict) or set(bootstrap) != {
        "schema_version",
        "state",
        "deploy_ref",
        "version",
        "runtime_provenance_sha256",
        "release_tree_sha256",
        "canonical_writer",
        "secret_versions",
        "reboot_helper",
        "completed_at",
    }:
        raise ValueError("bootstrap state shape is not exact")
    if (
        bootstrap.get("schema_version") != 2
        or bootstrap.get("state") != "complete"
        or FULL_SHA_RE.fullmatch(str(bootstrap.get("deploy_ref", ""))) is None
        or re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?",
            str(bootstrap.get("version", "")),
        )
        is None
        or re.fullmatch(
            r"[0-9a-f]{64}", str(bootstrap.get("runtime_provenance_sha256", ""))
        )
        is None
        or re.fullmatch(r"[0-9a-f]{64}", str(bootstrap.get("release_tree_sha256", "")))
        is None
        or not isinstance(bootstrap.get("secret_versions"), dict)
        or type(bootstrap.get("canonical_writer")) is not bool
        or not isinstance(bootstrap.get("reboot_helper"), dict)
        or not isinstance(bootstrap.get("completed_at"), str)
        or bootstrap["runtime_provenance_sha256"]
        != hashlib.sha256(provenance_raw).hexdigest()
    ):
        raise ValueError("bootstrap state identity/checksum is invalid")
    secret_versions = bootstrap["secret_versions"]
    required_secret_names = {
        "control_room_evidence_signing_key_id",
        "control_room_evidence_signing_key",
        "control_room_evidence_signing_previous_keys",
    }
    all_secret_names = required_secret_names | {
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
    }
    if set(secret_versions) != all_secret_names or any(
        re.fullmatch(r"[1-9][0-9]*", str(secret_versions[name])) is None
        for name in required_secret_names
    ):
        raise ValueError("bootstrap secret version provenance is invalid")
    hmac_versions = (
        secret_versions["gcs_hmac_access_key_id"],
        secret_versions["gcs_hmac_secret_access_key"],
    )
    if hmac_versions != ("", "") and any(
        re.fullmatch(r"[1-9][0-9]*", str(value)) is None for value in hmac_versions
    ):
        raise ValueError("bootstrap HMAC secret version provenance is invalid")
    state_link_after = state_link.lstat()
    state_dir_after = state_dir.lstat()
    if (
        (
            state_link_info.st_dev,
            state_link_info.st_ino,
            state_link_info.st_ctime_ns,
        )
        != (
            state_link_after.st_dev,
            state_link_after.st_ino,
            state_link_after.st_ctime_ns,
        )
        or os.readlink(state_link) != raw_target
        or (state_dir_info.st_dev, state_dir_info.st_ino, state_dir_info.st_ctime_ns)
        != (state_dir_after.st_dev, state_dir_after.st_ino, state_dir_after.st_ctime_ns)
        or {entry.name for entry in state_dir.iterdir()} != names
    ):
        raise ValueError("runtime-state changed during its authoritative read")
    del bootstrap_raw
    return state_link, raw_target, state_dir, bootstrap, provenance


def _load_current_release(
    app_root: Path,
    *,
    uid: int = 0,
    gid: int = 0,
) -> Path:
    releases = app_root / "releases"
    current_link = app_root / "current"
    releases_info = releases.lstat()
    current_info = current_link.lstat()
    if (
        not stat.S_ISDIR(releases_info.st_mode)
        or releases.is_symlink()
        or releases_info.st_uid != uid
        or releases_info.st_gid != gid
        or stat.S_IMODE(releases_info.st_mode) & 0o022
        or not stat.S_ISLNK(current_info.st_mode)
        or current_info.st_uid != uid
        or current_info.st_gid != gid
    ):
        raise ValueError("current release root/link ownership is invalid")
    raw_current_target = os.readlink(current_link)
    if not os.path.isabs(raw_current_target):
        raise ValueError("current release link target is not absolute")
    resolved_releases = releases.resolve(strict=True)
    current = current_link.resolve(strict=True)
    if (
        current.parent != resolved_releases
        or FULL_SHA_RE.fullmatch(current.name) is None
    ):
        raise ValueError("current release is not one confined direct child")
    current_directory_info = current.lstat()
    if (
        not stat.S_ISDIR(current_directory_info.st_mode)
        or current.is_symlink()
        or current_directory_info.st_uid != uid
        or current_directory_info.st_gid != gid
        or stat.S_IMODE(current_directory_info.st_mode) & 0o022
    ):
        raise ValueError("current release directory ownership is invalid")
    releases_after = releases.lstat()
    current_after = current_link.lstat()
    if (
        (releases_info.st_dev, releases_info.st_ino, releases_info.st_ctime_ns)
        != (releases_after.st_dev, releases_after.st_ino, releases_after.st_ctime_ns)
        or (current_info.st_dev, current_info.st_ino, current_info.st_ctime_ns)
        != (current_after.st_dev, current_after.st_ino, current_after.st_ctime_ns)
        or os.readlink(current_link) != raw_current_target
    ):
        raise ValueError("current release changed during authoritative resolution")
    return current


def _load_release_version(current: Path) -> str:
    raw = _read_owned_bytes(
        current / "VERSION",
        mode=0o444,
        maximum=128,
        label="release VERSION",
    )
    try:
        version = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("release VERSION is not ASCII") from exc
    if (
        not version
        or raw not in {version.encode("ascii"), f"{version}\n".encode("ascii")}
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version) is None
    ):
        raise ValueError("release VERSION identity is invalid")
    return version


def _rename_exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError("renameat2 is required for runtime-state CAS")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_exchange = 2
    result = renameat2(
        at_fdcwd,
        os.fsencode(first),
        at_fdcwd,
        os.fsencode(second),
        rename_exchange,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _runtime_paths_and_expected(
    app_root: Path,
    payload: dict[str, Any],
) -> tuple[str, str, list[str], str | None, dict[str, str]]:
    current = _load_current_release(app_root)
    deploy_ref = current.name
    version = _load_release_version(current)
    if payload.get("deploy_ref") != deploy_ref or payload.get("version") != version:
        raise ValueError("runtime provenance differs from current release identity")
    shared = app_root / "shared"
    runtime_inputs = [
        f"shared_env={shared / 'infra.env'}",
        f"base_compose={current / 'infra' / 'docker-compose.yml'}",
        f"gcp_compose={shared / 'docker-compose.gcp.yml'}",
    ]
    lock_env: str | None = None
    mode = payload["mode"]
    if mode == "day2":
        lock_path = shared / "image-locks" / deploy_ref / "release-images.env"
        lock_env = str(lock_path)
        runtime_inputs.append(
            "release_compose="
            f"{current / 'infra' / 'terraform-gcp' / 'release' / 'docker-compose.release.yml'}"
        )
        lock, lock_sha256 = load_lock(lock_path, version)
        if payload.get("image_lock_sha256") != lock_sha256:
            raise ValueError("runtime provenance image-lock checksum mismatch")
        expected = expected_service_refs(lock)
    else:
        recorded_inputs = payload["runtime_input_sha256"]
        if "legacy_image_compose" in recorded_inputs:
            runtime_inputs.append(
                f"legacy_image_compose={shared / 'docker-compose.legacy-images.gcp.yml'}"
            )
        expected = {
            service: str(value["configured_ref"])
            for service, value in payload["services"].items()
        }
    if payload["runtime_input_sha256"] != _runtime_input_hashes(runtime_inputs):
        raise ValueError("runtime provenance input hash drift")
    return deploy_ref, version, runtime_inputs, lock_env, expected


def _verify_payload_live(
    app_root: Path,
    payload: dict[str, Any],
    *,
    schema_version: int,
    project: str,
    restart_policy_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, str]]:
    payload = _validate_provenance_payload(
        payload,
        schema_version=schema_version,
        project=project,
    )
    deploy_ref, _version, _inputs, _lock, expected = _runtime_paths_and_expected(
        app_root, payload
    )
    observed = verify_runtime(
        project,
        expected_refs=expected,
        scheduler="required",
        check_one_shots=True,
        restart_policy_overrides=restart_policy_overrides,
        legacy_config_hash=schema_version == 1,
        expected_release_ref=deploy_ref,
    )
    _assert_observed_matches_provenance(
        payload, observed, schema_version=schema_version
    )
    return observed


def _assert_observed_matches_provenance(
    payload: dict[str, Any],
    observed: dict[str, dict[str, str]],
    *,
    schema_version: int,
) -> None:
    if set(observed) != PROVENANCE_SERVICE_NAMES:
        raise ValueError("observed runtime service inventory is not exact")
    expected_fields = {
        "configured_ref",
        "image_id",
        "runtime_config_sha256",
    }
    if schema_version == 2:
        expected_fields.add("container_id")
    for service in PROVENANCE_SERVICE_NAMES:
        recorded = payload["services"][service]
        if {key: observed[service][key] for key in expected_fields} != recorded:
            raise ValueError(f"runtime provenance live drift: {service}")


def _fenced_restart_policy_overrides(
    app_root: Path,
    project: str,
) -> dict[str, dict[str, Any]]:
    state = app_root / "shared" / "restart-policy-fence.json"
    payload = _load_restart_policy_state(state, project=project)
    if payload["state"] != "fenced":
        raise ValueError("provenance adoption requires a durable restart-policy fence")
    recorded = payload["containers"]
    current = _restart_policy_inventory(project)
    _assert_restart_inventory(current, recorded, fenced=True)
    return {
        service: {
            "Name": value["restart_policy"],
            "MaximumRetryCount": value["maximum_retry_count"],
        }
        for service, value in recorded.items()
    }


def _publish_adopted_bundle(
    app_root: Path,
    *,
    state_link: Path,
    expected_link_target: str,
    bootstrap: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    shared = app_root / "shared"
    bundles = shared / "state-bundles"
    bundle = Path(
        tempfile.mkdtemp(
            prefix=f"runtime-v2-{provenance['deploy_ref']}.",
            dir=bundles,
        )
    )
    os.chmod(bundle, 0o700)
    provenance_path = bundle / "runtime-provenance.json"
    bootstrap_path = bundle / "bootstrap-state.json"
    _write_json_exclusive(provenance_path, provenance)
    adopted_bootstrap = dict(bootstrap)
    adopted_provenance_raw = _read_owned_bytes(
        provenance_path,
        mode=0o600,
        maximum=MAX_PROVENANCE_BYTES,
        label="adopted runtime provenance",
    )
    adopted_bootstrap["runtime_provenance_sha256"] = hashlib.sha256(
        adopted_provenance_raw
    ).hexdigest()
    _write_json_exclusive(bootstrap_path, adopted_bootstrap)
    _fsync_dir(bundle)
    _fsync_dir(bundles)

    exchange_link = (
        shared / f".runtime-state-adopt.{os.getpid()}.{secrets.token_hex(8)}"
    )
    os.symlink(str(bundle), exchange_link)
    _fsync_dir(shared)
    if not state_link.is_symlink() or os.readlink(state_link) != expected_link_target:
        os.unlink(exchange_link)
        _fsync_dir(shared)
        raise RuntimeError("runtime-state changed before provenance adoption CAS")
    try:
        _rename_exchange(state_link, exchange_link)
    except OSError:
        if exchange_link.is_symlink() and os.readlink(exchange_link) == str(bundle):
            os.unlink(exchange_link)
            _fsync_dir(shared)
        raise
    _fsync_dir(shared)
    try:
        displaced_is_expected = (
            exchange_link.is_symlink()
            and os.readlink(exchange_link) == expected_link_target
        )
    except OSError:
        displaced_is_expected = False
    if not displaced_is_expected:
        try:
            _rename_exchange(state_link, exchange_link)
            _fsync_dir(shared)
        except OSError as exc:
            raise RuntimeError(
                "runtime-state CAS recovery failed; both paths retained"
            ) from exc
        if exchange_link.is_symlink() and os.readlink(exchange_link) == str(bundle):
            os.unlink(exchange_link)
            _fsync_dir(shared)
        raise RuntimeError("runtime-state changed during provenance adoption")
    os.unlink(exchange_link)
    _fsync_dir(shared)


def command_adopt_provenance(args: argparse.Namespace) -> int:
    app_root = Path(args.app_root)
    if os.geteuid() != 0:
        raise ValueError("provenance adoption requires root")
    if app_root != CANONICAL_APP_ROOT or app_root.is_symlink():
        raise ValueError("provenance adoption app root is not canonical")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.compose_project):
        raise ValueError("Compose project is invalid")
    state_link, raw_target, _state_dir, bootstrap, payload = _load_state_bundle(
        app_root
    )
    restart_policy_overrides = (
        _fenced_restart_policy_overrides(app_root, args.compose_project)
        if getattr(args, "restart_fenced", False)
        else None
    )
    schema_version = (
        payload.get("schema_version") if isinstance(payload, dict) else None
    )
    if schema_version == 2:
        _verify_payload_live(
            app_root,
            payload,
            schema_version=2,
            project=args.compose_project,
            restart_policy_overrides=restart_policy_overrides,
        )
        print("OMEGA_GCP_RUNTIME_CONTRACT\tPASS\tprovenance v2 already exact")
        return 0
    if schema_version != 1:
        raise ValueError("only exact provenance v1 can be adopted")
    _verify_payload_live(
        app_root,
        payload,
        schema_version=1,
        project=args.compose_project,
        restart_policy_overrides=restart_policy_overrides,
    )
    deploy_ref, _version, _inputs, _lock, expected = _runtime_paths_and_expected(
        app_root, payload
    )
    observed_v2 = verify_runtime(
        args.compose_project,
        expected_refs=expected,
        scheduler="required",
        check_one_shots=True,
        restart_policy_overrides=restart_policy_overrides,
        expected_release_ref=deploy_ref,
    )
    adopted = dict(payload)
    adopted["schema_version"] = 2
    adopted["services"] = observed_v2
    adopted = _validate_provenance_payload(
        adopted,
        schema_version=2,
        project=args.compose_project,
    )
    _publish_adopted_bundle(
        app_root,
        state_link=state_link,
        expected_link_target=raw_target,
        bootstrap=bootstrap,
        provenance=adopted,
    )
    _new_link, _new_target, _new_dir, _new_bootstrap, reread = _load_state_bundle(
        app_root
    )
    _verify_payload_live(
        app_root,
        reread,
        schema_version=2,
        project=args.compose_project,
        restart_policy_overrides=restart_policy_overrides,
    )
    print("OMEGA_GCP_RUNTIME_CONTRACT\tPASS\tprovenance v1 adopted to v2")
    return 0


def _validate_identity(deploy_ref: str, version: str, project: str) -> None:
    if not FULL_SHA_RE.fullmatch(deploy_ref):
        raise ValueError("deploy ref must be one full lowercase SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("version is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("Compose project is invalid")


def command_lock(args: argparse.Namespace) -> int:
    _validate_identity(args.deploy_ref, args.version, args.compose_project)
    lock_path = Path(args.lock_env)
    lock, lock_sha256 = load_lock(lock_path, args.version)
    expected = expected_service_refs(lock)
    app_root = Path(args.app_root)
    if app_root != CANONICAL_APP_ROOT or app_root.is_symlink():
        raise ValueError("day2 lock app root is not canonical")
    _state_link, _raw_target, _state_dir, _bootstrap, previous = _load_state_bundle(
        app_root
    )
    previous_schema = (
        previous.get("schema_version") if isinstance(previous, dict) else None
    )
    if previous_schema not in {1, 2}:
        raise ValueError("previous runtime provenance schema is unsupported")
    previous = _validate_provenance_payload(
        previous,
        schema_version=previous_schema,
        project=args.compose_project,
    )
    pinned_services = {
        *INFRASTRUCTURE_SERVICES,
        *(
            f"one-shot:{service}"
            for service in ONE_SHOT_MUTATORS
            if service != PROPRIETARY_INIT_SERVICE
        ),
    }
    for service in pinned_services:
        expected[service] = str(previous["services"][service]["configured_ref"])
    runtime_inputs = _runtime_input_hashes(args.runtime_input)
    if set(runtime_inputs) != {
        "shared_env",
        "base_compose",
        "gcp_compose",
        "release_compose",
    }:
        raise ValueError("day2 runtime inputs are not exact")
    observed = verify_runtime(
        args.compose_project,
        expected_refs=expected,
        scheduler=args.scheduler,
        check_one_shots=args.one_shots,
        expected_release_ref=args.deploy_ref,
    )
    for service in pinned_services:
        prior = previous["services"][service]
        current = observed[service]
        for field in (
            "container_id",
            "configured_ref",
            "image_id",
            "runtime_config_sha256",
        ):
            if current.get(field) != prior.get(field):
                raise ValueError(
                    f"day2 may not legitimize infrastructure drift: {service}.{field}"
                )
    if args.write_provenance:
        if args.scheduler != "required":
            raise ValueError("provenance may be written only with a healthy scheduler")
        _atomic_json(
            Path(args.write_provenance),
            {
                "schema_version": 2,
                "mode": "day2",
                "compose_project": args.compose_project,
                "deploy_ref": args.deploy_ref,
                "version": args.version,
                "image_lock_sha256": lock_sha256,
                "runtime_input_sha256": runtime_inputs,
                "services": observed,
            },
        )
    scheduler_count = 1 if args.scheduler == "required" else 0
    print(
        "OMEGA_GCP_RUNTIME_CONTRACT\tPASS\t"
        f"images=15/15 running={21 + scheduler_count} "
        f"healthy={21 + scheduler_count} scheduler={scheduler_count}"
    )
    return 0


def command_bootstrap_record(args: argparse.Namespace) -> int:
    _validate_identity(args.deploy_ref, args.version, args.compose_project)
    runtime_inputs = _runtime_input_hashes(args.runtime_input)
    expected_input_keys = {"shared_env", "base_compose", "gcp_compose"}
    if "legacy_image_compose" in runtime_inputs:
        expected_input_keys.add("legacy_image_compose")
    if set(runtime_inputs) != expected_input_keys:
        raise ValueError("bootstrap runtime inputs are not exact")
    observed = verify_runtime(
        args.compose_project,
        expected_refs=None,
        scheduler="required",
        check_one_shots=True,
        expected_release_ref=args.deploy_ref,
    )
    _atomic_json(
        Path(args.output),
        {
            "schema_version": 2,
            "mode": "bootstrap",
            "compose_project": args.compose_project,
            "deploy_ref": args.deploy_ref,
            "version": args.version,
            "runtime_input_sha256": runtime_inputs,
            "services": observed,
        },
    )
    print("OMEGA_GCP_RUNTIME_CONTRACT\tPASS\tbootstrap provenance recorded")
    return 0


def command_prestart(args: argparse.Namespace) -> int:
    """Validate persisted provenance before Compose starts dependencies/apps."""
    _validate_identity(args.deploy_ref, args.version, args.compose_project)
    path = Path(args.provenance)
    _raw_provenance, raw_payload = _read_owned_json(
        path,
        mode=0o600,
        maximum=MAX_PROVENANCE_BYTES,
        label="prestart runtime provenance",
    )
    schema_version = (
        raw_payload.get("schema_version") if isinstance(raw_payload, dict) else None
    )
    if schema_version not in {1, 2}:
        raise ValueError("prestart provenance schema is unsupported")
    payload = _validate_provenance_payload(
        raw_payload,
        schema_version=schema_version,
        project=args.compose_project,
    )
    expected_identity = {
        "compose_project": args.compose_project,
        "deploy_ref": args.deploy_ref,
        "version": args.version,
    }
    for key, value in expected_identity.items():
        if payload.get(key) != value:
            raise ValueError(f"runtime provenance mismatch: {key}")
    runtime_inputs = _runtime_input_hashes(args.runtime_input)
    if payload.get("runtime_input_sha256") != runtime_inputs:
        raise ValueError("runtime provenance input hash drift")
    mode = payload.get("mode")
    if mode == "day2":
        if not args.lock_env:
            raise ValueError("day2 prestart requires its exact image lock")
        lock_path = Path(args.lock_env)
        lock, lock_sha256 = load_lock(lock_path, args.version)
        if payload.get("image_lock_sha256") != lock_sha256:
            raise ValueError("day2 provenance image lock checksum mismatch")
        expected = expected_service_refs(lock)
    elif mode == "bootstrap":
        expected = {
            service: str(value["configured_ref"])
            for service, value in payload["services"].items()
        }
    else:
        raise ValueError("runtime provenance mode is invalid")
    fenced_app_root = Path(args.restart_fenced_app_root)
    if fenced_app_root != CANONICAL_APP_ROOT or fenced_app_root.is_symlink():
        raise ValueError("prestart restart-policy app root is not canonical")
    restart_policy_overrides = _fenced_restart_policy_overrides(
        fenced_app_root,
        args.compose_project,
    )
    observed = verify_prestart_runtime(
        args.compose_project,
        expected_refs=expected,
        restart_policy_overrides=restart_policy_overrides,
        legacy_config_hash=schema_version == 1,
        expected_release_ref=payload["deploy_ref"],
    )
    _assert_observed_matches_provenance(
        payload,
        observed,
        schema_version=schema_version,
    )
    print(
        "OMEGA_GCP_RUNTIME_CONTRACT\tPASS\t"
        f"prestart schema={schema_version} containers=26 mutators=stopped"
    )
    return 0


def command_provenance(args: argparse.Namespace) -> int:
    _validate_identity(args.deploy_ref, args.version, args.compose_project)
    path = Path(args.provenance)
    legacy_adoption = bool(getattr(args, "legacy_adoption", False))
    if legacy_adoption and (
        not getattr(args, "restart_fenced_app_root", None)
        or args.scheduler != "stopped"
        or args.one_shots
    ):
        raise ValueError(
            "legacy provenance verification requires the fenced scheduler-stopped path"
        )
    provenance_schema = 1 if legacy_adoption else 2
    raw_payload = getattr(args, "_authoritative_payload", None)
    if raw_payload is None:
        _raw_provenance, raw_payload = _read_owned_json(
            path,
            mode=0o600,
            maximum=MAX_PROVENANCE_BYTES,
            label="runtime provenance",
        )
    payload = _validate_provenance_payload(
        raw_payload,
        schema_version=provenance_schema,
        project=args.compose_project,
    )
    expected_identity = {
        "schema_version": provenance_schema,
        "compose_project": args.compose_project,
        "deploy_ref": args.deploy_ref,
        "version": args.version,
    }
    for key, value in expected_identity.items():
        if payload.get(key) != value:
            raise ValueError(f"runtime provenance mismatch: {key}")
    mode = payload.get("mode")
    runtime_inputs = _runtime_input_hashes(args.runtime_input)
    if payload.get("runtime_input_sha256") != runtime_inputs:
        raise ValueError("runtime provenance input hash drift")
    if mode == "day2":
        if not args.lock_env:
            raise ValueError("day2 provenance requires its exact image lock")
        lock_path = Path(args.lock_env)
        lock, lock_sha256 = load_lock(lock_path, args.version)
        if payload.get("image_lock_sha256") != lock_sha256:
            raise ValueError("day2 provenance image lock checksum mismatch")
        expected = expected_service_refs(lock)
    elif mode == "bootstrap":
        services = payload.get("services")
        assert isinstance(services, dict)
        expected = {
            service: str(value.get("configured_ref", ""))
            for service, value in services.items()
        }
    else:
        raise ValueError("runtime provenance mode is invalid")
    restart_policy_overrides: dict[str, dict[str, Any]] | None = None
    restart_fenced_app_root = getattr(args, "restart_fenced_app_root", None)
    if restart_fenced_app_root:
        fenced_app_root = Path(restart_fenced_app_root)
        if fenced_app_root != CANONICAL_APP_ROOT or fenced_app_root.is_symlink():
            raise ValueError("restart-policy app root is not canonical")
        restart_fence_state = fenced_app_root / "shared" / "restart-policy-fence.json"
        fence_payload = _load_restart_policy_state(
            restart_fence_state,
            project=args.compose_project,
        )
        if fence_payload["state"] != "fenced":
            raise ValueError(
                "runtime provenance requires a durable restart-policy fence"
            )
        recorded_inventory = fence_payload["containers"]
        current_inventory = _restart_policy_inventory(args.compose_project)
        _assert_restart_inventory(
            current_inventory,
            recorded_inventory,
            fenced=True,
        )
        restart_policy_overrides = {
            service: {
                "Name": value["restart_policy"],
                "MaximumRetryCount": value["maximum_retry_count"],
            }
            for service, value in recorded_inventory.items()
        }
    observed = verify_runtime(
        args.compose_project,
        expected_refs=expected,
        scheduler=args.scheduler,
        check_one_shots=args.one_shots,
        restart_policy_overrides=restart_policy_overrides,
        legacy_config_hash=legacy_adoption,
        expected_release_ref=payload["deploy_ref"],
    )
    for service, value in observed.items():
        recorded = payload.get("services", {}).get(service, {})
        if (
            not legacy_adoption
            and recorded.get("container_id") != value["container_id"]
        ):
            raise ValueError(f"runtime provenance container ID drift: {service}")
        if recorded.get("configured_ref") != value["configured_ref"]:
            raise ValueError(f"runtime provenance configured ref drift: {service}")
        if recorded.get("image_id") != value["image_id"]:
            raise ValueError(f"runtime provenance image ID drift: {service}")
        if recorded.get("runtime_config_sha256") != value["runtime_config_sha256"]:
            raise ValueError(f"runtime provenance config drift: {service}")
    print(
        "OMEGA_GCP_RUNTIME_CONTRACT\tPASS\t"
        f"mode={mode} images=15/15 running={22 if args.scheduler == 'required' else 21} "
        f"healthy={22 if args.scheduler == 'required' else 21} "
        f"scheduler={1 if args.scheduler == 'required' else 0}"
    )
    return 0


def command_live_state(args: argparse.Namespace) -> int:
    app_root = Path(args.app_root)
    if not app_root.is_absolute() or app_root.is_symlink():
        raise ValueError("application root must be one absolute directory")
    shared_root = app_root / "shared"
    current = _load_current_release(app_root)
    _state_link, _raw_target, state_dir, bootstrap, payload = _load_state_bundle(
        app_root
    )
    deploy_ref = current.name
    version = _load_release_version(current)
    if bootstrap.get("deploy_ref") != deploy_ref or bootstrap.get("version") != version:
        raise ValueError("bootstrap state differs from current release identity")
    provenance_path = state_dir / "runtime-provenance.json"
    runtime_inputs = [
        f"shared_env={shared_root / 'infra.env'}",
        f"base_compose={current / 'infra' / 'docker-compose.yml'}",
        f"gcp_compose={shared_root / 'docker-compose.gcp.yml'}",
    ]
    lock_env: str | None = None
    if payload.get("mode") == "day2":
        lock_env = str(shared_root / "image-locks" / deploy_ref / "release-images.env")
        runtime_inputs.append(
            "release_compose="
            f"{current / 'infra' / 'terraform-gcp' / 'release' / 'docker-compose.release.yml'}"
        )
    elif payload.get("mode") == "bootstrap":
        recorded_inputs = payload.get("runtime_input_sha256")
        if (
            isinstance(recorded_inputs, dict)
            and "legacy_image_compose" in recorded_inputs
        ):
            runtime_inputs.append(
                f"legacy_image_compose={shared_root / 'docker-compose.legacy-images.gcp.yml'}"
            )
    else:
        raise ValueError("runtime provenance mode is invalid")
    return command_provenance(
        argparse.Namespace(
            compose_project=args.compose_project,
            deploy_ref=deploy_ref,
            version=version,
            runtime_input=runtime_inputs,
            provenance=str(provenance_path),
            lock_env=lock_env,
            scheduler="required",
            one_shots=True,
            restart_fenced_app_root=(
                str(app_root) if getattr(args, "restart_fenced", False) else None
            ),
            _authoritative_payload=payload,
        )
    )


def command_release_identity(args: argparse.Namespace) -> int:
    app_root = Path(args.app_root)
    if not app_root.is_absolute() or app_root.is_symlink():
        raise ValueError("application root must be one absolute directory")
    current = _load_current_release(app_root)
    version = _load_release_version(current)
    print(f"{current.name}\t{version}\t{current}")
    return 0


def command_provenance_metadata(args: argparse.Namespace) -> int:
    _raw, raw_payload = _read_owned_json(
        Path(args.provenance),
        mode=0o600,
        maximum=MAX_PROVENANCE_BYTES,
        label="runtime provenance metadata",
    )
    schema = (
        raw_payload.get("schema_version") if isinstance(raw_payload, dict) else None
    )
    if schema not in {1, 2}:
        raise ValueError("runtime provenance schema is unsupported")
    payload = _validate_provenance_payload(
        raw_payload,
        schema_version=schema,
        project=args.compose_project,
    )
    has_legacy = int("legacy_image_compose" in payload["runtime_input_sha256"])
    print(f"{schema}\t{payload['mode']}\t{has_legacy}")
    return 0


def _validate_static_mounts(
    values: object,
    *,
    service: str,
    release_root: Path,
) -> None:
    if values is None:
        values = []
    if not isinstance(values, list):
        raise ValueError(f"rendered Compose mounts are malformed: {service}")
    allowed_binds = ALLOWED_BIND_MOUNTS.get(service, set())
    allowed_volumes = STATIC_NAMED_VOLUME_MOUNTS.get(service, set())
    observed_binds: set[tuple[str, str, bool]] = set()
    observed_volumes: set[tuple[str, str]] = set()
    for mount in values:
        if not isinstance(mount, dict):
            raise ValueError(f"rendered Compose mount is malformed: {service}")
        mount_type = mount.get("type")
        source = mount.get("source")
        target = mount.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError(f"rendered Compose mount identity is malformed: {service}")
        if mount_type == "bind":
            if set(mount) - {"type", "source", "target", "read_only", "bind"}:
                raise ValueError(
                    f"rendered Compose bind options are forbidden: {service}"
                )
            if mount.get("bind") not in (None, {}):
                raise ValueError(
                    f"rendered Compose bind options are forbidden: {service}"
                )
            read_write = mount.get("read_only") is not True
            if source == "/dev/null":
                relative = source
            else:
                source_path = Path(source)
                try:
                    if source_path.resolve(strict=True) != source_path:
                        raise ValueError
                    relative = source_path.relative_to(release_root).as_posix()
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        f"rendered Compose bind escapes the immutable release: {service}"
                    ) from exc
            candidate = (relative, target, read_write)
            if candidate not in allowed_binds or candidate in observed_binds:
                raise ValueError(
                    f"rendered Compose bind is not in the exact allowlist: {service}"
                )
            observed_binds.add(candidate)
        elif mount_type == "volume":
            if set(mount) - {"type", "source", "target", "read_only", "volume"}:
                raise ValueError(
                    f"rendered Compose named-volume options are forbidden: {service}"
                )
            if mount.get("volume") not in (None, {}) or mount.get("read_only") is True:
                raise ValueError(
                    f"rendered Compose named-volume access is forbidden: {service}"
                )
            candidate_volume = (source, target)
            if (
                candidate_volume not in allowed_volumes
                or candidate_volume in observed_volumes
            ):
                raise ValueError(
                    f"rendered Compose named volume is not allowlisted: {service}"
                )
            observed_volumes.add(candidate_volume)
        else:
            raise ValueError(f"rendered Compose mount type is forbidden: {service}")
    if observed_binds != allowed_binds or observed_volumes != allowed_volumes:
        raise ValueError(f"rendered Compose mount inventory is not exact: {service}")


def _validate_static_builds(
    services: dict[str, object],
    *,
    release_root: Path,
) -> None:
    observed = {
        service
        for service, config in services.items()
        if isinstance(config, dict) and config.get("build") is not None
    }
    if observed not in (set(), set(BUILD_SPECS)):
        raise ValueError("rendered Compose build inventory is not all-or-none")
    for service in observed:
        config = services[service]
        assert isinstance(config, dict)
        build = config.get("build")
        if not isinstance(build, dict) or set(build) != {"context", "dockerfile"}:
            raise ValueError(
                f"rendered Compose build policy/options are not exact: {service}"
            )
        context_relative, dockerfile = BUILD_SPECS[service]
        expected_context = (
            release_root if context_relative == "." else release_root / context_relative
        )
        if (
            build.get("context") != str(expected_context)
            or build.get("dockerfile") != dockerfile
        ):
            raise ValueError(f"rendered Compose build identity differs: {service}")


def _validate_static_networking(
    payload: dict[str, Any],
    services: dict[str, object],
) -> None:
    if payload.get("networks") != {"default": {"name": "infra_default"}}:
        raise ValueError("rendered Compose top-level network inventory is not exact")
    forbidden_fields = (
        "dns",
        "dns_opt",
        "dns_search",
        "external_links",
        "links",
    )
    for service, raw_config in services.items():
        if not isinstance(raw_config, dict):
            raise ValueError(f"rendered Compose service is malformed: {service}")
        networks = raw_config.get("networks")
        if networks not in (None, {}, {"default": None}):
            raise ValueError(
                f"rendered Compose network inventory is forbidden: {service}"
            )
        expected_ports = [
            {
                "mode": "ingress",
                "protocol": "tcp",
                "published": published,
                "target": target,
            }
            for published, target in PUBLISHED_TCP_PORTS.get(service, ())
        ]
        if (raw_config.get("ports") or []) != expected_ports:
            raise ValueError(
                f"rendered Compose published ports are not exact: {service}"
            )
        expected_extra_hosts = list(EXTRA_HOSTS.get(service, ()))
        if (raw_config.get("extra_hosts") or []) != expected_extra_hosts:
            raise ValueError(f"rendered Compose extra hosts are not exact: {service}")
        for field in forbidden_fields:
            if raw_config.get(field) not in (None, [], {}):
                raise ValueError(f"rendered Compose {field} is forbidden: {service}")


def command_compose_security(args: argparse.Namespace) -> int:
    app_root = Path(args.app_root)
    if app_root != CANONICAL_APP_ROOT or app_root.is_symlink():
        raise ValueError("application root must be the canonical GCP host root")
    release_root = _load_current_release(app_root)
    _raw, payload = _read_owned_json(
        Path(args.path),
        mode=0o600,
        maximum=2 * 1024 * 1024,
        label="rendered Compose security contract",
    )
    services = payload.get("services") if isinstance(payload, dict) else None
    if not isinstance(services, dict) or set(services) != set(ALL_CONTAINER_SERVICES):
        raise ValueError("rendered Compose service inventory is not exact")
    if payload.get("configs") not in (None, {}) or payload.get("secrets") not in (
        None,
        {},
    ):
        raise ValueError(
            "rendered Compose host-backed config/secret objects are forbidden"
        )
    volumes = payload.get("volumes")
    if volumes != {
        "airflow_logs": {"name": "infra_airflow_logs"},
        "minio_data": {"name": "infra_minio_data"},
        "postgres_data": {"name": "infra_postgres_data"},
        "postgres_gold_data": {"name": "infra_postgres_gold_data"},
    }:
        raise ValueError("rendered Compose top-level volume inventory is not exact")
    _validate_static_builds(services, release_root=release_root)
    _validate_static_networking(payload, services)
    forbidden_scalars = {
        "privileged": (None, False),
        "pid": (None, "", "private"),
        "uts": (None, "", "private"),
        "userns_mode": (None, "", "private"),
        "cgroup": (None, "", "private"),
        "cgroup_parent": (None, ""),
        "runtime": (None, "", "runc"),
    }
    forbidden_collections = {
        "cap_add",
        "devices",
        "device_cgroup_rules",
        "group_add",
        "volumes_from",
        "sysctls",
    }
    for service, config in services.items():
        if not isinstance(config, dict):
            raise ValueError(f"rendered Compose service is malformed: {service}")
        network_mode = config.get("network_mode")
        if network_mode not in (None, "", "default"):
            raise ValueError(
                f"rendered Compose network namespace is forbidden: {service}"
            )
        ipc = config.get("ipc")
        if ipc not in (None, "", "private", "shareable"):
            raise ValueError(f"rendered Compose IPC namespace is forbidden: {service}")
        for field, allowed in forbidden_scalars.items():
            if config.get(field) not in allowed:
                raise ValueError(f"rendered Compose {field} is forbidden: {service}")
        for field in forbidden_collections:
            if config.get(field) not in (None, [], {}):
                raise ValueError(f"rendered Compose {field} is forbidden: {service}")
        security_options = config.get("security_opt")
        if security_options not in (None, []):
            if (
                not isinstance(security_options, list)
                or any(not isinstance(value, str) for value in security_options)
                or {value.lower().replace(":", "=") for value in security_options}
                != {"no-new-privileges=true"}
            ):
                raise ValueError(
                    f"rendered Compose security options are not allowlisted: {service}"
                )
        if _has_runtime_socket(config.get("volumes")):
            raise ValueError(
                f"rendered Compose runtime socket mount is forbidden: {service}"
            )
        _validate_static_mounts(
            config.get("volumes"),
            service=service,
            release_root=release_root,
        )
    print(
        "OMEGA_GCP_RUNTIME_CONTRACT\tPASS\t"
        f"static Compose security services={len(services)}"
    )
    return 0


def command_restart_policy(args: argparse.Namespace) -> int:
    app_root = Path(args.app_root)
    state_path = Path(args.state)
    expected_state_path = app_root / "shared" / "restart-policy-fence.json"
    if (
        app_root != CANONICAL_APP_ROOT
        or app_root.is_symlink()
        or state_path != expected_state_path
        or state_path.is_symlink()
    ):
        raise ValueError("restart-policy state path is outside the canonical root")
    _validate_restart_policy_parent(state_path)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.compose_project):
        raise ValueError("Compose project is invalid")

    if args.restart_policy_action == "verify-fenced":
        payload = _load_restart_policy_state(
            state_path,
            project=args.compose_project,
        )
        if payload["state"] != "fenced":
            raise RuntimeError("restart-policy contract is not durably fenced")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "operation": "restart-policy-verify-fenced",
                    "containers": len(payload["containers"]),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.restart_policy_action == "prepare":
        try:
            existing = _load_restart_policy_state(
                state_path,
                project=args.compose_project,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and existing["state"] == "fenced":
            recorded = existing["containers"]
            current = _restart_policy_inventory(args.compose_project)
            _assert_restart_inventory(current, recorded, fenced=True)
            command_live_state(
                argparse.Namespace(
                    app_root=str(app_root),
                    compose_project=args.compose_project,
                    restart_fenced=True,
                )
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "operation": "restart-policy-prepare",
                        "idempotent": True,
                        "containers": len(recorded),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if existing is not None and existing["state"] != "restored":
            raise RuntimeError("restart-policy transition is incomplete")
        command_live_state(
            argparse.Namespace(
                app_root=str(app_root),
                compose_project=args.compose_project,
            )
        )
        current = _restart_policy_inventory(args.compose_project)
        _atomic_json(
            state_path,
            _restart_policy_state(
                state="preparing",
                project=args.compose_project,
                containers=current,
            ),
        )
        for service in RESTART_POLICY_SERVICES:
            _set_restart_policy(str(current[service]["container_id"]), "no", 0)
        fenced_inventory = _restart_policy_inventory(args.compose_project)
        _assert_restart_inventory(fenced_inventory, current, fenced=True)
        _atomic_json(
            state_path,
            _restart_policy_state(
                state="fenced",
                project=args.compose_project,
                containers=current,
            ),
        )
        # This final exact readback closes the ps -a/config race between the
        # first inventory and the durable fence publication. Callers may stop
        # Docker only after all 26 canonical containers and normalized
        # provenance still match the newly-published fenced state.
        command_live_state(
            argparse.Namespace(
                app_root=str(app_root),
                compose_project=args.compose_project,
                restart_fenced=True,
            )
        )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "operation": "restart-policy-prepare",
                    "idempotent": False,
                    "containers": len(current),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.restart_policy_action != "restore":
        raise ValueError("restart-policy action is invalid")
    command_live_state(
        argparse.Namespace(
            app_root=str(app_root),
            compose_project=args.compose_project,
            restart_fenced=True,
        )
    )
    current = _restart_policy_inventory(args.compose_project)
    payload = _load_restart_policy_state(
        state_path,
        project=args.compose_project,
    )
    if payload["state"] != "fenced":
        raise RuntimeError("restart-policy restore requires a durable fence")
    recorded = payload["containers"]
    _assert_restart_inventory(current, recorded, fenced=True)
    _atomic_json(
        state_path,
        _restart_policy_state(
            state="restoring",
            project=args.compose_project,
            containers=recorded,
        ),
    )
    for service in RESTART_POLICY_SERVICES:
        value = recorded[service]
        _set_restart_policy(
            str(value["container_id"]),
            str(value["restart_policy"]),
            int(value["maximum_retry_count"]),
        )
    restored_inventory = _restart_policy_inventory(args.compose_project)
    _assert_restart_inventory(restored_inventory, recorded, fenced=False)
    _atomic_json(
        state_path,
        _restart_policy_state(
            state="restored",
            project=args.compose_project,
            containers=recorded,
        ),
    )
    command_live_state(
        argparse.Namespace(
            app_root=str(app_root),
            compose_project=args.compose_project,
            restart_fenced=False,
        )
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "operation": "restart-policy-restore",
                "containers": len(recorded),
            },
            sort_keys=True,
        )
    )
    return 0


def command_fence(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.compose_project):
        raise ValueError("Compose project is invalid")
    verify_global_fence(args.compose_project)
    print("OMEGA_GCP_RUNTIME_CONTRACT\tPASS\tglobal writers fenced")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--compose-project", required=True)
    common.add_argument("--deploy-ref", required=True)
    common.add_argument("--version", required=True)
    common.add_argument("--runtime-input", action="append", required=True)

    lock = commands.add_parser("lock", parents=[common])
    lock.add_argument("--lock-env", required=True)
    lock.add_argument("--scheduler", choices=("stopped", "required"), required=True)
    lock.add_argument("--one-shots", action="store_true")
    lock.add_argument("--write-provenance")
    lock.add_argument("--app-root", required=True)
    lock.set_defaults(handler=command_lock)

    bootstrap = commands.add_parser("bootstrap-record", parents=[common])
    bootstrap.add_argument("--output", required=True)
    bootstrap.set_defaults(handler=command_bootstrap_record)

    prestart = commands.add_parser("prestart", parents=[common])
    prestart.add_argument("--provenance", required=True)
    prestart.add_argument("--lock-env")
    prestart.add_argument("--restart-fenced-app-root", required=True)
    prestart.set_defaults(handler=command_prestart)

    provenance = commands.add_parser("provenance", parents=[common])
    provenance.add_argument("--provenance", required=True)
    provenance.add_argument("--lock-env")
    provenance.add_argument(
        "--scheduler", choices=("stopped", "required"), default="required"
    )
    provenance.add_argument("--one-shots", action="store_true")
    provenance.add_argument("--restart-fenced-app-root")
    provenance.add_argument("--legacy-adoption", action="store_true")
    provenance.set_defaults(handler=command_provenance)

    live_state = commands.add_parser("live-state")
    live_state.add_argument("--app-root", required=True)
    live_state.add_argument("--compose-project", required=True)
    live_state.add_argument("--restart-fenced", action="store_true")
    live_state.set_defaults(handler=command_live_state)

    release_identity = commands.add_parser("release-identity")
    release_identity.add_argument("--app-root", required=True)
    release_identity.set_defaults(handler=command_release_identity)

    provenance_metadata = commands.add_parser("provenance-metadata")
    provenance_metadata.add_argument("--provenance", required=True)
    provenance_metadata.add_argument("--compose-project", required=True)
    provenance_metadata.set_defaults(handler=command_provenance_metadata)

    compose_security = commands.add_parser("compose-security")
    compose_security.add_argument("--path", required=True)
    compose_security.add_argument("--app-root", required=True)
    compose_security.set_defaults(handler=command_compose_security)

    adopt = commands.add_parser("adopt-provenance")
    adopt.add_argument("--app-root", required=True)
    adopt.add_argument("--compose-project", required=True)
    adopt.add_argument("--restart-fenced", action="store_true")
    adopt.set_defaults(handler=command_adopt_provenance)

    restart_policy = commands.add_parser("restart-policy")
    restart_policy.add_argument(
        "restart_policy_action",
        choices=("prepare", "verify-fenced", "restore"),
    )
    restart_policy.add_argument("--app-root", required=True)
    restart_policy.add_argument("--compose-project", required=True)
    restart_policy.add_argument("--state", required=True)
    restart_policy.set_defaults(handler=command_restart_policy)

    fence = commands.add_parser("writer-fence")
    fence.add_argument("--compose-project", required=True)
    fence.set_defaults(handler=command_fence)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"OMEGA_GCP_RUNTIME_CONTRACT\tFAIL\t{exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
