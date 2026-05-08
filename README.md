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
