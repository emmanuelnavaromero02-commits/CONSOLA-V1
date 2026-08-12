from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
GCP_TF = REPO / "infra" / "terraform-gcp"


def _quoted_hcl_values(source: str, name: str) -> set[str]:
    match = re.search(
        rf"\b{name}\s*=\s*toset\(\[(.*?)\]\)",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))


def _quoted_python_tuple(source: str, name: str) -> set[str]:
    match = re.search(
        rf"^{name}\s*=\s*\((.*?)^\)",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None
    return set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))


def _hcl_resource_block(source: str, resource_type: str, name: str) -> str:
    marker = f'resource "{resource_type}" "{name}" {{'
    start = source.index(marker)
    depth = 0
    for offset, character in enumerate(source[start:]):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start : start + offset + 1]
    raise AssertionError(f"unterminated Terraform resource: {resource_type}.{name}")


def test_gcp_terraform_files_stay_modular():
    offenders = []
    for path in GCP_TF.rglob("*"):
        if any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(GCP_TF).parts
        ):
            continue
        if path.is_file() and path.suffix not in {".hcl"}:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 300:
                offenders.append(f"{path.relative_to(REPO)}:{len(lines)}")
    assert offenders == []


def test_gcp_startup_uses_native_lakehouse_provider_not_gcsfuse():
    startup = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    runtime = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    compose = (GCP_TF / "templates" / "docker-compose.gcp.yml.tftpl").read_text(
        encoding="utf-8"
    )
    combined = f"{startup}\n{runtime}\n{compose}"

    assert "gcsfuse" not in combined
    assert "gcs_fuse" not in combined
    assert "LAKEHOUSE_PROVIDER gcs" in runtime
    assert "LAKEHOUSE_PROVIDER: $${LAKEHOUSE_PROVIDER:-gcs}" in compose


def test_pr1_foundation_cannot_cross_the_exact_image_lock_handoff() -> None:
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    runtime = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    assert "exact_runtime_contract_ready = false" in locals_tf
    guard = runtime.index('if [[ "${EXACT_RUNTIME_CONTRACT_READY}" != "true" ]]')
    blocked = runtime.index("exit 0", guard)
    handoff = runtime.index('"${WATCHDOG}" foundation-handoff', guard)
    check = runtime.index('"${WATCHDOG}" foundation-check', handoff)
    receipt = runtime.index("write_foundation_receipt", check)
    assert guard < handoff < check < receipt < blocked
    assert blocked < runtime.index("systemctl unmask", blocked)
    assert blocked < runtime.index("download_source", blocked)
    assert blocked < runtime.index(
        '"${COMPOSE[@]}" up --no-build --pull never', blocked
    )


def test_canonical_data_and_secret_containers_are_destroy_protected() -> None:
    storage = (GCP_TF / "storage.tf").read_text(encoding="utf-8")
    secrets = (GCP_TF / "secrets.tf").read_text(encoding="utf-8")
    lakehouse = storage[
        storage.index('resource "google_storage_bucket" "lakehouse"') : storage.index(
            'resource "google_storage_bucket" "release_backups"'
        )
    ]
    runtime_secrets = secrets[
        secrets.index(
            'resource "google_secret_manager_secret" "runtime"'
        ) : secrets.index(
            'resource "google_secret_manager_secret" "ghcr_pull_credentials"'
        )
    ]
    ghcr_secret = secrets[
        secrets.index(
            'resource "google_secret_manager_secret" "ghcr_pull_credentials"'
        ) :
    ]
    for block in (lakehouse, runtime_secrets, ghcr_secret):
        assert "prevent_destroy = true" in block


def test_legacy_plaintext_forwarders_can_only_reach_the_https_redirect() -> None:
    terraform = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(GCP_TF.glob("*.tf"))
    )
    for name in ("public", "workspace"):
        proxy = _hcl_resource_block(terraform, "google_compute_target_http_proxy", name)
        assert "url_map = google_compute_url_map.http_redirect[0].id" in proxy
        assert "google_compute_url_map.public" not in proxy
        assert "google_compute_url_map.workspace" not in proxy

    public_rule = _hcl_resource_block(
        terraform, "google_compute_global_forwarding_rule", "http"
    )
    workspace_rule = _hcl_resource_block(
        terraform, "google_compute_global_forwarding_rule", "workspace_http"
    )
    assert (
        "target                = google_compute_target_http_proxy.public.id"
        in public_rule
    )
    assert (
        "target                = google_compute_target_http_proxy.workspace.id"
        in workspace_rule
    )
    redirect = _hcl_resource_block(terraform, "google_compute_url_map", "http_redirect")
    assert "https_redirect         = true" in redirect
    assert 'redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"' in redirect
    checks = (GCP_TF / "checks.tf").read_text(encoding="utf-8")
    assert "condition     = local.public_https_enabled" in checks


def test_gcp_secret_manifest_includes_macro_cartridges():
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    required = {
        "internal_api_key_banxico_to_console",
        "internal_api_key_inegi_to_console",
        "internal_api_key_sec_edgar_to_console",
        "omega_cartridge_banxico_password",
        "omega_cartridge_inegi_password",
        "omega_cartridge_sec_edgar_password",
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
    }
    missing = sorted(item for item in required if item not in locals_tf)
    assert missing == []


def test_secret_permission_probe_inventory_matches_terraform_exactly() -> None:
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    probe = (REPO / "scripts/gcp/verify-secret-access.sh").read_text(encoding="utf-8")

    managed = _quoted_hcl_values(locals_tf, "secret_names")
    host_allow = _quoted_hcl_values(locals_tf, "app_host_secret_names")
    probe_managed = _quoted_python_tuple(probe, "managed")
    probe_host_allow = _quoted_python_tuple(probe, "host_allow")

    assert len(managed) == 69
    assert managed == probe_managed
    assert host_allow == probe_host_allow
    assert len(host_allow) == 5
    assert "ghcr_pull_credentials" not in managed
    assert 'allow = (*host_allow,\n    "ghcr_pull_credentials",\n)' in probe
    assert "projects/{project}/secrets/omega-{environment}-{name}" in probe
    assert ":testIamPermissions" in probe
    assert "versions/latest:access" not in probe


def test_lakehouse_iam_binds_the_effective_canonical_bucket():
    iam = (GCP_TF / "iam.tf").read_text(encoding="utf-8")
    resource = iam[
        iam.index(
            'resource "google_storage_bucket_iam_member" "app_lakehouse"'
        ) : iam.index(
            'resource "google_project_iam_custom_role" "release_backup_writer"'
        )
    ]
    assert "bucket = local.lakehouse_bucket" in resource
    assert "google_storage_bucket.lakehouse" not in resource


def test_startup_embeds_watchdog_and_all_host_http_bypasses_ambient_proxies():
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    startup = (GCP_TF / "templates/startup.sh.tftpl").read_text(encoding="utf-8")
    safe_io = (REPO / "scripts/gcp/safe_io.py").read_text(encoding="utf-8")
    secret_probe = (REPO / "scripts/gcp/verify-secret-access.sh").read_text(
        encoding="utf-8"
    )
    bootstrap = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    adoption = (REPO / "scripts/gcp/finalize-startup-adoption.sh").read_text(
        encoding="utf-8"
    )

    assert "operation_watchdog_base64" in locals_tf
    assert "OPERATION_WATCHDOG_BASE64" in startup
    assert "OMEGA_GCP_OPERATION_WATCHDOG_SOURCE" in startup
    assert (
        "ExecStartPre=/usr/local/sbin/omega-operation-gate authorize-start" in bootstrap
    )
    assert "OMEGA_GCP_RUNTIME_START_AUTHORIZED=1" in bootstrap
    assert "urllib.request.urlopen" not in safe_io
    assert "urllib.request.urlopen" not in secret_probe
    assert "urllib.request.ProxyHandler({})" in safe_io
    assert "urllib.request.ProxyHandler({})" in secret_probe
    assert "curl -q -fsS --noproxy '*' --max-time 5" in bootstrap
    assert "--show-error --noproxy '*' --max-time 10" in adoption


def test_startup_publishes_exact_server_owned_gcp_host_identity_before_runtime() -> (
    None
):
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    startup = (GCP_TF / "templates/startup.sh.tftpl").read_text(encoding="utf-8")
    safe_io = (REPO / "scripts/gcp/safe_io.py").read_text(encoding="utf-8")

    for field in (
        "project_id",
        "instance_id",
        "instance_name",
        "zone",
        "service_account_email",
    ):
        assert field in locals_tf[locals_tf.index("host_identity = {") :]
    publish = startup.index("host-identity-install")
    owner = startup.index("systemd-run")
    assert publish < owner
    assert "--output /etc/omega/gcp-host-identity.json" in startup
    assert 'HOST_IDENTITY_PATH = Path("/etc/omega/gcp-host-identity.json")' in safe_io
    assert "os.fchmod(descriptor, 0o400)" in safe_io
    assert "live GCP host identity differs from Terraform authority" in safe_io


def test_every_privileged_curl_ignores_hostile_root_curlrc_first() -> None:
    sources = (
        REPO / "scripts/gcp/bootstrap-runtime.sh",
        REPO / "scripts/gcp/finalize-startup-adoption.sh",
        REPO / "scripts/gcp/metadata-firewall.sh",
    )
    command_lines: list[str] = []
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if (
                stripped.startswith(("curl ", "if curl ", "if ! curl "))
                or "$(curl " in stripped
            ):
                command_lines.append(stripped)

    assert len(command_lines) == 6
    assert all(
        re.search(r"(?:^|\s|\$\()curl -q(?:\s|$)", line) for line in command_lines
    )


def test_bootstrap_hmac_pair_is_resolved_before_any_env_mutation() -> None:
    source = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    start = source.index('if [[ -n "${GCS_HMAC_ACCESS_KEY_VERSION}"')
    end = source.index("unset hmac_access_key hmac_secret_key", start)
    block = source[start:end]
    first_mutation = block.index("set_env AWS_ACCESS_KEY_ID")
    access_read = block.index("gcs_hmac_access_key_id")
    secret_read = block.index("gcs_hmac_secret_access_key")
    assert access_read < secret_read < first_mutation
    assert 'elif [[ -z "${GCS_HMAC_ACCESS_KEY_VERSION}"' in source[start:]
    assert '      -n "${GCS_HMAC_SECRET_KEY_VERSION}" ]]' in block
    assert '        -z "${GCS_HMAC_SECRET_KEY_VERSION}" ]]' in source[start:]
    assert "set +e" not in block
    assert "GCS HMAC secret pair is partial or unreadable" in source[start:]


def test_bootstrap_requires_data_readiness_before_sealing_provenance() -> None:
    source = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    basic = source.index("http://127.0.0.1:8000/readyz")
    data = source.index("http://127.0.0.1:8000/readyz?require_data=1", basic)
    ok_true = source.index('payload.get("ok") is True', data)
    provenance = source.index("bootstrap-record", ok_true)
    publish = source.index('"${SAFE_IO}" symlink-publish', provenance)
    assert basic < data < ok_true < provenance < publish


def test_secret_permission_probe_ignores_hostile_process_proxies() -> None:
    source = (REPO / "scripts/gcp/verify-secret-access.sh").read_text(encoding="utf-8")
    delimiter = "<<'PY'\n"
    start = source.index(delimiter) + len(delimiter)
    python_probe = source[start : source.index("\nPY", start)]
    harness = r"""
import io
import json
import urllib.request

TOKEN = "server-owned-token-never-log"

class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

class DirectOpener:
    def open(self, request, timeout):
        headers = {key.lower(): value for key, value in request.header_items()}
        if request.full_url.startswith("http://metadata.google.internal/"):
            assert timeout == 10
            assert headers == {"metadata-flavor": "Google"}
            return Response(json.dumps({"access_token": TOKEN}).encode())
        assert timeout == 30
        assert headers["authorization"] == f"Bearer {TOKEN}"
        assert headers["content-type"] == "application/json"
        assert json.loads(request.data) == {
            "permissions": ["secretmanager.versions.access"]
        }
        allowed = (
            "control_room_evidence_signing_key_id",
            "control_room_evidence_signing_key",
            "control_room_evidence_signing_previous_keys",
            "gcs_hmac_access_key_id",
            "gcs_hmac_secret_access_key",
            "ghcr_pull_credentials",
        )
        permissions = (
            ["secretmanager.versions.access"]
            if any(name in request.full_url for name in allowed)
            else []
        )
        return Response(json.dumps({"permissions": permissions}).encode())

def direct_build_opener(*handlers):
    assert len(handlers) == 3
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    try:
        handlers[1].redirect_request(None, None, 307, "redirect", {}, "https://attacker.invalid")
    except RuntimeError as exc:
        assert "redirects are forbidden" in str(exc)
    else:
        raise AssertionError("authenticated redirect handler accepted a 307")
    assert isinstance(handlers[2], urllib.request.HTTPSHandler)
    return DirectOpener()

def forbidden_urlopen(*_args, **_kwargs):
    raise AssertionError("ambient-proxy-aware urlopen must never be used")

urllib.request.build_opener = direct_build_opener
urllib.request.urlopen = forbidden_urlopen
"""
    ca_candidates = (
        Path("/private/etc/ssl/cert.pem"),
        Path(ssl.get_default_verify_paths().cafile or "/missing-system-ca").resolve(),
    )
    test_ca = next(
        (
            path
            for path in ca_candidates
            if path.is_file()
            and not path.is_symlink()
            and path.stat().st_uid == 0
            and not path.stat().st_mode & 0o022
            and path.resolve(strict=True) == path
        ),
        None,
    )
    if test_ca is None:
        pytest.skip("host has no immutable root-owned system CA bundle fixture")
    python_probe = python_probe.replace(
        'pathlib.Path("/etc/ssl/certs/ca-certificates.crt")',
        f"pathlib.Path({str(test_ca)!r})",
    )
    env = {**os.environ, "NO_PROXY": "", "no_proxy": ""}
    for variable in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env[variable] = "http://127.0.0.1:9"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"{harness}\n{python_probe}",
            "omega-production",
            "production",
            "revoke",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "PASS"
    assert json.loads(result.stdout)["forbidden_resources_checked"] == 64
    assert json.loads(result.stdout)["forbidden_access"] == 0
    assert "server-owned-token-never-log" not in result.stdout
    assert "server-owned-token-never-log" not in result.stderr


def test_bootstrap_arms_watchdog_before_runtime_start_and_disarms_last():
    source = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")

    marker = source.index('/usr/bin/python3 -I - "${OPERATION_MARKER}" "${SOURCE_SHA}"')
    trap = source.index("trap bootstrap_fail_closed EXIT", marker)
    arm = source.index('"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}"', trap)
    assert '"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}" 7200' in source
    unmask = source.index("systemctl unmask --runtime", trap)
    start = source.index(
        "systemctl start containerd.service docker.socket docker.service", unmask
    )
    compose_up = source.index('"${COMPOSE[@]}" up --no-build --pull never -d', start)
    assert marker < trap < arm < unmask < start < compose_up
    assert "systemctl enable docker" not in source
    assert source.index("systemctl disable docker.service", trap) < unmask
    assert unmask < source.index("systemctl is-enabled", unmask) < start
    assert "masked-runtime)" not in source

    revoke = source.rindex(
        "rm -f -- /run/systemd/system/docker.service.d/omega-initial-bootstrap.conf"
    )
    complete = source.index('"${WATCHDOG}" complete "$$" "${OPERATION_MARKER}"')
    assert revoke < complete
    assert 'rm -f -- "${OPERATION_MARKER}"' not in source


def test_bootstrap_publishes_authorization_only_after_package_disable() -> None:
    source = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")

    install_docker = source.index(
        '/usr/bin/apt-get install -y --no-install-recommends -- "${DOCKER_PACKAGES[@]}"'
    )
    disable = source.index(
        "systemctl disable docker.service docker.socket containerd.service",
        install_docker,
    )
    authorization = source.index('mv -Tf "${INITIAL_AUTH_TMP}"', disable)
    authorization_target = source.index(
        "/run/systemd/system/docker.service.d/omega-initial-bootstrap.conf",
        authorization,
    )
    arm = source.index('"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}" 7200')
    unmask = source.index(
        "systemctl unmask --runtime docker.service docker.socket containerd.service",
        arm,
    )

    assert (
        install_docker < disable < authorization < authorization_target < arm < unmask
    )
    assert source.count("OMEGA_GCP_RUNTIME_START_AUTHORIZED=1") == 1
    assert "omega-initial-bootstrap.conf" not in source[:install_docker]
    assert "systemctl enable docker" not in source


def test_metadata_firewall_is_mandatory_before_existing_or_fresh_runtime_start() -> (
    None
):
    source = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    existing = source[
        source.index("# Existing hosts recover") : source.index(
            'install -d -m 0755 "${APP_ROOT}/releases"'
        )
    ]
    tools = existing.index("command -v iptables")
    install = existing.index('"${METADATA_FIREWALL_SOURCE}" install', tools)
    gate = existing.index("/usr/local/sbin/omega-operation-gate", install)
    reboot = existing.index('/bin/bash -p "${REBOOT_RUNTIME_SOURCE}"', gate)
    assert tools < install < gate < reboot
    assert "apt-get" not in existing

    docker_package = source.index(
        '/usr/bin/apt-get install -y --no-install-recommends -- "${DOCKER_PACKAGES[@]}"'
    )
    disable = source.index(
        "systemctl disable docker.service docker.socket containerd.service",
        docker_package,
    )
    mandatory_install = source.index('"${METADATA_FIREWALL_SOURCE}" install', disable)
    authorization = source.index('mv -Tf "${INITIAL_AUTH_TMP}"', mandatory_install)
    arm = source.index('"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}"', authorization)
    docker_unmask = source.index("systemctl unmask --runtime", arm)
    docker_start = source.index(
        "systemctl start containerd.service docker.socket docker.service",
        docker_unmask,
    )
    assert docker_package < disable < mandatory_install < authorization
    assert authorization < arm < docker_unmask < docker_start

    gpg_start = source.index("curl -q -fsSL --proto '=https' --proto-redir '=https'")
    gpg_end = source.index("/etc/apt/keyrings/docker.asc", gpg_start)
    gpg_command = source[gpg_start:gpg_end]
    assert "--connect-timeout 10" in gpg_command
    assert "--max-time 60" in gpg_command
    assert gpg_start < docker_package


def test_startup_embeds_and_fresh_bootstrap_compares_every_safety_helper() -> None:
    locals_tf = (GCP_TF / "locals.tf").read_text(encoding="utf-8")
    startup = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    bootstrap = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    helper_contracts = {
        "bootstrap_runtime_base64": "bootstrap-runtime.sh",
        "safe_io_base64": "safe_io.py",
        "metadata_firewall_base64": "metadata-firewall.sh",
        "operation_guard_base64": "omega-operation-gate",
        "operation_watchdog_base64": "operation-watchdog.sh",
        "reboot_runtime_base64": "reboot-runtime.sh",
        "runtime_contract_base64": "runtime_contract.py",
    }

    for local_name, filename in helper_contracts.items():
        assert local_name in locals_tf
        assert f'"$RUNTIME_ROOT/{filename}"' in startup
    assert locals_tf.count("base64gzip(file(") == 7
    assert startup.count("| base64 -d | gzip -d >") == 7

    comparisons = (
        ('"${RELEASE_WATCHDOG}"', '"${WATCHDOG}"'),
        ('"${REBOOT_RUNTIME_SOURCE}"', '"${RELEASE_REBOOT}"'),
        ('"${RUNTIME_CONTRACT_SOURCE}"', '"${RELEASE_CONTRACT}"'),
        ('"${SAFE_IO}"', '"${RELEASE_SAFE_IO}"'),
        ('"$0"', '"${RELEASE_BOOTSTRAP}"'),
        ('"${METADATA_FIREWALL_SOURCE}"', '"${RELEASE_FIREWALL}"'),
        ('"${OPERATION_GUARD_SOURCE}"', '"${RELEASE_GATE}"'),
    )
    for left, right in comparisons:
        assert f"! cmp -s {left} {right}" in bootstrap

    existing = bootstrap[
        bootstrap.index("# Existing hosts recover") : bootstrap.index(
            'install -d -m 0755 "${APP_ROOT}/releases"'
        )
    ]
    assert '/bin/bash -p "${REBOOT_RUNTIME_SOURCE}"' in existing
    assert 'OMEGA_GCP_RUNTIME_CONTRACT="${RUNTIME_CONTRACT_SOURCE}"' in existing
    assert 'OMEGA_GCP_SAFE_IO="${SAFE_IO}"' in existing
    assert 'OMEGA_GCP_WATCHDOG="${WATCHDOG}"' in existing
    assert "gcs-download" not in existing
    assert "docker pull" not in existing


def test_bootstrap_delegates_exact_fstab_and_mount_readback_to_safe_io() -> None:
    bootstrap = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    safe_io = (REPO / "scripts/gcp/safe_io.py").read_text(encoding="utf-8")
    paths = bootstrap.index('"${SAFE_IO}" docker-storage paths')
    prepare = bootstrap.index('"${SAFE_IO}" docker-storage prepare', paths)
    unused = bootstrap.index('"${SAFE_IO}" docker-storage verify-unused', prepare)
    mount = bootstrap.index(
        '/usr/bin/mount -t ext4 -o discard "${DATA_DISK}" /var/lib/docker', unused
    )
    readback = bootstrap.index('"${SAFE_IO}" docker-storage verify-mounted', mount)
    assert paths < prepare < unused < mount < readback

    reconcile = safe_io[
        safe_io.index("def _reconcile_docker_fstab(") : safe_io.index(
            "def _findmnt_docker_row("
        )
    ]
    assert "os.open(fstab, flags)" in reconcile
    assert "os.pread(descriptor, info.st_size, 0)" in reconcile
    assert "while view:" in reconcile
    assert "os.fsync(descriptor)" in reconcile
    assert "_fsync_dir(fstab.parent)" in reconcile
    assert (
        'canonical = f"{device} {mountpoint} ext4 discard,defaults,nofail 0 2"'
        in reconcile
    )


def test_normal_prefence_never_persists_database_read_only_state() -> None:
    watchdog = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    emergency = watchdog[
        watchdog.index("emergency_fence() (") : watchdog.index("fence_until_safe() {")
    ]
    database_call = emergency.index("database_fence_best_effort")
    guarded_block = emergency.rfind(
        'if [[ "$record_failure" == "1" ]]', 0, database_call
    )

    assert guarded_block >= 0
    assert guarded_block < database_call < emergency.index("fi", database_call)
    assert "fence) fence_until_safe 0" in watchdog
    assert "fence-failure) fence_until_safe 1" in watchdog


def test_reboot_recovery_closes_owner_kill_window_around_runtime_start():
    source = (REPO / "scripts/gcp/reboot-runtime.sh").read_text(encoding="utf-8")

    policy_fence = source.index("restart-policy verify-fenced")
    marker = source.index(
        '/usr/bin/python3 -I - "$OPERATION_MARKER" "$DEPLOY_REF"', policy_fence
    )
    authorization = source.index('AUTH_TMP="$(mktemp', marker)
    arm = source.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"', authorization)
    assert '"$WATCHDOG" arm "$$" "$OPERATION_MARKER" 1800' in source
    unmask = source.index("systemctl unmask --runtime", arm)
    start = source.index(
        "systemctl start containerd.service docker.socket docker.service", unmask
    )
    prestart = source.index('"$RUNTIME_CONTRACT" prestart', start)
    dependency_start = source.index(
        '"${COMPOSE[@]}" start "${DEPENDENCY_SERVICES[@]}"', prestart
    )
    writer_fence = source.index("writer-fence", dependency_start)
    application_start = source.index(
        '"${COMPOSE[@]}" start -- "${application_services[@]}"', writer_fence
    )
    assert policy_fence < marker < authorization < arm < unmask < start < prestart
    assert prestart < dependency_start < writer_fence < application_start
    assert unmask < source.index("systemctl is-enabled", unmask) < start

    revoke = source.rindex('rm -f -- "$RUNTIME_AUTH_DROPIN"')
    reload = source.index("systemctl daemon-reload", revoke)
    complete = source.index('"$WATCHDOG" complete "$$" "$OPERATION_MARKER"', reload)
    assert revoke < reload < complete
    assert 'rm -f -- "$OPERATION_MARKER"' not in source

    cleanup = source[source.index("cleanup() {") : source.index("trap cleanup EXIT")]
    assert '"$WATCHDOG" fence-failure "$$" "$OPERATION_MARKER"' in cleanup
    assert "pgrep" not in cleanup
    assert "hard fence incomplete; durable marker retained" in cleanup


def test_reboot_explicitly_starts_dependencies_whose_compose_policy_is_no() -> None:
    compose = yaml.safe_load(
        (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    restart_policies = {
        service: services[service].get("restart", "no")
        for service in ("postgres", "postgres_gold", "redis", "minio", "mailhog")
    }
    assert restart_policies == {
        "postgres": "no",
        "postgres_gold": "no",
        "redis": "no",
        "minio": "no",
        "mailhog": "unless-stopped",
    }

    reboot = (REPO / "scripts/gcp/reboot-runtime.sh").read_text(encoding="utf-8")
    assert "DEPENDENCY_SERVICES=(postgres postgres_gold redis minio mailhog)" in reboot
    prestart = reboot.index('"$RUNTIME_CONTRACT" prestart')
    dependency_start = reboot.index(
        '"${COMPOSE[@]}" start "${DEPENDENCY_SERVICES[@]}"', prestart
    )
    writer_fence = reboot.index("writer-fence", dependency_start)
    application_start = reboot.index(
        '"${COMPOSE[@]}" start -- "${application_services[@]}"', writer_fence
    )
    assert prestart < dependency_start < writer_fence < application_start


def test_startup_hard_fences_live_restore_before_exporting_or_bootstrapping():
    source = (GCP_TF / "templates/startup.sh.tftpl").read_text(encoding="utf-8")

    watchdog_decode = source.index(
        "printf '%s' \"$OPERATION_WATCHDOG_BASE64\" | base64 -d"
    )
    chmod = source.index('chmod 0700 "$RUNTIME_ROOT/bootstrap-runtime.sh"')
    prepare = source.index("restart-policy prepare", chmod)
    stop = source.index("systemctl stop docker.service", prepare)
    fence = source.index('"$RUNTIME_ROOT/operation-watchdog.sh" fence-prestart 1', stop)
    unit_verification = source.index("runtime_units_hard_fenced ||", fence)
    owner = source.index("systemd-run --quiet --wait --pipe --collect", fence)
    private_config = source.index(
        '--setenv="OMEGA_GCP_STARTUP_CONFIG_FILE=$RUNTIME_ROOT/startup-config.base64"',
        owner,
    )
    bootstrap = source.index('"$RUNTIME_ROOT/bootstrap-runtime.sh"', private_config)
    assert watchdog_decode < chmod < prepare < stop < fence
    assert fence < unit_verification < owner < private_config < bootstrap


@pytest.mark.parametrize(
    ("mode", "expected_rc"),
    (
        ("not-found", 0),
        ("masked", 0),
        ("unmasked", 1),
        ("timeout", 1),
    ),
)
def test_startup_runtime_unit_readback_is_authoritative(
    mode: str,
    expected_rc: int,
) -> None:
    source = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    function = source[
        source.index("runtime_units_hard_fenced() {") : source.index(
            "\nruntime_units_hard_fenced ||"
        )
    ]
    harness = f"""set -uo pipefail
MODE={mode!r}
timeout() {{
  [[ "$MODE" == timeout ]] && return 124
  while [[ "$#" -gt 0 && "$1" != systemctl ]]; do shift; done
  [[ "${{1:-}}" == systemctl ]] || return 124
  shift
  systemctl "$@"
}}
systemctl() {{
  if [[ "$1" == show ]]; then
    local property="${{3#--property=}}"
    case "$property" in
      LoadState)
        [[ "$MODE" == not-found ]] && printf 'not-found\n' || printf 'loaded\n'
        ;;
      ActiveState) printf 'inactive\n' ;;
      SubState) printf 'dead\n' ;;
      MainPID|ControlPID) printf '0\n' ;;
      *) return 98 ;;
    esac
    return 0
  fi
  if [[ "$1" == is-enabled ]]; then
    if [[ "$MODE" == unmasked ]]; then printf 'enabled\n'; return 0; fi
    printf 'masked\n'
    return 1
  fi
  return 99
}}
{function}
runtime_units_hard_fenced
"""

    result = subprocess.run(
        ["bash", "-c", harness, "startup-unit-readback"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == expected_rc, result.stderr


@pytest.mark.parametrize(
    ("mode", "expected_rc"),
    (
        ("not-found", 0),
        ("masked", 0),
        ("unmasked", 1),
        ("timeout", 1),
    ),
)
def test_watchdog_emergency_fence_accepts_only_verified_unit_state(
    tmp_path: Path,
    mode: str,
    expected_rc: int,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    helpers = source[
        source.index("service_fully_stopped() {") : source.index(
            "\nsafe_io_bounded() {"
        )
    ]
    emergency = source[
        source.index("emergency_fence() (") : source.index("\nfence_until_safe() {")
    ]
    # macOS ships Bash 3.2 without associative arrays. This harness returns an
    # empty Docker inventory, so an indexed array is behaviorally identical.
    emergency = emergency.replace("declare -A", "declare -a")
    harness = f"""set -uo pipefail
MODE={mode!r}
LOCK_PATH="$1/lock"
SAFE_IO=/usr/bin/true
systemctl_bounded() {{
  [[ "$MODE" == timeout && "$1" == show ]] && return 124
  if [[ "$1" == show ]]; then
    local property="${{3#--property=}}"
    case "$property" in
      LoadState)
        [[ "$MODE" == not-found ]] && printf 'not-found\n' || printf 'loaded\n'
        ;;
      ActiveState) printf 'inactive\n' ;;
      SubState) printf 'dead\n' ;;
      MainPID|ControlPID) printf '0\n' ;;
      *) return 98 ;;
    esac
    return 0
  fi
  if [[ "$1" == is-enabled ]]; then
    if [[ "$MODE" == unmasked ]]; then printf 'enabled\n'; return 0; fi
    printf 'masked\n'
    return 1
  fi
  return 0
}}
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
record_fence_evidence() {{ :; }}
revoke_runtime_authorization() {{ :; }}
database_fence_best_effort() {{ :; }}
docker_bounded() {{ [[ "$1" == ps ]] && return 0; return 1; }}
safe_io_bounded() {{ :; }}
logger() {{ :; }}
{helpers}
{emergency}
emergency_fence 0
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-unit-readback", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == expected_rc, result.stderr


def test_operation_marker_publish_never_replaces_a_dangling_symlink(
    tmp_path: Path,
) -> None:
    for script_name in ("bootstrap-runtime.sh", "reboot-runtime.sh"):
        source = (REPO / "scripts" / "gcp" / script_name).read_text(encoding="utf-8")
        guard = (
            '[[ -e "${OPERATION_MARKER}" || -L "${OPERATION_MARKER}" ]]'
            if script_name == "bootstrap-runtime.sh"
            else '[[ -e "$OPERATION_MARKER" || -L "$OPERATION_MARKER" ]]'
        )
        assert guard in source
        writer_invocation = (
            '/usr/bin/python3 -I - "${OPERATION_MARKER}"'
            if script_name == "bootstrap-runtime.sh"
            else '/usr/bin/python3 -I - "$OPERATION_MARKER"'
        )
        anchor = (
            source.index('install -d -m 0755 "${APP_ROOT}/releases"')
            if script_name == "bootstrap-runtime.sh"
            else source.index("restart-policy verify-fenced")
        )
        writer_start = source.index(writer_invocation, anchor)
        writer_end = source.index("\nPY", writer_start)
        writer = source[writer_start:writer_end]
        assert "path.lstat()" in writer
        assert "os.link(temporary, path, follow_symlinks=False)" in writer
        assert "os.replace(temporary, path)" not in writer

    marker = tmp_path / "operation-state.json"
    marker.symlink_to(tmp_path / "missing-target")
    candidate = tmp_path / ".operation-state.candidate"
    candidate.write_text("durable candidate\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        os.link(candidate, marker, follow_symlinks=False)
    assert marker.is_symlink()
    assert os.readlink(marker) == str(tmp_path / "missing-target")


def test_startup_owner_collision_fences_the_complete_old_cgroup() -> None:
    source = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    safe_io = (REPO / "scripts/gcp/safe_io.py").read_text(encoding="utf-8")

    owner = source.index("systemd-run --quiet --wait --pipe --collect")
    owner_contract = source.index("--property=KillMode=control-group", owner)
    empty_probe = source.index("owner_unit_empty()", owner_contract)
    delegated_probe = source.index(
        '"$RUNTIME_ROOT/safe_io.py" owner-unit-empty', empty_probe
    )
    collision = source.index('if [[ "$OWNER_RC" != "0" ]]', delegated_probe)
    external_fence = source.index(
        '"$RUNTIME_ROOT/operation-watchdog.sh" fence-external 1', collision
    )

    assert owner < owner_contract < empty_probe < delegated_probe < collision
    assert collision < external_fence
    owner_helper = safe_io[
        safe_io.index("def _owner_cgroup_empty(") : safe_io.index(
            "def command_owner_unit_empty("
        )
    ]
    assert 'rows.get("populated") == "0"' in owner_helper
    assert 'path / "cgroup.events"' in owner_helper
    assert "--property=Type=exec" in source[owner:owner_contract]
    assert "--property=SendSIGKILL=yes" in source[owner:delegated_probe]
    assert "--property=TimeoutStopSec=5s" in source[owner:delegated_probe]


def test_existing_host_prepares_restart_fence_before_any_runtime_stop() -> None:
    source = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    existing = source.index('if [[ -e "$STATE_LINK" || -L "$STATE_LINK" ]]')
    legacy_gate = source.index('"$RUNTIME_ROOT/omega-operation-gate"', existing)
    adoption = source.index('runtime_contract.py" adopt-provenance', legacy_gate)
    strict_gate = source.index('"$RUNTIME_ROOT/omega-operation-gate"', adoption)
    live_state = source.index('runtime_contract.py" live-state', strict_gate)
    prepare = source.index('runtime_contract.py" restart-policy prepare', live_state)
    verify_inactive = source.index(
        'runtime_contract.py" restart-policy verify-fenced', prepare
    )
    legacy = source.index("uninitialized host has an active legacy", verify_inactive)
    stop = source.index("systemctl stop docker.service", legacy)
    prestart = source.index('operation-watchdog.sh" fence-prestart', stop)

    assert existing < legacy_gate < adoption < strict_gate < live_state < prepare
    assert prepare < verify_inactive < legacy
    assert legacy < stop < prestart
    assert "early_fail_closed" in source[existing:stop]
    assert (
        "OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE=1" in source[legacy_gate - 100 : adoption]
    )
    assert (
        "legacy runtime provenance could not be adopted exactly"
        in source[adoption:strict_gate]
    )
    assert "restart-policy prepare" not in source[:existing]


def test_inactive_legacy_provenance_adopts_under_watchdog_and_restart_fence() -> None:
    startup = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    bootstrap = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    reboot = (REPO / "scripts/gcp/reboot-runtime.sh").read_text(encoding="utf-8")

    inactive = startup.index("restart-policy verify-fenced", startup.index("else"))
    stop = startup.index("systemctl stop docker.service", inactive)
    owner = startup.index("systemd-run --quiet --wait --pipe --collect", stop)
    assert inactive < stop < owner

    legacy_gate = bootstrap.index("OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE=1")
    reboot_exec = bootstrap.index(
        '/bin/bash -p "${REBOOT_RUNTIME_SOURCE}"', legacy_gate
    )
    assert legacy_gate < reboot_exec

    schema = reboot.index(
        'PROVENANCE_IDENTITY="$("$RUNTIME_CONTRACT" provenance-metadata'
    )
    policy_fence = reboot.index("restart-policy verify-fenced", schema)
    marker = reboot.index('/usr/bin/python3 -I - "$OPERATION_MARKER"', policy_fence)
    watchdog = reboot.index('"$WATCHDOG" arm', marker)
    auth = reboot.index("OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE=1", marker)
    start = reboot.index("systemctl start containerd.service", watchdog)
    legacy_check = reboot.index("--legacy-adoption", start)
    adoption = reboot.index("adopt-provenance", legacy_check)
    final = reboot.index("FINAL_GREEN=0", adoption)
    restore = reboot.index("restart-policy restore", final)
    complete = reboot.index('"$WATCHDOG" complete', restore)

    assert schema < policy_fence < marker < auth < watchdog < start
    assert start < legacy_check < adoption < final < restore < complete
    assert "--restart-fenced" in reboot[adoption:final]


def test_clean_shutdown_hook_and_post_start_metadata_gates_are_fail_closed() -> None:
    bootstrap = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    reboot = (REPO / "scripts/gcp/reboot-runtime.sh").read_text(encoding="utf-8")

    dropin = bootstrap.index(
        "ExecStop=/usr/local/sbin/omega-runtime-contract restart-policy prepare"
    )
    daemon_reload = bootstrap.index("systemctl daemon-reload", dropin)
    bootstrap_green = bootstrap.index('if [[ "${runtime_green}" != "1" ]]')
    bootstrap_metadata = bootstrap.index(
        "/usr/local/sbin/omega-metadata-firewall verify-container",
        bootstrap_green,
    )
    bootstrap_complete = bootstrap.index(
        '"${WATCHDOG}" complete "$$" "${OPERATION_MARKER}"', bootstrap_metadata
    )
    assert dropin < daemon_reload
    assert bootstrap_green < bootstrap_metadata < bootstrap_complete

    verify_fenced = reboot.index("restart-policy verify-fenced")
    marker = reboot.index('/usr/bin/python3 -I - "$OPERATION_MARKER"', verify_fenced)
    arm = reboot.index('"$WATCHDOG" arm "$$" "$OPERATION_MARKER"', marker)
    docker_start = reboot.index(
        "systemctl start containerd.service docker.socket docker.service", arm
    )
    prestart = reboot.index('"$RUNTIME_CONTRACT" prestart', docker_start)
    dependency_start = reboot.index(
        '"${COMPOSE[@]}" start "${DEPENDENCY_SERVICES[@]}"', prestart
    )
    writer_fence = reboot.index("writer-fence", dependency_start)
    application_start = reboot.index(
        '"${COMPOSE[@]}" start -- "${application_services[@]}"', writer_fence
    )
    final_green = reboot.index('if [[ "$FINAL_GREEN" != "1" ]]', application_start)
    metadata = reboot.index('"$METADATA_FIREWALL" verify-container', final_green)
    restore = reboot.index("restart-policy restore", metadata)
    complete = reboot.index('"$WATCHDOG" complete "$$" "$OPERATION_MARKER"', restore)

    assert verify_fenced < marker < arm < docker_start < prestart
    assert prestart < dependency_start < writer_fence < application_start
    assert application_start < final_green < metadata < restore < complete
    assert '"${COMPOSE[@]}" stop --timeout' not in reboot
    assert reboot.count('--restart-fenced-app-root "$APP_ROOT"') == 3


def test_watchdog_cannot_self_disarm_before_owner_acknowledges_fsync():
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    monitor = source[
        source.index("monitor() {") : source.index("require_transient_unit_absent() {")
    ]
    arm = source[source.index("arm() (") : source.index("transition() (")]
    transition = source[
        source.index("transition() (") : source.index("stop_monitor_locked() {")
    ]
    emergency = source[
        source.index("emergency_fence() (") : source.index("process_identity() {")
    ]
    complete = source[source.index("complete() (") : source.index("fence_external() (")]

    assert "load_state_locked" in monitor
    assert "process_is_same" in monitor
    assert "now >= STATE_DEADLINE" in monitor
    assert "kill_owner_unit" in monitor
    assert "monitor_unit_exact" in arm
    assert arm.index("systemd-run") < arm.index("write_state_locked owner")
    assert 'local timeout_seconds="$WATCHED_STARTTIME"' in arm
    assert "write_state_locked hold 0 0" in transition
    assert 'write_state_locked owner "$WATCHED_PID"' in transition
    assert "candidate < deadline" in transition
    assert transition.count("monitor_unit_exact") >= 2
    assert "record_fence_evidence" in emergency
    assert emergency.index("revoke_runtime_authorization") < emergency.index(
        "systemctl_bounded stop docker.socket"
    )
    assert "fence_until_safe() {" in source
    assert "until emergency_fence" in source
    assert 'exec 9>>"$LOCK_FILE"' in source
    assert "pgrep" not in source
    assert "moby-fence" in emergency
    assert source.count(' docker "$@"') == 1
    assert "open_watchdog_lock" in complete
    assert "flock -x 9" not in complete
    lock = source[
        source.index("open_watchdog_lock() {") : source.index(
            "prepare_runtime_dir || {"
        )
    ]
    descriptor_open = lock.index('exec 9>>"$LOCK_FILE"')
    precheck = lock.index('/usr/bin/python3 -I - "$LOCK_FILE"', descriptor_open)
    acquire = lock.index("flock -x 9", precheck)
    postcheck = lock.index('/usr/bin/python3 -I - "$LOCK_FILE"', acquire)
    assert descriptor_open < precheck < acquire < postcheck
    assert "stop_monitor_locked" in complete
    assert "remove_marker_locked" in complete
    assert "write_state_locked complete" in complete
    assert complete.count("now >= STATE_DEADLINE") >= 4
    assert "fence_prestart" in source
    assert "arm-hold" not in source
    assert "disarm() " not in source


def test_watchdog_handoff_preserves_the_shorter_absolute_deadline(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    transition = source[
        source.index("transition() (") : source.index("stop_monitor_locked() {")
    ]
    harness = f"""set -Eeuo pipefail
{transition}
WATCHED_PID=42
WATCHED_STARTTIME=99
FENCE_SENTINEL="$1/fenced"
LOCK_PATH="$1/lock"
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
valid_timeout() {{ :; }}
marker_is_valid() {{ :; }}
marker_matches_state() {{ :; }}
load_state_locked() {{
  STATE_MODE=owner; STATE_PID=42; STATE_STARTTIME=99
  STATE_DEADLINE=1000
}}
monitor_unit_exact() {{ :; }}
process_identity() {{ printf 'S\t99\n'; }}
owner_unit_contract() {{ :; }}
owner_unit_matches() {{ :; }}
monotonic_seconds() {{ printf '100\n'; }}
write_state_locked() {{ printf '%s\n' "$*"; }}
record_fence_evidence() {{ :; }}
fence_until_safe() {{ printf 'unexpected-fence\n'; }}
transition hold
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-test", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "hold 0 0 199\n"


@pytest.mark.parametrize(
    "self_cgroup",
    (
        "/system.slice/omega-gcp-operation.service",
        "/system.slice/omega-gcp-operation.service/nested-worker",
    ),
)
def test_external_fence_rejects_execution_inside_the_owner_cgroup(
    self_cgroup: str,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    external = source[
        source.index("fence_external() (") : source.index('case "$ACTION"')
    ]
    harness = f"""set -Eeuo pipefail
{external}
OWNER_UNIT=omega-gcp-operation.service
SELF_CGROUP={self_cgroup!r}
awk() {{ printf '%s\n' "$SELF_CGROUP"; }}
fence_until_safe() {{ printf 'hard-fenced\n'; }}
open_watchdog_lock() {{ printf 'unsafe-lock-open\n'; return 1; }}
fence_external
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-self-cgroup"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 15
    assert result.stdout == "hard-fenced\n"


def test_watchdog_postcheck_failure_releases_stale_descriptor_lock(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    lock = source[
        source.index("open_watchdog_lock() {") : source.index(
            "\n\nprepare_runtime_dir || {"
        )
    ].replace(
        '/usr/bin/python3 -I - "$LOCK_FILE" "${EUID:-$(id -u)}"',
        "lock_check",
    )
    harness = f"""set -Eeuo pipefail
{lock}
LOCK_FILE="$1/watchdog.lock"
CHECKS=0
PYTHON={sys.executable!r}
prepare_runtime_dir() {{ :; }}
flock() {{
  local operation="$1" descriptor="$2"
  "$PYTHON" -c 'import fcntl,sys; fcntl.flock(int(sys.argv[2]), fcntl.LOCK_UN if sys.argv[1] == "-u" else fcntl.LOCK_EX)' "$operation" "$descriptor"
}}
lock_check() {{
  cat >/dev/null
  CHECKS=$((CHECKS + 1))
  [[ "$CHECKS" == "1" ]]
}}
rc=0
open_watchdog_lock || rc=$?
[[ "$rc" == "1" ]]
[[ ! -e /dev/fd/9 ]]
"$PYTHON" -c 'import fcntl,sys; f=open(sys.argv[1], "a"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)' "$LOCK_FILE"
printf 'competitor-acquired\n'
"""
    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-lock-test", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "competitor-acquired\n"
    assert not (tmp_path / "watchdog-state.json").exists()
    assert not (tmp_path / "operation-state.json").exists()


def test_monitor_lock_failure_enters_fence_without_state_or_evidence_mutation(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    monitor = source[
        source.index("monitor() {") : source.index("require_transient_unit_absent() {")
    ]
    harness = f"""set -Eeuo pipefail
{monitor}
open_watchdog_lock() {{ return 1; }}
write_state_locked() {{ : > "$1/state-mutated"; }}
record_fence_evidence() {{ : > "$1/evidence-mutated"; }}
fence_until_safe() {{ printf 'hard-fence-retried\n'; }}
monitor "$1"
"""
    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-monitor-test", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "hard-fence-retried\n"
    assert not (tmp_path / "state-mutated").exists()
    assert not (tmp_path / "evidence-mutated").exists()


def test_restarted_watchdog_fences_missing_state_and_the_complete_owner(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    monitor = source[
        source.index("monitor() {") : source.index("require_transient_unit_absent() {")
    ]
    harness = f"""set -Eeuo pipefail
{monitor}
LOCK_PATH="$1/lock"
EVIDENCE_PATH="$1/evidence"
OPERATION_MARKER="$1/operation-state.json"
FENCE_SENTINEL="$1/fenced"
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
load_state_locked() {{ return 1; }}
monotonic_seconds() {{ printf '1\n'; }}
write_state_locked() {{ :; }}
record_fence_evidence() {{ : > "$EVIDENCE_PATH"; }}
kill_owner_unit() {{ printf 'owner-killed\n'; }}
emergency_fence() {{ return 99; }}
fence_until_safe() {{ printf 'runtime-fenced\n'; }}
monitor
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-test", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "evidence").exists()
    assert result.stdout == "owner-killed\nruntime-fenced\n"


def test_monitor_never_accepts_terminal_complete_at_the_deadline(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    monitor = source[
        source.index("monitor() {") : source.index("require_transient_unit_absent() {")
    ]
    harness = f"""set -Eeuo pipefail
{monitor}
LOCK_PATH="$1/lock"
EVIDENCE_PATH="$1/evidence"
OPERATION_MARKER="$1/operation-state.json"
FENCE_SENTINEL="$1/fenced"
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
load_state_locked() {{
  STATE_MODE=complete; STATE_PID=42; STATE_STARTTIME=99
  STATE_DEADLINE=100
}}
monotonic_seconds() {{ printf '100\n'; }}
write_state_locked() {{ printf 'fencing-state\n'; }}
record_fence_evidence() {{ : > "$EVIDENCE_PATH"; }}
kill_owner_unit() {{ printf 'owner-killed\n'; }}
emergency_fence() {{ return 99; }}
fence_until_safe() {{ printf 'runtime-fenced\n'; }}
monitor
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-terminal-boundary", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "evidence").exists()
    assert result.stdout == "owner-killed\nruntime-fenced\n"


def test_watchdog_adopt_does_not_extend_the_persisted_hold_deadline(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    transition = source[
        source.index("transition() (") : source.index("stop_monitor_locked() {")
    ]
    harness = f"""set -Eeuo pipefail
{transition}
WATCHED_PID=42
WATCHED_STARTTIME=99
FENCE_SENTINEL="$1/fenced"
LOCK_PATH="$1/lock"
EVIDENCE_PATH="$1/evidence"
STATE_LOG="$1/state-log"
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
valid_timeout() {{ :; }}
marker_is_valid() {{ :; }}
marker_matches_state() {{ :; }}
load_state_locked() {{
  STATE_MODE=hold; STATE_PID=0; STATE_STARTTIME=0
  STATE_DEADLINE=150
}}
monitor_unit_exact() {{ :; }}
process_identity() {{ printf 'S\t99\n'; }}
owner_unit_contract() {{ :; }}
owner_unit_matches() {{ :; }}
monotonic_seconds() {{ printf '100\n'; }}
write_state_locked() {{ printf '%s\n' "$*"; }}
record_fence_evidence() {{ :; }}
fence_until_safe() {{ printf 'unexpected-fence\n'; }}
transition owner
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-adopt-test", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "owner 42 99 150\n"


def test_watchdog_complete_rejects_the_exact_deadline_boundary(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    complete = source[source.index("complete() (") : source.index("fence_external() (")]
    harness = f"""set -Eeuo pipefail
{complete}
WATCHED_PID=42
FENCE_SENTINEL="$1/fenced"
LOCK_PATH="$1/lock"
EVIDENCE_PATH="$1/evidence"
STATE_LOG="$1/state-log"
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
marker_is_valid() {{ :; }}
marker_matches_state() {{ :; }}
load_state_locked() {{
  STATE_MODE=owner; STATE_PID=42; STATE_STARTTIME=99
  STATE_DEADLINE=100
}}
monitor_unit_exact() {{ :; }}
process_identity() {{ printf 'S\t99\n'; }}
owner_unit_contract() {{ :; }}
owner_unit_matches() {{ :; }}
monotonic_seconds() {{ printf '100\n'; }}
remove_marker_locked() {{ printf 'marker-removed\n'; }}
write_state_locked() {{ printf '%s\n' "$*" > "$STATE_LOG"; }}
record_fence_evidence() {{ : > "$EVIDENCE_PATH"; }}
stop_monitor_locked() {{ printf 'monitor-stopped\n'; }}
watchdog_state() {{ printf 'state-deleted\n'; }}
fence_until_safe() {{ printf 'runtime-fenced\n'; }}
set +e
complete
rc=$?
set -e
printf 'rc=%s\n' "$rc"
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-deadline", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "marker-removed" not in result.stdout
    assert "monitor-stopped" not in result.stdout
    assert (tmp_path / "state-log").read_text(encoding="utf-8") == "fencing 0 0 100\n"
    assert (tmp_path / "evidence").exists()
    assert result.stdout == "runtime-fenced\nrc=15\n"


def test_watchdog_complete_rechecks_deadline_after_terminal_fsync(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/operation-watchdog.sh").read_text(encoding="utf-8")
    complete = source[source.index("complete() (") : source.index("fence_external() (")]
    harness = f"""set -Eeuo pipefail
{complete}
WATCHED_PID=42
FENCE_SENTINEL="$1/fenced"
LOCK_PATH="$1/lock"
EVIDENCE_PATH="$1/evidence"
STATE_LOG="$1/state-log"
CLOCK="$1/clock"
printf '0\n' > "$CLOCK"
open_watchdog_lock() {{ exec 9>"$LOCK_PATH"; }}
flock() {{ :; }}
marker_is_valid() {{ :; }}
marker_matches_state() {{ :; }}
load_state_locked() {{
  STATE_MODE=owner; STATE_PID=42; STATE_STARTTIME=99
  STATE_DEADLINE=100
}}
monitor_unit_exact() {{ :; }}
process_identity() {{ printf 'S\t99\n'; }}
owner_unit_contract() {{ :; }}
owner_unit_matches() {{ :; }}
monotonic_seconds() {{
  local count
  count="$(<"$CLOCK")"
  count=$((count + 1))
  printf '%s\n' "$count" > "$CLOCK"
  if (( count < 4 )); then printf '99\n'; else printf '100\n'; fi
}}
remove_marker_locked() {{ printf 'marker-removed\n'; }}
write_state_locked() {{ printf '%s\n' "$*" >> "$STATE_LOG"; }}
record_fence_evidence() {{ : > "$EVIDENCE_PATH"; }}
stop_monitor_locked() {{ printf 'monitor-stopped\n'; }}
watchdog_state() {{ printf 'state-deleted\n'; }}
fence_until_safe() {{ printf 'runtime-fenced\n'; }}
set +e
complete
rc=$?
set -e
printf 'rc=%s\n' "$rc"
"""

    result = subprocess.run(
        ["bash", "-c", harness, "watchdog-post-fsync-deadline", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "marker-removed\nruntime-fenced\nrc=15\n"
    assert (tmp_path / "state-log").read_text(encoding="utf-8") == (
        "complete 42 99 100\nfencing 0 0 100\n"
    )
    assert (tmp_path / "evidence").exists()


def test_adoption_finalizer_keeps_fail_closed_guard_through_post_complete() -> None:
    source = (REPO / "scripts/gcp/finalize-startup-adoption.sh").read_text(
        encoding="utf-8"
    )

    adopt = source.index('"$WATCHDOG" adopt-rebind "$$" "$OPERATION_MARKER" 900')
    trap = source.index("trap adoption_fail_closed EXIT", adopt)
    pre_complete_runtime = source.index("verify_exact_runtime 1", trap)
    complete = source.index(
        '"$WATCHDOG" complete "$$" "$OPERATION_MARKER"', pre_complete_runtime
    )
    post_gate = source.index(
        "/usr/local/sbin/omega-operation-gate >/dev/null", complete
    )
    post_runtime = source.index("verify_exact_runtime 0", post_gate)
    disarm_trap = source.index("ADOPTED=0", post_runtime)

    assert adopt < trap < pre_complete_runtime < complete
    assert complete < post_gate < post_runtime < disarm_trap
    assert '"$WATCHDOG" fence-failure "$$" "$OPERATION_MARKER"' in source
    idempotent = source[
        source.index('if [[ ! -e "$OPERATION_MARKER"') : source.index(
            '/usr/bin/python3 -I - "$OPERATION_MARKER"'
        )
    ]
    assert "idempotent_fail_closed" in idempotent
    assert "verify_exact_runtime 0" in idempotent
    assert (
        'CANDIDATE_RUNTIME_CONTRACT="/usr/local/sbin/omega-runtime-contract"' in source
    )
    assert "verify_candidate_runtime_contract" in source
    exact = source[
        source.index("verify_exact_runtime() {") : source.index(
            "idempotent_fail_closed() {"
        )
    ]
    assert '"$CANDIDATE_RUNTIME_CONTRACT" live-state' in exact
    assert "print-helpers" not in exact

    completion_start = source.index(
        '/usr/bin/python3 -I - "$COMPLETION_MARKER" "$COMPLETION_HISTORY" "$COMPLETION_RECEIPT"',
        source.index("verify_exact_runtime 1 ||"),
    )
    completion = source[
        completion_start : source.index("verify_completion || fail", completion_start)
    ]
    assert 'COMPLETION_HISTORY="${SHARED_ROOT}/startup-adoption-history"' in source
    assert 'COMPLETION_RECEIPT="${COMPLETION_HISTORY}/${ADOPTION_ID}.json"' in source
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL" in completion
    assert "json.loads(read_owned(receipt)" in completion
    assert "while view:" in completion
    assert "os.fsync(descriptor)" in completion
    assert "os.replace(temporary_link, current)" in completion
    assert "current.resolve(strict=True) != old_receipt" in completion
    assert "json.loads(read_owned(old_receipt)" in completion
    assert "append-only receipt/current publication failed" in source


def test_adoption_completion_history_supports_two_consecutive_adoptions(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/finalize-startup-adoption.sh").read_text(
        encoding="utf-8"
    )
    start = source.index(
        'fail "completion record" "append-only receipt/current publication failed" 16'
    )
    start = source.index("\n", start) + 1
    end = source.index("\nPY\nverify_completion", start)
    publisher = source[start:end]
    # Production requires root ownership. Exercise the exact publisher under
    # the test process owner without weakening that production assertion.
    publisher = publisher.replace(".st_uid != 0", ".st_uid != os.getuid()")
    publisher = publisher.replace(".st_gid != 0", ".st_gid != os.getgid()")

    current = tmp_path / "startup-adoption-complete.json"
    history = tmp_path / "startup-adoption-history"
    adoption_ids = (
        "20260812T010101.000000001Z-startup-adoption-1111111111111111",
        "20260812T020202.000000002Z-startup-adoption-2222222222222222",
    )
    identities = {
        adoption_ids[0]: ("a" * 40, "b" * 40, "c" * 64),
        adoption_ids[1]: ("d" * 40, "e" * 40, "f" * 64),
    }

    def publish(adoption_id: str) -> subprocess.CompletedProcess[str]:
        deploy_ref, helper_ref, startup_sha = identities[adoption_id]
        return subprocess.run(
            [
                sys.executable,
                "-c",
                publisher,
                str(current),
                str(history),
                str(history / f"{adoption_id}.json"),
                adoption_id,
                deploy_ref,
                helper_ref,
                startup_sha,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    first = publish(adoption_ids[0])
    assert first.returncode == 0, first.stderr
    first_receipt = history / f"{adoption_ids[0]}.json"
    first_bytes = first_receipt.read_bytes()
    first_stat = first_receipt.stat()

    second = publish(adoption_ids[1])
    assert second.returncode == 0, second.stderr
    second_receipt = history / f"{adoption_ids[1]}.json"
    assert first_receipt.read_bytes() == first_bytes
    assert first_receipt.stat().st_ino == first_stat.st_ino
    assert second_receipt.is_file()
    assert current.is_symlink()
    assert os.readlink(current) == (f"startup-adoption-history/{adoption_ids[1]}.json")
    assert current.resolve(strict=True) == second_receipt

    repeated = publish(adoption_ids[1])
    assert repeated.returncode == 0, repeated.stderr
    assert first_receipt.read_bytes() == first_bytes
    assert sorted(path.name for path in history.iterdir()) == [
        f"{adoption_ids[0]}.json",
        f"{adoption_ids[1]}.json",
    ]

    context_start = source.index("completion_context() {")
    context_start = source.index("<<'PY'\n", context_start) + len("<<'PY'\n")
    context_end = source.index("\nPY\n}", context_start)
    classifier = source[context_start:context_end]
    classifier = classifier.replace(".st_uid != 0", ".st_uid != os.getuid()")
    classifier = classifier.replace(".st_gid != 0", ".st_gid != os.getgid()")
    first_deploy, first_helper, first_startup = identities[adoption_ids[0]]
    stale_retry = subprocess.run(
        [
            sys.executable,
            "-c",
            classifier,
            str(current),
            str(history),
            str(first_receipt),
            adoption_ids[0],
            first_deploy,
            first_helper,
            first_startup,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    second_deploy, second_helper, second_startup = identities[adoption_ids[1]]
    assert stale_retry.returncode == 0, stale_retry.stderr
    assert stale_retry.stdout.strip() == (
        f"superseded\t{second_deploy}\t{second_helper}\t{second_startup}"
    )
    idempotent = source[
        source.index('if [[ ! -e "$OPERATION_MARKER"') : source.index(
            '/usr/bin/python3 -I - "$OPERATION_MARKER"'
        )
    ]
    assert 'if [[ "$COMPLETION_STATUS" == "superseded" ]]' in idempotent
    assert '"superseded":true' in idempotent


def test_invalid_idempotent_completion_invokes_hard_fence_with_expected_rc(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/finalize-startup-adoption.sh").read_text(
        encoding="utf-8"
    )
    function = source[
        source.index("idempotent_fail_closed() {") : source.index(
            '\nif [[ ! -e "$OPERATION_MARKER"'
        )
    ]
    watchdog = tmp_path / "watchdog"
    watchdog.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$OMEGA_TEST_FENCE_LOG"\n',
        encoding="utf-8",
    )
    watchdog.chmod(0o700)
    fence_log = tmp_path / "fence.log"
    harness = f"""set -Eeuo pipefail
{function}
SAFE_IO=/test/safe-io
WATCHDOG={str(watchdog)!r}
OPERATION_MARKER={str(tmp_path / "operation-state.json")!r}
fail() {{ printf 'fail:%s:%s:%s\n' "$1" "$2" "$3"; return "$3"; }}
set +e
idempotent_fail_closed completion corrupt 13
rc=$?
set -e
printf 'rc=%s\n' "$rc"
"""
    result = subprocess.run(
        ["bash", "-c", harness, "idempotent-fence-test"],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "OMEGA_TEST_FENCE_LOG": str(fence_log)},
    )

    assert result.returncode == 0, result.stderr
    assert fence_log.read_text(encoding="utf-8") == (
        f"fence-failure 1 {tmp_path / 'operation-state.json'}\n"
    )
    assert result.stdout == "fail:completion:corrupt; runtime hard-fenced:13\nrc=13\n"


def test_candidate_runtime_contract_is_sealed_and_reverified_before_execution() -> None:
    bootstrap = (REPO / "scripts/gcp/bootstrap-runtime.sh").read_text(encoding="utf-8")
    finalizer = (REPO / "scripts/gcp/finalize-startup-adoption.sh").read_text(
        encoding="utf-8"
    )

    install = bootstrap.index(
        'mv -Tf "${RUNTIME_CONTRACT_TMP}" "${CANONICAL_RUNTIME_CONTRACT}"'
    )
    record = bootstrap.index(
        "/usr/bin/python3 -I - /var/lib/omega-gcp/candidate-runtime-contract.json",
        install,
    )
    assert install < record
    assert (
        '"${SOURCE_SHA}" "${CONTROLLER_REF}" "${STARTUP_CONTRACT_SHA256}"'
        in bootstrap[record:]
    )
    sealed = bootstrap[record : bootstrap.index("# A SIGKILL", record)]
    assert "install -d -m 0700 /var/lib/omega-gcp" in bootstrap[:record]
    assert '"schema_version": 2' in sealed
    assert '"deploy_ref": sys.argv[2]' in sealed
    assert '"helper_ref": sys.argv[3]' in sealed
    assert '"startup_contract_sha256": sys.argv[4]' in sealed
    assert '"helper_sha256": {' in sealed
    assert "hashlib.sha256(read_helper(helper_path)).hexdigest()" in sealed
    for helper in (
        "bootstrap_runtime",
        "safe_io",
        "metadata_firewall",
        "operation_gate",
        "operation_watchdog",
        "reboot_runtime",
        "runtime_contract",
    ):
        assert f'"{helper}"' in sealed
    assert "os.fchmod(descriptor, 0o600)" in sealed
    assert "os.fsync(stream.fileno())" in sealed
    assert "os.replace(temporary, path)" in sealed
    assert "os.fsync(directory)" in sealed

    verifier = finalizer[
        finalizer.index("verify_candidate_runtime_contract() {") : finalizer.index(
            "verify_completion() {"
        )
    ]
    assert 'payload.get("schema_version") != 2' in verifier
    assert 'payload.get("deploy_ref") != sys.argv[4]' in verifier
    assert 'payload.get("helper_ref") != sys.argv[3]' in verifier
    assert 'payload.get("startup_contract_sha256") != sys.argv[5]' in verifier
    assert "hashlib.sha256(helper_raw).hexdigest()" in verifier
    assert "set(helper_sha256) != expected_helpers" in verifier
    assert '"bootstrap_runtime"' in verifier
    assert '"runtime_contract"' in verifier
    assert "object_pairs_hook=no_duplicates" in verifier
    exact = finalizer[
        finalizer.index("verify_exact_runtime() {") : finalizer.index(
            "idempotent_fail_closed() {"
        )
    ]
    assert 'verify_candidate_runtime_contract "$expected_helper_ref"' in exact
    assert '"$expected_deploy_ref"' in exact
    assert '"$expected_startup_sha" || return 1' in exact
    assert '"$CANDIDATE_RUNTIME_CONTRACT" live-state' in exact
    assert "print-helpers" not in exact


def test_candidate_runtime_validator_accepts_only_the_exact_v2_helper_map(
    tmp_path: Path,
) -> None:
    source = (REPO / "scripts/gcp/finalize-startup-adoption.sh").read_text(
        encoding="utf-8"
    )
    function = source[
        source.index("verify_candidate_runtime_contract() {") : source.index(
            "completion_context() {"
        )
    ]
    script_start = function.index("<<'PY'\n") + len("<<'PY'\n")
    script_end = function.index("\nPY", script_start)
    validator = function[script_start:script_end]
    validator = validator.replace(".st_uid != 0", ".st_uid != os.getuid()")
    validator = validator.replace(".st_gid != 0", ".st_gid != os.getgid()")

    record_dir = tmp_path / "record"
    record_dir.mkdir(mode=0o700)
    record_dir.chmod(0o700)
    record = record_dir / "candidate-runtime-contract.json"
    helper = tmp_path / "runtime_contract.py"
    helper.write_text("#!/usr/bin/python3\nprint('sealed helper')\n", encoding="utf-8")
    helper.chmod(0o755)
    deploy_ref = "a" * 40
    helper_ref = "b" * 40
    startup_sha = "c" * 64
    helper_names = {
        "bootstrap_runtime",
        "safe_io",
        "metadata_firewall",
        "operation_gate",
        "operation_watchdog",
        "reboot_runtime",
        "runtime_contract",
    }
    helper_hashes = {name: "d" * 64 for name in helper_names}
    helper_hashes["runtime_contract"] = hashlib.sha256(helper.read_bytes()).hexdigest()
    valid = {
        "schema_version": 2,
        "deploy_ref": deploy_ref,
        "helper_ref": helper_ref,
        "startup_contract_sha256": startup_sha,
        "helper_sha256": helper_hashes,
    }

    def execute(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        record.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        record.chmod(0o600)
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                validator,
                str(record),
                str(helper),
                helper_ref,
                deploy_ref,
                startup_sha,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    assert execute(valid).returncode == 0

    stale_v1 = json.loads(json.dumps(valid))
    stale_v1["schema_version"] = 1
    missing_helper = json.loads(json.dumps(valid))
    del missing_helper["helper_sha256"]["safe_io"]
    extra_helper = json.loads(json.dumps(valid))
    extra_helper["helper_sha256"]["unexpected"] = "e" * 64
    mismatched_helper = json.loads(json.dumps(valid))
    mismatched_helper["helper_sha256"]["runtime_contract"] = "f" * 64

    for invalid in (stale_v1, missing_helper, extra_helper, mismatched_helper):
        result = execute(invalid)
        assert result.returncode != 0, invalid


def test_compute_protects_disks_and_enforces_rendered_metadata_budget() -> None:
    compute = (GCP_TF / "compute.tf").read_text(encoding="utf-8")
    template = (GCP_TF / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    paths = {
        "bootstrap_runtime_base64": REPO / "scripts/gcp/bootstrap-runtime.sh",
        "metadata_firewall_base64": REPO / "scripts/gcp/metadata-firewall.sh",
        "operation_guard_base64": GCP_TF / "templates/omega-operation-gate",
        "operation_watchdog_base64": REPO / "scripts/gcp/operation-watchdog.sh",
        "reboot_runtime_base64": REPO / "scripts/gcp/reboot-runtime.sh",
        "runtime_contract_base64": REPO / "scripts/gcp/runtime_contract.py",
        "safe_io_base64": REPO / "scripts/gcp/safe_io.py",
    }
    rendered = template.replace(
        "${startup_config_base64}", base64.b64encode(b"x" * 8192).decode()
    )
    for name, path in paths.items():
        rendered = rendered.replace(
            "${" + name + "}",
            base64.b64encode(gzip.compress(path.read_bytes(), mtime=0)).decode(),
        )

    assert "deletion_protection = false" in compute
    assert "auto_delete = true" in compute
    assert compute.count("prevent_destroy = true") == 2
    assert "length(base64encode(local.startup_script)) <= 349524" in compute
    assert len(rendered.encode()) <= 262144 - 4096


def test_metadata_firewall_replaces_a_restrictive_lookalike_rule(
    tmp_path: Path,
) -> None:
    firewall = (REPO / "scripts/gcp/metadata-firewall.sh").read_text(encoding="utf-8")
    functions = firewall[
        firewall.index("ensure_family() {") : firewall.index("\nenforce() {")
    ]
    state = tmp_path / "rules"
    fake = tmp_path / "iptables"
    destination = "169.254.169.254/32"
    restrictive = (
        "-A DOCKER-USER -s 172.18.0.0/16 "
        f"-d {destination} -m comment --comment "
        "omega-deny-container-gcp-metadata -j REJECT\n"
    )
    state.write_text(restrictive, encoding="utf-8")
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
case " $* " in
  *" -N DOCKER-USER "*) exit 0 ;;
  *" -S FORWARD "*) printf '%s\\n' '-A FORWARD -j DOCKER-USER' ;;
  *" -S DOCKER-USER "*) cat "$OMEGA_TEST_RULE_STATE" ;;
  *" -I DOCKER-USER 1 "*)
    printf '%s\\n' "-A DOCKER-USER -d $OMEGA_TEST_DESTINATION -m comment --comment omega-deny-container-gcp-metadata -j REJECT --reject-with icmp-port-unreachable" > "$OMEGA_TEST_RULE_STATE"
    ;;
  *) exit 99 ;;
esac
""",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    harness = f"""set -Eeuo pipefail
COMMENT=omega-deny-container-gcp-metadata
{functions}
    ensure_family "$1" "$2" icmp-port-unreachable
    verify_family "$1" "$2" icmp-port-unreachable
"""
    env = {
        **os.environ,
        "OMEGA_TEST_RULE_STATE": str(state),
        "OMEGA_TEST_DESTINATION": destination,
    }

    result = subprocess.run(
        ["bash", "-c", harness, "firewall-test", str(fake), destination],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert state.read_text(encoding="utf-8") == (
        f"-A DOCKER-USER -d {destination} -m comment --comment "
        "omega-deny-container-gcp-metadata -j REJECT "
        "--reject-with icmp-port-unreachable\n"
    )
