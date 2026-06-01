#!/usr/bin/env bash
# Sprint v1.23 — end-to-end smoke test (audit B3).
#
# Verifies the stack is FUNCTIONAL, not just "containers running":
#   1-5. /healthz on the 5 app services
#   6.   Postgres pg_isready
#   7.   MinIO /minio/health/live
#   8.   console /healthz returns a parseable JSON payload
#   9.   /monitoring/invoke without auth is rejected (gate works)
#   10.  Postgres has > 10 public tables (migrations ran)
#   11.  omega_vault has SELECT on vault_entries (the role exists + got its GRANT)
#   12.  omega_console is DENIED SELECT on vault_entries (defense-in-depth v1.19)
#
# Idempotent: every check is a GET or a read-only SQL probe; re-running
# yields the same result. No state mutation, no fixtures left behind.
#
# Assumes the compose stack is already up (`make up`). Doesn't try to
# start it — that's the operator's job. Fails loudly if anything is
# unreachable rather than spinning indefinitely (5 s curl timeouts).
set -euo pipefail

FAILURES=0
TOTAL=0

log()  { echo "[smoke] $*"; }
fail() { echo "[smoke FAIL] $*"; FAILURES=$((FAILURES + 1)); TOTAL=$((TOTAL + 1)); }
pass() { echo "[smoke PASS] $*";                              TOTAL=$((TOTAL + 1)); }

# curl wrapper that times out fast — a stuck connection shouldn't block
# the whole smoke run.
fetch() {
  curl -sf --max-time 5 -o /dev/null "$@"
}

fetch_body() {
  curl -s --max-time 5 "$@"
}

# Classify an UNAUTHENTICATED probe against a protected endpoint HONESTLY,
# so a DOWN service is never mislabelled as a security regression:
#   000          → service down / no connection (start the stack) — NOT a gate result
#   401 | 403    → auth gate working (request correctly rejected)
#   5xx          → server error (service broken) — NOT a gate result
#   anything else→ UNEXPECTED: the anonymous request was NOT rejected (real auth gap)
auth_gate_check() {
  local url="$1" label="$2" code
  # curl already prints "000" via -w on connect failure; `|| true` keeps
  # set -e happy without double-appending another 000.
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || true)"
  code="${code:-000}"
  case "$code" in
    000)         fail "${label}: service DOWN / no connection (000) — is the stack up? (not an auth result)" ;;
    401|403)     pass "${label}: auth gate OK (unauthenticated rejected, ${code})" ;;
    5[0-9][0-9]) fail "${label}: server error ${code} — service broken (not an auth result)" ;;
    *)           fail "${label}: UNEXPECTED ${code} — anonymous request was NOT rejected (possible auth gap)" ;;
  esac
}

# ── 1-5. /healthz on the 5 app services ────────────────────────────────
for svc_port in console:8000 workspace:8001 mcp-infra:8010 vault:8300 refinement:8500; do
  svc="${svc_port%:*}"
  port="${svc_port#*:}"
  if fetch "http://localhost:${port}/healthz"; then
    pass "healthz ${svc} (port ${port})"
  else
    fail "healthz ${svc} did not respond (port ${port})"
  fi
done

# Sprint v1.40: Replicon cartridge restored. Exposes /health (legacy
# from the original ZIP, intentionally kept), not /healthz like the
# rest of the platform.
if fetch "http://localhost:8201/health"; then
  pass "health replicon (port 8201)"
else
  fail "health replicon did not respond (port 8201)"
fi

if fetch "http://localhost:8210/health"; then
  pass "health hubspot (port 8210)"
else
  fail "health hubspot did not respond (port 8210)"
fi

if fetch "http://localhost:8205/health"; then
  pass "health salesforce (port 8205)"
else
  fail "health salesforce did not respond (port 8205)"
fi

# ── SAP cartridges (v1.40.3) ──
for pair in "sap-hcm:8202" "sap-successfactors:8203" "sap-s4hana:8204"; do
  name="${pair%:*}"
  port="${pair#*:}"
  if fetch "http://localhost:${port}/health"; then
    pass "health ${name} (port ${port})"
  else
    fail "health ${name} did not respond (port ${port})"
  fi
done

# ── Replicon /mcp/tools and /skills/* without auth MUST be rejected ──
# (honest classification: down vs auth-OK vs server-error vs real gap)
auth_gate_check "http://localhost:8201/mcp/tools" "replicon /mcp/tools"
auth_gate_check "http://localhost:8201/skills/entities" "replicon /skills/*"
auth_gate_check "http://localhost:8210/mcp/tools" "hubspot /mcp/tools"
auth_gate_check "http://localhost:8210/skills/entities" "hubspot /skills/*"
auth_gate_check "http://localhost:8205/mcp/tools" "salesforce /mcp/tools"
auth_gate_check "http://localhost:8205/skills/entities" "salesforce /skills/*"

# ── Detect containers in restart loop ──
restarting="$(docker ps --filter 'status=restarting' --format '{{.Names}}' 2>/dev/null)"
if [ -z "$restarting" ]; then
  pass "no containers in restart loop"
else
  fail "containers in restart loop: ${restarting}"
fi

# ── 6. Postgres pg_isready ─────────────────────────────────────────────
if docker exec mode_postgres pg_isready -h 127.0.0.1 -p 5432 -U postgres -q 2>/dev/null; then
  pass "postgres ready"
else
  fail "postgres NOT ready"
fi

# ── 7. MinIO live probe ────────────────────────────────────────────────
if fetch http://localhost:9000/minio/health/live; then
  pass "minio live"
else
  fail "minio NOT live"
fi

# ── 8. console /healthz returns parseable JSON ─────────────────────────
RESP="$(fetch_body http://localhost:8000/healthz || echo '')"
if echo "$RESP" | grep -q '"ok"' && echo "$RESP" | grep -q 'true'; then
  pass "console /healthz returns valid JSON"
else
  fail "console /healthz JSON invalid: ${RESP}"
fi

# ── 9. Auth gate: POST without credentials must be rejected ────────────
# After v1.22 the CSRF dep fires before auth on cookie paths, so an
# anonymous POST gets 403 (csrf) instead of 401 (auth). Both mean
# "rejected" — accept either. A 2xx here would be a security regression.
CODE="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' \
        -X POST http://localhost:8000/monitoring/invoke \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null || true)"
CODE="${CODE:-000}"
case "$CODE" in
  000)         fail "console DOWN / no connection (POST /monitoring/invoke → 000) — is the stack up? (not an auth result)" ;;
  401|403)     pass "auth gate working (POST /monitoring/invoke → ${CODE})" ;;
  5[0-9][0-9]) fail "console server error (POST /monitoring/invoke → ${CODE}) — service broken (not an auth result)" ;;
  *)           fail "auth gate broken (POST /monitoring/invoke → ${CODE}, expected 401|403)" ;;
esac

# ── v1.43.4 (Codex C2): authenticated /mcp/tools must return tools ────
# v1.43.3 shipped with SAP cartridges' /mcp/tools returning HTTP 500
# (fastmcp 2.5.0 pin vs 3.x API in main.py). The auth-rejection check
# above only verified the gate; it can't tell whether the endpoint
# behind the gate actually works. These 4 checks land an authenticated
# probe at each cartridge and assert the response shape ``{"tools":
# [...]}`` has at least one entry. If INTERNAL_API_KEY is unavailable
# (running from a workstation without infra/.env), the block warns and
# skips — smoke stays useful in that environment without giving false
# greens in the developer Mac where the keys are present.
CARTRIDGE_KEY=""
if [ -f "infra/.env" ]; then
  # The request identifies itself as X-Internal-Service: console, so it
  # must use the console→cartridge pair key. INTERNAL_API_KEY remains a
  # legacy fallback outside production but is not the v1.0 contract.
  CARTRIDGE_KEY="$(grep -E '^INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE=' infra/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
  if [ -z "$CARTRIDGE_KEY" ]; then
    CARTRIDGE_KEY="$(grep -E '^INTERNAL_API_KEY=' infra/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
  fi
fi
if [ -z "$CARTRIDGE_KEY" ]; then
    echo "[smoke] WARN  INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE not found in infra/.env — skipping 6 MCP tool probes"
else
  for pair in "replicon:8201" "hubspot:8210" "salesforce:8205" "sap_hcm:8202" "sap_successfactors:8203" "sap_s4hana:8204"; do
    name="${pair%:*}"
    port="${pair#*:}"
    BODY="$(curl -sS --max-time 5 \
            -H "X-Internal-Api-Key: ${CARTRIDGE_KEY}" \
            -H "X-Internal-Service: console" \
            "http://localhost:${port}/mcp/tools" 2>/dev/null || echo '')"
    # Count tool entries without jq (smoke must work without extra deps):
    # the body is JSON like {"tools":[{...},{...}]} — count the
    # closing braces preceded by "input_schema" markers. Fall back to a
    # tools-array length match if jq is available.
    if command -v jq >/dev/null 2>&1; then
      TOOL_COUNT="$(echo "$BODY" | jq '.tools | length' 2>/dev/null || echo 0)"
    else
      # Defensive: count occurrences of '"name":' inside the body —
      # one per tool. Works for both the cartridge response shape and
      # an error shape (errors don't contain "name":).
      TOOL_COUNT="$(echo "$BODY" | grep -o '"name":' | wc -l | tr -d '[:space:]')"
    fi
    if [ "${TOOL_COUNT:-0}" -gt 0 ]; then
      pass "${name} /mcp/tools returns ${TOOL_COUNT} tools (port ${port})"
    else
      fail "${name} /mcp/tools returned 0 tools — Codex C1 regression (port ${port}, body: ${BODY:0:200})"
    fi
  done
fi

# ── 10. Postgres: enough public tables to mean migrations ran ──────────
# Fresh init creates 30+ tables across the various 0*-2*.sql scripts.
# If we see <10 the migrations didn't run and the next checks would
# all fail in confusing ways — fail fast here with a clearer message.
RESULT="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
          "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public';" 2>/dev/null || echo 0)"
RESULT="${RESULT//[[:space:]]/}"
if [ "${RESULT:-0}" -gt 10 ]; then
  pass "postgres has ${RESULT} public tables (schema migrated)"
else
  fail "postgres has only ${RESULT} public tables (migrations may have failed)"
fi

SALESFORCE_MCP_ROW="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
          "SELECT COALESCE((SELECT category || '|' || url FROM mcp_servers WHERE id='salesforce'), '');" 2>/dev/null || echo '')"
SALESFORCE_MCP_ROW="${SALESFORCE_MCP_ROW//[[:space:]]/}"
if [ "$SALESFORCE_MCP_ROW" = "cartridge|http://salesforce:8205" ]; then
  pass "mcp_servers registers salesforce cartridge"
else
  fail "mcp_servers missing salesforce cartridge row (got: '${SALESFORCE_MCP_ROW}')"
fi

# ── 11. omega_vault HAS SELECT on vault_entries (role + GRANT applied) ─
ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
          "SELECT has_table_privilege('omega_vault', 'vault_entries', 'SELECT');" 2>/dev/null || echo '')"
ACCESS="${ACCESS//[[:space:]]/}"
if [ "$ACCESS" = "t" ]; then
  pass "omega_vault has SELECT on vault_entries"
else
  fail "omega_vault SELECT on vault_entries broken (got: '${ACCESS}')"
fi

# ── 12. omega_console DENIED on vault_entries (regression guard v1.19) ─
# This is the keystone of the per-service partitioning: only omega_vault
# touches vault_entries. If a future GRANT regression leaks it to
# omega_console, that's a CRITICAL audit finding.
NO_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
             "SELECT has_table_privilege('omega_console', 'vault_entries', 'SELECT');" 2>/dev/null || echo '')"
NO_ACCESS="${NO_ACCESS//[[:space:]]/}"
if [ "$NO_ACCESS" = "f" ]; then
  pass "omega_console correctly DENIED on vault_entries (defense-in-depth)"
else
  fail "omega_console has access to vault_entries — REGRESSION of v1.19 (got: '${NO_ACCESS}')"
fi

# ── 13-17. omega_mcp_infra DENIED on identity/auth/decisions tables ────
# Sprint v1.36 (audit B4 P0): omega_mcp_infra must not be able to read
# users / tenants / decisions / roles / workspaces. The MCP-infra
# tools never query these — the previous GRANT was the path to a
# password-hash exfil if combined with postgres_execute_query.
for sensitive in users tenants decisions roles workspaces; do
  NO_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
               "SELECT has_table_privilege('omega_mcp_infra', '${sensitive}', 'SELECT');" 2>/dev/null || echo '')"
  NO_ACCESS="${NO_ACCESS//[[:space:]]/}"
  if [ "$NO_ACCESS" = "f" ]; then
    pass "omega_mcp_infra correctly DENIED on ${sensitive} (v1.36)"
  else
    fail "omega_mcp_infra has access to ${sensitive} — REGRESSION of v1.36 (got: '${NO_ACCESS}')"
  fi
done

# ── 18. omega_mcp_infra still HAS SELECT on cartridge_dags (no regression) ─
# Sprint v1.36 lockdown must not break legitimate operational reads.
# cartridge_dags / entity_config / pipeline_runs are what Studio actually
# uses; verify one of them still grants SELECT.
HAS_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
              "SELECT has_table_privilege('omega_mcp_infra', 'cartridge_dags', 'SELECT');" 2>/dev/null || echo '')"
HAS_ACCESS="${HAS_ACCESS//[[:space:]]/}"
if [ "$HAS_ACCESS" = "t" ]; then
  pass "omega_mcp_infra still has SELECT on cartridge_dags (operational reads OK)"
else
  fail "omega_mcp_infra lost SELECT on cartridge_dags — v1.36 over-revoked (got: '${HAS_ACCESS}')"
fi

# ── 19-22. SAP cartridge + airflow_dag roles DENIED on users (v1.38) ────
# Sprint v1.38 (audit B5+B6 P0.5): SAP cartridges and the Airflow DAG
# role must never read identity tables. Pre-v1.38 they connected as
# the postgres superuser, so a compromise of any one of them was
# game over. The cartridge_and_meta_roles migration applies an
# explicit REVOKE on users (and 13 sibling tables); verify the
# REVOKE held on users for all operational roles.
for v138_role in omega_cartridge_sap_hcm omega_cartridge_sap_s4 omega_cartridge_sap_sf omega_cartridge_salesforce omega_cartridge_hubspot omega_airflow_dag; do
  NO_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
               "SELECT has_table_privilege('${v138_role}', 'users', 'SELECT');" 2>/dev/null || echo '')"
  NO_ACCESS="${NO_ACCESS//[[:space:]]/}"
  if [ "$NO_ACCESS" = "f" ]; then
    pass "${v138_role} correctly DENIED on users (v1.38)"
  else
    fail "${v138_role} has access to users — REGRESSION of v1.38 (got: '${NO_ACCESS}')"
  fi
done

# ── 23. omega_cartridge_sap_hcm HAS SELECT on entity_config (positive check) ──
# Make sure the lockdown didn't over-revoke: SAP cartridges still
# need to read their own config.
HAS_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
              "SELECT has_table_privilege('omega_cartridge_sap_hcm', 'entity_config', 'SELECT');" 2>/dev/null || echo '')"
HAS_ACCESS="${HAS_ACCESS//[[:space:]]/}"
if [ "$HAS_ACCESS" = "t" ]; then
  pass "omega_cartridge_sap_hcm still has SELECT on entity_config (operational reads OK)"
else
  fail "omega_cartridge_sap_hcm lost SELECT on entity_config — v1.38 over-revoked (got: '${HAS_ACCESS}')"
fi

HAS_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
              "SELECT has_table_privilege('omega_cartridge_hubspot', 'entity_config', 'SELECT');" 2>/dev/null || echo '')"
HAS_ACCESS="${HAS_ACCESS//[[:space:]]/}"
if [ "$HAS_ACCESS" = "t" ]; then
  pass "omega_cartridge_hubspot still has SELECT on entity_config (operational reads OK)"
else
  fail "omega_cartridge_hubspot lost SELECT on entity_config (got: '${HAS_ACCESS}')"
fi

HAS_ACCESS="$(docker exec mode_postgres psql -U postgres -d modecissions -tAc \
              "SELECT has_table_privilege('omega_cartridge_salesforce', 'entity_config', 'SELECT');" 2>/dev/null || echo '')"
HAS_ACCESS="${HAS_ACCESS//[[:space:]]/}"
if [ "$HAS_ACCESS" = "t" ]; then
  pass "omega_cartridge_salesforce still has SELECT on entity_config (operational reads OK)"
else
  fail "omega_cartridge_salesforce lost SELECT on entity_config (got: '${HAS_ACCESS}')"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
if [ "$FAILURES" -eq 0 ]; then
  echo "[smoke] ✅ ${TOTAL}/${TOTAL} checks passed"
  exit 0
else
  PASSED=$((TOTAL - FAILURES))
  echo "[smoke] ❌ ${FAILURES}/${TOTAL} check(s) failed (${PASSED} passed)"
  exit 1
fi
