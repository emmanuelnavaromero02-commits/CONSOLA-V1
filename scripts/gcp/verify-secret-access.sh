#!/usr/bin/env bash
# Verify effective VM Secret Manager permissions without reading secret values.
set -Eeuo pipefail
set +x
umask 077

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

python3 - "$PROJECT_ID" "$ENVIRONMENT" "$STAGE" <<'PY'
import json
import re
import sys
import urllib.parse
import urllib.request

project, environment, stage = sys.argv[1:]
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:  # nosec B310
    token_payload = json.load(response)
token = token_payload.get("access_token")
if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
    raise SystemExit("invalid metadata identity token")

allow = (
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
    "ghcr_pull_credentials",
)

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
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        payload = json.load(response)
    return payload.get("permissions") == ["secretmanager.versions.access"]

for secret in allow:
    if not permitted(secret):
        raise SystemExit(f"required resource access denied: {secret}")
if stage == "revoke" and permitted("anthropic_api_key"):
    raise SystemExit("non-allowlisted canary remains effectively accessible")
print(
    json.dumps(
        {
            "status": "PASS",
            "stage": stage,
            "resource_grants": len(allow),
            "canary_denied": stage == "revoke",
            "secret_values_read": False,
        },
        sort_keys=True,
    )
)
PY
