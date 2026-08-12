from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.gcp import runtime_contract


def _secure_info(*, service: str = "console", index: int = 1) -> dict[str, object]:
    network_id = "e" * 64
    container_name = f"mode_{service.replace('-', '_')}"
    container_id = f"{index:064x}"
    endpoint_id = f"{index + 100:064x}"
    ip_address = f"172.18.0.{index + 1}"
    port_bindings = {
        f"{target}/tcp": [{"HostIp": "", "HostPort": published}]
        for published, target in runtime_contract.PUBLISHED_TCP_PORTS.get(service, ())
    }
    return {
        "Id": container_id,
        "Name": f"/{container_name}",
        "Image": "sha256:" + "1" * 64,
        "Path": "/entrypoint",
        "Args": ["serve"],
        "Config": {
            "Image": "example.invalid/image:tag",
            "Env": ["SECRET=value"],
            "Labels": {
                "com.docker.compose.project": "infra",
                "com.docker.compose.service": service,
            },
        },
        "HostConfig": {
            "NetworkMode": "infra_default",
            "Privileged": False,
            "AutoRemove": False,
            "PublishAllPorts": False,
            "PortBindings": port_bindings,
            "ExtraHosts": list(runtime_contract.EXTRA_HOSTS.get(service, ())),
            "Dns": None,
            "DnsOptions": None,
            "DnsSearch": None,
            "Links": None,
            "PidMode": "",
            "UTSMode": "",
            "IpcMode": "private",
            "Devices": [],
            "DeviceRequests": [],
            "DeviceCgroupRules": [],
            "Sysctls": None,
            "CgroupParent": "",
            "Runtime": "runc",
            "Binds": [],
            "RestartPolicy": {
                "Name": "unless-stopped",
                "MaximumRetryCount": 0,
            },
        },
        "Mounts": [],
        "NetworkSettings": {
            "Networks": {
                "infra_default": {
                    "Aliases": [container_name, service],
                    "DriverOpts": None,
                    "GwPriority": 0,
                    "IPAMConfig": None,
                    "Links": None,
                    "NetworkID": network_id,
                    "EndpointID": endpoint_id,
                    "Gateway": "172.18.0.1",
                    "IPAddress": ip_address,
                    "MacAddress": f"02:00:00:00:00:{index:02x}",
                    "IPPrefixLen": 16,
                    "IPv6Gateway": "",
                    "GlobalIPv6Address": "",
                    "GlobalIPv6PrefixLen": 0,
                    "DNSNames": [container_name, service, container_id[:12]],
                }
            }
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
    }


def _network_info(
    canonical: dict[str, str],
    *,
    driver: str = "bridge",
) -> dict[str, object]:
    containers: dict[str, object] = {}
    for index, (service, container_id) in enumerate(canonical.items(), start=1):
        info = _secure_info(service=service, index=index)
        attachment = info["NetworkSettings"]["Networks"]["infra_default"]  # type: ignore[index]
        containers[container_id] = {
            "Name": str(info["Name"])[1:],
            "EndpointID": attachment["EndpointID"],  # type: ignore[index]
            "MacAddress": attachment["MacAddress"],  # type: ignore[index]
            "IPv4Address": f"{attachment['IPAddress']}/16",  # type: ignore[index]
            "IPv6Address": "",
        }
    return {
        "Id": "e" * 64,
        "Name": "infra_default",
        "Driver": driver,
        "Scope": "local",
        "EnableIPv4": True,
        "EnableIPv6": False,
        "IPAM": {
            "Driver": "default",
            "Options": None,
            "Config": [{"Subnet": "172.18.0.0/16", "Gateway": "172.18.0.1"}],
        },
        "Internal": False,
        "Attachable": False,
        "Ingress": False,
        "ConfigFrom": {"Network": ""},
        "ConfigOnly": False,
        "Options": {},
        "Labels": {
            "com.docker.compose.config-hash": "c" * 64,
            "com.docker.compose.network": "default",
            "com.docker.compose.project": "infra",
            "com.docker.compose.version": "2.40.3",
        },
        "Containers": containers,
    }


def _provenance_payload(schema_version: int) -> dict[str, object]:
    services: dict[str, dict[str, str]] = {}
    for index, service in enumerate(
        sorted(runtime_contract.PROVENANCE_SERVICE_NAMES), start=1
    ):
        value = {
            "configured_ref": "example.invalid/image:tag",
            "image_id": "sha256:" + "1" * 64,
            "runtime_config_sha256": f"{index:064x}",
        }
        if schema_version == 2:
            value["container_id"] = f"{index + 100:064x}"
        services[service] = value
    return {
        "schema_version": schema_version,
        "mode": "bootstrap",
        "compose_project": "infra",
        "deploy_ref": "a" * 40,
        "version": "1.45.207-beta",
        "runtime_input_sha256": {
            "shared_env": "1" * 64,
            "base_compose": "2" * 64,
            "gcp_compose": "3" * 64,
        },
        "services": services,
    }


def _state_bundle_layout(
    tmp_path: Path,
    provenance: dict[str, object] | None = None,
) -> tuple[Path, Path, Path]:
    app_root = tmp_path / "modecissions"
    shared = app_root / "shared"
    bundles = shared / "state-bundles"
    bundle = bundles / "legacy"
    bundle.mkdir(parents=True, mode=0o700)
    shared.chmod(0o755)
    bundles.chmod(0o700)
    bundle.chmod(0o700)
    provenance_path = bundle / "runtime-provenance.json"
    provenance_path.write_text(
        json.dumps(provenance or _provenance_payload(1), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    provenance_path.chmod(0o600)
    bootstrap_path = bundle / "bootstrap-state.json"
    bootstrap_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "state": "complete",
                "deploy_ref": "a" * 40,
                "version": "1.45.207-beta",
                "runtime_provenance_sha256": hashlib.sha256(
                    provenance_path.read_bytes()
                ).hexdigest(),
                "release_tree_sha256": "d" * 64,
                "canonical_writer": True,
                "secret_versions": {
                    "control_room_evidence_signing_key_id": "1",
                    "control_room_evidence_signing_key": "2",
                    "control_room_evidence_signing_previous_keys": "3",
                    "gcs_hmac_access_key_id": "",
                    "gcs_hmac_secret_access_key": "",
                },
                "reboot_helper": {"mode": "current"},
                "completed_at": "2026-08-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    bootstrap_path.chmod(0o600)
    state_link = shared / "runtime-state"
    state_link.symlink_to(bundle, target_is_directory=True)
    return app_root, bundle, state_link


def _policy_inventory(*, fenced: bool) -> dict[str, dict[str, str | int]]:
    inventory: dict[str, dict[str, str | int]] = {}
    for index, service in enumerate(runtime_contract.RESTART_POLICY_SERVICES, start=1):
        name = (
            "no"
            if fenced or service in runtime_contract.ONE_SHOT_MUTATORS
            else "unless-stopped"
        )
        inventory[service] = {
            "container_id": f"{index:064x}",
            "restart_policy": name,
            "maximum_retry_count": 0,
        }
    return inventory


def test_restart_policy_fence_covers_all_26_and_stays_bounded() -> None:
    assert runtime_contract.RESTART_POLICY_SERVICES == (
        runtime_contract.ALL_CONTAINER_SERVICES
    )
    payload = runtime_contract._restart_policy_state(
        state="fenced",
        project="infra",
        containers=_policy_inventory(fenced=False),
    )
    assert len(json.dumps(payload, sort_keys=True).encode()) < 16 * 1024


def test_image_lock_is_single_read_private_and_bound_to_exact_version(
    tmp_path: Path,
) -> None:
    def write_lock(path: Path, version: str) -> None:
        path.write_text(
            "\n".join(
                f"{key}=ghcr.io/{runtime_contract.GHCR_OWNER}/{repository}:"
                f"v{version}@sha256:{index:064x}"
                for index, (_service, (key, repository)) in enumerate(
                    runtime_contract.IMAGE_KEYS.items(),
                    start=1,
                )
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    lock = tmp_path / "release-images.env"
    write_lock(lock, "1.45.207-beta")
    values, digest = runtime_contract.load_lock(
        lock,
        "1.45.207-beta",
        uid=os.getuid(),
        gid=os.getgid(),
    )
    assert len(values) == 15
    assert digest == hashlib.sha256(lock.read_bytes()).hexdigest()

    write_lock(lock, "1.45.206-beta")
    with pytest.raises(ValueError, match="release tag is not canonical"):
        runtime_contract.load_lock(
            lock,
            "1.45.207-beta",
            uid=os.getuid(),
            gid=os.getgid(),
        )

    write_lock(lock, "1.45.207-beta")
    lock.chmod(0o644)
    with pytest.raises(ValueError, match="descriptor identity"):
        runtime_contract.load_lock(
            lock,
            "1.45.207-beta",
            uid=os.getuid(),
            gid=os.getgid(),
        )
    link = tmp_path / "linked.env"
    link.symlink_to(lock)
    with pytest.raises(OSError):
        runtime_contract.load_lock(
            link,
            "1.45.207-beta",
            uid=os.getuid(),
            gid=os.getgid(),
        )


def _stub_container(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, str], Callable[[str], list[str]]]:
    ids = {
        service: f"container-{index}"
        for index, service in enumerate(
            runtime_contract.ALL_CONTAINER_SERVICES,
            start=1,
        )
    }

    def container_ids(
        _project: str, service: str, *, running_only: bool = False
    ) -> list[str]:
        del running_only
        return [ids[service]]

    monkeypatch.setattr(runtime_contract, "_container_ids", container_ids)
    monkeypatch.setattr(
        runtime_contract,
        "_all_container_ids",
        lambda: sorted(ids.values()),
    )
    monkeypatch.setattr(runtime_contract, "_validate_project_network", lambda *_a: None)
    monkeypatch.setattr(
        runtime_contract,
        "_inspect",
        lambda container_id: _secure_info(
            service=next(name for name, value in ids.items() if value == container_id)
        ),
    )
    # These tests isolate global inventory and network semantics. Exact mount
    # equality has dedicated adversarial coverage below.
    monkeypatch.setattr(
        runtime_contract, "_validate_runtime_mounts", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        runtime_contract,
        "_require_exact_container",
        lambda *_args, **_kwargs: {
            "container_id": "3" * 64,
            "configured_ref": "example.invalid/image@sha256:" + "0" * 64,
            "image_id": "sha256:" + "1" * 64,
            "runtime_config_sha256": "2" * 64,
        },
    )
    return ids, container_ids


def test_runtime_rejects_any_global_container_outside_the_exact_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids, _ = _stub_container(monkeypatch)
    canonical = {
        ids[service]
        for service in (
            *runtime_contract.IMAGE_KEYS,
            *runtime_contract.INFRASTRUCTURE_SERVICES,
            runtime_contract.SCHEDULER_SERVICE,
        )
    }
    monkeypatch.setattr(
        runtime_contract,
        "_global_running_ids",
        lambda service: [ids[service]],
    )
    monkeypatch.setattr(
        runtime_contract,
        "_all_running_ids",
        lambda: sorted(canonical | {"sha256-alias-or-unlabelled-extra"}),
    )
    monkeypatch.setattr(
        runtime_contract, "_verify_global_writer_inventory", lambda *_a, **_k: None
    )

    with pytest.raises(RuntimeError, match="global running-container inventory"):
        runtime_contract.verify_runtime(
            "infra",
            expected_refs=None,
            scheduler="required",
            check_one_shots=False,
        )


def test_runtime_rejects_an_extra_stopped_always_restart_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids, _ = _stub_container(monkeypatch)
    monkeypatch.setattr(
        runtime_contract,
        "_all_container_ids",
        lambda: sorted((*ids.values(), "stopped-extra-with-restart-always")),
    )

    with pytest.raises(RuntimeError, match="exact 26 canonical containers"):
        runtime_contract.verify_runtime(
            "infra",
            expected_refs=None,
            scheduler="required",
            check_one_shots=False,
        )


@pytest.mark.parametrize("service", ["console", "airflow-init"])
def test_runtime_rejects_host_network_for_running_or_stopped_canonical_container(
    service: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids, _ = _stub_container(monkeypatch)

    def inspect(container_id: str) -> dict[str, object]:
        name = next(name for name, value in ids.items() if value == container_id)
        info = _secure_info(service=name)
        if container_id == ids[service]:
            info["HostConfig"]["NetworkMode"] = "host"  # type: ignore[index]
        return info

    monkeypatch.setattr(runtime_contract, "_inspect", inspect)
    with pytest.raises(RuntimeError, match="forbidden network namespace"):
        runtime_contract.verify_runtime(
            "infra",
            expected_refs=None,
            scheduler="required",
            check_one_shots=False,
        )


@pytest.mark.parametrize("driver", ["macvlan", "ipvlan"])
def test_runtime_rejects_non_bridge_canonical_network(
    driver: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_id = "e" * 64
    canonical = {
        service: f"{index:064x}"
        for index, service in enumerate(
            runtime_contract.ALL_CONTAINER_SERVICES,
            start=1,
        )
    }
    monkeypatch.setattr(
        runtime_contract,
        "_inspect",
        lambda container_id: _secure_info(
            service=next(
                name for name, value in canonical.items() if value == container_id
            ),
            index=next(
                index
                for index, value in enumerate(canonical.values(), start=1)
                if value == container_id
            ),
        ),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_project_network_rows",
        lambda _project: [(network_id[:12], "infra_default")],
    )
    monkeypatch.setattr(
        runtime_contract,
        "_network_inspect",
        lambda _network_id: _network_info(canonical, driver=driver),
    )

    with pytest.raises(RuntimeError, match="not a local bridge"):
        runtime_contract._validate_project_network("infra", canonical)


def test_runtime_rejects_network_identity_or_inventory_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {"console": "1" * 64, "workspace": "2" * 64}

    def inspect(container_id: str) -> dict[str, object]:
        service = next(
            name for name, value in canonical.items() if value == container_id
        )
        info = _secure_info(service=service)
        attachment = info["NetworkSettings"]["Networks"]["infra_default"]  # type: ignore[index]
        attachment["NetworkID"] = (  # type: ignore[index]
            "e" * 64 if container_id == canonical["console"] else "f" * 64
        )
        return info

    monkeypatch.setattr(runtime_contract, "_inspect", inspect)
    monkeypatch.setattr(
        runtime_contract,
        "_project_network_rows",
        lambda _project: [("e" * 12, "infra_default")],
    )
    with pytest.raises(RuntimeError, match="do not share one exact network"):
        runtime_contract._validate_project_network("infra", canonical)

    monkeypatch.setattr(
        runtime_contract,
        "_inspect",
        lambda container_id: _secure_info(
            service=next(
                name for name, value in canonical.items() if value == container_id
            )
        ),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_project_network_rows",
        lambda _project: [
            ("e" * 12, "infra_default"),
            ("f" * 12, "infra_extra"),
        ],
    )
    with pytest.raises(RuntimeError, match="network inventory is not exact"):
        runtime_contract._validate_project_network("infra", canonical)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda attachment: attachment["Aliases"].append("postgres"),
            "attachment is not exact",
        ),
        (lambda attachment: attachment.update(Links=["postgres"]), "not exact"),
        (lambda attachment: attachment.update(DriverOpts={"foo": "bar"}), "not exact"),
        (
            lambda attachment: attachment.update(
                IPAMConfig={"IPv4Address": "172.18.0.5"}
            ),
            "not exact",
        ),
        (lambda attachment: attachment.update(GwPriority=1), "not exact"),
        (
            lambda attachment: attachment["DNSNames"].append("postgres"),
            "DNS names are not exact",
        ),
    ],
)
def test_runtime_network_attachment_rejects_alias_links_driver_ipam_or_dns_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    message: str,
) -> None:
    canonical = {"console": "1" * 64}
    info = _secure_info(service="console")
    info["Id"] = canonical["console"]
    attachment = info["NetworkSettings"]["Networks"]["infra_default"]  # type: ignore[index]
    attachment["DNSNames"][-1] = canonical["console"][:12]  # type: ignore[index]
    mutation(attachment)
    monkeypatch.setattr(runtime_contract, "_inspect", lambda _container_id: info)
    monkeypatch.setattr(
        runtime_contract,
        "_project_network_rows",
        lambda _project: [("e" * 12, "infra_default")],
    )
    monkeypatch.setattr(
        runtime_contract,
        "_network_inspect",
        lambda _network_id: _network_info(canonical),
    )
    with pytest.raises(RuntimeError, match=message):
        runtime_contract._validate_project_network("infra", canonical)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda network: network.update(
                Options={"com.docker.network.bridge.name": "x"}
            ),
            "local bridge",
        ),
        (lambda network: network.update(Attachable=True), "local bridge"),
        (lambda network: network.update(Ingress=True), "local bridge"),
        (
            lambda network: network["IPAM"].update(Options={"foo": "bar"}),  # type: ignore[union-attr]
            "local bridge",
        ),
        (
            lambda network: network["Labels"].update({"unexpected": "label"}),  # type: ignore[union-attr]
            "local bridge",
        ),
        (
            lambda network: network["Containers"].update(  # type: ignore[union-attr]
                {"f" * 64: {"Name": "foreign"}}
            ),
            "container inventory",
        ),
    ],
)
def test_runtime_network_inspect_rejects_options_ipam_labels_or_container_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    message: str,
) -> None:
    canonical = {"console": "1" * 64}
    info = _secure_info(service="console")
    info["Id"] = canonical["console"]
    attachment = info["NetworkSettings"]["Networks"]["infra_default"]  # type: ignore[index]
    attachment["DNSNames"][-1] = canonical["console"][:12]  # type: ignore[index]
    network = _network_info(canonical)
    mutation(network)
    monkeypatch.setattr(runtime_contract, "_inspect", lambda _container_id: info)
    monkeypatch.setattr(
        runtime_contract,
        "_project_network_rows",
        lambda _project: [("e" * 12, "infra_default")],
    )
    monkeypatch.setattr(
        runtime_contract, "_network_inspect", lambda _network_id: network
    )
    with pytest.raises(RuntimeError, match=message):
        runtime_contract._validate_project_network("infra", canonical)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda info: info["HostConfig"]["PortBindings"].update(  # type: ignore[index]
                {"22/tcp": [{"HostIp": "", "HostPort": "22"}]}
            ),
            "published port inventory",
        ),
        (
            lambda info: info["HostConfig"].update(  # type: ignore[union-attr]
                ExtraHosts=["metadata.google.internal:169.254.169.254"]
            ),
            "extra host inventory",
        ),
        (
            lambda info: info["HostConfig"].update(Dns=["8.8.8.8"]),  # type: ignore[union-attr]
            "host Dns is forbidden",
        ),
        (
            lambda info: info["HostConfig"].update(NetworkMode="foreign_default"),  # type: ignore[union-attr]
            "network identity differs",
        ),
    ],
)
def test_runtime_container_rejects_ports_extra_hosts_dns_or_network_drift(
    mutation,
    message: str,
) -> None:
    info = _secure_info(service="console")
    mutation(info)
    with pytest.raises(RuntimeError, match=message):
        runtime_contract._validate_container_security(
            info,
            service="console",
            project="infra",
        )


def _static_network_payload() -> tuple[dict[str, object], dict[str, object]]:
    services: dict[str, object] = {}
    for service in runtime_contract.ALL_CONTAINER_SERVICES:
        services[service] = {
            "networks": {"default": None},
            "ports": [
                {
                    "mode": "ingress",
                    "protocol": "tcp",
                    "published": published,
                    "target": target,
                }
                for published, target in runtime_contract.PUBLISHED_TCP_PORTS.get(
                    service, ()
                )
            ],
            "extra_hosts": list(runtime_contract.EXTRA_HOSTS.get(service, ())),
        }
    return {"networks": {"default": {"name": "infra_default"}}}, services


@pytest.mark.parametrize(
    ("service", "field", "value", "message"),
    [
        (
            "console",
            "networks",
            {"default": {"aliases": ["postgres"]}},
            "network inventory",
        ),
        (
            "console",
            "ports",
            [{"mode": "host", "protocol": "tcp", "published": "8000", "target": 8000}],
            "published ports",
        ),
        (
            "console",
            "extra_hosts",
            ["metadata.google.internal:host-gateway"],
            "extra hosts",
        ),
        ("console", "dns", ["8.8.8.8"], "dns is forbidden"),
        ("console", "links", ["postgres"], "links is forbidden"),
    ],
)
def test_static_compose_networking_rejects_alias_ports_hosts_dns_or_links(
    service: str,
    field: str,
    value: object,
    message: str,
) -> None:
    payload, services = _static_network_payload()
    services[service][field] = value  # type: ignore[index]
    with pytest.raises(ValueError, match=message):
        runtime_contract._validate_static_networking(payload, services)


def test_static_compose_networking_requires_exact_top_level_network() -> None:
    payload, services = _static_network_payload()
    payload["networks"] = {
        "default": {"name": "infra_default"},
        "foreign": {"external": True},
    }
    with pytest.raises(ValueError, match="top-level network inventory"):
        runtime_contract._validate_static_networking(payload, services)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("PidMode", "host", "forbidden PidMode"),
        ("UTSMode", "host", "forbidden UTSMode"),
        ("IpcMode", "host", "forbidden IpcMode"),
        ("Devices", [{"PathOnHost": "/dev/mem"}], "host device access"),
        ("DeviceRequests", [{"Driver": "nvidia"}], "host device access"),
        ("DeviceCgroupRules", ["a *:* rwm"], "host device access"),
        ("Sysctls", {"kernel.core_pattern": "x"}, "host sysctls"),
        ("CgroupParent", "/", "cgroup parent"),
        ("Runtime", "runsc", "nonstandard OCI runtime"),
        ("AutoRemove", True, "AutoRemove"),
        ("PublishAllPorts", True, "PublishAllPorts"),
        ("GroupAdd", ["docker"], "supplemental host groups"),
    ),
)
def test_runtime_semantic_denies_cover_legacy_unhashed_host_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    info = _secure_info()
    info["HostConfig"][field] = value  # type: ignore[index]
    with pytest.raises(RuntimeError, match=message):
        runtime_contract._validate_container_security(info, service="console")


def test_runtime_semantic_deny_blocks_any_docker_socket_mount() -> None:
    info = _secure_info()
    info["Mounts"] = [
        {
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Destination": "/run/engine.sock",
        }
    ]
    with pytest.raises(RuntimeError, match="Docker socket access"):
        runtime_contract._validate_container_security(info, service="console")


def test_release_mount_source_is_exact_regular_path_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    release_ref = "a" * 40
    source = tmp_path / "releases" / release_ref / "cartridges"
    source.mkdir(parents=True)
    assert runtime_contract._release_mount_source(str(source), app_root=tmp_path) == (
        release_ref,
        "cartridges",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    source.rmdir()
    source.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        runtime_contract._release_mount_source(str(source), app_root=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda mounts: mounts.pop(), "inventory is not exact"),
        (
            lambda mounts: mounts.append(dict(mounts[0])),
            "not in the exact host allowlist",
        ),
        (lambda mounts: mounts[0].update(RW=True), "allowlist"),
        (lambda mounts: mounts[0].update(Propagation="shared"), "propagation"),
        (lambda mounts: mounts[0].update(Type="tmpfs"), "type is forbidden"),
    ],
)
def test_runtime_mount_inventory_fails_closed_for_missing_extra_or_mode_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    message: str,
) -> None:
    release_ref = "a" * 40
    mounts = [
        {
            "Type": "bind",
            "Source": f"/opt/modecissions/releases/{release_ref}/airflow/dags",
            "Destination": "/opt/airflow/dags",
            "RW": False,
            "Propagation": "rprivate",
        },
        {
            "Type": "bind",
            "Source": f"/opt/modecissions/releases/{release_ref}/cartridges",
            "Destination": "/registry/cartridges",
            "RW": False,
            "Propagation": "rprivate",
        },
    ]
    monkeypatch.setattr(
        runtime_contract,
        "_release_mount_source",
        lambda source: (release_ref, source.split(f"/{release_ref}/", 1)[1]),
    )
    mutation(mounts)
    with pytest.raises(RuntimeError, match=message):
        runtime_contract._validate_runtime_mounts(
            mounts,
            service="mcp-infra",
            expected_release_ref=release_ref,
        )


def test_runtime_mounts_require_exact_named_volume_and_single_release_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_ref = "a" * 40
    mounts = [
        {
            "Type": "bind",
            "Source": f"/opt/modecissions/releases/{release_ref}/infra/init",
            "Destination": "/docker-entrypoint-initdb.d",
            "RW": False,
            "Propagation": "rprivate",
        },
        {
            "Type": "volume",
            "Name": "infra_postgres_data",
            "Source": "/var/lib/docker/volumes/infra_postgres_data/_data",
            "Destination": "/var/lib/postgresql/data",
            "RW": True,
            "Driver": "local",
        },
    ]
    monkeypatch.setattr(
        runtime_contract,
        "_release_mount_source",
        lambda source: (release_ref, source.split(f"/{release_ref}/", 1)[1]),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_volume_inspect",
        lambda name: {
            "Name": name,
            "Driver": "local",
            "Scope": "local",
            "Options": None,
            "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
            "Labels": {
                "com.docker.compose.config-hash": "c" * 64,
                "com.docker.compose.project": "infra",
                "com.docker.compose.version": "2.40.3",
                "com.docker.compose.volume": "postgres_data",
            },
        },
    )
    runtime_contract._validate_runtime_mounts(mounts, service="postgres")
    mounts[1]["Name"] = "foreign_postgres_data"
    with pytest.raises(RuntimeError, match="not allowlisted"):
        runtime_contract._validate_runtime_mounts(mounts, service="postgres")

    bind_mounts = [
        {
            "Type": "bind",
            "Source": f"/opt/modecissions/releases/{release_ref}/airflow/dags",
            "Destination": "/opt/airflow/dags",
            "RW": False,
        },
        {
            "Type": "bind",
            "Source": f"/opt/modecissions/releases/{'b' * 40}/cartridges",
            "Destination": "/registry/cartridges",
            "RW": False,
        },
    ]
    monkeypatch.setattr(
        runtime_contract,
        "_release_mount_source",
        lambda source: (
            source.split("/releases/", 1)[1].split("/", 1)[0],
            source.split("/releases/", 1)[1].split("/", 1)[1],
        ),
    )
    with pytest.raises(RuntimeError, match="mix release identities"):
        runtime_contract._validate_runtime_mounts(
            bind_mounts, service="mcp-infra", expected_release_ref=release_ref
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda volume: volume.update(Driver="local-persist"), "exact local"),
        (lambda volume: volume.update(Scope="global"), "exact local"),
        (
            lambda volume: volume.update(Options={"type": "none", "o": "bind"}),
            "exact local",
        ),
        (lambda volume: volume.update(Mountpoint="/srv/data"), "exact local"),
        (
            lambda volume: volume["Labels"].update(  # type: ignore[union-attr]
                {"com.docker.compose.project": "foreign"}
            ),
            "exact local",
        ),
        (
            lambda volume: volume["Labels"].update(  # type: ignore[union-attr]
                {"unexpected": "label"}
            ),
            "exact local",
        ),
    ],
)
def test_runtime_named_volume_inspect_rejects_driver_options_mountpoint_or_labels(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    message: str,
) -> None:
    name = "infra_postgres_data"
    volume: dict[str, object] = {
        "Name": name,
        "Driver": "local",
        "Scope": "local",
        "Options": {},
        "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
        "Labels": {
            "com.docker.compose.config-hash": "c" * 64,
            "com.docker.compose.project": "infra",
            "com.docker.compose.version": "2.40.3",
            "com.docker.compose.volume": "postgres_data",
        },
    }
    mutation(volume)
    monkeypatch.setattr(runtime_contract, "_volume_inspect", lambda _name: volume)
    with pytest.raises(RuntimeError, match=message):
        runtime_contract._validate_named_volume(
            project="infra",
            volume_name=name,
            mount_source=f"/var/lib/docker/volumes/{name}/_data",
        )


def test_runtime_named_volume_rejects_noncanonical_source_before_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_contract,
        "_volume_inspect",
        lambda _name: pytest.fail("noncanonical source must fail before inspect"),
    )
    with pytest.raises(RuntimeError, match="source is not canonical"):
        runtime_contract._validate_named_volume(
            project="infra",
            volume_name="infra_postgres_data",
            mount_source="/host/data",
        )


def test_writer_fence_allows_exactly_five_non_mutating_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids, _ = _stub_container(monkeypatch)
    allowed = tuple(
        service
        for service in runtime_contract.INFRASTRUCTURE_SERVICES
        if service not in runtime_contract.MUTATING_SERVICES
    )
    assert allowed == ("postgres", "postgres_gold", "redis", "minio", "mailhog")
    assert "superset" in runtime_contract.MUTATING_SERVICES
    monkeypatch.setattr(
        runtime_contract,
        "_global_running_ids",
        lambda service: [ids[service]],
    )
    monkeypatch.setattr(
        runtime_contract,
        "_all_running_ids",
        lambda: sorted(ids[service] for service in allowed),
    )

    runtime_contract.verify_global_fence("infra")

    monkeypatch.setattr(
        runtime_contract,
        "_all_running_ids",
        lambda: [*(ids[service] for service in allowed), ids["superset"]],
    )
    with pytest.raises(RuntimeError, match="exact five"):
        runtime_contract.verify_global_fence("infra")


def test_runtime_hash_normalizes_only_the_recorded_restart_policy() -> None:
    original = {
        "Path": "/entrypoint",
        "Args": ["serve"],
        "Config": {"Image": "example.invalid/image:tag", "Labels": {}},
        "HostConfig": {
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "ReadonlyRootfs": True,
        },
        "Mounts": [],
        "NetworkSettings": {"Networks": {}},
    }
    fenced = copy.deepcopy(original)
    fenced["HostConfig"]["RestartPolicy"] = {
        "Name": "no",
        "MaximumRetryCount": 0,
    }
    expected_policy = original["HostConfig"]["RestartPolicy"]

    expected = runtime_contract._runtime_config_sha256(original)
    assert runtime_contract._runtime_config_sha256(fenced) != expected
    assert (
        runtime_contract._runtime_config_sha256(
            fenced,
            restart_policy_override=expected_policy,
        )
        == expected
    )

    other_drift = copy.deepcopy(fenced)
    other_drift["HostConfig"]["ReadonlyRootfs"] = False
    assert (
        runtime_contract._runtime_config_sha256(
            other_drift,
            restart_policy_override=expected_policy,
        )
        != expected
    )


def test_runtime_full_hash_covers_pid_devices_and_network_identity() -> None:
    original = _secure_info()
    expected = runtime_contract._runtime_config_sha256(original)
    for field, value in (
        ("PidMode", "host"),
        ("Devices", [{"PathOnHost": "/dev/mem"}]),
        ("AutoRemove", True),
        ("PublishAllPorts", True),
    ):
        drift = copy.deepcopy(original)
        drift["HostConfig"][field] = value  # type: ignore[index]
        assert runtime_contract._runtime_config_sha256(drift) != expected
    network_drift = copy.deepcopy(original)
    network_drift["NetworkSettings"]["Networks"]["infra_default"][  # type: ignore[index]
        "NetworkID"
    ] = "f" * 64
    assert runtime_contract._runtime_config_sha256(network_drift) != expected


def test_legacy_runtime_hash_has_a_fixed_golden_contract() -> None:
    info = _secure_info()
    assert runtime_contract._legacy_runtime_config_sha256(info) == (
        "773bdeedd366f34ea7a8585eede972b2538cfd2f719eac6755e950d54c21c78f"
    )


def test_exact_container_records_full_container_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = _secure_info(service="workspace")
    monkeypatch.setattr(runtime_contract, "_container_ids", lambda *_a, **_k: ["short"])
    monkeypatch.setattr(runtime_contract, "_inspect", lambda _container_id: info)
    observed = runtime_contract._require_exact_container(
        "infra",
        "workspace",
        expected_ref=None,
        running=True,
        healthy=True,
    )
    assert observed["container_id"] == info["Id"]


@pytest.mark.parametrize("schema_version", [1, 2])
def test_provenance_schema_requires_exact_top_and_service_shapes(
    schema_version: int,
) -> None:
    payload = _provenance_payload(schema_version)
    assert (
        runtime_contract._validate_provenance_payload(
            payload,
            schema_version=schema_version,
            project="infra",
        )
        is payload
    )
    extra = copy.deepcopy(payload)
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="top-level shape"):
        runtime_contract._validate_provenance_payload(
            extra,
            schema_version=schema_version,
            project="infra",
        )
    missing = copy.deepcopy(payload)
    missing["services"].pop("console")  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="service inventory"):
        runtime_contract._validate_provenance_payload(
            missing,
            schema_version=schema_version,
            project="infra",
        )
    wrong_shape = copy.deepcopy(payload)
    wrong_shape["services"]["console"]["extra"] = "x"  # type: ignore[index]
    with pytest.raises(ValueError, match="service shape"):
        runtime_contract._validate_provenance_payload(
            wrong_shape,
            schema_version=schema_version,
            project="infra",
        )


def test_v2_live_verification_rejects_container_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _provenance_payload(2)
    observed = copy.deepcopy(payload["services"])
    observed["console"]["container_id"] = "f" * 64  # type: ignore[index]
    monkeypatch.setattr(
        runtime_contract,
        "_runtime_paths_and_expected",
        lambda *_a: ("a" * 40, "1.45.207-beta", [], None, {}),
    )
    monkeypatch.setattr(runtime_contract, "verify_runtime", lambda *_a, **_k: observed)
    with pytest.raises(ValueError, match="live drift: console"):
        runtime_contract._verify_payload_live(
            tmp_path,
            payload,
            schema_version=2,
            project="infra",
        )


def test_state_bundle_loader_requires_exact_private_regular_pair(
    tmp_path: Path,
) -> None:
    app_root, bundle, state_link = _state_bundle_layout(tmp_path)
    uid = os.geteuid()
    gid = os.getegid()
    loaded = runtime_contract._load_state_bundle(app_root, uid=uid, gid=gid)
    assert loaded[0] == state_link
    assert loaded[2] == bundle.resolve()
    assert loaded[4]["schema_version"] == 1

    (bundle / "unexpected").write_text("tamper\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file inventory"):
        runtime_contract._load_state_bundle(app_root, uid=uid, gid=gid)


@pytest.mark.parametrize("tamper", ["mode", "symlink", "owner", "regular-link"])
def test_state_bundle_loader_rejects_mode_link_and_owner_tamper(
    tmp_path: Path,
    tamper: str,
) -> None:
    app_root, bundle, state_link = _state_bundle_layout(tmp_path)
    uid = os.geteuid()
    gid = os.getegid()
    provenance = bundle / "runtime-provenance.json"
    expected_uid = uid
    if tamper == "mode":
        provenance.chmod(0o644)
    elif tamper == "symlink":
        data = provenance.read_text(encoding="utf-8")
        provenance.unlink()
        outside = tmp_path / "outside.json"
        outside.write_text(data, encoding="utf-8")
        provenance.symlink_to(outside)
    elif tamper == "owner":
        expected_uid = uid + 1
    else:
        target = os.readlink(state_link)
        state_link.unlink()
        state_link.write_text(target, encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        runtime_contract._load_state_bundle(
            app_root,
            uid=expected_uid,
            gid=gid,
        )


def test_adopted_bundle_uses_cas_and_preserves_legacy_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root, legacy_bundle, state_link = _state_bundle_layout(tmp_path)
    uid = os.geteuid()
    gid = os.getegid()
    _link, raw_target, _bundle, bootstrap, _legacy = (
        runtime_contract._load_state_bundle(app_root, uid=uid, gid=gid)
    )
    adopted = _provenance_payload(2)

    def exchange(first: Path, second: Path) -> None:
        temporary = first.parent / ".test-exchange"
        os.rename(first, temporary)
        os.rename(second, first)
        os.rename(temporary, second)

    monkeypatch.setattr(runtime_contract, "_rename_exchange", exchange)
    runtime_contract._publish_adopted_bundle(
        app_root,
        state_link=state_link,
        expected_link_target=raw_target,
        bootstrap=bootstrap,
        provenance=adopted,
    )

    assert legacy_bundle.exists()
    assert state_link.resolve() != legacy_bundle.resolve()
    loaded = runtime_contract._load_state_bundle(app_root, uid=uid, gid=gid)
    assert loaded[4]["schema_version"] == 2
    assert (
        loaded[3]["runtime_provenance_sha256"]
        == hashlib.sha256(
            (loaded[2] / "runtime-provenance.json").read_bytes()
        ).hexdigest()
    )


def test_adoption_cas_restores_a_concurrent_non_symlink_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root, legacy_bundle, state_link = _state_bundle_layout(tmp_path)
    uid = os.geteuid()
    gid = os.getegid()
    _link, raw_target, _bundle, bootstrap, _legacy = (
        runtime_contract._load_state_bundle(app_root, uid=uid, gid=gid)
    )
    calls = 0

    def exchange(first: Path, second: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            first.unlink()
            first.write_text("concurrent evidence\n", encoding="utf-8")
        temporary = first.parent / f".test-exchange-{calls}"
        os.rename(first, temporary)
        os.rename(second, first)
        os.rename(temporary, second)

    monkeypatch.setattr(runtime_contract, "_rename_exchange", exchange)
    with pytest.raises(RuntimeError, match="changed during"):
        runtime_contract._publish_adopted_bundle(
            app_root,
            state_link=state_link,
            expected_link_target=raw_target,
            bootstrap=bootstrap,
            provenance=_provenance_payload(2),
        )
    assert calls == 2
    assert not state_link.is_symlink()
    assert state_link.read_text(encoding="utf-8") == "concurrent evidence\n"
    assert legacy_bundle.exists()


def test_adoption_flow_is_idempotent_and_has_no_fallback_after_legacy_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    v1 = _provenance_payload(1)
    v2 = _provenance_payload(2)
    bootstrap = {"deploy_ref": "a" * 40, "version": "1.45.207-beta"}
    overrides = {
        service: {"Name": "unless-stopped", "MaximumRetryCount": 0}
        for service in runtime_contract.RESTART_POLICY_SERVICES
    }
    verifies: list[tuple[int, dict[str, dict[str, object]] | None]] = []
    publications: list[int] = []
    loads = iter(
        (
            (tmp_path / "runtime-state", "/legacy", tmp_path, bootstrap, v1),
            (tmp_path / "runtime-state", "/v2", tmp_path, bootstrap, v2),
        )
    )
    monkeypatch.setattr(runtime_contract.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime_contract, "CANONICAL_APP_ROOT", tmp_path)
    monkeypatch.setattr(runtime_contract, "_load_state_bundle", lambda *_a: next(loads))
    monkeypatch.setattr(
        runtime_contract,
        "_verify_payload_live",
        lambda _root,
        _payload,
        *,
        schema_version,
        project,
        restart_policy_overrides=None: verifies.append(
            (schema_version, restart_policy_overrides)
        )
        or {},
    )
    monkeypatch.setattr(
        runtime_contract,
        "_fenced_restart_policy_overrides",
        lambda _root, _project: overrides,
    )
    monkeypatch.setattr(
        runtime_contract,
        "_runtime_paths_and_expected",
        lambda *_a: ("a" * 40, "1.45.207-beta", [], None, {}),
    )
    monkeypatch.setattr(
        runtime_contract,
        "verify_runtime",
        lambda *_a, **_k: copy.deepcopy(v2["services"]),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_publish_adopted_bundle",
        lambda *_a, **_k: publications.append(1),
    )
    args = argparse.Namespace(
        app_root=str(tmp_path),
        compose_project="infra",
        restart_fenced=True,
    )
    assert runtime_contract.command_adopt_provenance(args) == 0
    assert verifies == [(1, overrides), (2, overrides)]
    assert publications == [1]

    monkeypatch.setattr(
        runtime_contract,
        "_load_state_bundle",
        lambda *_a: (tmp_path / "runtime-state", "/v2", tmp_path, bootstrap, v2),
    )
    assert runtime_contract.command_adopt_provenance(args) == 0
    assert verifies == [(1, overrides), (2, overrides), (2, overrides)]
    assert publications == [1]

    monkeypatch.setattr(
        runtime_contract,
        "_load_state_bundle",
        lambda *_a: (tmp_path / "runtime-state", "/legacy", tmp_path, bootstrap, v1),
    )

    def reject_legacy(
        _root: Path,
        _payload: dict[str, object],
        *,
        schema_version: int,
        project: str,
        restart_policy_overrides: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, dict[str, str]]:
        del schema_version, project, restart_policy_overrides
        raise ValueError("legacy hash drift")

    monkeypatch.setattr(runtime_contract, "_verify_payload_live", reject_legacy)
    with pytest.raises(ValueError, match="legacy hash drift"):
        runtime_contract.command_adopt_provenance(args)
    assert publications == [1]


def test_legacy_provenance_command_accepts_schema_one_only_on_fenced_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _provenance_payload(1)
    provenance = tmp_path / "runtime-provenance.json"
    provenance.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    provenance.chmod(0o600)
    recorded = _policy_inventory(fenced=False)
    fenced = _policy_inventory(fenced=True)
    observed_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runtime_contract, "CANONICAL_APP_ROOT", tmp_path)
    monkeypatch.setattr(
        runtime_contract,
        "_runtime_input_hashes",
        lambda _inputs: copy.deepcopy(payload["runtime_input_sha256"]),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_load_restart_policy_state",
        lambda *_args, **_kwargs: {
            "state": "fenced",
            "containers": recorded,
        },
    )
    monkeypatch.setattr(
        runtime_contract,
        "_restart_policy_inventory",
        lambda _project: fenced,
    )

    def verify_runtime_stub(_project: str, **kwargs: object) -> dict[str, object]:
        observed_calls.append(kwargs)
        return copy.deepcopy(payload["services"])

    monkeypatch.setattr(runtime_contract, "verify_runtime", verify_runtime_stub)
    args = argparse.Namespace(
        compose_project="infra",
        deploy_ref="a" * 40,
        version="1.45.207-beta",
        runtime_input=["unused=/dev/null"],
        provenance=str(provenance),
        lock_env=None,
        scheduler="stopped",
        one_shots=False,
        restart_fenced_app_root=str(tmp_path),
        legacy_adoption=True,
    )

    assert runtime_contract.command_provenance(args) == 0
    assert len(observed_calls) == 1
    assert observed_calls[0]["legacy_config_hash"] is True
    assert observed_calls[0]["scheduler"] == "stopped"
    assert observed_calls[0]["restart_policy_overrides"] == {
        service: {
            "Name": value["restart_policy"],
            "MaximumRetryCount": value["maximum_retry_count"],
        }
        for service, value in recorded.items()
    }

    args.legacy_adoption = False
    with pytest.raises(ValueError, match="runtime provenance identity is invalid"):
        runtime_contract.command_provenance(args)


@pytest.mark.parametrize("schema_version", [1, 2])
def test_prestart_mutator_drift_aborts_before_caller_can_start_compose(
    schema_version: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = {
        service: f"{index:064x}"
        for index, service in enumerate(
            runtime_contract.ALL_CONTAINER_SERVICES,
            start=1,
        )
    }
    infos: dict[str, dict[str, object]] = {}
    for index, service in enumerate(runtime_contract.ALL_CONTAINER_SERVICES, start=1):
        info = _secure_info(service=service, index=index)
        if service in runtime_contract.ONE_SHOT_MUTATORS:
            info["State"] = {
                "Running": False,
                "Status": "exited",
                "ExitCode": 0,
            }
        else:
            info["State"] = {"Running": False, "Status": "exited"}
        infos[canonical[service]] = info

    monkeypatch.setattr(
        runtime_contract,
        "_container_ids",
        lambda _project, service, *, running_only=False: (
            [canonical[service]]
            if not running_only or bool(infos[canonical[service]]["State"]["Running"])
            else []
        ),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_all_container_ids",
        lambda: list(canonical.values()),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_all_running_ids",
        lambda: [
            container_id
            for container_id, info in infos.items()
            if bool(info["State"]["Running"])
        ],
    )
    monkeypatch.setattr(runtime_contract, "_inspect", lambda value: infos[value])
    monkeypatch.setattr(runtime_contract, "_validate_project_network", lambda *_a: None)
    # This test isolates stopped mutator/provenance drift. Mount equality is
    # tested independently with real release paths.
    monkeypatch.setattr(
        runtime_contract, "_validate_runtime_mounts", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        runtime_contract,
        "_image_id",
        lambda _ref: "sha256:" + "1" * 64,
    )
    overrides = {
        service: {"Name": "unless-stopped", "MaximumRetryCount": 0}
        for service in runtime_contract.RESTART_POLICY_SERVICES
    }
    expected_refs = {
        service: "example.invalid/image:tag"
        for service in runtime_contract.PROVENANCE_SERVICE_NAMES
    }
    observed = runtime_contract.verify_prestart_runtime(
        "infra",
        expected_refs=expected_refs,
        restart_policy_overrides=overrides,
        legacy_config_hash=schema_version == 1,
    )
    services = copy.deepcopy(observed)
    if schema_version == 1:
        for value in services.values():
            value.pop("container_id")
    payload = _provenance_payload(schema_version)
    payload["services"] = services
    provenance = tmp_path / "runtime-provenance.json"
    provenance.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    provenance.chmod(0o600)
    monkeypatch.setattr(runtime_contract, "CANONICAL_APP_ROOT", tmp_path)
    monkeypatch.setattr(
        runtime_contract,
        "_runtime_input_hashes",
        lambda _inputs: copy.deepcopy(payload["runtime_input_sha256"]),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_fenced_restart_policy_overrides",
        lambda _root, _project: overrides,
    )
    args = argparse.Namespace(
        compose_project="infra",
        deploy_ref="a" * 40,
        version="1.45.207-beta",
        runtime_input=["unused=/dev/null"],
        provenance=str(provenance),
        lock_env=None,
        restart_fenced_app_root=str(tmp_path),
    )
    assert runtime_contract.command_prestart(args) == 0

    mailhog = infos[canonical["mailhog"]]
    mailhog["State"] = {
        "Running": True,
        "Status": "running",
        "Health": {"Status": "healthy"},
    }
    with pytest.raises(RuntimeError, match="infrastructure service=mailhog"):
        runtime_contract.command_prestart(args)
    mailhog["State"] = {"Running": False, "Status": "exited"}

    console = infos[canonical["console"]]
    console["State"] = {
        "Running": True,
        "Status": "running",
        "Health": {"Status": "healthy"},
    }
    starts: list[str] = []

    def prestart_then_start() -> None:
        runtime_contract.command_prestart(args)
        starts.append("compose-start")

    with pytest.raises(RuntimeError, match="mutator service=console"):
        prestart_then_start()
    assert starts == []


def test_restart_policy_state_path_is_bound_to_the_canonical_app_root() -> None:
    with pytest.raises(ValueError, match="outside the canonical root"):
        runtime_contract.command_restart_policy(
            argparse.Namespace(
                app_root="/opt/modecissions",
                compose_project="infra",
                restart_policy_action="verify-fenced",
                state="/tmp/attacker-controlled-restart-fence.json",
            )
        )


def test_restart_policy_prepare_is_durable_before_and_after_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _policy_inventory(fenced=False)
    fenced = _policy_inventory(fenced=True)
    live_checks: list[bool] = []
    states: list[str] = []
    updates: list[tuple[str, str, int]] = []

    monkeypatch.setattr(
        runtime_contract,
        "_validate_restart_policy_parent",
        lambda _path: None,
    )
    monkeypatch.setattr(
        runtime_contract,
        "_load_restart_policy_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        runtime_contract,
        "command_live_state",
        lambda args: (
            live_checks.append(bool(getattr(args, "restart_fenced", False))) or 0
        ),
    )
    inventories = iter((original, fenced))
    monkeypatch.setattr(
        runtime_contract,
        "_restart_policy_inventory",
        lambda _project: next(inventories),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_atomic_json",
        lambda _path, payload: states.append(str(payload["state"])),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_set_restart_policy",
        lambda container_id, name, retries: updates.append(
            (container_id, name, retries)
        ),
    )

    assert (
        runtime_contract.command_restart_policy(
            argparse.Namespace(
                app_root="/opt/modecissions",
                compose_project="infra",
                restart_policy_action="prepare",
                state="/opt/modecissions/shared/restart-policy-fence.json",
            )
        )
        == 0
    )
    assert live_checks == [False, True]
    assert states == ["preparing", "fenced"]
    assert len(updates) == len(runtime_contract.RESTART_POLICY_SERVICES)
    assert {name for _container, name, _retries in updates} == {"no"}


def test_restart_policy_idempotent_prepare_rechecks_exact_fenced_live_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _policy_inventory(fenced=False)
    fenced = _policy_inventory(fenced=True)
    payload = runtime_contract._restart_policy_state(
        state="fenced",
        project="infra",
        containers=recorded,
    )
    live_checks: list[bool] = []

    monkeypatch.setattr(
        runtime_contract,
        "_validate_restart_policy_parent",
        lambda _path: None,
    )
    monkeypatch.setattr(
        runtime_contract,
        "_load_restart_policy_state",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        runtime_contract,
        "_restart_policy_inventory",
        lambda _project: fenced,
    )
    monkeypatch.setattr(
        runtime_contract,
        "command_live_state",
        lambda args: live_checks.append(bool(args.restart_fenced)) or 0,
    )

    assert (
        runtime_contract.command_restart_policy(
            argparse.Namespace(
                app_root="/opt/modecissions",
                compose_project="infra",
                restart_policy_action="prepare",
                state="/opt/modecissions/shared/restart-policy-fence.json",
            )
        )
        == 0
    )
    assert live_checks == [True]


def test_restart_policy_final_fenced_drift_aborts_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _policy_inventory(fenced=False)
    fenced = _policy_inventory(fenced=True)
    states: list[str] = []
    live_checks = 0

    monkeypatch.setattr(
        runtime_contract,
        "_validate_restart_policy_parent",
        lambda _path: None,
    )
    monkeypatch.setattr(
        runtime_contract,
        "_load_restart_policy_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    inventories = iter((original, fenced))
    monkeypatch.setattr(
        runtime_contract,
        "_restart_policy_inventory",
        lambda _project: next(inventories),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_atomic_json",
        lambda _path, payload: states.append(str(payload["state"])),
    )
    monkeypatch.setattr(runtime_contract, "_set_restart_policy", lambda *_args: None)

    def live_state(args: argparse.Namespace) -> int:
        nonlocal live_checks
        live_checks += 1
        if getattr(args, "restart_fenced", False):
            raise RuntimeError("global stopped-container inventory drift")
        return 0

    monkeypatch.setattr(runtime_contract, "command_live_state", live_state)

    with pytest.raises(RuntimeError, match="stopped-container inventory drift"):
        runtime_contract.command_restart_policy(
            argparse.Namespace(
                app_root="/opt/modecissions",
                compose_project="infra",
                restart_policy_action="prepare",
                state="/opt/modecissions/shared/restart-policy-fence.json",
            )
        )
    assert live_checks == 2
    assert states == ["preparing", "fenced"]


def test_restart_policy_restore_uses_fenced_then_exact_live_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _policy_inventory(fenced=False)
    fenced = _policy_inventory(fenced=True)
    payload = runtime_contract._restart_policy_state(
        state="fenced",
        project="infra",
        containers=recorded,
    )
    live_checks: list[bool] = []
    states: list[str] = []
    updates: list[tuple[str, str, int]] = []

    monkeypatch.setattr(
        runtime_contract,
        "_validate_restart_policy_parent",
        lambda _path: None,
    )
    monkeypatch.setattr(
        runtime_contract,
        "_load_restart_policy_state",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        runtime_contract,
        "command_live_state",
        lambda args: (
            live_checks.append(bool(getattr(args, "restart_fenced", False))) or 0
        ),
    )
    inventories = iter((fenced, recorded))
    monkeypatch.setattr(
        runtime_contract,
        "_restart_policy_inventory",
        lambda _project: next(inventories),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_atomic_json",
        lambda _path, value: states.append(str(value["state"])),
    )
    monkeypatch.setattr(
        runtime_contract,
        "_set_restart_policy",
        lambda container_id, name, retries: updates.append(
            (container_id, name, retries)
        ),
    )

    assert (
        runtime_contract.command_restart_policy(
            argparse.Namespace(
                app_root="/opt/modecissions",
                compose_project="infra",
                restart_policy_action="restore",
                state="/opt/modecissions/shared/restart-policy-fence.json",
            )
        )
        == 0
    )
    assert live_checks == [True, False]
    assert states == ["restoring", "restored"]
    assert len(updates) == len(runtime_contract.RESTART_POLICY_SERVICES)
    assert any(name == "unless-stopped" for _container, name, _retries in updates)
