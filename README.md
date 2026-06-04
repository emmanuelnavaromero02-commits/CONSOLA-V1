# Security Phase 1 Residuals + Phase 2 Quick Wins

### Phase 1 - Residual Closures
1. **RLS en duckdb_engine.py**: The regular expression now intercepts ANY query hitting the `pggold` schema regardless of its casing or name (e.g., `pggold.anything`), enforcing default-deny logic accurately without bypasses. `read_only=True` is now used for DuckDB connection queries via the `_read_conn()` abstraction.
2. **vault/secrets.yaml**: Bash-style variable interpolation (`${VAR:-""}`) was replaced with standard `${VAR}` referencing, to ensure proper environment resolution during python parsing and backend startup.
3. **Credenciales Hardcodeadas**: Visually confirmed and successfully replaced remaining fallback defaults with their respective secure environment mapping.
4. **Workspace API**: The `/api/data/{dataset}` route actively passes user context, ensuring `apply_rls` intercept logic properly filters multi-tenant access dynamically.
5. **Airflow Connections**: Confirmed connections are dynamically injected into tasks via vault extraction logic, not exposed raw in templates.

### Phase 2 - Quick Wins
1. **Security Headers**: Standard HTTP headers (`X-Frame-Options`, `Strict-Transport-Security`, `Content-Security-Policy`, `X-Content-Type-Options`) injected globally into `console` and `workspace` middleware.
2. **Rate Limiting**: Lightweight, dependency-free in-memory rate-limiter applied to critical endpoints (`/login`, `/api/data/*`, `/mcp/*`) to mitigate basic brute force/DDoS risks.
3. **Higiene Docker**: All core `Dockerfiles` (`console`, `mcp-infra`, `refinement`, `vault`, `workspace`) now execute using a non-root `appuser`. Furthermore, basic DB connection `HEALTHCHECK` was added to postgres within `docker-compose.yml`.

### Modified Files:
* `refinement/app/duckdb_engine.py` (RLS Regex + Read Only Conn)
* `vault/secrets.yaml` (Variables Formatting)
* `console/app/main.py` (FastAPI Middlewares)
* `workspace/app/main.py` (FastAPI Middlewares)
* `console/Dockerfile`, `mcp-infra/Dockerfile`, `refinement/Dockerfile`, `vault/Dockerfile`, `workspace/Dockerfile` (App User Setup)
* `infra/docker-compose.yml` (Healthcheck)
* `.github/workflows/docker-image.yml` (CI Fixes)

### Phase 1 Overview
| Component | Before | After |
| --------- | ------ | ----- |
| **Gold Datasets** | Vulnerable to cross-tenant data access | Strict RLS (default-deny) via parameterized queries |
| **DAG Templates** | Internal HTTP endpoints open | Enforced `X-Internal-Api-Key` for all webhook calls |
| **Secrets** | Hardcoded defaults | Sourced safely from ENV or Vault |
| **Internal MCP APIs** | Mixed authentication | `verify_internal_api_key` enforced universally |
| **Frontend UI** | Bare `innerHTML` injection risks | Standardized `esc()` sanitization wrapper |

---

## Known Security Debt

### CSP `'unsafe-inline'` (script-src and style-src)

**Status:** OPEN. Not resolved in any hardening round to date.

Both `console/app/main.py` and `workspace/app/main.py` ship a CSP that still
includes `script-src 'self' 'unsafe-inline'` and `style-src 'self' 'unsafe-inline'`.
This neutralises CSP's primary protection against XSS: any reflected or stored
script injection becomes immediately exploitable in the browser even though
the response headers advertise a CSP.

The defence-in-depth fix (escaping in templates, output sanitisation in the
`esc()` wrapper, the `editUser(...)` attribute-quoting fix, etc.) is in place
and verified, so a known XSS is not currently exploitable. But the surface
remains one missed `escHtml()` away from compromise.

**Why it's still open:** the console HTML files contain on the order of 6,000
lines of inline `<script>` blocks across ~15 pages. Removing `'unsafe-inline'`
requires either:

1. Extracting every inline script into a separate `.js` asset; or
2. Serving the HTMLs through Jinja templates so the middleware can inject a
   per-request `nonce-{...}` token into every `<script>` and `<style>` tag.

Either path is a multi-day refactor. See `docs/security/csp-migration-plan.md`
for the proposed approach.

**Workarounds in effect today:**
- Strict `esc()` / `escHtml()` discipline in every dynamic `innerHTML`
- `X-Frame-Options: DENY` on non-viewer routes
- `frame-ancestors 'none'` (or `'self'` for `/viewer`)
- Strict `Referrer-Policy: same-origin`
- HSTS + `Permissions-Policy` block sensor APIs

Do not consider this resolved until both services serve responses with no
`'unsafe-inline'` directive and the inline-script discipline is enforced
by a lint rule in CI.

## Operación

### Local non-production bootstrap

For a fresh local/demo stack:

```bash
make preflight
make up
```

For a destructive local reset only:

```bash
make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev
make preflight
make up
```

`make nuke` deletes local Docker volumes. Never use it for AWS,
staging, production, or any host containing customer data.

If local Docker volumes were created with older secrets, use:

```bash
make repair-local-stack
```

For a local Superset metastore encrypted with a previous
`SUPERSET_SECRET_KEY`, the repair is intentionally explicit:

```bash
CONFIRM_SUPERSET_METASTORE_REPAIR=LOCAL_SUPERSET_REPAIR make repair-local-stack
```

Para administrar OMEGA en producción consulta `docs/runbook/`:

- [01 Arrancar desde cero](docs/runbook/01_arrancar_desde_cero.md) — pre-requisitos, `.env`, smoke 30/30, sanity HTTP.
- [02 Primer tenant](docs/runbook/02_primer_tenant.md) — login bootstrap admin, crear workspace, invitar primer usuario.
- [03 Configurar Replicon](docs/runbook/03_configurar_replicon.md) — credenciales en Vault, test_connection, primera carga.
- [04 Configurar SAP (HCM / S/4 / SuccessFactors)](docs/runbook/04_configurar_sap.md) — mismo flujo, variables por sistema.
- [05 Rotar secretos](docs/runbook/05_rotar_secretos.md) — `FIELD_ENCRYPTION_KEY` (Fernet), `INTERNAL_API_KEY`, passwords admin, roles `omega_*` de DB.
- [06 Backup / restore](docs/runbook/06_backup_restore.md) — `pg_dumpall`, `mc mirror` para MinIO, recuperación end-to-end.
- [07 Debug de fallos](docs/runbook/07_debug_fallos.md) — uso de `X-Request-ID` + `audit_events` (ip + user_agent) + `extraction_runs` para reconstruir incidentes.
- [08 Usar el copiloto](docs/runbook/08_usar_copiloto.md) — chat IA con approval gate, RBAC por `risk_level`, auditoría forense de cada tool call.
- [09 Demo / beta controlada](docs/runbook/09_demo_beta.md) — preflight, bootstrap, smoke, tests y checklist de demo.
- [10 v1 pública HTTPS](docs/runbook/10_v1_public_https.md) — ALB/ACM, SSM, smoke público, live LLM, backup/restore/rollback.
- [11 Estabilización y rollback](docs/runbook/11_release_stabilization.md) — métricas de Intelligence Engine, freeze de releases y rollback por tag inmutable.
- [12 Scope hardening](docs/runbook/12_scope_hardening.md) — reglas de tenant/workspace, Vault y pipeline scope.
