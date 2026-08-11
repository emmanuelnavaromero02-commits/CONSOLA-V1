#!/usr/bin/env bash
# Independent systemd watchdog for a canonical GCP mutation window.
set -Eeuo pipefail
set +x
umask 077

UNIT="omega-gcp-operation-watchdog.service"
ACTION="${1:-}"
WATCHED_PID="${2:-}"
OPERATION_MARKER="${3:-}"
WATCHED_STARTTIME="${4:-}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "omega-operation-watchdog: root is required" >&2
  exit 10
fi
if [[ ! "$OPERATION_MARKER" =~ ^/[A-Za-z0-9._/-]+/shared/operation-state\.json$ || \
      "$OPERATION_MARKER" == *"/../"* ]]; then
  echo "omega-operation-watchdog: invalid operation marker" >&2
  exit 11
fi

database_fence_best_effort() {
  local container port database
  for spec in "mode_postgres:5432:modecissions" "mode_postgres_gold:5433:modecissions_gold"; do
    IFS=: read -r container port database <<<"$spec"
    docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres \
      -d postgres -p "$port" \
      -c "ALTER DATABASE ${database} CONNECTION LIMIT 0; ALTER DATABASE ${database} SET default_transaction_read_only = on;" \
      >/dev/null 2>&1 || :
    docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres \
      -d postgres -p "$port" \
      -c "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a JOIN pg_roles r ON r.rolname = a.usename WHERE a.datname = '${database}' AND a.pid <> pg_backend_pid() AND NOT r.rolsuper;" \
      >/dev/null 2>&1 || :
  done
}

emergency_fence() {
  local safe=1 docker_query_ok=0
  local -a container_ids=()
  set +e
  database_fence_best_effort
  mapfile -t container_ids < <(docker ps -q 2>/dev/null)
  if docker info >/dev/null 2>&1; then
    docker_query_ok=1
    if [[ "${#container_ids[@]}" -gt 0 ]]; then
      docker stop --time 30 "${container_ids[@]}" >/dev/null 2>&1
    fi
    if [[ -n "$(docker ps -q 2>/dev/null)" ]]; then
      safe=0
    fi
  fi
  systemctl stop docker.socket docker.service containerd.service >/dev/null 2>&1
  systemctl mask --runtime docker.socket docker.service containerd.service >/dev/null 2>&1
  for service in docker.socket docker.service containerd.service; do
    if systemctl is-active --quiet "$service"; then
      safe=0
    fi
  done
  # Docker live-restore can leave containers behind after dockerd exits. The
  # moby namespace shim check closes that ambiguity when the daemon is gone.
  if pgrep -f 'containerd-shim-runc-v2.*-namespace moby' >/dev/null 2>&1; then
    safe=0
  fi
  if [[ "$docker_query_ok" == "0" ]] && systemctl is-active --quiet docker.service; then
    safe=0
  fi
  if [[ "$safe" == "1" ]]; then
    logger -t omega-operation-watchdog \
      "operation owner disappeared; Docker runtime fenced with marker retained"
    return 0
  fi
  logger -t omega-operation-watchdog \
    "operation owner disappeared; emergency fence incomplete; retrying"
  return 1
}

process_identity() {
  python3 - "$1" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path("/proc") / sys.argv[1] / "stat"
try:
    value = path.read_text(encoding="utf-8")
except (FileNotFoundError, PermissionError, OSError):
    raise SystemExit(1)
match = re.fullmatch(r"[0-9]+ \(.*\) (.*)\n?", value)
if match is None:
    raise SystemExit(1)
fields = match.group(1).split()
if len(fields) < 20 or not fields[19].isdigit():
    raise SystemExit(1)
print(f"{fields[0]}\t{fields[19]}")
PY
}

process_is_same() {
  local identity state starttime
  identity="$(process_identity "$WATCHED_PID" 2>/dev/null)" || return 1
  IFS=$'\t' read -r state starttime <<<"$identity"
  [[ "$state" != "Z" && "$starttime" == "$WATCHED_STARTTIME" ]] && \
    kill -0 "$WATCHED_PID" 2>/dev/null
}

monitor() {
  local marker_seen=0
  if [[ ! "$WATCHED_PID" =~ ^[1-9][0-9]*$ || "$WATCHED_PID" == "1" || \
        ! "$WATCHED_STARTTIME" =~ ^[1-9][0-9]*$ ]]; then
    exit 12
  fi
  while :; do
    if [[ -e "$OPERATION_MARKER" ]]; then
      marker_seen=1
    fi
    if ! process_is_same; then
      if [[ "$marker_seen" == "1" || -e "$OPERATION_MARKER" ]]; then
        emergency_fence
      fi
      return 0
    fi
    if [[ "$marker_seen" == "1" && ! -e "$OPERATION_MARKER" ]]; then
      return 0
    fi
    sleep 0.2
  done
}

monitor_hold() {
  local timeout_seconds="$WATCHED_PID" elapsed=0
  if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ || "$timeout_seconds" -gt 86400 || \
        ! -e "$OPERATION_MARKER" ]]; then
    exit 12
  fi
  # A startup-metadata CAS is owned by the external controller, so there is no
  # trustworthy remote PID to watch across the hand-off.  The durable marker
  # blocks Docker on reboot; this bounded monitor fences the already-running
  # runtime if the controller never performs the exact read-back/finalization.
  while [[ -e "$OPERATION_MARKER" ]]; do
    if (( elapsed >= timeout_seconds * 5 )); then
      emergency_fence || exit 1
      return 0
    fi
    sleep 0.2
    elapsed=$((elapsed + 1))
  done
}

arm() {
  local identity state starttime
  if [[ ! "$WATCHED_PID" =~ ^[1-9][0-9]*$ || "$WATCHED_PID" == "1" || \
        -L "$OPERATION_MARKER" ]]; then
    echo "omega-operation-watchdog: PID/marker precondition failed" >&2
    exit 13
  fi
  identity="$(process_identity "$WATCHED_PID" 2>/dev/null)" || exit 13
  IFS=$'\t' read -r state starttime <<<"$identity"
  if [[ "$state" == "Z" || ! "$starttime" =~ ^[1-9][0-9]*$ ]]; then
    exit 13
  fi
  systemctl stop "$UNIT" >/dev/null 2>&1 || :
  systemctl reset-failed "$UNIT" >/dev/null 2>&1 || :
  for _ in $(seq 1 50); do
    if [[ "$(systemctl show "$UNIT" --property=LoadState --value 2>/dev/null)" == "not-found" ]]; then
      break
    fi
    sleep 0.1
  done
  systemd-run --quiet --collect --unit="${UNIT%.service}" \
    --property=Type=exec \
    --property=Restart=on-failure \
    --property=RestartSec=2s \
    --property=StartLimitIntervalSec=0 \
    -- "$0" monitor "$WATCHED_PID" "$OPERATION_MARKER" "$starttime"
  for _ in $(seq 1 50); do
    if systemctl is-active --quiet "$UNIT"; then
      return 0
    fi
    sleep 0.1
  done
  echo "omega-operation-watchdog: transient monitor did not become active" >&2
  exit 14
}

arm_hold() {
  local timeout_seconds="$WATCHED_PID"
  if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ || "$timeout_seconds" -gt 86400 || \
        -L "$OPERATION_MARKER" || ! -e "$OPERATION_MARKER" ]]; then
    echo "omega-operation-watchdog: hold precondition failed" >&2
    exit 13
  fi
  systemctl stop "$UNIT" >/dev/null 2>&1 || :
  systemctl reset-failed "$UNIT" >/dev/null 2>&1 || :
  systemd-run --quiet --collect --unit="${UNIT%.service}" \
    --property=Type=exec \
    --property=Restart=on-failure \
    --property=RestartSec=2s \
    --property=StartLimitIntervalSec=0 \
    -- "$0" monitor-hold "$timeout_seconds" "$OPERATION_MARKER"
  for _ in $(seq 1 50); do
    if systemctl is-active --quiet "$UNIT"; then
      return 0
    fi
    sleep 0.1
  done
  echo "omega-operation-watchdog: transient hold monitor did not become active" >&2
  exit 14
}

disarm() {
  if [[ -e "$OPERATION_MARKER" ]]; then
    echo "omega-operation-watchdog: refusing disarm while marker exists" >&2
    exit 15
  fi
  systemctl stop "$UNIT" >/dev/null 2>&1 || :
  systemctl reset-failed "$UNIT" >/dev/null 2>&1 || :
}

case "$ACTION" in
  arm) arm ;;
  arm-hold) arm_hold ;;
  monitor) monitor ;;
  monitor-hold) monitor_hold ;;
  disarm) disarm ;;
  *)
    echo "usage: omega-operation-watchdog {arm|arm-hold|monitor|monitor-hold|disarm} ARG MARKER [STARTTIME]" >&2
    exit 64
    ;;
esac
