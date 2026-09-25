#!/usr/bin/env bash
set -euo pipefail

ask() {  # ask VAR "prompt": read from the terminal when the variable is empty
    local name="$1" prompt="$2"
    if [[ -z "${!name:-}" ]]; then
        read -r -p "$prompt: " value
        printf -v "$name" '%s' "$value"
    fi
}

ask SAP_B1_HOST "Host de HANA"
SAP_B1_PORT="${SAP_B1_PORT:-30015}"
ask SAP_B1_USER "Usuario de solo lectura"
ask SAP_B1_COMPANIES "Esquemas de empresa (alias=ESQUEMA,... o ESQUEMA,...)"
SAP_B1_DATABASE="${SAP_B1_DATABASE:-}"
SAP_B1_ENCRYPT="${SAP_B1_ENCRYPT:-true}"
SAP_B1_SSL_VALIDATE_CERTIFICATE="${SAP_B1_SSL_VALIDATE_CERTIFICATE:-true}"
HDB_USERSTORE_KEY="${HDB_USERSTORE_KEY:-}"

if [[ ! "$SAP_B1_HOST" =~ ^[A-Za-z0-9.-]{1,253}$ ]]; then
    echo "Host no válido: solo letras, dígitos, punto y guion." >&2
    exit 1
fi
if [[ ! "$SAP_B1_PORT" =~ ^[0-9]{1,5}$ ]]; then
    echo "Puerto no válido: ${SAP_B1_PORT}" >&2
    exit 1
fi

schemas=()
IFS=',;' read -r -a parts <<< "$SAP_B1_COMPANIES"
for part in "${parts[@]}"; do
    part="${part// /}"
    [[ -z "$part" ]] && continue
    part="${part##*=}"
    if [[ ! "$part" =~ ^[A-Za-z0-9_\$][A-Za-z0-9_\$-]{0,127}$ ]]; then
        echo "Nombre de esquema no válido: $part" >&2
        exit 1
    fi
    schemas+=("$part")
done
if [[ ${#schemas[@]} -eq 0 ]]; then
    echo "No hay esquemas de empresa que probar." >&2
    exit 1
fi

echo
echo "== Paso 1: puerto TCP ${SAP_B1_HOST}:${SAP_B1_PORT} =="
tcp_port_responds() {
    ( exec 3<>"/dev/tcp/${SAP_B1_HOST}/${SAP_B1_PORT}" ) 2>/dev/null &
    local pid=$! tenths=0
    while kill -0 "$pid" 2>/dev/null; do
        if (( tenths >= 50 )); then
            kill "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null || true
            return 1
        fi
        sleep 0.1
        tenths=$((tenths + 1))
    done
    wait "$pid"
}
if tcp_port_responds; then
    echo "OK: el puerto responde."
else
    echo "FALLO: el puerto no responde."
    echo "  - Compruebe el cortafuegos o el túnel (wg show en el host WireGuard)."
    echo "  - Compruebe el puerto: 3NN13 es SYSTEMDB, el tenant suele responder en 3NN15 (00_find_tenant_sql_port.sql)."
    exit 2
fi

echo
echo "== Paso 2: sesión SQL =="
sql_file="$(mktemp)"
trap 'rm -f "$sql_file"' EXIT
for schema in "${schemas[@]}"; do
    printf "SELECT '%s' AS COMPANY_DB, \"Version\" FROM \"%s\".\"CINF\";\n" "$schema" "$schema" >> "$sql_file"
done

if command -v hdbsql >/dev/null 2>&1; then
    args=(-n "${SAP_B1_HOST}:${SAP_B1_PORT}")
    [[ -n "$SAP_B1_DATABASE" ]] && args+=(-d "$SAP_B1_DATABASE")
    if [[ -n "$HDB_USERSTORE_KEY" ]]; then args+=(-U "$HDB_USERSTORE_KEY"); else args+=(-u "$SAP_B1_USER"); fi   # no -p: hdbsql asks
    [[ "${SAP_B1_ENCRYPT,,}" == "true" ]] && args+=(-e)
    [[ "${SAP_B1_SSL_VALIDATE_CERTIFICATE,,}" != "true" ]] && args+=(-ssltrustcert)
    args+=(-A -I "$sql_file")
    echo "Ejecutando: hdbsql ${args[*]}"
    if hdbsql "${args[@]}"; then
        echo "OK: cada empresa devolvió su versión de Business One."
        exit 0
    fi
    echo "FALLO: hdbsql terminó con error (autenticación, GRANT o TLS; ver 02_verify_readonly_user.sql)."
    exit 3
fi

if python3 -c "import hdbcli" >/dev/null 2>&1; then
    echo "hdbsql no está instalado; se usa python3 + hdbcli (la contraseña se pide en pantalla)."
    SQL_FILE="$sql_file" SAP_B1_HOST="$SAP_B1_HOST" SAP_B1_PORT="$SAP_B1_PORT" \
        SAP_B1_USER="$SAP_B1_USER" SAP_B1_DATABASE="$SAP_B1_DATABASE" \
        SAP_B1_ENCRYPT="$SAP_B1_ENCRYPT" SAP_B1_SSL_VALIDATE_CERTIFICATE="$SAP_B1_SSL_VALIDATE_CERTIFICATE" \
        python3 - <<'PY'
import getpass, os, sys
from hdbcli import dbapi

def truthy(name, default):
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "on"}

kwargs = {
    "address": os.environ["SAP_B1_HOST"],
    "port": int(os.environ["SAP_B1_PORT"]),
    "user": os.environ["SAP_B1_USER"],
    "password": getpass.getpass("Contraseña de %s: " % os.environ["SAP_B1_USER"]),
    "encrypt": truthy("SAP_B1_ENCRYPT", True),
    "sslValidateCertificate": truthy("SAP_B1_SSL_VALIDATE_CERTIFICATE", True),
    "connectTimeout": 15000,
}
if os.environ.get("SAP_B1_DATABASE"):
    kwargs["databaseName"] = os.environ["SAP_B1_DATABASE"]
try:
    conn = dbapi.connect(**kwargs)
except Exception as exc:  # never echo the password: hdbcli messages do not carry it
    print("FALLO al conectar: %s" % type(exc).__name__)
    sys.exit(3)
cur = conn.cursor()
failed = False
with open(os.environ["SQL_FILE"], encoding="utf-8") as handle:
    for statement in handle.read().split(";"):
        statement = statement.strip()
        if not statement:
            continue
        try:
            cur.execute(statement)
            print(cur.fetchone())
        except Exception as exc:
            print("FALLO: %s" % type(exc).__name__)
            failed = True
conn.close()
sys.exit(3 if failed else 0)
PY
    exit $?
fi

echo "Ni hdbsql ni hdbcli están disponibles en este host; el paso 2 se omite."
echo "  El paso 1 ya confirma que la red llega al puerto SQL. Para la prueba SQL"
echo "  use el contenedor sap-b1 (POST /skills/test_connection) o instale el cliente de HANA."
exit 0
