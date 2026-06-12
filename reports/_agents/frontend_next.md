# Frontend/Next.js Audit — OMEGA/CONSOLA-BETA

- Commit: `bab2a4f` (main HEAD), VERSION `1.45.68-beta`. Audit date: 2026-06-12.
- Scope: `console-next/` (Next.js static export), `console/app/static/` (served output + legacy HTML/JS), `console/app/routers/pages.py` and route cross-check against the FastAPI backend.
- Method: every claim below carries file:line evidence. No sources modified. Build/lint/typecheck/tests executed locally (results in §6).

---

## 0. Stack & build pipeline — claims verified

| Claim | Verdict | Evidence |
|---|---|---|
| Next.js 16 | **TRUE** — `next: "16.2.6"` | `console-next/package.json:23` (React 18.3, not 19: `package.json:24-25`) |
| `output: "export"` static | **TRUE** | `console-next/next.config.mjs:4`; `assetPrefix: "/static/console-next"` (`:6`), fixed `generateBuildId: "console-next-static"` (`:8`) |
| Served same-origin from `/static` | **TRUE** | `console/app/main.py:679` `app.mount("/static", StaticFiles(...))`; pages served as `FileResponse` of `static/console-next/*/index.html` via `_console_next_response` (`console/app/routers/pages.py:82-89`) with per-file CSP incl. sha256 of inline scripts (`pages.py:49-74`) |
| Build lands in console | **TRUE** | `export:copy` script: build → `cp -R out ../console/app/static/console-next` → **`rm -rf .../console-next/studio`** → strip trailing whitespace (`console-next/package.json:9`); drift-guard `verify:static` = export + `git diff --exit-code` (`package.json:10`) |
| Committed export fresh? | **TRUE — no drift** | I rebuilt (`npm run build`, exit 0) and diffed `out/` (after replaying export:copy transforms) against committed `console/app/static/console-next/`: **only delta is an empty `_next/console-next-static/` dir** (git can't track empty dirs). Export last touched by `27ac92c` (parent of HEAD). |
| Mock-service-workers / faker in prod | **NONE** | `msw` appears only as an *unfulfilled peerDependency* of vitest in `package-lock.json:2420`; zero `msw`/`faker` imports under `src/` (grep). `public/` is empty. Vitest runs in `environment: "node"` with no setupFiles (`vitest.config.ts:11-13`). |

API client: single wrapper `console-next/src/lib/api.ts` — relative paths (same-origin), `credentials: "include"` (`api.ts:98`), CSRF from `csrf_token` cookie injected as `X-CSRF-Token` on non-GET (`api.ts:69-72`), `X-Request-ID` propagation (`api.ts:59-61`), 30 s timeout (`api.ts:26`), typed error mapping 401/403/404/5xx (`api.ts:116-127`). No axios, no second client.

**Workspace :8001 cross-origin: does not happen from the browser.** Chat is proxied same-origin by the console: `POST /workspace/chat`, `/workspace/chat/stream`, `/workspace/chat/refresh-context` → `httpx` to `WORKSPACE_INTERNAL_URL` (default `http://workspace:8001`) with cookie/CSRF passthrough and audit events (`pages.py:25,120-187,418-475`). Apps HTML is likewise proxied: `/apps/{name}` and `/apps/{name}/content` → workspace service (`main.py:3338,3347,3300-3335`). `next dev --port 8001` (`package.json:7`) collides with the workspace service's port number — dev-only nuisance.

---

## 1. Route map & which frontend serves it (TWO frontends confirmed)

**Canonical = console-next** for ~30 routes; **legacy HTML survives on exactly 5 surfaces**; 15 legacy HTML pages are dead-but-shipped.

### Served by console-next export (FastAPI route → static HTML)
| URL | Gate | Evidence |
|---|---|---|
| `/login` | — | `main.py:1792` |
| `/dashboard` (and `/` → 307) | authenticated | `pages.py:200-202,190-192` |
| `/control-room` | `workspace.access` | `pages.py:210-217` |
| `/monitor` | `monitor.read` | `pages.py:195-197` |
| `/copilot`, `/copilot/actions`, `/copilot/knowledge` (admin), `/copilot/tokens` | `copilot.use` / `mcp.registry.read`+admin | `pages.py:478-519` |
| `/my-access` (+`/mis-accesos` alias), `/me` | authenticated | `pages.py:225-234`, `main.py:2410` |
| `/settings` | `settings.read`+admin | `pages.py:249-254` |
| `/operations{,/users,/audit,/vault,/workflows,/metrics}` | per-permission (+admin on some) | `pages.py:257-310` |
| `/security` | `security.audit.read`+admin | `pages.py:205-207` |
| `/data/catalog`, `/data/lineage`, `/data/bronze` | `datasets.read`/`datasets.write`+admin | `pages.py:350-365` |
| `/viewer` (+ `/viewer/jobs|schema|datasets|semantic` 307 redirects) | type-dependent permission | `pages.py:522-524,315-337,92-108` |
| `/apps-gallery`, `/cartridges`, `/cartridges/viewer` | `apps.read` / `cartridges.read` | `pages.py:383-402` |
| `/marketplace`, `/customer/cartridges`, `/admin/installations`, `/admin/licenses` | marketplace perms | `routers/marketplace.py:17,22,32,42` |
| `/explorer`, `/agents`, `/decisions` | per-permission | `main.py:5074,5096,6597` |

### Still legacy HTML (the real "second frontend")
| URL | File | Evidence |
|---|---|---|
| **`/workspace`** | `static/workspace.html` + `static/js/workspace.js` (851 lines) | `pages.py:408-415`; JS calls `/auth/me`, `/api/apps`, `/workspace/chat/stream`, `/api/users`, `/api/decisions` CRUD, `/api/datasets` (`js/workspace.js:37,62,269,355,385-783`) — all real endpoints |
| **`/studio`** | `static/studio.html` + `static/js/studio/*` (12 modules) | `main.py:5051`; scripts at `studio.html` ref `js/studio/main.js`, `legacy-bootstrap.js`, `action-bridge.js` |
| `/activate`, `/forgot-password`, `/reset-password` | `activate.html`, `forgot_password.html`, `reset_password.html` | `main.py:1997,2038,2059` |

### Dead legacy pages — shipped but unreachable
Direct requests to `/static/*.html` are 404'd by middleware (`main.py:1609-1611` `_is_direct_static_html_request` → `main.py:1630-1631` returns 404), and no route serves them: `admin_users.html, agents.html, apps_gallery.html, cartridges.html, copilot.html, decisions.html, explorer.html, iam.html, index.html, login.html, me.html, monitor.html, my_access.html, security.html, settings.html` plus the whole `static/viewers/*.html` set (9 files) and `static/apps/pnl_ejecutivo.html`. Dead JS riding along: `js/home/*` (only ref: dead `index.html`), `js/copilot_fab.js` (zero refs), `js/i18n/*` (only refs: dead `iam.html`/`security.html`), and the page-paired `monitor.js`, `copilot.js`, `marketplace.js`, etc. `/iam` now 307s to `/operations/users` (`pages.py:241-246`).

---

## 2. Real connected UI surfaces (verified fetch → backend route exists)

Every interactive surface below was traced from component → `api.*` call → existing FastAPI route (matrix in §5).

| Surface | Frontend evidence | Backend route exists |
|---|---|---|
| **Control Room** (3,498-line page) | load: `lib/control-room/client.ts:13-63` (dashboard/lessons/thresholds/gold-kpis/semantic); mutations: step `page.tsx:1344`, option `:1369`, decision `:1383`, action-preview `:1397`, dry-run `:1413`, **execute `:1436`**, auto-run `:1454`, **approve `:1467`**, dismiss `:1482`, control `:1497`, lessons `:1517,1554`, intelligence outcome `:1532`, alerts ack/snooze/assign/false-positive `:1575`, thresholds PATCH `:1312`. Errors surface (`page.tsx:903-914`), 401 → `/login` redirect (`:905-907`). **No fallback fake data anywhere.** | `routers/control_room.py:111,651,606,116,244,386,366,409,432,475,455,535,560,317,271,293,137-203`; `routers/intelligence.py:193`; contract test `page.contract.test.ts:33-50` even asserts the source contains the endpoints and does NOT contain `"mock"` |
| **Copilot chat** | conversations/messages/stream/approve: `lib/copilot/client.ts:54-200` incl. SSE reader `:113-187`; approval gate posts real approve (`components/workspace/ChatLayout.tsx:281-298`) | `routers/copilot.py:79,117,127,162` |
| Copilot memory/drafts/workflows/advanced | `client.ts:206-242,253-259,265-278,284-360` | `copilot_memory.py:38,78,108,152`; `copilot_drafts.py:264`; `copilot_workflows.py:80,125,161,179,200,447`; `copilot_advanced.py:201,279,349,528,540,601,716` |
| Copilot knowledge (RAG admin) | ingest/reindex/ask/search/sources `copilot/knowledge/page.tsx:118-187` | `main.py:5566-5622` |
| Copilot tokens | GET/PUT `/api/copilot/llm-key` `copilot/tokens/page.tsx:57,65` | `main.py:2643` |
| **Dashboard** | KPIs poll 30 s `lib/hooks/useKpis.ts:31`; briefing poll + dismiss `lib/copilot/useBriefing.ts:43,50`; freshness rows derived from payload (`dashboard/page.tsx:30-36`) | `routers/dashboard.py:280`; `routers/copilot.py:288,304` |
| **Monitor** | jobs/logs/pipeline/freshness/semantic/datasets/vault `lib/monitor/client.ts:19-110`; extract buttons POST real (`components/monitor/PipelineTable.tsx:41,58`) | `main.py:2770-2778,3960,4479,4552,2926,2831,5357,5487`; `routers/freshness.py:107,112` |
| **Operations** users/audit/vault/workflows/metrics | `lib/operations/client.ts:25-196` incl. send-reset `:50`, vault reveal `:79,118`, workflow execute/cancel `:168,176` | `main.py:7029,7440,5387-5532`; `routers/security.py:193`; `copilot_workflows.py:179-203`; `metrics.py:109`; `operations.py:72` |
| **Marketplace / admin licenses** | request/retry/approve/pause/revoke/reactivate/access `lib/marketplace.ts:109-163` (actions typed `:18`) | `routers/marketplace.py:45,69,77,91,105,124,150,163,189,203,217,231` — all four admin actions exist |
| Cartridges wizard + viewer | list/schema/credentials/test/activate `lib/cartridges.ts:111-201` | `routers/cartridges.py:150,157,250,338,400` |
| Settings / Security / Me | `lib/admin-surfaces.ts:241-415` (sessions, settings reveal/rotate/PUT, me, change-password) | `routers/settings.py:36-74` (PUT `:60`); `security.py:136,168,193`; `main.py:2413,2418,2598` |
| Decisions / Explorer / Agents / Apps | `admin-surfaces.ts:271-395` | `main.py:6676-6862,3090-3184,5099-5295,3750` |
| Data catalog/lineage/bronze | `lib/data/client.ts:32-65` | `main.py:5707,5731,5736,3217,2938,3769` |
| Login/logout | CSRF dance `lib/auth-flow.ts` (GET `/login` seeds cookie → POST `/auth/login` w/ X-CSRF-Token); logout `components/auth/LogoutButton.tsx:24` | `main.py:1827,1876,1910` |
| Legacy `/workspace` | `js/workspace.js` calls listed in §1 | all exist (`main.py:3750,6676,6862,2734`; `pages.py:458`) |

Tests: 21 files / 79 tests pass, including a control-room **contract test** that pins endpoint wiring and forbids the string "mock" in the page (`src/app/(shell)/control-room/page.contract.test.ts:33-50`).

---

## 3. Disconnected / mock / fake / dead UI table

The smoke hunt found **no fake execute/approve buttons and no hardcoded metric arrays rendered as live data**. What it did find:

| # | Component (file:line) | What it pretends / promises | What it actually does | Class |
|---|---|---|---|---|
| 1 | `console-next/src/app/(shell)/studio/page.tsx:35-61` (`CARTRIDGES` hardcoded array of 5) + banner `:88-94` | "Studio — Configura DAGs, refinamiento…" | Slim launcher only: renders 5 hardcoded cards (freshness overlay from real `/api/dashboard/kpis`), each links out to **legacy** `/studio?cartridge=…` (`:136-138`). No Studio function in Next. Honest banner shown. | Intentional stub (documented `:10-34`) |
| 2 | Same file, comment `:13-20`: "Every /api/studio/* endpoint is a v1.44.3.3 stub returning {stub:true,…}" + `main.py:7495` comment "(stub)" | Claims backend Studio API is fake | **STALE/FALSE**: `routers/studio.py` is 2,618 lines of real implementation ("backed by real platform services", `studio.py:1-6`) with 17 routed endpoints incl. `dag-deploy` (`:2216`), `entities/upload` (`:2339`), `superset/dataset` (`:2488`), live OData/OpenAPI/SQL introspection (`:621-815`) | Stale doc — misleads auditors/devs |
| 3 | Next Studio page is **built then deleted from the export** — `package.json:9` `rm -rf ../console/app/static/console-next/studio`; committed export has no `studio/` dir; `/studio` serves legacy HTML (`main.py:5051`) | Sidebar "Studio" looks like a Next page | In production the Next Studio page (and its launcher cards) is **unreachable dead code**; users land on legacy `studio.html` | Dead component |
| 4 | `operations/page.tsx:84-97` `PENDING_MODULES` ("Workspaces", "Settings (por workspace)") | Tiles in Operations overview | Explicit "Próximamente" badges linking to `legacyConsoleUrl("/iam"|"/settings")` — but `/iam` 307s to the **Next** users page (`pages.py:246`) and `/settings` serves the **Next** settings page (`pages.py:254`); the "classic console" label is wrong | Honest pending + mislabeled links |
| 5 | Hardcoded domain catalogs: cartridge lists in `studio/page.tsx:35-61`, `cartridges/page.tsx:9-33` (`META`), `monitor/page.tsx` (`CARTRIDGES`), `operations/VaultConnectionsTable.tsx:28-34`, `data/catalog/page.tsx:20-32` (`SF_GOLD_DATASETS`), `viewer/page.tsx:61,75-84` | Cartridge/dataset pickers | Filter/decorate **real** API data; adding a cartridge requires editing ≥5 components (drift hazard, not smoke) | Semantic constants |
| 6 | Hardcoded prompt templates: `ChatLayout.tsx:48-103` (`COMMAND_CATALOG` incl. "reporte_mensual", "cash_position"…), `SuggestedPrompts.tsx:27-50`, `CopilotActionsConsole.tsx:38-42` (`QUICK_GOALS`) | Slash commands / suggested prompts | Templates that send the prompt to the **real** chat API (`ChatLayout.tsx:328`); whether the LLM can actually fulfill "aging_report" etc. is backend territory | Acceptable UX |
| 7 | `SuccessFactorsGoldPanel.tsx:415` hardcodes `FEMSA · SuccessFactors` | Generic product UI | Customer name baked into the component | Branding hardcode |
| 8 | Backend-only feature with **zero UI**: onboarding API `routers/onboarding.py:32,50` (`/api/system/onboarding/state|complete`, "v1.44.1 Tarea F") | — | No caller in console-next nor legacy JS; only `tests-e2e/specs/07-*.spec.ts` touch it | Orphan API |
| 9 | Intelligence: `routers/intelligence.py:90-197` exposes ~8 endpoint pairs (`/api/intelligence/*` + `/api/v1/intelligence/*`) | "Intelligence" surface | Only `signals/{id}/outcome` is called by UI (`control-room/page.tsx:1532`); no Intelligence page exists in either frontend; rest consumed server-side/tests | Mostly headless |
| 10 | Dead legacy assets (§1): 15 page HTML + 9 viewer HTML + ≥10 JS bundles incl. `js/home/*`, `js/i18n/*`, `copilot_fab.js` | Look like live pages in the repo | Unreachable (middleware 404 `main.py:1609-1631`; no serving route) | Dead code shipped |

Grep for `mock|fake|stub|placeholder|hardcode|demo|TODO` over `console-next/src` non-test files: **zero production hits** (only `vi.mock` in `*.test.ts*`).

---

## 4. Findings P0–P3 (estado = confirmed unless noted)

**P0 — none.** No button that fakes a backend action, no fabricated metrics rendered as live, no MSW/fixtures in the production bundle, no missing backend route behind any UI action (113/113 paths resolve — §5).

**P1-1 — Two frontends in production; Workspace & Studio are still the legacy stack.** `/workspace` = `workspace.html` + 851-line vanilla JS (`pages.py:413`), `/studio` = `studio.html` + 12 legacy JS modules (`main.py:5051`). The Next "Espacio de Trabajo" sidebar entry (`AppSidebar.tsx:67`) sends users from the new shell into the old UI. Estado: confirmed, by design ("migration"), but it is the platform's *core* surfaces that remain unmigrated.

**P1-2 — Next Studio page is dead code with a false comment.** Built, then deleted from the export by `export:copy` (`console-next/package.json:9`); served `/studio` is legacy. Its header comment (and `main.py:7495`) still claims `/api/studio/*` are stubs, while `routers/studio.py` is a real 2,618-line implementation. Anyone trusting the code comments gets the architecture backwards. Estado: confirmed (comment stale since studio.py graduated).

**P1-3 — ~35 dead legacy files shipped in the image** (15 page HTML, 9 viewer HTML, ≥10 JS incl. whole `js/home/`, `js/i18n/`). Unreachable but a maintenance/drift/security-review burden, and they make the "legacy vs next" boundary illegible. Estado: confirmed via middleware 404 + zero serving routes.

**P2-1 — Onboarding API has no UI** (`onboarding.py:32,50`): feature exists backend-only; nothing in any frontend calls it. Estado: confirmed orphan.

**P2-2 — Intelligence has no surface**: only the outcome POST is wired from Control Room; no page lists `/api/intelligence/signals|readiness|external/sources`. Estado: confirmed headless (may be intentional — consumed via control-room service layer).

**P2-3 — Cartridge catalog duplicated in ≥5 frontend constants** (table row 5). New cartridge = N manual edits; `monitor`, `vault`, `catalog`, `studio`, `viewer` lists can silently diverge from `/api/cartridges`. Estado: confirmed hazard, not smoke.

**P3-1 — `FEMSA` branding hardcoded** in `SuccessFactorsGoldPanel.tsx:415`. **P3-2 — Operations "pending" tiles mislabel their targets** (`operations/page.tsx:84-97` → links resolve to Next pages, not a classic console). **P3-3 — dev port clash**: `next dev --port 8001` vs workspace service :8001 (`package.json:7` vs `pages.py:25`). **P3-4 — `cartridges/page.tsx:9-33` META descriptions** shadow whatever the API returns for known IDs.

---

## 5. API path cross-check matrix (frontend → backend exists?)

113 distinct frontend-called paths extracted from `console-next/src` (non-test) + legacy `workspace.js`; each grep-verified against `main.py` + `routers/*` (511 route decorators inventoried, prefixes resolved). **Result: 113/113 exist. 0 missing.**

| Frontend path (method) | Caller | Backend | Exists |
|---|---|---|---|
| GET /api/control-room/dashboard | control-room/client.ts:14 | control_room.py:111 | YES |
| GET /api/control-room/sap-successfactors/gold-kpis | client.ts:17 | control_room.py:116 | YES |
| POST /api/control-room/items/{id}/execute | page.tsx:1436 | control_room.py:475 | YES |
| POST /api/control-room/items/{id}/approve | page.tsx:1467 | control_room.py:535 | YES |
| POST /api/control-room/alerts/{id}/{ack,snooze,assign,false-positive} | page.tsx:1575 (ops enum :1564-1569) | control_room.py:137,159,181,203 | YES (all 4) |
| PATCH /api/control-room/thresholds | page.tsx:1312 | control_room.py:606→623 | YES |
| POST /api/intelligence/signals/{id}/outcome | page.tsx:1532 | intelligence.py:193 | YES |
| POST /api/copilot/conversations · /{id}/messages · GET chat/{id}/stream | copilot/client.ts:64,86,119 | copilot.py:79,127,162 | YES |
| GET/POST /api/copilot/briefing · /{id}/dismiss · /v2 | useBriefing.ts:43,50; client.ts:314 | copilot.py:288,304; copilot_advanced.py:601 | YES |
| /api/copilot/memory ("",fact,fact/{id},preference/{key}) | client.ts:207-239 | copilot_memory.py:78,38,108,152 | YES |
| POST /api/copilot/drafts/generate | client.ts:255 | copilot_drafts.py:264 | YES |
| /api/copilot/workflow ("",{id},{id}/plan,{id}/execute,{id}/cancel) | operations/client.ts:142-176 | copilot_workflows.py:161,125,447,200,179 | YES |
| /api/copilot/goals · /{id}/diagnose · lessons · watchdogs(+match) · ask-with-context | client.ts:286-356 | copilot_advanced.py:201,279,349,528,540,716 | YES |
| GET/PUT /api/copilot/llm-key | tokens/page.tsx:57,65 | main.py:2643,2654 | YES |
| GET /api/dashboard/kpis | useKpis.ts:31 | dashboard.py:280 | YES |
| GET /api/jobs · /{id} · /{id}/logs | monitor/client.ts:19-30 | main.py:2770,2774,2778 | YES |
| GET /api/pipeline · POST …/{c}/{e}/extract · …/{c}/extract_all | client.ts:37; PipelineTable.tsx:41,58 | main.py:3960,4479,4552 | YES |
| GET /api/freshness/{cartridge} | client.ts:44 | freshness.py:107 | YES |
| GET /api/semantic · /api/schema · /api/sources · /api/datasets/{n}/detail|lineage | client.ts:51-94 | main.py:5682,2831,2850,2926,2980 | YES |
| /api/vault/connections/{c} (+/{conn} PUT/DELETE, /reveal) | operations/client.ts:69-101 | main.py:5357,5421,5448,5387 | YES |
| /api/vault/secrets/{s} (+/{k} PUT/DELETE, /reveal) | client.ts:107-136 | main.py:5487,5510,5532,5499 | YES |
| /api/admin/users ("",POST,PATCH/{id},DELETE/{id},/{id}/send-reset) | client.ts:25-50 | main.py:7029,7079,7155,7240,7440 | YES |
| GET /security/audit · /security/sessions (+DELETE) | client.ts:57; admin-surfaces.ts:241 | security.py:193,136,168 | YES |
| GET /api/metrics/operational · /api/operations/health | client.ts:185,196 | metrics.py:109; operations.py:72 | YES |
| /api/settings ("",/{key} GET/PUT,/reveal,/rotate) | admin-surfaces.ts:251-266 | settings.py:36,41,60,49,74 | YES |
| /api/marketplace/products (+/{id}/request,/{id}/activate) · installations/{id}/retry | marketplace.ts:109-128 | marketplace.py:45,77,91,105 | YES |
| /api/admin/installations ("",/{id}/approve|pause|revoke|reactivate,/access,/access/{uid}) | marketplace.ts:135-163 | marketplace.py:124,189,203,217,231,150,163 | YES (all 4 actions) |
| GET /api/customer/cartridges | marketplace.ts:114 | marketplace.py:69 | YES |
| /api/cartridges ("",/{id}/connector_schema,/credentials POST+DELETE,/test_connection) | cartridges.ts:111-194 | cartridges.py:150,157,338,400,250 | YES |
| /api/catalog ("",entries,relationships) · /api/lineage · /api/bronze/query · /api/data/{dataset} | data/client.ts:32-65 | main.py:5707,5731,5736,3217,2938,3769 | YES |
| /api/decisions ("",/{id} GET/PATCH/DELETE,/{id}/actions) | admin-surfaces.ts:312-344 | main.py:6676,6724,6748(patch),6800(delete),6820 | YES |
| /api/agents ("",_tool-catalog,/{id} GET/PUT/DELETE,/{id}/runs,/{id}/invoke) · /api/agent-runs/{id} | admin-surfaces.ts:351-395 | main.py:5099,5109,5133,5143,5160,5290,5175,5295 | YES |
| /api/explorer/buckets · list · download · object(DELETE) | admin-surfaces.ts:280-308 | main.py:3090,3104,3149,3184 | YES |
| /api/apps · /{name}(DELETE) | admin-surfaces.ts:271-276 | main.py:3750,3920 | YES |
| /api/me · /access · /change-password | admin-surfaces.ts:402-415 | main.py:2413,2418,2598 | YES |
| /auth/login · /auth/logout · /auth/me | auth-flow.ts; LogoutButton.tsx:24 | main.py:1827,1876,1910 | YES |
| POST /workspace/chat/stream (legacy js + proxy) | js/workspace.js:269 | pages.py:458 → workspace:8001 | YES (proxied) |
| GET /api/users · /api/config · /api/datasets (legacy workspace.js:355,53,631) | legacy | main.py:6862, 2710(config), 2734 | YES |

(Paths like `/api/example`, `/api/slow`, `/api/cartridges/sap%20hcm/...` appear only in `*.test.ts` fixtures — excluded.)

---

## 6. lint / typecheck / test / build results

Environment: `npm ci` (clean install, exit 0), then:

| Command | Result |
|---|---|
| `npm run typecheck` (`tsc --noEmit`) | **PASS** — 0 errors |
| `npm run lint` (`eslint .`, flat config) | **PASS** — 0 warnings/errors |
| `npm run test` (`vitest run`) | **PASS — 21 files, 79/79 tests**, 3.4 s |
| `npm run build` (`next build`, Next 16.2.6) | **PASS**; rebuilt `out/` matches committed `console/app/static/console-next/` byte-for-byte after export:copy transforms (sole delta: empty buildId dir untrackable by git) — `verify:static` would pass |

Dev server not run; docker not used; no source files modified (git status clean except this `reports/` dir).

---

## 7. Bottom line

The Next.js frontend is **genuinely wired**: every button that claims to execute/approve/save hits a real same-origin FastAPI route, all 113 called paths exist, errors are surfaced instead of papered over with fallback data, and the committed static export is in sync with source. The smoke is **architectural, not behavioral**: a second (legacy) frontend still owns the two most important surfaces (Workspace, Studio), the Next Studio replacement is dead code deleted from its own export while its comments misdescribe the backend as stubbed, ~35 dead legacy files ship in the image, and two backend feature families (onboarding, most of intelligence) have no UI at all.
