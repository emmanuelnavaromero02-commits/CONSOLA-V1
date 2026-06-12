# Backend / FastAPI Audit — console/ (OMEGA CONSOLA-BETA)

- Repo: `/home/user/CONSOLA-BETA`  branch `claude/magical-davinci-no3jmu`  HEAD `bab2a4f`  VERSION `1.45.68-beta`
- Service: `console` FastAPI monolith, entrypoint `console/app/main.py` (`uvicorn app.main:app`, port 8000), `console/Dockerfile:10`
- `main.py` = 7,554 lines. 21 routers in `console/app/routers/` + 13 v1 sub-routers in `console/app/routers/v1/`.
- Method: read-only code reading + grep/python. No servers/docker run. Evidence is `file:line`.

---

## 1) What's REAL and verified working by code-reading

### AUTH — JWT + session + refresh + blacklist (largely real)
- **Access token TTL = 15 min (real, configurable).** `console/app/services/jwt_auth.py:13` `DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES=15`; honored from `ACCESS_TOKEN_EXPIRE_MINUTES` (`jwt_auth.py:33-44`). Compose sets it to 15 (`infra/docker-compose.yml:216`, `infra/.env:ACCESS_TOKEN_EXPIRE_MINUTES=15`). HS256, claims include `sub/email/role/iat/exp/jti` with `jti=secrets.token_hex(16)` (`jwt_auth.py:48-60`). Output claim-presence validated (`jwt_auth.py:132-135`).
- **Refresh tokens (real).** SHA-256-hashed, stored in `refresh_tokens` table, 7-day lifetime, single-use w/ revoke (`console/app/services/auth.py:31,513-566`). `/auth/refresh` rotates: revoke old + issue new (`main.py:1840-1873`). Refresh cookie HttpOnly+Secure+Lax (`main.py:1773-1779`).
- **Blacklist enforced on every JWT request (real).** `verify_access_token_async` decodes then checks Redis blacklist by jti (`jwt_auth.py:76-94`); used by the dependency path (`dependencies.py:33-52`) and the middleware bearer fallback (`main.py:1693`). Logout revokes the jti with TTL=exp (`main.py:1885-1901`, `jwt_blacklist.py:77-92`).
- **Blacklist is FAIL-CLOSED in production (real).** `jwt_blacklist.is_revoked` returns `True` (deny) when Redis is unreachable and `_fail_closed_enabled()` (default = production) (`jwt_blacklist.py:94-118`, `32-44`). Override via `JWT_BLACKLIST_FAIL_CLOSED`.
- **Passwords (real).** bcrypt rounds=12 with SHA-256 pre-hash to dodge bcrypt 72-byte truncation (`auth.py:128-149`). MIN_PASSWORD_LENGTH=12 enforced on every setter (`auth.py:37,248,265,345`). Brute-force lockout: 5 failures / 15 min via `login_attempts` (`auth.py:393-406`).
- **Sessions (real).** Server-side random token in `user_sessions`, HttpOnly cookie, sliding window + absolute 12h cap that deletes the session server-side (`auth.py:21-30,449-492`). `cookie_secure()` defaults to secure unless `APP_ENV=development` (`auth.py:582-597`).
- **Bootstrap admin refuses placeholder passwords (real).** `bootstrap_admin.py:_PLACEHOLDER_PASSWORDS` rejects documented defaults (`bootstrap_admin.py:33-66`).

### RBAC — permission-based, consistently applied on API routers (real)
- 56-permission registry + role→permission map (`permissions.py:9-66,155-`). `require_permission(perm)` reads `request.state.user` (set by `auth_middleware`) and 401/403s (`permissions.py:322-333`). Effective perms include workspace+global role union (`permissions.py:293-307`).
- Routers apply `require_permission`/`require_authenticated`/`require_admin` per route AND many use router-level `dependencies=[...]`: cartridges, control_room, copilot* (router-level `copilot.use`), intelligence, marketplace, metrics (router-level `operations.read`), operations, security, settings, studio, freshness (router-level `require_authenticated`), all v1 routers. Sampled >40 routes; the API surface is broadly gated. Vault/settings reveal require `vault.secrets.reveal`/`settings.write` + CSRF (`v1/vault.py:65,154`; `settings.py:49-82`).
- Defense in depth: `auth_middleware` rejects unauthenticated API-like paths with 401 before the route runs (`main.py:1726-1732`), and gates `_RBAC_DEPENDENCY_PREFIXES` (`main.py:1573-1615`).

### CSRF — double-submit cookie, real and wired end-to-end
- `services/csrf.py` implements DSC: cookie `csrf_token` (not HttpOnly) must equal `X-CSRF-Token` header or `_csrf` body, constant-time compare, default-deny (`csrf.py:71-84`). `require_csrf` applied to virtually every mutating route (login, logout, refresh, activate, reset, all POST/PUT/DELETE in routers — verified ~ all mutating routes carry it except legitimate internal-key endpoints).
- Bearer-authed requests are intentionally CSRF-exempt (token isn't auto-attached) (`csrf.py:127-132`) — correct per OWASP.
- **Frontend uses it (real).** Compiled console-next client reads `csrf_token` cookie and sets `X-CSRF-Token` on every non-GET/HEAD `fetch` with `credentials:"include"` (`console/app/static/console-next/_next/static/chunks/0ldbrfd~17g9n.js:1`).

### API gateway / internal-service auth (real, strong)
- Outbound: `_hdr_for(server)` attaches `x-api-key` (per-pair `INTERNAL_API_KEY_CONSOLE_TO_<SERVER>`, prod refuses legacy fallback) + `x-internal-service: console` + request-id (`main.py:313-334`). Proxies to REFINEMENT (`/mcp/invoke`), VAULT, MCP_INFRA, WORKSPACE, AIRFLOW.
- Inbound: `verify_internal_api_key` requires whitelisted `x-internal-service` + matching per-pair key, constant-time; legacy shared key accepted only outside prod (`auth.py:600-624`). Prod startup refuses to boot if any per-pair key missing (`auth.py:84-96`).
- **x-security-context HMAC-SHA256 signing (real).** `sign_security_context` adds `_signed_at`,`_signature_version=hmac-sha256-v1`,`_signature` over canonical JSON; key from `SECURITY_CONTEXT_SIGNING_KEY` (≥32 chars, must differ from every transport key) (`security_context.py:37-63`). `build_security_context` is derived from the authenticated server-side user, never from LLM/tool args (`security_context.py:102-158`).
- **TTL = 300s (real, matches spec).** `_SIGNATURE_TTL_SECONDS=300`, +30s future skew (`security_context.py:19-20,87-90`). Downstream verifiers all hard-code 300s: `refinement/app/main.py:47,110`, `vault/app/main.py:68,124`, `mcp-infra/app/main.py:70,116`.
- ~29 `INTERNAL_API_KEY_<from>_TO_<to>` pairs configured in `infra/.env` (close to the ~30 expected).

### Error handling — does NOT leak stack traces / SQL (real)
- Global `Exception` handler returns `{"error":"Internal Error","request_id":...}` only (500), logs full trace server-side with redaction (`main.py:575-586`). HTTPException handler returns `{detail}` for API or a sanitized Spanish HTML status page for 403/404/503 (`main.py:557-564`).
- Structured JSON logging with aggressive secret redaction (Bearer/JWT/hex≥32/key=value/Authorization) applied to messages AND `exc_info` (`logging_config.py:33-132`). Upstream error bodies pass through `_redact()` before re-raising (`main.py:5770-5781`).

### Audit trail — `audit_events` writes, fail-closed option (real)
- `audit_service.record_event` durably inserts with dedup `ON CONFLICT DO NOTHING`; `critical=True` callers re-raise if the table is missing / insert fails (fail-closed) (`audit_service.py:47-165`). ~25 call sites across main.py, vault, users, cartridges, copilot*, intelligence, control_room, settings, studio, marketplace, agents.
- Verified audited actions include: `user.created/deleted/invited/reinvited/password_changed`, `password_reset.sent`, `vpn.reissued`, `vault.connection.{reveal,upsert,deleted}`, `vault.secret.{upsert,deleted}`, `settings.{reveal,rotate,update}`, `cartridge.{entity.run,test_connection,credentials.write,credentials.delete}`, `copilot.*`, `intelligence.run`, `explorer.object.{download,delete}`.

### Intelligence router — signals computed from GOLD data (real, NOT hardcoded)
- `intelligence.py` routes → `intelligence_engine.run_intelligence` → `intelligence/engine.py:run_intelligence` fetches rows via `query_intelligence_dataset_rows` which reads **workspace-scoped Gold tables** (`gold_<dataset>`) directly through asyncpg with RLS `set_config('app.tenant_id'/'app.workspace_id')`, then falls back to Refinement (`intelligence/gold_fetcher.py:79-151`).
- Signals are computed by `build_metric_artifacts` (moving-average baseline, deviation %, severity) over those rows using per-cartridge YAML contracts in `console/app/config/intelligence_contracts/*.yaml` (`intelligence/baseline.py:23-`). Contracts are metric *definitions* (dataset/field/threshold), not fake data. No hardcoded signal lists found.

### APP_ENV=production fail-closed behavior (real, multiple guards)
- Default env is **production** when unset (`security.py:7-21`).
- Fails to start in prod if missing: `INTERNAL_API_KEY` (or insecure) (`security.py:41-57`), per-pair internal keys (`auth.py:84-96`), `INTERNAL_API_KEY_CONSOLE_TO_<SERVER>` (`main.py:313-325`), `JWT_SECRET_KEY` (≥32, not insecure) (`jwt_auth.py:27-30`), `SECURITY_CONTEXT_SIGNING_KEY` (`security_context.py:37-44`), `ALLOWED_ORIGINS` (no localhost fallback, no empty, no `*` with credentials) (`main.py:589-643`).
- Cookies default Secure; rate-limit + blacklist default fail-closed in prod.

---

## 2) What's partial / suspicious

- **Anti-replay is freshness-only, NO nonce/jti replay cache.** The 300s TTL window is the *only* replay defense for `x-security-context`. There is no nonce/seen-signature cache in console signer or in refinement/vault/mcp-infra verifiers (grep for `nonce|replay|seen_signature|jti` in those files returns nothing). A captured signed context is fully replayable for up to 300s by anyone who can reach an internal endpoint with a valid transport key. The task brief calls this "TTL 300s anti-replay" — it is a freshness window, not true anti-replay. (P2)
- **`infra/.env` contains real-looking populated secrets** (JWT_SECRET_KEY, SECURITY_CONTEXT_SIGNING_KEY=`13bc33…`, INTERNAL_API_KEY_* etc.). It is NOT git-tracked (`git ls-files` errors), so this is a local working file, not committed — but its presence on disk is a handling smell. (P3 / verify it never gets committed)
- **`auth_middleware` swallows enrichment exceptions** — on workspace-enrichment failure for non-RBAC-prefixed paths it logs and proceeds with the un-enriched user (`main.py:1678-1684,1712-1718`). RBAC-prefixed paths fail closed (403/401); non-prefixed authenticated pages could render with missing `allowed_cartridges`. Low impact because data routes are RBAC-prefixed. (P3)
- **v1 routers are a parallel re-binding of main.py handlers** (`routers/v1/*` rebind `app.main` functions via `_bind_to_main`, `routers/v1/__init__.py` exports `ROUTERS`). They are imported by tests but **`ROUTERS` is never `include_router`'d in `main.py`** (only `intelligence_router.v1_router` and the 21 first-class routers are mounted, `main.py:7473-7495`). So the "unprotected" routes flagged by static analysis inside `routers/v1/auth.py`, `system.py`, `misc.py` are the public auth/health endpoints duplicated for the test harness — not an extra live attack surface. Confirmed they mirror the same `_AUTH_PUBLIC_EXACT` allowlist. (informational)
- **`require_csrf` reads the request body to find `_csrf`** when header absent (`csrf.py:145-153`); fine, but means a malformed body silently degrades to header-only — acceptable since default-deny still applies.
- **`/api/data/{dataset}/query` (POST) has no `require_csrf`** but is `require_authenticated` + builds SQL server-side from a filter dict (no raw SQL) (`main.py:3857-3870`). State-shaped POST without CSRF; low risk because it's read-only and not state-mutating, but inconsistent with the rest of the codebase. (P3)

---

## 3) Findings table

| # | Sev | Area | Finding | Evidence (file:line) | Estado | Fix |
|---|-----|------|---------|----------------------|--------|-----|
| F1 | P2 | Gateway / replay | `x-security-context` "anti-replay" is a 300s freshness window only — no nonce/jti replay cache in signer or any verifier. Signed context replayable ≤300s. | `security_context.py:19,87-90`; `refinement/app/main.py:47,110`; `vault/app/main.py:68,124`; `mcp-infra/app/main.py:70,116` | confirmado | Add a short-lived Redis SET of consumed signatures (key = HMAC, TTL=300s) checked in each verifier; or embed+enforce a one-time nonce. |
| F2 | P3 | Secrets hygiene | `infra/.env` holds real populated secrets on disk (signing key, JWT secret, internal keys). Not git-tracked. | `infra/.env:7` (+29 INTERNAL_API_KEY lines) | confirmado | Confirm `.gitignore` covers `infra/.env`; rotate the on-disk values; keep only `.env.example` in repo. |
| F3 | P3 | Auth middleware | Workspace-enrichment failure on non-RBAC-prefixed authenticated paths proceeds with un-enriched user instead of failing closed. | `main.py:1678-1684,1712-1718` | confirmado | Fail closed (403) for any authenticated page when enrichment raises, not only RBAC-prefixed paths. |
| F4 | P3 | CSRF consistency | `POST /api/data/{dataset}/query` lacks `require_csrf` (read-only, server-built SQL, so low risk) — inconsistent with the rest. | `main.py:3857` | confirmado | Add `Depends(require_csrf)` for parity, or document the read-only exemption. |
| F5 | P3 | Dashboard scope | `/api/dashboard/kpis` falls back to the FULL built-in cartridge catalog when no scoped Vault connection exists (fresh/demo stacks), so KPIs can reflect catalog rather than the tenant's real connections. | `routers/dashboard.py:287-301` | confirmado | Gate the fallback behind a dev/demo flag; in prod show empty/zero state. |
| F6 | P3 | Audit gaps | Many mutating routes have no INLINE `record_event`; auditing is delegated to service-layer calls. Some genuinely lack audit (e.g. `/api/datasets/save`, `/datasets/{name}/refresh`, `/api/bronze/query`, RAG ingest/reindex, catalog writes). | `main.py:2763,2862,2938,5603,5612,5731,5736` | sospecha | Confirm service-layer audit for each; add `record_event` for dataset save/refresh, bronze query, RAG ingest/reindex, catalog mutations. |
| F7 | P3 | Info disclosure | `/api/system/info` (auth required) discloses `app_env`, `dev_mode`, `rce_tools_enabled`, `dag_deploy_enabled` to any authenticated user. | `main.py:2382-2404` | confirmado | Acceptable for ops UI; consider gating behind `operations.read`. |
| F8 | P2 | RBAC (verify) | `auth_middleware` lets unauthenticated requests to RBAC-prefixed paths fall through to the route (relying on the route's own dep) rather than 401ing in the middleware. | `main.py:1726-1727` | sospecha | Correct as designed (route dep 401s), but the dual path is fragile — ensure EVERY RBAC-prefixed route actually declares a dep; add a test asserting it. |

No P0/P1 issues found in the audited backend areas. The auth/RBAC/CSRF/gateway core is genuinely implemented, not stubbed.

---

## 4) Endpoint inventory summary

- **Total live HTTP routes: ~209 mounted.** `main.py` declares 141 `@app.*` decorators (139 unique route fns; 2 are bare-path continuations); 21 first-class routers add ~68 more via `include_router` (`main.py:7473-7495`). v1 `ROUTERS` tuple is NOT mounted in main (test-harness only); `intelligence v1_router` IS mounted.
- Router line counts: `studio.py` 2617, `control_room.py` 663, `copilot_advanced.py` 783, `copilot_workflows.py` 545, `cartridges.py` 440, `metrics.py` 420, `security.py` 382, `copilot_drafts.py` 366, `copilot.py` 334, `dashboard.py` 301, `marketplace.py` 241, `intelligence.py` 208, `pages.py` 524.

Main functional areas:
1. **Auth/session/IAM** — login/logout/refresh/activate/reset/me, admin user CRUD + invite + VPN reissue (`main.py:1784-2095`, `7000-7470`).
2. **Security Center** — sessions, audit, permissions, login-attempts (`routers/security.py`).
3. **Settings + Vault proxy** — masked/reveal/rotate, vault connections + secrets (`routers/settings*.py`, `v1/vault.py`, `main.py:5380-5560`).
4. **Cartridges + Marketplace** — list/schema/entities/extract/credentials, entitlements (`routers/cartridges.py`, `routers/marketplace.py`).
5. **Studio / Pipelines** — DAG graph/source/deploy, entities, silver/gold preview, superset, RAG, semantic, bronze query (`routers/studio.py`, `main.py:2700-5000`).
6. **Control Room + Intelligence** — summary/dashboard/alerts/anomalies, signals from Gold, outcomes (`routers/control_room.py`, `routers/intelligence.py`).
7. **Copilot** — conversations, messages, drafts, memory, workflows, goals/lessons/watchdogs (`routers/copilot*.py`).
8. **Agents** — CRUD + invoke + scheduled (internal-token) + stream (`main.py:5090-5300`, `v1/agents.py`).
9. **MCP** — internal-only `/mcp` (`routers/mcp.py`, key-authed) + admin `/mcp` public (`routers/mcp_public.py`) + monitoring/studio_ops MCP tools.
10. **Dashboard / Metrics / Operations / Onboarding / Freshness / Decisions / Datasets / Data / Jobs**.

---

## 5) Smoke smells found

- **Hardcoded cartridge catalog used as a fallback "real" surface**: `_CARTRIDGES = ("replicon","hubspot","sap_hcm","sap_s4hana","sap_successfactors")` returned by `/api/dashboard/kpis` when no Vault connection exists (`routers/dashboard.py:39,292`) and `_OPERATIONAL_CARTRIDGES` in operations (`routers/operations.py:28`). KPIs/health can present catalog data as if it were the tenant's connected reality on fresh/demo installs.
- **`/api/config` returns only URLs/bucket name, no secrets** — verified clean (`main.py:2366-2380`).
- **No "fixed success" stub endpoints found** in the audited surface. The only `NotImplementedError`s are legitimate adapter-registry guards (`services/control_room/core.py:160`, `services/adapters/factory.py:55`) that raise when no write-back adapter is registered — not silent fake-success.
- **Stub/placeholder DETECTION exists as a feature** (Control Room marks datasets `stub`/`partial` as not-operational, `services/control_room/state.py:393`) — this is real readiness gating, not a smell.
- **`pass` on logout-revoke failure** is intentional (already-expired JWT) (`main.py:1898-1901`).
- **`/api/data/{dataset}/query` POST without CSRF** (read-only) — minor inconsistency (see F4).
- **Audit-by-side-effect**: mutating routes rely on service-layer `record_event` rather than inline calls; a handful of data/RAG/catalog mutations appear to have no audit at all (see F6).
- **v1 router dead-ish code**: `routers/v1/*` re-bind main handlers and export a `ROUTERS` tuple that `main.py` never mounts (only used by tests) — maintenance hazard / drift risk.
