#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

expected_project="${1:-}"
expected_instance="${2:-}"
expected_zone="${3:-}"
expected_sa="${4:-}"

if [[ -z "${expected_project}" || -z "${expected_instance}" || -z "${expected_zone}" || -z "${expected_sa}" ]]; then
  echo "ERROR: verify-host-identity.sh requires <project-id> <instance-name> <zone> <service-account-email>." >&2
  echo "       Refusing to run: the canonical target must be declared explicitly." >&2
  exit 2
fi
if [[ ! "${expected_project}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "ERROR: expected project id is not a valid GCP project id." >&2
  exit 3
fi
if [[ ! "${expected_instance}" =~ ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "ERROR: expected instance name is not a valid Compute instance name." >&2
  exit 3
fi
if [[ ! "${expected_zone}" =~ ^[a-z]+-[a-z0-9]+-[a-z]$ ]]; then
  echo "ERROR: expected zone is not a valid Compute zone (e.g. us-central1-a)." >&2
  exit 3
fi
if [[ ! "${expected_sa}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$ ]]; then
  echo "ERROR: expected service-account email is not a valid GCP service account." >&2
  exit 3
fi

metadata_get() {
  curl --fail --silent --show-error --max-time 5 \
    --noproxy '*' \
    -H 'Metadata-Flavor: Google' \
    "http://metadata.google.internal/computeMetadata/v1/${1}"
}

if ! live_project="$(metadata_get project/project-id)"; then
  echo "ERROR: GCP project identity is unavailable from instance metadata (is this a GCP VM?)." >&2
  exit 4
fi
if ! live_instance="$(metadata_get instance/name)"; then
  echo "ERROR: GCP instance name is unavailable from instance metadata." >&2
  exit 4
fi
if ! live_zone_path="$(metadata_get instance/zone)"; then
  echo "ERROR: GCP instance zone is unavailable from instance metadata." >&2
  exit 4
fi
if ! live_sa="$(metadata_get instance/service-accounts/default/email)"; then
  echo "ERROR: GCP default service account is unavailable from instance metadata." >&2
  exit 4
fi

live_zone="${live_zone_path##*/}"

fail=0
if [[ "${live_project}" != "${expected_project}" ]]; then
  echo "ERROR: project mismatch: live='${live_project}' expected='${expected_project}'" >&2
  fail=1
fi
if [[ "${live_instance}" != "${expected_instance}" ]]; then
  echo "ERROR: instance mismatch: live='${live_instance}' expected='${expected_instance}'" >&2
  fail=1
fi
if [[ "${live_zone}" != "${expected_zone}" ]]; then
  echo "ERROR: zone mismatch: live='${live_zone}' expected='${expected_zone}'" >&2
  fail=1
fi
if [[ "${live_sa}" != "${expected_sa}" ]]; then
  echo "ERROR: service-account mismatch: live='${live_sa}' expected='${expected_sa}'" >&2
  fail=1
fi

if [[ "${fail}" -ne 0 ]]; then
  printf 'HOST_IDENTITY\tFAIL\tthis VM is NOT the declared canonical release target\n' >&2
  exit 1
fi

printf 'HOST_IDENTITY\tPASS\tproject=%s\tinstance=%s\tzone=%s\tsa=%s\ttoken_read=false\tsecret_values_read=false\n' \
  "${live_project}" "${live_instance}" "${live_zone}" "${live_sa}"
