#!/bin/bash -p
# Verify effective VM Secret Manager permissions without reading secret values.
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE \
  PYTHONWARNINGS PYTHONBREAKPOINT PYTHONSAFEPATH
unset SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE SSLKEYLOGFILE

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: verify-secret-access.sh must run through sudo" >&2
  exit 10
fi
PROJECT_ID="${1:-}"
ENVIRONMENT="${2:-}"
STAGE="${3:-}"
if [[ ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ || \
      ! "$ENVIRONMENT" =~ ^[a-z][a-z0-9-]{0,29}$ || \
      ( "$STAGE" != "grants" && "$STAGE" != "revoke" ) ]]; then
  echo "ERROR: invalid effective-IAM verification contract" >&2
  exit 20
fi

/usr/bin/python3 -I - "$PROJECT_ID" "$ENVIRONMENT" "$STAGE" <<'PY'
import json
import os
import pathlib
import re
import ssl
import stat
import sys
import urllib.parse
import urllib.request

project, environment, stage = sys.argv[1:]

class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        del request, fp, code, message, headers, new_url
        raise RuntimeError("authenticated HTTP redirects are forbidden")

ca_bundle = pathlib.Path("/etc/ssl/certs/ca-certificates.crt")
ca_info = ca_bundle.lstat()
if (
    not stat.S_ISREG(ca_info.st_mode)
    or ca_bundle.is_symlink()
    or ca_info.st_uid != 0
    or stat.S_IMODE(ca_info.st_mode) & 0o022
    or ca_bundle.resolve(strict=True) != ca_bundle
):
    raise SystemExit("system CA bundle ownership/path is invalid")
tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
tls_context.check_hostname = True
tls_context.verify_mode = ssl.CERT_REQUIRED
tls_context.load_verify_locations(cafile=os.fspath(ca_bundle))
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    RejectRedirects(),
    urllib.request.HTTPSHandler(context=tls_context),
)
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with opener.open(token_request, timeout=10) as response:  # nosec B310
    token_payload = json.load(response)
token = token_payload.get("access_token")
if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
    raise SystemExit("invalid metadata identity token")

host_allow = (
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
)
allow = (*host_allow,
    "ghcr_pull_credentials",
)
managed = (
    "anthropic_api_key",
    "jwt_secret_key",
    "internal_api_key",
    "security_context_signing_key",
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "internal_api_key_console_to_console",
    "internal_api_key_console_to_refinement",
    "internal_api_key_console_to_vault",
    "internal_api_key_console_to_mcp_infra",
    "internal_api_key_mcp_infra_to_console",
    "internal_api_key_console_to_cartridge",
    "internal_api_key_airflow_to_cartridge",
    "internal_api_key_workspace_to_console",
    "internal_api_key_workspace_to_refinement",
    "internal_api_key_workspace_to_mcp_infra",
    "internal_api_key_airflow_to_mcp_infra",
    "internal_api_key_airflow_to_refinement",
    "internal_api_key_airflow_to_console",
    "internal_api_key_refinement_to_mcp_infra",
    "internal_api_key_replicon_to_console",
    "internal_api_key_replicon_to_mcp_infra",
    "internal_api_key_replicon_to_refinement",
    "internal_api_key_hubspot_to_console",
    "internal_api_key_hubspot_to_mcp_infra",
    "internal_api_key_hubspot_to_refinement",
    "internal_api_key_salesforce_to_console",
    "internal_api_key_banxico_to_console",
    "internal_api_key_inegi_to_console",
    "internal_api_key_sec_edgar_to_console",
    "internal_api_key_sap_hcm_to_console",
    "internal_api_key_sap_s4hana_to_console",
    "internal_api_key_sap_successfactors_to_console",
    "internal_api_key_mcp_infra_to_vault",
    "internal_api_key_cartridge_to_console",
    "internal_api_key_cartridge_to_refinement",
    "internal_api_key_workspace_to_vault",
    "internal_api_key_refinement_to_vault",
    "postgres_password",
    "field_encryption_key",
    "vault_encryption_key",
    "omega_console_password",
    "omega_refinement_password",
    "omega_vault_password",
    "omega_workspace_password",
    "omega_mcp_infra_password",
    "omega_refinement_gold_password",
    "omega_airflow_dag_password",
    "omega_airflow_meta_password",
    "omega_superset_meta_password",
    "omega_cartridge_sap_hcm_password",
    "omega_cartridge_sap_s4_password",
    "omega_cartridge_sap_sf_password",
    "omega_cartridge_replicon_password",
    "omega_cartridge_salesforce_password",
    "omega_cartridge_hubspot_password",
    "omega_cartridge_banxico_password",
    "omega_cartridge_inegi_password",
    "omega_cartridge_sec_edgar_password",
    "airflow_secret_key",
    "airflow_admin_password",
    "agent_runner_token",
    "superset_secret_key",
    "superset_admin_password",
    "superset_service_password",
    "smtp_password",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
)
if len(set(managed)) != 69 or not set(host_allow).issubset(managed):
    raise SystemExit("managed secret permission inventory is invalid")
forbidden = tuple(sorted(set(managed) - set(host_allow)))
if len(forbidden) != 64:
    raise SystemExit("forbidden secret permission inventory is invalid")

def permitted(name: str) -> bool:
    resource = urllib.parse.quote(
        f"projects/{project}/secrets/omega-{environment}-{name}", safe="/"
    )
    request = urllib.request.Request(
        f"https://secretmanager.googleapis.com/v1/{resource}:testIamPermissions",
        data=json.dumps(
            {"permissions": ["secretmanager.versions.access"]}
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with opener.open(request, timeout=30) as response:  # nosec B310
        payload = json.load(response)
    return payload.get("permissions") == ["secretmanager.versions.access"]

for secret in allow:
    if not permitted(secret):
        raise SystemExit(f"required resource access denied: {secret}")
forbidden_access = 0
if stage == "revoke":
    forbidden_access = sum(1 for secret in forbidden if permitted(secret))
    if forbidden_access:
        raise SystemExit(
            f"non-allowlisted resources remain effectively accessible: {forbidden_access}"
        )
print(
    json.dumps(
        {
            "status": "PASS",
            "stage": stage,
            "resource_grants": len(allow),
            "forbidden_resources_checked": len(forbidden) if stage == "revoke" else 0,
            "forbidden_access": forbidden_access,
            "canary_denied": stage == "revoke" and forbidden_access == 0,
            "secret_values_read": False,
        },
        sort_keys=True,
    )
)
PY
