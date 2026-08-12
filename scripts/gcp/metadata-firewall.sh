#!/bin/bash -p
# Keep the VM metadata identity host-only; Docker bridge traffic is denied.
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE \
  PYTHONWARNINGS PYTHONBREAKPOINT PYTHONSAFEPATH
unset SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE SSLKEYLOGFILE
unset DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH \
  DOCKER_API_VERSION DOCKER_CONFIG DOCKER_AUTH_CONFIG COMPOSE_FILE \
  COMPOSE_PATH_SEPARATOR COMPOSE_PROFILES COMPOSE_PROJECT_NAME
export DOCKER_HOST=unix:///run/docker.sock

ACTION="${1:-}"
CANONICAL="/usr/local/sbin/omega-metadata-firewall"
DROPIN_DIR="/etc/systemd/system/docker.service.d"
DROPIN="${DROPIN_DIR}/omega-metadata-firewall.conf"
COMMENT="omega-deny-container-gcp-metadata"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "omega-metadata-firewall: root is required" >&2
  exit 10
fi

fsync_paths() {
  /usr/bin/python3 -I - "$@" <<'PY'
import os
import pathlib
import sys

for raw in sys.argv[1:]:
    path = pathlib.Path(raw)
    descriptor = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
}

ensure_family() {
  local binary="$1" destination="$2" reject_with="$3"
  "$binary" -w 10 -N DOCKER-USER >/dev/null 2>&1 || :
  local first_forward
  first_forward="$("$binary" -w 10 -S FORWARD | awk '$1 == "-A" {print; exit}')"
  if [[ "$first_forward" != "-A FORWARD -j DOCKER-USER" ]]; then
    "$binary" -w 10 -I FORWARD 1 -j DOCKER-USER
  fi
  local first_user
  first_user="$("$binary" -w 10 -S DOCKER-USER | awk '$1 == "-A" {print; exit}')"
  if [[ "$first_user" != "$(canonical_rule "$destination" "$reject_with")" ]]; then
    "$binary" -w 10 -I DOCKER-USER 1 -d "$destination" \
      -m comment --comment "$COMMENT" -j REJECT --reject-with "$reject_with"
  fi
}

canonical_rule() {
  printf '%s\n' \
    "-A DOCKER-USER -d $1 -m comment --comment ${COMMENT} -j REJECT --reject-with $2"
}

verify_family() {
  local binary="$1" destination="$2" reject_with="$3" first_forward first_user
  first_forward="$("$binary" -w 10 -S FORWARD | awk '$1 == "-A" {print; exit}')"
  first_user="$("$binary" -w 10 -S DOCKER-USER | awk '$1 == "-A" {print; exit}')"
  [[ "$first_forward" == "-A FORWARD -j DOCKER-USER" ]]
  [[ "$first_user" == "$(canonical_rule "$destination" "$reject_with")" ]]
}

enforce() {
  command -v iptables >/dev/null
  command -v ip6tables >/dev/null
  ensure_family iptables "169.254.169.254/32" icmp-port-unreachable
  ensure_family ip6tables "fd20:ce::254/128" icmp6-port-unreachable
  verify_family iptables "169.254.169.254/32" icmp-port-unreachable
  verify_family ip6tables "fd20:ce::254/128" icmp6-port-unreachable
}

verify_host_metadata() {
  local status
  status="$(curl -q --fail --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 5 --noproxy '*' -H 'Metadata-Flavor: Google' \
    'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/email')"
  [[ "$status" == "200" ]]
  if ip -6 route get fd20:ce::254 >/dev/null 2>&1; then
    status="$(curl -q --fail --silent --output /dev/null --write-out '%{http_code}' \
      --max-time 5 --noproxy '*' -g -H 'Metadata-Flavor: Google' \
      'http://[fd20:ce::254]/computeMetadata/v1/instance/service-accounts/default/email')"
    [[ "$status" == "200" ]]
  fi
}

verify_installation() {
  [[ ! -L "$CANONICAL" && -x "$CANONICAL" && ! -L "$DROPIN" && -f "$DROPIN" ]]
  [[ "$(stat -c '%U:%G:%a' "$CANONICAL")" == "root:root:755" ]]
  [[ "$(stat -c '%U:%G:%a' "$DROPIN")" == "root:root:644" ]]
  grep -Fqx 'ExecStartPre=/usr/local/sbin/omega-metadata-firewall enforce' "$DROPIN"
  systemctl show docker.service --property=ExecStartPre --value | \
    grep -Fq '/usr/local/sbin/omega-metadata-firewall enforce'
}

install_host() {
  local self_tmp dropin_tmp
  install -d -m 0755 "$(dirname "$CANONICAL")" "$DROPIN_DIR"
  if [[ "$(readlink -f "$0")" != "$CANONICAL" ]]; then
    self_tmp="$(mktemp "$(dirname "$CANONICAL")/.omega-metadata-firewall.XXXXXX")"
    install -m 0755 "$0" "$self_tmp"
    fsync_paths "$self_tmp"
    mv -Tf "$self_tmp" "$CANONICAL"
    fsync_paths "$(dirname "$CANONICAL")"
  fi
  dropin_tmp="$(mktemp "${DROPIN_DIR}/.omega-metadata-firewall.conf.XXXXXX")"
  printf '%s\n' '[Service]' \
    'ExecStartPre=/usr/local/sbin/omega-metadata-firewall enforce' > "$dropin_tmp"
  chmod 0644 "$dropin_tmp"
  fsync_paths "$dropin_tmp"
  mv -Tf "$dropin_tmp" "$DROPIN"
  fsync_paths "$DROPIN_DIR"
  systemctl daemon-reload
  enforce
  verify_installation
  # The policy is forwarding-only: host workload identity must stay available.
  verify_host_metadata
}

verify_container() {
  verify_installation
  enforce
  verify_host_metadata
  if [[ "$(docker inspect mode_console --format '{{.State.Running}}' 2>/dev/null)" != "true" ]]; then
    echo "omega-metadata-firewall: proprietary proof container is not running" >&2
    exit 20
  fi
  docker exec -i mode_console python - <<'PY'
import urllib.error
import urllib.request

class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        del request, fp, code, message, headers, new_url
        raise SystemExit("container reached VM metadata and received a redirect")


opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    RejectRedirects(),
)
for url in (
    "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
    "http://[fd20:ce::254]/computeMetadata/v1/instance/service-accounts/default/token",
):
    request = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with opener.open(request, timeout=2):
            raise SystemExit("container unexpectedly reached VM metadata")
    except urllib.error.HTTPError:
        raise SystemExit("container reached VM metadata and received HTTP")
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
print("metadata_endpoints_blocked=2/2")
PY
}

case "$ACTION" in
  install) install_host ;;
  install-and-verify-container)
    install_host
    verify_container
    ;;
  enforce) enforce ;;
  verify)
    enforce
    verify_installation
    verify_host_metadata
    ;;
  verify-container) verify_container ;;
  *)
    echo "usage: omega-metadata-firewall {install|install-and-verify-container|enforce|verify|verify-container}" >&2
    exit 64
    ;;
esac
