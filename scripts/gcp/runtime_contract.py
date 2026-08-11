#!/usr/bin/env python3
"""Validate the exact GCP Compose runtime against immutable provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
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


def _run(*command: str) -> str:
    result = subprocess.run(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(command[:2])}")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_input_hashes(values: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        if not separator or key not in RUNTIME_INPUT_KEYS or key in hashes:
            raise ValueError("runtime input inventory is invalid or duplicated")
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"runtime input is missing or linked: {key}")
        hashes[key] = _sha256(path)
    base = {"shared_env", "base_compose", "gcp_compose"}
    if not base.issubset(hashes) or not set(hashes).issubset(RUNTIME_INPUT_KEYS):
        raise ValueError("runtime input inventory lacks its canonical base")
    if "legacy_image_compose" in hashes and "release_compose" in hashes:
        raise ValueError("legacy and day-2 image overlays cannot be active together")
    return hashes


def load_lock(path: Path) -> dict[str, str]:
    expected_keys = {value[0] for value in IMAGE_KEYS.values()}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
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

    tag = None
    for service, (key, repository) in IMAGE_KEYS.items():
        match = re.fullmatch(
            rf"ghcr\.io/([a-z0-9][a-z0-9-]*)/{re.escape(repository)}:"
            r"(v[0-9][0-9A-Za-z._-]*)@(sha256:[0-9a-f]{64})",
            values[key],
        )
        if not match:
            raise ValueError(f"invalid locked reference for {service}")
        tag = tag or match.group(2)
        if match.group(1) != GHCR_OWNER or tag != match.group(2):
            raise ValueError("image lock owner or release tag is not canonical")
    return values


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
        str((_inspect(canonical_ids[service]).get("Config") or {}).get("Image", "")):
        (service, canonical_ids[service])
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
    for service in MUTATING_SERVICES:
        if _global_running_ids(service):
            raise RuntimeError(f"GCP host-local mutator remains running: {service}")
    for container_id in _all_running_ids():
        configured = str((_inspect(container_id).get("Config") or {}).get("Image", ""))
        if _proprietary_repository(configured) is not None:
            raise RuntimeError("a proprietary writer image remains globally running")
        if configured.startswith("apache/superset:") or configured.startswith(
            "apache/superset@"
        ):
            raise RuntimeError("a Superset Analytics writer remains globally running")


def _inspect(container_id: str) -> dict[str, Any]:
    payload = json.loads(_run("docker", "inspect", container_id))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("docker inspect did not return one container")
    return payload[0]


def _image_id(reference: str) -> str:
    payload = json.loads(_run("docker", "image", "inspect", reference))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("docker image inspect did not return one image")
    image_id = payload[0].get("Id", "")
    if not SHA256_RE.fullmatch(image_id):
        raise RuntimeError("locked image ID is invalid")
    return image_id


def _runtime_config_sha256(info: dict[str, Any]) -> str:
    """Hash deterministic runtime configuration without emitting secret values."""
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
            key: host.get(key)
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
) -> dict[str, str]:
    ids = _container_ids(project, service)
    if len(ids) != 1:
        raise RuntimeError(f"service={service} containers={len(ids)} expected=1")
    info = _inspect(ids[0])
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
    return {
        "configured_ref": configured_ref,
        "image_id": image_id,
        "runtime_config_sha256": _runtime_config_sha256(info),
    }


def verify_runtime(
    project: str,
    *,
    expected_refs: dict[str, str] | None,
    scheduler: str,
    check_one_shots: bool,
) -> dict[str, dict[str, str]]:
    if expected_refs is not None and not set(IMAGE_KEYS).issubset(expected_refs):
        raise ValueError("runtime image inventory must contain exactly 15 services")
    observed: dict[str, dict[str, str]] = {}
    canonical_ids: dict[str, str] = {}
    for service in IMAGE_KEYS:
        observed[service] = _require_exact_container(
            project,
            service,
            expected_ref=(expected_refs or {}).get(service),
            running=True,
            healthy=True,
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
            expected_ref=None,
            running=True,
            healthy=True,
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
            observed[f"one-shot:{service}"] = {
                "configured_ref": configured_ref,
                "image_id": image_id,
                "runtime_config_sha256": _runtime_config_sha256(info),
            }
    _verify_global_writer_inventory(project, canonical_ids, scheduler=scheduler)
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
    lock = load_lock(lock_path)
    expected = expected_service_refs(lock)
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
    )
    if args.write_provenance:
        if args.scheduler != "required":
            raise ValueError("provenance may be written only with a healthy scheduler")
        _atomic_json(
            Path(args.write_provenance),
            {
                "schema_version": 1,
                "mode": "day2",
                "compose_project": args.compose_project,
                "deploy_ref": args.deploy_ref,
                "version": args.version,
                "image_lock_sha256": _sha256(lock_path),
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
    )
    _atomic_json(
        Path(args.output),
        {
            "schema_version": 1,
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


def command_provenance(args: argparse.Namespace) -> int:
    _validate_identity(args.deploy_ref, args.version, args.compose_project)
    path = Path(args.provenance)
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_identity = {
        "schema_version": 1,
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
        if payload.get("image_lock_sha256") != _sha256(lock_path):
            raise ValueError("day2 provenance image lock checksum mismatch")
        expected = expected_service_refs(load_lock(lock_path))
    elif mode == "bootstrap":
        services = payload.get("services")
        expected_names = (
            set(IMAGE_KEYS)
            | set(INFRASTRUCTURE_SERVICES)
            | {SCHEDULER_SERVICE}
            | {f"one-shot:{service}" for service in ONE_SHOT_MUTATORS}
        )
        if not isinstance(services, dict) or set(services) != expected_names:
            raise ValueError("bootstrap provenance service inventory mismatch")
        expected = {
            service: str(value.get("configured_ref", ""))
            for service, value in services.items()
        }
    else:
        raise ValueError("runtime provenance mode is invalid")
    observed = verify_runtime(
        args.compose_project,
        expected_refs=expected,
        scheduler=args.scheduler,
        check_one_shots=args.one_shots,
    )
    for service, value in observed.items():
        recorded = payload.get("services", {}).get(service, {})
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
    lock.set_defaults(handler=command_lock)

    bootstrap = commands.add_parser("bootstrap-record", parents=[common])
    bootstrap.add_argument("--output", required=True)
    bootstrap.set_defaults(handler=command_bootstrap_record)

    provenance = commands.add_parser("provenance", parents=[common])
    provenance.add_argument("--provenance", required=True)
    provenance.add_argument("--lock-env")
    provenance.add_argument(
        "--scheduler", choices=("stopped", "required"), default="required"
    )
    provenance.add_argument("--one-shots", action="store_true")
    provenance.set_defaults(handler=command_provenance)

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
