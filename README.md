# Security Phase 1 - Closure

### Overview
This commit finalizes the Phase 1 security rollout for the MODecissionsPaaS platform by securing data access, sealing credentials, hardening internal APIs, and preventing XSS vulnerabilities.

### Changes Included:
1. **RLS Fuerte en Gold Datasets / DuckDB**
   - Implemented dynamic default-deny Row-Level Security in `refinement/app/duckdb_engine.py`.
   - Now intercepts `pggold.gold_*` queries and automatically injects security filters for `tenant_id`, `workspace_id`, `user_id`, or `revenue_manager` based on the user's context and the dataset schema using secure parameterized queries.
   - If no valid policy matches, blocks execution via Default Deny.
2. **Blindaje de DAG Templates**
   - In `console/app/services/dag_templates.py`, all HTTP requests (watermarks, pipelines, and trigger silver) are now authenticated with the `X-Internal-Api-Key` header.
3. **Eliminación de Secretos Hardcodeados**
   - Purged default fallback passwords (`minio123`, `postgres:postgres`, etc.) across `vault`, `refinement`, `cartridges`, and `mcp-infra`.
   - Added fail-fast logic in `main.py` files to raise errors and crash immediately if `INTERNAL_API_KEY` is missing or still set to the `dev-secret-key`.
4. **MCP / Internal API Hardening**
   - Added the `verify_internal_api_key` dependency to all unprotected `/mcp/` endpoints in `console/app/main.py`.
5. **XSS Sanitization**
   - Implemented `esc()` wrapping for all frontend dynamic attributes rendering directly into `innerHTML` within `console/app/static/viewers/`.

### Riesgos Restantes:
- Airflow variables still require the environment secrets to be safely injected via Vault rather than plain text during initialization scripts.

| Component | Before | After |
| --------- | ------ | ----- |
| **Gold Datasets** | Vulnerable to cross-tenant data access | Strict RLS (default-deny) via parameterized queries |
| **DAG Templates** | Internal HTTP endpoints open | Enforced `X-Internal-Api-Key` for all webhook calls |
| **Secrets** | Hardcoded `minio123` / `postgres:postgres` | Sourced safely from ENV or Vault |
| **Internal MCP APIs** | Mixed authentication | `verify_internal_api_key` enforced universally |
| **Frontend UI** | Bare `innerHTML` injection risks | Standardized `esc()` sanitization wrapper |
