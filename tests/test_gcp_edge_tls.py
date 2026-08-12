from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import re

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "scripts" / "gcp" / "verify_edge_tls.py"
SPEC = importlib.util.spec_from_file_location("verify_edge_tls", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)


PROJECT_ID = "project-dd5ba7fa-374c-4554-ae6"
PROJECT_NUMBER = "894064513501"
EXPECTED_IP = "34.144.248.147"
NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _inventory() -> dict[str, object]:
    prefix = "omega-staging"
    proxy_url = (
        f"https://www.googleapis.com/compute/v1/projects/{PROJECT_ID}/global/"
        f"targetHttpsProxies/{prefix}-https-proxy"
    )
    map_name = (
        f"projects/{PROJECT_ID}/locations/global/certificateMaps/"
        "sevenbs-production-map"
    )
    certificate_reference = (
        f"projects/{PROJECT_NUMBER}/locations/global/certificates/"
        "sevenbs-production-cert"
    )
    backend_prefix = (
        f"https://www.googleapis.com/compute/v1/projects/{PROJECT_ID}/global/"
        "backendServices/"
    )
    updated = (NOW - timedelta(days=1)).isoformat().replace("+00:00", ".123456789Z")
    http_redirect_map = (
        f"https://www.googleapis.com/compute/v1/projects/{PROJECT_ID}/global/"
        f"urlMaps/{prefix}-http-redirect"
    )
    global_api = f"https://www.googleapis.com/compute/v1/projects/{PROJECT_ID}/global"
    legacy_certificate_url = f"{global_api}/sslCertificates/{prefix}-public-cert"
    return {
        "proxy": {
            "name": f"{prefix}-https-proxy",
            "certificateMap": (
                "https://certificatemanager.googleapis.com/v1/" + map_name
            ),
            "urlMap": (
                f"https://www.googleapis.com/compute/v1/projects/{PROJECT_ID}/"
                f"global/urlMaps/{prefix}-url-map"
            ),
            "sslCertificates": [legacy_certificate_url],
            "quicOverride": "NONE",
            "tlsEarlyData": "DISABLED",
        },
        "forwarding": {
            "name": f"{prefix}-https",
            "IPAddress": EXPECTED_IP,
            "portRange": "443-443",
            "target": proxy_url,
            "loadBalancingScheme": "EXTERNAL_MANAGED",
            "IPProtocol": "TCP",
            "networkTier": "PREMIUM",
        },
        "url_map": {
            "defaultService": f"{backend_prefix}{prefix}-console-backend",
            "hostRules": [
                {"hosts": ["*"], "pathMatcher": "console"},
                {
                    "hosts": [edge.WORKSPACE_DOMAIN],
                    "pathMatcher": "workspace",
                },
                {
                    "hosts": ["gcp-workspace.7businesssolutions.com"],
                    "pathMatcher": "workspace",
                },
            ],
            "pathMatchers": [
                {
                    "name": "console",
                    "defaultService": f"{backend_prefix}{prefix}-console-backend",
                    "pathRules": [
                        {
                            "paths": ["/airflow", "/airflow/*"],
                            "service": f"{backend_prefix}{prefix}-airflow-backend",
                        }
                    ],
                },
                {
                    "name": "workspace",
                    "defaultService": f"{backend_prefix}{prefix}-workspace-backend",
                },
            ],
        },
        "certificate_map": {
            "name": map_name,
            "gclbTargets": [
                {
                    "targetHttpsProxy": (
                        f"//compute.googleapis.com/projects/{PROJECT_NUMBER}/"
                        f"global/targetHttpsProxies/{prefix}-https-proxy"
                    ),
                    "ipConfigs": [{"ipAddress": EXPECTED_IP, "ports": [443]}],
                },
                {
                    "targetHttpsProxy": (
                        f"//compute.googleapis.com/projects/{PROJECT_NUMBER}/"
                        "global/targetHttpsProxies/sevenbs-web-gcp-https-proxy"
                    ),
                    "ipConfigs": [{"ipAddress": "136.68.185.124", "ports": [443]}],
                },
            ],
        },
        "entries": [
            {
                "name": f"{map_name}/certificateMapEntries/{entry_name}",
                "hostname": hostname,
                "state": "ACTIVE",
                "certificates": [certificate_reference],
                "createTime": updated,
                "updateTime": updated,
            }
            for hostname, entry_name in edge.ENTRY_NAMES.items()
        ],
        "certificate": {
            "name": (
                f"projects/{PROJECT_ID}/locations/global/certificates/"
                "sevenbs-production-cert"
            ),
            "managed": {
                "state": "ACTIVE",
                "domains": sorted(edge.SHARED_DOMAINS),
                "authorizationAttemptInfo": [
                    {
                        "attemptTime": updated,
                        "domain": domain,
                        "state": "AUTHORIZED",
                    }
                    for domain in sorted(edge.SHARED_DOMAINS)
                ],
            },
            "sanDnsnames": sorted(edge.SHARED_DOMAINS),
            "expireTime": (NOW + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updateTime": updated,
        },
        "forwarding_inventory": [
            {
                "name": name,
                "IPAddress": ip,
                "portRange": port,
                "target": f"{global_api}/{target_type}/{target}",
                "loadBalancingScheme": "EXTERNAL_MANAGED",
                "IPProtocol": "TCP",
                "networkTier": "PREMIUM",
            }
            for name, ip, port, target_type, target in (
                (
                    f"{prefix}-http",
                    "136.68.67.95",
                    "80-80",
                    "targetHttpProxies",
                    f"{prefix}-http-proxy",
                ),
                (
                    f"{prefix}-http-redirect",
                    EXPECTED_IP,
                    "80-80",
                    "targetHttpProxies",
                    f"{prefix}-http-redirect-proxy",
                ),
                (
                    f"{prefix}-https",
                    EXPECTED_IP,
                    "443-443",
                    "targetHttpsProxies",
                    f"{prefix}-https-proxy",
                ),
                (
                    f"{prefix}-workspace-http",
                    "8.233.29.138",
                    "80-80",
                    "targetHttpProxies",
                    f"{prefix}-workspace-http-proxy",
                ),
            )
        ],
        "http_proxy_inventory": [
            {"name": name, "urlMap": http_redirect_map}
            for name in (
                f"{prefix}-http-proxy",
                f"{prefix}-http-redirect-proxy",
                f"{prefix}-workspace-http-proxy",
            )
        ],
        "https_proxy_inventory": [
            {
                "name": f"{prefix}-https-proxy",
                "sslCertificates": [legacy_certificate_url],
            },
        ],
        "url_map_inventory": [
            {
                "name": f"{prefix}-http-redirect",
                "defaultUrlRedirect": {
                    "httpsRedirect": True,
                    "redirectResponseCode": "MOVED_PERMANENTLY_DEFAULT",
                    "stripQuery": False,
                },
            },
            {"name": f"{prefix}-url-map"},
            {
                "name": f"{prefix}-workspace-url-map",
                "defaultService": f"{global_api}/backendServices/{prefix}-workspace-backend",
            },
        ],
        "address_inventory": [
            {
                "name": name,
                "address": ip,
                "addressType": "EXTERNAL",
                "networkTier": "PREMIUM",
                "status": "IN_USE",
            }
            for name, ip in (
                (f"{prefix}-public-https-ip", EXPECTED_IP),
                (f"{prefix}-public-ip", "136.68.67.95"),
                (f"{prefix}-workspace-ip", "8.233.29.138"),
            )
        ],
        "legacy_certificate": {
            "name": f"{prefix}-public-cert",
            "type": "MANAGED",
            "managed": {
                "status": "PROVISIONING_FAILED_PERMANENTLY",
                "domains": [
                    "gcp-console.7businesssolutions.com",
                    "gcp-workspace.7businesssolutions.com",
                ],
            },
        },
        "ssl_certificate_inventory": [
            {
                "name": f"{prefix}-public-cert",
                "selfLink": legacy_certificate_url,
            }
        ],
    }


def _validate(inventory: dict[str, object], edge_mode: str = "post-transition") -> None:
    edge.validate_inventory(
        project_id=PROJECT_ID,
        project_number=PROJECT_NUMBER,
        environment="staging",
        expected_ip=EXPECTED_IP,
        edge_mode=edge_mode,
        now=NOW,
        **inventory,
    )


def test_exact_live_shape_passes_and_parses_rfc3339_nanoseconds() -> None:
    _validate(_inventory())


def test_pre_transition_accepts_only_the_two_exact_reviewed_plaintext_routes() -> None:
    inventory = _inventory()
    api = f"https://www.googleapis.com/compute/v1/projects/{PROJECT_ID}/global/urlMaps/"
    inventory["http_proxy_inventory"][0]["urlMap"] = api + "omega-staging-url-map"
    inventory["http_proxy_inventory"][2]["urlMap"] = (
        api + "omega-staging-workspace-url-map"
    )
    _validate(inventory, "pre-transition")
    inventory["http_proxy_inventory"][0]["urlMap"] = "evil"
    with pytest.raises(edge.GateError, match="redirect inventory"):
        _validate(inventory, "pre-transition")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["proxy"].update(certificateMap="evil"), "exact map"),
        (
            lambda value: value["forwarding"].update(IPAddress="192.0.2.1"),
            "rule differs",
        ),
        (lambda value: value["url_map"].update(hostRules=[]), "host-rule inventory"),
        (lambda value: value["entries"][0].update(state="PENDING"), "entry differs"),
        (
            lambda value: value["certificate"]["managed"].update(state="PROVISIONING"),
            "not ACTIVE",
        ),
    ],
)
def test_inventory_drift_fails_closed(mutation, message: str) -> None:
    inventory = _inventory()
    mutation(inventory)
    with pytest.raises(edge.GateError, match=message):
        _validate(inventory)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["forwarding_inventory"].append(
            {"name": "omega-staging-unreviewed"}
        ),
        lambda value: value["forwarding_inventory"].append(
            {
                "name": "attacker-different-prefix",
                "IPAddress": EXPECTED_IP,
                "target": "evil",
            }
        ),
        lambda value: value["http_proxy_inventory"][0].update(urlMap="evil"),
        lambda value: value["address_inventory"][1].update(status="RESERVED"),
        lambda value: value["legacy_certificate"]["managed"].update(status="ACTIVE"),
        lambda value: value["proxy"].update(sslCertificates=[]),
        lambda value: value["ssl_certificate_inventory"].append(
            {
                "name": "attacker-orphan",
                "selfLink": value["proxy"]["sslCertificates"][0],
            }
        ),
    ],
)
def test_global_edge_or_quarantine_drift_fails_closed(mutation) -> None:
    inventory = _inventory()
    mutation(inventory)
    with pytest.raises(edge.GateError):
        _validate(inventory)


@pytest.mark.parametrize(
    "name",
    [
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_COMPUTE",
        "HTTPS_PROXY",
        "SSL_CERT_FILE",
        "SSLKEYLOGFILE",
        "CURL_HOME",
    ],
)
def test_ambient_network_and_cloud_overrides_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "hostile")
    with pytest.raises(edge.GateError, match="override variables"):
        edge._clean_environment(Path("/config"), Path("/ca"))


def test_edge_cli_rejects_any_unsealed_gcloud_execution() -> None:
    with pytest.raises(edge.GateError, match="sealed transaction runtime"):
        edge.main([])


def test_cli_uses_fixed_binaries_and_disables_curlrc() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert 'Path("/usr/bin/curl")' in source
    assert 'Path("/usr/bin/dig")' in source
    assert re.search(r'"-q",\s*"--fail"', source)
    assert re.search(r'"--cacert",\s*str\(ca_bundle\)', source)
    assert "ipaddress.IPv4Address" in source
    assert "MAX_COMMAND_OUTPUT" in source
    assert "CLOUDSDK_API_ENDPOINT_OVERRIDES" not in os.environ


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["url_map"].update(defaultService="evil"),
        lambda value: value["url_map"]["pathMatchers"][0].update(defaultService="evil"),
        lambda value: value["url_map"]["pathMatchers"][0]["pathRules"][0].update(
            paths=["/airflow/*"]
        ),
        lambda value: value["url_map"]["pathMatchers"][0]["pathRules"][0].update(
            service="evil"
        ),
        lambda value: value["url_map"]["pathMatchers"][1].update(extra="drift"),
    ],
)
def test_url_map_backend_and_path_rule_drift_fails_closed(mutation) -> None:
    inventory = _inventory()
    mutation(inventory)
    with pytest.raises(edge.GateError, match="service differs|path-matcher differs"):
        _validate(inventory)


def test_main_wires_private_ca_and_exact_gcloud_identity_then_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _inventory()
    for name in tuple(os.environ):
        if edge.FORBIDDEN_ENVIRONMENT.search(name):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(edge, "_binary", lambda name: f"/fixed/{name}")
    monkeypatch.setattr(
        edge,
        "_ca_bundle",
        lambda: b"-----BEGIN CERTIFICATE-----\nfixture\n",
    )
    validated: list[tuple[Path, str, str]] = []

    def validate_config(path, *, expected_account, expected_project):
        validated.append((path, expected_account, expected_project))
        return tmp_path

    monkeypatch.setattr(edge, "_validate_gcloud_config", validate_config)
    values = iter(
        [
            inventory["proxy"],
            inventory["forwarding"],
            inventory["url_map"],
            inventory["certificate_map"],
            inventory["entries"],
            inventory["certificate"],
            inventory["forwarding_inventory"],
            inventory["http_proxy_inventory"],
            inventory["https_proxy_inventory"],
            inventory["url_map_inventory"],
            inventory["address_inventory"],
            inventory["legacy_certificate"],
            inventory["ssl_certificate_inventory"],
        ]
    )
    monkeypatch.setattr(edge, "_gcloud_json", lambda *args, **kwargs: next(values))
    monkeypatch.setattr(edge, "_verify_dns", lambda *args, **kwargs: None)
    observed_ca: list[Path] = []

    def run(command, *, environment, timeout_seconds=30):
        path = Path(environment["SSL_CERT_FILE"])
        observed_ca.append(path)
        assert path.is_file()
        assert path.stat().st_mode & 0o777 == 0o400
        return (
            "301\thttps://attacker.invalid/healthz"
            if command[-1].startswith("http://")
            else "200"
        )

    monkeypatch.setattr(edge, "_run", run)
    account = "operator@example.com"
    assert (
        edge.main(
            [
                "--project-id",
                PROJECT_ID,
                "--project-number",
                PROJECT_NUMBER,
                "--environment",
                "staging",
                "--expected-ip",
                EXPECTED_IP,
                "--legacy-console-ip",
                "136.68.67.95",
                "--legacy-workspace-ip",
                "8.233.29.138",
                "--edge-mode",
                "post-transition",
                "--expected-account",
                account,
                "--gcloud-config",
                str(tmp_path),
            ],
            sealed_gcloud_command=[
                "/sealed/python",
                "-I",
                "-S",
                "-B",
                "/sealed/gcloud.py",
            ],
            sealed_runtime_environment={
                "CLOUDSDK_CONFIG": str(tmp_path),
                "OMEGA_SEALED_PYTHON": "/sealed/python",
                "PATH": "/usr/bin:/bin",
            },
        )
        == 0
    )
    assert validated == [(tmp_path, account, PROJECT_ID)]
    assert len(observed_ca) == 5
    assert all(not path.exists() for path in observed_ca)
