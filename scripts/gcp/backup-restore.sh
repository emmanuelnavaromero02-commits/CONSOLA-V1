#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${OMEGA_COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"

DB_MAIN_SERVICE="postgres";      DB_MAIN_PORT="5432"; DB_MAIN_NAME="modecissions"
DB_GOLD_SERVICE="postgres_gold"; DB_GOLD_PORT="5433"; DB_GOLD_NAME="modecissions_gold"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "$1" | awk '{print $1}'
  else
    echo "ERROR: no sha256 tool (sha256sum/shasum) available." >&2
    return 3
  fi
}

dc() { docker compose -f "${COMPOSE_FILE}" "$@"; }

usage() {
  echo "Usage: backup-restore.sh <backup|restore|verify> <backup-dir>" >&2
  exit 2
}

subcommand="${1:-}"
backup_dir="${2:-}"
[[ -n "${subcommand}" && -n "${backup_dir}" ]] || usage

manifest_path() { printf '%s/manifest.tsv' "$1"; }

do_backup() {
  local dir="$1"
  if [[ -e "${dir}" ]]; then
    echo "ERROR: backup dir ${dir} already exists; refusing to overwrite a restore-point." >&2
    exit 4
  fi
  mkdir -p "${dir}"
  chmod 700 "${dir}"

  local manifest; manifest="$(manifest_path "${dir}")"
  : > "${manifest}"
  printf '# omega day-two backup manifest\tref=%s\tcaptured_before_fence=true\n' \
    "${OMEGA_DEPLOY_REF:-unset}" >> "${manifest}"

  local name service port dump sha
  for triple in \
    "${DB_MAIN_NAME}:${DB_MAIN_SERVICE}:${DB_MAIN_PORT}" \
    "${DB_GOLD_NAME}:${DB_GOLD_SERVICE}:${DB_GOLD_PORT}"; do
    name="${triple%%:*}"; service="${triple#*:}"; port="${service#*:}"; service="${service%%:*}"
    dump="${dir}/${name}.sql"
    echo "[backup] pg_dump ${name} (${service}:${port})"
    dc exec -T "${service}" pg_dump --clean --if-exists -U postgres -p "${port}" -d "${name}" > "${dump}"
    if [[ ! -s "${dump}" ]]; then
      echo "ERROR: pg_dump produced an empty dump for ${name}; failing closed." >&2
      exit 5
    fi
    sha="$(sha256_of "${dump}")"
    printf '%s\t%s\t%s\t%s\n' "${name}" "${service}" "${port}" "${sha}" >> "${manifest}"
    printf '%s  %s\n' "${sha}" "${name}.sql" >> "${dir}/SHA256SUMS"
  done
  chmod -R go-rwx "${dir}"
  echo "[backup] restore-point ready at ${dir}"
  printf 'DAY2_BACKUP\tPASS\tdir=%s\tdatabases=2\n' "${dir}"
}

verify_manifest() {
  local dir="$1"
  local manifest; manifest="$(manifest_path "${dir}")"
  if [[ ! -s "${manifest}" ]]; then
    echo "ERROR: backup manifest missing or empty at ${manifest}." >&2
    exit 6
  fi
  local checked=0 name service port sha dump disk
  while IFS=$'\t' read -r name service port sha; do
    [[ "${name}" == \#* || -z "${name}" ]] && continue
    dump="${dir}/${name}.sql"
    if [[ ! -s "${dump}" ]]; then
      echo "ERROR: manifest references ${name}.sql but it is missing/empty." >&2
      exit 7
    fi
    disk="$(sha256_of "${dump}")"
    if [[ "${disk}" != "${sha}" ]]; then
      echo "ERROR: checksum mismatch for ${name}.sql: manifest=${sha} disk=${disk}." >&2
      exit 8
    fi
    checked=$((checked + 1))
  done < "${manifest}"
  if [[ "${checked}" -ne 2 ]]; then
    echo "ERROR: expected exactly 2 verified databases in the manifest, got ${checked}." >&2
    exit 9
  fi
  echo "[verify] both databases verified against manifest sha256"
}

do_restore() {
  local dir="$1"
  verify_manifest "${dir}"
  local name service port sha dump
  while IFS=$'\t' read -r name service port sha; do
    [[ "${name}" == \#* || -z "${name}" ]] && continue
    dump="${dir}/${name}.sql"
    echo "[restore] psql ${name} (${service}:${port}) from ${dump}"
    dc exec -T "${service}" psql -v ON_ERROR_STOP=1 -U postgres -p "${port}" -d "${name}" < "${dump}"
  done < "$(manifest_path "${dir}")"
  printf 'DAY2_RESTORE\tPASS\tdir=%s\tdatabases=2\n' "${dir}"
}

case "${subcommand}" in
  backup)  do_backup "${backup_dir}" ;;
  verify)  verify_manifest "${backup_dir}"; printf 'DAY2_BACKUP_VERIFY\tPASS\tdir=%s\n' "${backup_dir}" ;;
  restore) do_restore "${backup_dir}" ;;
  *) usage ;;
esac
