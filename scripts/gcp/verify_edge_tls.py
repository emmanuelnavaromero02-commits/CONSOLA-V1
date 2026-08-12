#!/usr/bin/python3 -I
"""Read-only, fail-closed verification of the canonical GCP TLS edge."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


CONSOLE_DOMAIN = "console.7businesssolutions.com"
WORKSPACE_DOMAIN = "workspace.7businesssolutions.com"
SHARED_DOMAINS = {
    "api.7businesssolutions.com",
    CONSOLE_DOMAIN,
    "workspace.7businesssolutions.com",
    "www.7businesssolutions.com",
}
MAP_NAME = "sevenbs-production-map"
CERTIFICATE_NAME = "sevenbs-production-cert"
ENTRY_NAMES = {
    "api.7businesssolutions.com": "sevenbs-api-entry",
    CONSOLE_DOMAIN: "sevenbs-console-entry",
    WORKSPACE_DOMAIN: "sevenbs-workspace-entry",
    "www.7businesssolutions.com": "sevenbs-www-entry",
}
SHARED_MAP_TARGETS = {
    "omega-staging-https-proxy": "34.144.248.147",
    "sevenbs-web-gcp-https-proxy": "136.68.185.124",
}
DNS_ZONE = "7businesssolutions.com."
MIN_PROPAGATION = timedelta(minutes=30)
MIN_CERTIFICATE_LIFETIME = timedelta(days=14)
MAX_COMMAND_OUTPUT = 4 * 1024 * 1024
FORBIDDEN_ENVIRONMENT = re.compile(
    r"^(?:all|http|https|no)_proxy$|^CLOUDSDK_|^SSL_CERT_|^SSLKEYLOGFILE$|"
    r"^REQUESTS_CA_BUNDLE$|^CURL_CA_BUNDLE$|^CURL_HOME$",
    re.IGNORECASE,
)


class GateError(RuntimeError):
    pass


def _timestamp(value: object) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:[.][0-9]{1,9})?Z",
            value,
        )
        is None
    ):
        raise GateError("timestamp is invalid")
    if "." in value:
        prefix, fraction = value[:-1].split(".", 1)
        normalized = f"{prefix}.{fraction[:6].ljust(6, '0')}+00:00"
    else:
        normalized = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise GateError("timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise GateError("timestamp is not UTC")
    return parsed


def validate_inventory(
    *,
    project_id: str,
    project_number: str,
    environment: str,
    expected_ip: str,
    proxy: dict[str, Any],
    forwarding: dict[str, Any],
    url_map: dict[str, Any],
    certificate_map: dict[str, Any],
    entries: list[dict[str, Any]],
    certificate: dict[str, Any],
    forwarding_inventory: list[dict[str, Any]] | None = None,
    http_proxy_inventory: list[dict[str, Any]] | None = None,
    https_proxy_inventory: list[dict[str, Any]] | None = None,
    url_map_inventory: list[dict[str, Any]] | None = None,
    address_inventory: list[dict[str, Any]] | None = None,
    legacy_certificate: dict[str, Any] | None = None,
    ssl_certificate_inventory: list[dict[str, Any]] | None = None,
    edge_mode: str = "post-transition",
    legacy_console_ip: str = "136.68.67.95",
    legacy_workspace_ip: str = "8.233.29.138",
    now: datetime,
) -> None:
    proxy_name = f"omega-{environment}-https-proxy"
    forwarding_name = f"omega-{environment}-https"
    url_map_name = f"omega-{environment}-url-map"
    proxy_url = (
        f"https://www.googleapis.com/compute/v1/projects/{project_id}/global/"
        f"targetHttpsProxies/{proxy_name}"
    )
    map_api_url = (
        "https://certificatemanager.googleapis.com/v1/projects/"
        f"{project_id}/locations/global/certificateMaps/{MAP_NAME}"
    )
    url_map_url = (
        f"https://www.googleapis.com/compute/v1/projects/{project_id}/global/"
        f"urlMaps/{url_map_name}"
    )
    legacy_certificate_url = (
        f"https://www.googleapis.com/compute/v1/projects/{project_id}/global/"
        f"sslCertificates/omega-{environment}-public-cert"
    )
    if (
        proxy.get("name") != proxy_name
        or proxy.get("certificateMap") != map_api_url
        or proxy.get("urlMap") != url_map_url
        or proxy.get("sslCertificates") != [legacy_certificate_url]
        or proxy.get("quicOverride") != "NONE"
        or proxy.get("tlsEarlyData") != "DISABLED"
    ):
        raise GateError("HTTPS proxy is not bound to the exact map/url-map")
    if (
        forwarding.get("name") != forwarding_name
        or forwarding.get("IPAddress") != expected_ip
        or forwarding.get("portRange") not in {"443", "443-443"}
        or forwarding.get("target") != proxy_url
        or forwarding.get("loadBalancingScheme") != "EXTERNAL_MANAGED"
        or forwarding.get("IPProtocol") != "TCP"
        or forwarding.get("networkTier") != "PREMIUM"
    ):
        raise GateError("HTTPS forwarding rule differs")

    host_rules = url_map.get("hostRules")
    if not isinstance(host_rules, list) or len(host_rules) != 3:
        raise GateError("canonical url-map host-rule inventory differs")
    host_routes = {
        (host, rule.get("pathMatcher"))
        for rule in host_rules
        if isinstance(rule, dict) and set(rule) == {"hosts", "pathMatcher"}
        for host in rule.get("hosts", [])
        if isinstance(host, str)
    }
    expected_host_routes = {
        ("*", "console"),
        ("gcp-workspace.7businesssolutions.com", "workspace"),
        (WORKSPACE_DOMAIN, "workspace"),
    }
    if host_routes != expected_host_routes or any(
        not isinstance(rule.get("hosts"), list) or len(rule["hosts"]) != 1
        for rule in host_rules
        if isinstance(rule, dict)
    ):
        raise GateError("canonical url-map host routes are not exact")
    path_matchers = url_map.get("pathMatchers")
    if not isinstance(path_matchers, list) or len(path_matchers) != 2:
        raise GateError("canonical url-map path-matcher inventory differs")
    backend_prefix = (
        f"https://www.googleapis.com/compute/v1/projects/{project_id}/global/"
        "backendServices/"
    )
    console_backend = f"{backend_prefix}omega-{environment}-console-backend"
    workspace_backend = f"{backend_prefix}omega-{environment}-workspace-backend"
    airflow_backend = f"{backend_prefix}omega-{environment}-airflow-backend"
    if url_map.get("defaultService") != console_backend:
        raise GateError("canonical url-map default service differs")
    matchers = {
        item.get("name"): item for item in path_matchers if isinstance(item, dict)
    }
    if set(matchers) != {"console", "workspace"}:
        raise GateError("canonical url-map path-matcher inventory differs")
    if matchers["workspace"] != {
        "defaultService": workspace_backend,
        "name": "workspace",
    }:
        raise GateError("workspace path-matcher differs")
    if matchers["console"] != {
        "defaultService": console_backend,
        "name": "console",
        "pathRules": [
            {
                "paths": ["/airflow", "/airflow/*"],
                "service": airflow_backend,
            }
        ],
    }:
        raise GateError("console path-matcher differs")

    map_name = f"projects/{project_id}/locations/global/certificateMaps/{MAP_NAME}"
    if certificate_map.get("name") != map_name:
        raise GateError("certificate map identity differs")
    targets = certificate_map.get("gclbTargets")
    if not isinstance(targets, list) or len(targets) != len(SHARED_MAP_TARGETS):
        raise GateError("certificate map target inventory differs")
    observed_targets: dict[str, str] = {}
    for target in targets:
        if not isinstance(target, dict) or set(target) != {
            "ipConfigs",
            "targetHttpsProxy",
        }:
            raise GateError("certificate map target shape differs")
        target_url = target.get("targetHttpsProxy")
        ip_configs = target.get("ipConfigs")
        if (
            not isinstance(target_url, str)
            or not isinstance(ip_configs, list)
            or len(ip_configs) != 1
            or not isinstance(ip_configs[0], dict)
            or set(ip_configs[0]) != {"ipAddress", "ports"}
            or ip_configs[0].get("ports") != [443]
        ):
            raise GateError("certificate map target IP shape differs")
        leaf = target_url.rsplit("/", 1)[-1]
        if (
            target_url
            != (
                f"//compute.googleapis.com/projects/{project_number}/global/"
                f"targetHttpsProxies/{leaf}"
            )
            or leaf in observed_targets
        ):
            raise GateError("certificate map target identity differs")
        observed_targets[leaf] = str(ip_configs[0].get("ipAddress"))
    if (
        observed_targets != SHARED_MAP_TARGETS
        or observed_targets.get(proxy_name) != expected_ip
    ):
        raise GateError("certificate map target association differs")

    certificate_name = (
        f"projects/{project_id}/locations/global/certificates/{CERTIFICATE_NAME}"
    )
    certificate_reference = (
        f"projects/{project_number}/locations/global/certificates/"
        f"{CERTIFICATE_NAME}"
    )
    if certificate.get("name") != certificate_name:
        raise GateError("certificate identity differs")
    managed = certificate.get("managed")
    if not isinstance(managed, dict) or managed.get("state") != "ACTIVE":
        raise GateError("shared certificate is not ACTIVE")
    if set(managed.get("domains", [])) != SHARED_DOMAINS:
        raise GateError("shared certificate SAN contract differs")
    authorization = managed.get("authorizationAttemptInfo")
    if (
        not isinstance(authorization, list)
        or len(authorization) != len(SHARED_DOMAINS)
        or any(
            not isinstance(item, dict)
            or set(item) != {"attemptTime", "domain", "state"}
            or item.get("state") != "AUTHORIZED"
            for item in authorization
        )
        or [item["domain"] for item in authorization]
        != list(dict.fromkeys(item["domain"] for item in authorization))
        or {item["domain"] for item in authorization} != SHARED_DOMAINS
    ):
        raise GateError("certificate authorization is not exact/ACTIVE")
    if set(certificate.get("sanDnsnames", [])) != SHARED_DOMAINS:
        raise GateError("issued certificate SANs differ")
    if _timestamp(certificate.get("expireTime")) - now < MIN_CERTIFICATE_LIFETIME:
        raise GateError("certificate expiry is inside the safety window")

    if len(entries) != len(ENTRY_NAMES) or any(
        not isinstance(entry, dict) for entry in entries
    ):
        raise GateError("certificate-map entry inventory differs")
    hostnames = [entry.get("hostname") for entry in entries]
    if len(set(hostnames)) != len(hostnames) or set(hostnames) != set(ENTRY_NAMES):
        raise GateError("certificate-map entry hostname inventory differs")
    by_hostname = {entry["hostname"]: entry for entry in entries}
    newest_activation = _timestamp(certificate.get("updateTime"))
    for hostname, entry_leaf in ENTRY_NAMES.items():
        entry = by_hostname.get(hostname)
        expected_name = f"{map_name}/certificateMapEntries/{entry_leaf}"
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {
                "certificates",
                "createTime",
                "hostname",
                "name",
                "state",
                "updateTime",
            }
            or entry.get("name") != expected_name
            or entry.get("state") != "ACTIVE"
            or entry.get("certificates") != [certificate_reference]
        ):
            raise GateError(f"certificate-map entry differs: {hostname}")
        newest_activation = max(newest_activation, _timestamp(entry.get("updateTime")))
    if now - newest_activation < MIN_PROPAGATION:
        raise GateError("certificate-map activation has not aged 30 minutes")

    if any(
        value is None
        for value in (
            forwarding_inventory,
            http_proxy_inventory,
            https_proxy_inventory,
            url_map_inventory,
            address_inventory,
            legacy_certificate,
            ssl_certificate_inventory,
        )
    ):
        return
    _validate_global_edge_inventory(
        project_id=project_id,
        environment=environment,
        canonical_ip=expected_ip,
        legacy_console_ip=legacy_console_ip,
        legacy_workspace_ip=legacy_workspace_ip,
        forwarding=forwarding_inventory or [],
        http_proxies=http_proxy_inventory or [],
        https_proxies=https_proxy_inventory or [],
        url_maps=url_map_inventory or [],
        addresses=address_inventory or [],
        legacy_certificate=legacy_certificate or {},
        ssl_certificates=ssl_certificate_inventory or [],
        edge_mode=edge_mode,
    )


def _leaf_map(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    if any(not isinstance(item, dict) for item in items):
        raise GateError(f"{label} inventory shape differs")
    names = [item.get("name") for item in items]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(
        names
    ):
        raise GateError(f"{label} inventory identity differs")
    return {str(item["name"]): item for item in items}


def _validate_global_edge_inventory(
    *,
    project_id: str,
    environment: str,
    canonical_ip: str,
    legacy_console_ip: str,
    legacy_workspace_ip: str,
    forwarding: list[dict[str, Any]],
    http_proxies: list[dict[str, Any]],
    https_proxies: list[dict[str, Any]],
    url_maps: list[dict[str, Any]],
    addresses: list[dict[str, Any]],
    legacy_certificate: dict[str, Any],
    ssl_certificates: list[dict[str, Any]],
    edge_mode: str,
) -> None:
    prefix = f"omega-{environment}"
    api = f"https://www.googleapis.com/compute/v1/projects/{project_id}/global"
    related_ips = {canonical_ip, legacy_console_ip, legacy_workspace_ip}
    related_tokens = {
        prefix,
        f"{prefix}-http-proxy",
        f"{prefix}-http-redirect-proxy",
        f"{prefix}-workspace-http-proxy",
        f"{prefix}-https-proxy",
        f"{prefix}-http-redirect",
        f"{prefix}-url-map",
        f"{prefix}-workspace-url-map",
    }

    def semantically_related(item: dict[str, Any]) -> bool:
        serialized = json.dumps(item, sort_keys=True, separators=(",", ":"))
        return item.get("name", "").startswith(f"{prefix}-") or any(
            token in serialized for token in (*related_ips, *related_tokens)
        )

    rules = _leaf_map(
        [item for item in forwarding if semantically_related(item)],
        "global forwarding-rule",
    )
    expected_rules = {
        f"{prefix}-http": (
            legacy_console_ip,
            f"{api}/targetHttpProxies/{prefix}-http-proxy",
            "80-80",
        ),
        f"{prefix}-http-redirect": (
            canonical_ip,
            f"{api}/targetHttpProxies/{prefix}-http-redirect-proxy",
            "80-80",
        ),
        f"{prefix}-https": (
            canonical_ip,
            f"{api}/targetHttpsProxies/{prefix}-https-proxy",
            "443-443",
        ),
        f"{prefix}-workspace-http": (
            legacy_workspace_ip,
            f"{api}/targetHttpProxies/{prefix}-workspace-http-proxy",
            "80-80",
        ),
    }
    if set(rules) != set(expected_rules):
        raise GateError("global forwarding-rule inventory differs")
    for name, (ip, target, port) in expected_rules.items():
        rule = rules[name]
        if (
            rule.get("IPAddress") != ip
            or rule.get("target") != target
            or rule.get("portRange") not in {port, port.split("-", 1)[0]}
            or rule.get("IPProtocol") != "TCP"
            or rule.get("loadBalancingScheme") != "EXTERNAL_MANAGED"
            or rule.get("networkTier") != "PREMIUM"
        ):
            raise GateError(f"global forwarding rule differs: {name}")

    redirect_map = f"{api}/urlMaps/{prefix}-http-redirect"
    proxies = _leaf_map(
        [item for item in http_proxies if semantically_related(item)], "HTTP proxy"
    )
    if edge_mode not in {"pre-transition", "post-transition"}:
        raise GateError("edge transition mode is invalid")
    expected_http = {
        f"{prefix}-http-proxy": (
            redirect_map
            if edge_mode == "post-transition"
            else f"{api}/urlMaps/{prefix}-url-map"
        ),
        f"{prefix}-http-redirect-proxy": redirect_map,
        f"{prefix}-workspace-http-proxy": (
            redirect_map
            if edge_mode == "post-transition"
            else f"{api}/urlMaps/{prefix}-workspace-url-map"
        ),
    }
    if set(proxies) != set(expected_http) or any(
        proxies[name].get("urlMap") != url_map
        for name, url_map in expected_http.items()
    ):
        raise GateError("HTTP proxy redirect inventory differs")
    https = _leaf_map(
        [item for item in https_proxies if semantically_related(item)], "HTTPS proxy"
    )
    expected_cert_url = f"{api}/sslCertificates/{prefix}-public-cert"
    if set(https) != {f"{prefix}-https-proxy"} or https[f"{prefix}-https-proxy"].get(
        "sslCertificates"
    ) != [expected_cert_url]:
        raise GateError("HTTPS proxy inventory differs")

    maps = _leaf_map(
        [item for item in url_maps if semantically_related(item)], "URL map"
    )
    if set(maps) != {
        f"{prefix}-http-redirect",
        f"{prefix}-url-map",
        f"{prefix}-workspace-url-map",
    }:
        raise GateError("URL-map inventory differs")
    redirect = maps[f"{prefix}-http-redirect"].get("defaultUrlRedirect")
    if redirect != {
        "httpsRedirect": True,
        "redirectResponseCode": "MOVED_PERMANENTLY_DEFAULT",
        "stripQuery": False,
    }:
        raise GateError("HTTP redirect map differs")
    if maps[f"{prefix}-workspace-url-map"].get("defaultService") != (
        f"{api}/backendServices/{prefix}-workspace-backend"
    ):
        raise GateError("quarantined workspace URL map differs")

    by_address = _leaf_map(
        [item for item in addresses if semantically_related(item)], "global address"
    )
    expected_addresses = {
        f"{prefix}-public-https-ip": canonical_ip,
        f"{prefix}-public-ip": legacy_console_ip,
        f"{prefix}-workspace-ip": legacy_workspace_ip,
    }
    if set(by_address) != set(expected_addresses) or any(
        by_address[name].get("address") != ip
        or by_address[name].get("addressType") != "EXTERNAL"
        or by_address[name].get("networkTier") != "PREMIUM"
        or by_address[name].get("status") != "IN_USE"
        for name, ip in expected_addresses.items()
    ):
        raise GateError("global address inventory differs")
    managed = legacy_certificate.get("managed")
    if (
        legacy_certificate.get("name") != f"{prefix}-public-cert"
        or legacy_certificate.get("type") != "MANAGED"
        or not isinstance(managed, dict)
        or managed.get("status") != "PROVISIONING_FAILED_PERMANENTLY"
        or set(managed.get("domains", []))
        != {
            "gcp-console.7businesssolutions.com",
            "gcp-workspace.7businesssolutions.com",
        }
    ):
        raise GateError("legacy certificate quarantine identity differs")
    certificates = _leaf_map(
        [item for item in ssl_certificates if semantically_related(item)],
        "Compute SSL certificate",
    )
    if set(certificates) != {f"{prefix}-public-cert"}:
        raise GateError("Compute SSL certificate inventory differs")


def _validate_path_chain(path: Path, *, allow_operator_owner: bool) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as error:
        raise GateError(f"required path is absent/unsafe: {path}") from error
    allowed_owners = {0}
    if allow_operator_owner:
        allowed_owners.add(os.geteuid())
    for candidate in (resolved, *resolved.parents):
        info = candidate.stat()
        writable_mask = 0o002 if allow_operator_owner else 0o022
        if (
            info.st_uid not in allowed_owners
            or stat.S_IMODE(info.st_mode) & writable_mask
        ):
            raise GateError(f"required path chain is writable/unowned: {path}")
        if candidate == Path("/"):
            break
    return resolved


def _binary(name: str) -> str:
    if sys.platform == "darwin":
        expected = {
            "curl": Path("/usr/bin/curl"),
            "dig": Path("/usr/bin/dig"),
            "gcloud": Path("/opt/homebrew/bin/gcloud"),
        }
    else:
        expected = {
            "curl": Path("/usr/bin/curl"),
            "dig": Path("/usr/bin/dig"),
            "gcloud": Path("/usr/bin/gcloud"),
        }
    if name not in expected:
        raise GateError(f"required binary is not allowlisted: {name}")
    resolved = _validate_path_chain(
        expected[name], allow_operator_owner=name == "gcloud"
    )
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or not stat.S_IMODE(info.st_mode) & 0o111:
        raise GateError(f"required binary is not executable: {name}")
    return str(resolved)


def _ca_bundle() -> bytes:
    candidate = (
        Path("/private/etc/ssl/cert.pem")
        if sys.platform == "darwin"
        else Path("/etc/ssl/certs/ca-certificates.crt")
    )
    resolved = _validate_path_chain(candidate, allow_operator_owner=False)
    descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 8 * 1024 * 1024
        ):
            raise GateError("system CA bundle is unsafe")
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
            raise GateError("system CA bundle changed during validation")
    finally:
        os.close(descriptor)
    if b"-----BEGIN CERTIFICATE-----" not in raw:
        raise GateError("system CA bundle contents are invalid")
    return raw


def _private_ca_copy(raw: bytes) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    directory = tempfile.TemporaryDirectory(prefix="omega-edge-ca.", dir="/tmp")
    path = Path(directory.name) / "ca-bundle.pem"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GateError("private CA copy write did not make progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return directory, path


def _validate_gcloud_config(
    path: Path, *, expected_account: str, expected_project: str
) -> Path:
    resolved = _validate_path_chain(path, allow_operator_owner=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise GateError("gcloud config directory is not operator-owned")
    config = resolved / "configurations" / "config_default"
    config_resolved = _validate_path_chain(config, allow_operator_owner=True)
    descriptor = os.open(config_resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 64 * 1024
        ):
            raise GateError("gcloud configuration identity is unsafe")
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
            raise GateError("gcloud configuration changed during read")
    finally:
        os.close(descriptor)
    expected = (
        f"[core]\naccount = {expected_account}\nproject = {expected_project}\n"
    ).encode()
    if raw != expected:
        raise GateError("gcloud configuration differs from the exact core identity")
    return resolved


def _clean_environment(
    config: Path,
    ca_bundle: Path,
    *,
    sealed_runtime_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    hostile = sorted(key for key in os.environ if FORBIDDEN_ENVIRONMENT.search(key))
    if hostile:
        raise GateError("ambient network/cloud override variables are forbidden")
    value = {
        "CLOUDSDK_CONFIG": str(config),
        "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
        "HOME": "/var/empty",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "SSL_CERT_FILE": str(ca_bundle),
    }
    if sealed_runtime_environment is not None:
        allowed = {
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
        if set(sealed_runtime_environment) - allowed:
            raise GateError("sealed gcloud runtime environment contains an override")
        value.update(sealed_runtime_environment)
        value["CLOUDSDK_CONFIG"] = str(config)
        value["SSL_CERT_FILE"] = str(ca_bundle)
    return value


def _run(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout_seconds: int = 30,
) -> str:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise GateError("read-only probe timed out") from error
        stdout_size = stdout.tell()
        stderr_size = stderr.tell()
        if stdout_size > MAX_COMMAND_OUTPUT or stderr_size > MAX_COMMAND_OUTPUT:
            raise GateError("read-only probe output exceeded the evidence cap")
        stdout.seek(0)
        stderr.seek(0)
        raw_stdout = stdout.read()
        raw_stderr = stderr.read()
    if completed.returncode != 0:
        raise GateError(f"read-only probe failed: {Path(command[0]).name}")
    if raw_stderr:
        raise GateError(
            f"read-only probe emitted unexpected stderr: {Path(command[0]).name}"
        )
    try:
        return raw_stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GateError("read-only probe returned non-UTF-8 output") from error


def _gcloud_json(
    gcloud: str | list[str],
    args: list[str],
    project_id: str,
    expected_account: str,
    environment: dict[str, str],
) -> Any:
    raw = _run(
        [
            *([gcloud] if isinstance(gcloud, str) else gcloud),
            *args,
            "--configuration=default",
            f"--account={expected_account}",
            f"--project={project_id}",
            "--format=json",
            "--quiet",
        ],
        environment=environment,
    )
    try:
        return json.loads(raw, object_pairs_hook=_json_no_duplicates)
    except json.JSONDecodeError as error:
        raise GateError("gcloud returned invalid JSON") from error


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GateError("gcloud JSON contains duplicate keys")
        value[key] = item
    return value


def _verify_dns(dig: str, expected_ip: str, *, environment: dict[str, str]) -> None:
    nameservers = sorted(
        line.rstrip(".")
        for line in _run(
            [dig, "+short", "NS", DNS_ZONE], environment=environment
        ).splitlines()
        if line
    )
    if not nameservers:
        raise GateError("authoritative nameservers are absent")
    for hostname in (CONSOLE_DOMAIN, WORKSPACE_DOMAIN):
        if _run(
            [dig, "+short", "CNAME", f"{hostname}."], environment=environment
        ).strip():
            raise GateError(f"canonical hostname unexpectedly uses CNAME: {hostname}")
        if _run(
            [dig, "+short", "AAAA", f"{hostname}."], environment=environment
        ).strip():
            raise GateError(
                f"canonical hostname unexpectedly publishes AAAA: {hostname}"
            )
        recursive = sorted(
            value
            for value in _run(
                [dig, "+short", "A", f"{hostname}."], environment=environment
            ).splitlines()
            if value
        )
        if recursive != [expected_ip]:
            raise GateError(f"recursive A differs: {hostname}")
        for nameserver in nameservers:
            authoritative = sorted(
                value
                for value in _run(
                    [dig, "+short", "A", f"{hostname}.", f"@{nameserver}"],
                    environment=environment,
                ).splitlines()
                if value
            )
            if authoritative != [expected_ip]:
                raise GateError(f"authoritative A differs: {hostname}@{nameserver}")


def main(
    argv: list[str] | None = None,
    *,
    sealed_gcloud_command: list[str] | None = None,
    sealed_runtime_environment: dict[str, str] | None = None,
) -> int:
    if not sealed_gcloud_command or sealed_runtime_environment is None:
        raise GateError(
            "edge verifier requires the canonical sealed transaction runtime"
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-number", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--legacy-console-ip", required=True)
    parser.add_argument("--legacy-workspace-ip", required=True)
    parser.add_argument(
        "--edge-mode",
        choices=("pre-transition", "post-transition"),
        required=True,
    )
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--gcloud-config", type=Path, required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"[a-z][a-z0-9-]{4,29}", args.project_id) is None:
        raise GateError("project ID is invalid")
    if re.fullmatch(r"[1-9][0-9]{5,19}", args.project_number) is None:
        raise GateError("project number is invalid")
    if re.fullmatch(r"[a-z][a-z0-9-]{0,19}", args.environment) is None:
        raise GateError("environment is invalid")
    if (
        re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:[.][A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
            args.expected_account,
        )
        is None
    ):
        raise GateError("expected account is invalid")
    try:
        expected_ip = str(ipaddress.IPv4Address(args.expected_ip))
        legacy_console_ip = str(ipaddress.IPv4Address(args.legacy_console_ip))
        legacy_workspace_ip = str(ipaddress.IPv4Address(args.legacy_workspace_ip))
    except ipaddress.AddressValueError:
        raise GateError("expected IPv4 address is invalid")
    gcloud = list(sealed_gcloud_command)
    dig = _binary("dig")
    curl = _binary("curl")
    ca_directory, ca_bundle = _private_ca_copy(_ca_bundle())
    try:
        gcloud_config = _validate_gcloud_config(
            args.gcloud_config,
            expected_account=args.expected_account,
            expected_project=args.project_id,
        )
        clean_environment = _clean_environment(
            gcloud_config,
            ca_bundle,
            sealed_runtime_environment=sealed_runtime_environment,
        )
        prefix = f"omega-{args.environment}"
        proxy = _gcloud_json(
            gcloud,
            [
                "compute",
                "target-https-proxies",
                "describe",
                f"{prefix}-https-proxy",
                "--global",
            ],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        forwarding = _gcloud_json(
            gcloud,
            ["compute", "forwarding-rules", "describe", f"{prefix}-https", "--global"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        url_map = _gcloud_json(
            gcloud,
            ["compute", "url-maps", "describe", f"{prefix}-url-map", "--global"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        certificate_map = _gcloud_json(
            gcloud,
            ["certificate-manager", "maps", "describe", MAP_NAME, "--location=global"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        entries = _gcloud_json(
            gcloud,
            [
                "certificate-manager",
                "maps",
                "entries",
                "list",
                f"--map={MAP_NAME}",
                "--location=global",
            ],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        certificate = _gcloud_json(
            gcloud,
            [
                "certificate-manager",
                "certificates",
                "describe",
                CERTIFICATE_NAME,
                "--location=global",
            ],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        forwarding_inventory = _gcloud_json(
            gcloud,
            ["compute", "forwarding-rules", "list", "--global"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        http_proxy_inventory = _gcloud_json(
            gcloud,
            ["compute", "target-http-proxies", "list"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        https_proxy_inventory = _gcloud_json(
            gcloud,
            ["compute", "target-https-proxies", "list"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        url_map_inventory = _gcloud_json(
            gcloud,
            ["compute", "url-maps", "list"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        address_inventory = _gcloud_json(
            gcloud,
            ["compute", "addresses", "list", "--global"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        legacy_certificate = _gcloud_json(
            gcloud,
            [
                "compute",
                "ssl-certificates",
                "describe",
                f"{prefix}-public-cert",
                "--global",
            ],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        ssl_certificate_inventory = _gcloud_json(
            gcloud,
            ["compute", "ssl-certificates", "list", "--global"],
            args.project_id,
            args.expected_account,
            clean_environment,
        )
        if not all(
            isinstance(item, dict)
            for item in (proxy, forwarding, url_map, certificate_map, certificate)
        ) or not isinstance(entries, list):
            raise GateError("GCP inventory shape differs")
        if not all(
            isinstance(item, list)
            for item in (
                forwarding_inventory,
                http_proxy_inventory,
                https_proxy_inventory,
                url_map_inventory,
                address_inventory,
                ssl_certificate_inventory,
            )
        ) or not isinstance(legacy_certificate, dict):
            raise GateError("GCP global edge inventory shape differs")
        now = datetime.now(timezone.utc)
        validate_inventory(
            project_id=args.project_id,
            project_number=args.project_number,
            environment=args.environment,
            expected_ip=expected_ip,
            proxy=proxy,
            forwarding=forwarding,
            url_map=url_map,
            certificate_map=certificate_map,
            entries=entries,
            certificate=certificate,
            forwarding_inventory=forwarding_inventory,
            http_proxy_inventory=http_proxy_inventory,
            https_proxy_inventory=https_proxy_inventory,
            url_map_inventory=url_map_inventory,
            address_inventory=address_inventory,
            legacy_certificate=legacy_certificate,
            ssl_certificate_inventory=ssl_certificate_inventory,
            legacy_console_ip=legacy_console_ip,
            legacy_workspace_ip=legacy_workspace_ip,
            edge_mode=args.edge_mode,
            now=now,
        )
        _verify_dns(dig, expected_ip, environment=clean_environment)
        for domain in (CONSOLE_DOMAIN, WORKSPACE_DOMAIN):
            status = _run(
                [
                    curl,
                    "-q",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--noproxy",
                    "*",
                    "--cacert",
                    str(ca_bundle),
                    "--proto",
                    "=https",
                    "--tlsv1.2",
                    "--max-time",
                    "20",
                    "--resolve",
                    f"{domain}:443:{expected_ip}",
                    "--output",
                    os.devnull,
                    "--write-out",
                    "%{http_code}",
                    f"https://{domain}/healthz",
                ],
                environment=clean_environment,
                timeout_seconds=25,
            )
            if status != "200":
                raise GateError(f"exact-edge HTTPS probe failed: {domain}")
        for redirect_ip in (expected_ip, legacy_console_ip, legacy_workspace_ip):
            redirect = _run(
                [
                    curl,
                    "-q",
                    "--silent",
                    "--show-error",
                    "--noproxy",
                    "*",
                    "--proto",
                    "=http",
                    "--max-time",
                    "20",
                    "--header",
                    "Host: attacker.invalid",
                    "--output",
                    os.devnull,
                    "--write-out",
                    "%{http_code}\t%{redirect_url}",
                    f"http://{redirect_ip}/healthz",
                ],
                environment=clean_environment,
                timeout_seconds=25,
            )
            expected_redirect = "301\thttps://attacker.invalid/healthz"
            if args.edge_mode == "pre-transition" and redirect_ip in {
                legacy_console_ip,
                legacy_workspace_ip,
            }:
                if redirect != "200\t":
                    raise GateError(
                        f"pre-transition plaintext edge differs from reviewed exposure: {redirect_ip}"
                    )
            elif redirect != expected_redirect:
                raise GateError(
                    f"plaintext edge is not an exact HTTPS redirect: {redirect_ip}"
                )
    finally:
        ca_directory.cleanup()
    print(
        json.dumps(
            {
                "certificate": CERTIFICATE_NAME,
                "domains": [CONSOLE_DOMAIN, WORKSPACE_DOMAIN],
                "ip": expected_ip,
                "map": MAP_NAME,
                "mode": args.edge_mode,
                "operation": "gcp-edge-tls-read-only",
                "status": "PASS",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as error:
        print(f"OMEGA_GCP_EDGE_TLS_CHECK\tFAIL\t{error}", file=sys.stderr)
        raise SystemExit(1) from None
