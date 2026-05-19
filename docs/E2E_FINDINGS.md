# E2E Findings — v1.44.3.2.1

This document is the **post-run triage form** for the deep
Playwright E2E suite shipped in v1.44.3.2.1 (200+ tests).
Populate it after running `make e2e` on your Mac against a
booted stack; the contents drive the v1.44.3.3 bug-fix sprint.

The suite under `tests-e2e/specs/` is **detection-only** — every
spec is meant to fail loudly when the corresponding surface is
broken. The findings below capture the post-detection state with
**severity buckets** that map to fix-card priority.

---

## How to populate

1. Boot the stack:
   ```bash
   docker compose -f infra/docker-compose.yml --profile sap up -d --build
   sleep 240
   ```

2. Make sure `tests-e2e/.env` carries the verified credentials
   (`emmanuel@local.ai` / `Admin123!`):
   ```bash
   cp tests-e2e/.env.example tests-e2e/.env
   ```

3. Run the suite:
   ```bash
   make e2e
   ```

4. Parse the JSON report into a categorised digest:
   ```bash
   bash scripts/e2e-report-summary.sh >> docs/E2E_FINDINGS.md
   ```

5. Triage each failure into a bucket below (`🔴 / 🟡 / 🟢 / 🔵`).

---

## Severity buckets

| Icon | Bucket | Definition | Default heuristic |
|---|---|---|---|
| 🔴 | **Críticos** | Security regressions, auth gate bypass, MCP cartridge crashes, user-reported BUGS already pinned in `05-studio*.spec.ts` | API auth gate, MCP cartridges, studio user-reported bugs |
| 🟡 | **Altos** | Primary user flows broken (login, dashboard, cartridges form, copilot chat) | Next.js dashboard, cartridges, login |
| 🟢 | **Medios** | Legacy admin pages (users / audit / monitor / etc.) + API contract drifts | Legacy HTML, API endpoint drifts |
| 🔵 | **Bajos** | UX polish, mobile responsive, dark mode, perf budget, deferred copilot UI | Mobile / a11y / perf, deferred copilot |

`scripts/e2e-report-summary.sh` applies these defaults; rewrite
the ranking as needed once the first real run surfaces what's
actually critical for your demo.

---

## Tests que PASAN ✅

_(Fill in after running the suite. Group by spec file.)_

- [ ] `01-login.spec.ts` + `01-login-deep.spec.ts`
- [ ] `02-dashboard.spec.ts` + `02-dashboard-deep.spec.ts`
- [ ] `03-cartridges.spec.ts` + `03-cartridges-deep.spec.ts`
- [ ] `06-html-pages.spec.ts`
- [ ] `07-api-endpoints.spec.ts` + `07-apis-deep.spec.ts`
- [ ] `08-external-services.spec.ts`
- [ ] `09-ux-mobile.spec.ts` (mobile-chromium project)
- [ ] `10-mcp-cartridges.spec.ts`
- [ ] `11-copilot-deep.spec.ts` (non-deferred subset)

---

## 🔴 Críticos

_(Filled by `scripts/e2e-report-summary.sh` OR manually.)_

| Test | Page | Screenshot | Diagnosis | Fix card |
|---|---|---|---|---|
|  |  |  |  |  |

---

## 🟡 Altos

| Test | Page | Screenshot | Diagnosis | Fix card |
|---|---|---|---|---|
|  |  |  |  |  |

---

## 🟢 Medios

| Test | Page | Screenshot | Diagnosis | Fix card |
|---|---|---|---|---|
|  |  |  |  |  |

---

## 🔵 Bajos

| Test | Page | Screenshot | Diagnosis | Fix card |
|---|---|---|---|---|
|  |  |  |  |  |

---

## Pre-flagged user-reported bugs

These failures are **expected on the first run** because the user
already reported them. Each maps to a v1.44.3.3 fix card. Status
column to be filled after the first run.

| User report | Spec | Status after run |
|---|---|---|
| "Botón Grafo no responde" | `05-studio.spec.ts:41` | TBD |
| "Botón Deploy a Airflow no responde" | `05-studio.spec.ts:73` | TBD |
| "Plantillas no abre" | `05-studio.spec.ts:217` | TBD |
| "Subir spec de entidades no acepta archivos" | `05-studio.spec.ts:106` | TBD |
| "Silver/Gold/Master no interactivos" | `05-studio.spec.ts:142` + `05-studio-deep.spec.ts` | TBD |
| "Crear en Superset no funciona" | `05-studio.spec.ts:165` | TBD |
| "Superset reporta healthy pero no responde" | `08-external-services.spec.ts:62` | TBD |
| "Airflow en :8082 (no :8080)" | `08-external-services.spec.ts:17` | TBD |

---

## Pending Copilot tests

`04-copilot.spec.ts` and the streaming subset of
`11-copilot-deep.spec.ts` are intentionally marked
`test.fail(true, …)` because the Next.js `/copilot` chat page +
SSE stream are deferred to v1.44.4. Those failures are expected
and NOT blockers — they document the gap, and the moment v1.44.4
lands the suite automatically flips those tests to passing.

---

## v1.44.5 Studio sentinel cleanup

Sprint v1.44.5 removed the hidden `#studio-e2e-sentinels` block from
`console/app/static/studio.html` and its invisible CSS rules from
`console/app/static/css/studio.css`.

The Studio specs now drive the real rendered UI through `window.goStep`
after the legacy init finishes. Missing real affordances are skipped
instead of hidden-sentinel-passed:

| Gap | Spec | Reason |
|---|---|---|
| Studio assistant dock | `05-studio-deep.spec.ts` | Reintroduced as a visible, scoped Studio assistant in PR #167. |
| Silver editable query / execute controls | `05-studio*.spec.ts` | Real visible Studio refine UI does not expose this control yet. |
| Superset create/open affordance | `05-studio*.spec.ts` | No canonical visible control on the current Studio surface. |
| Entity extraction mode controls | `05-studio-deep.spec.ts` | Current visible entities UI does not expose full/incremental mode controls. |

These are product/UI gaps, not hidden test scaffolding. They should be
reintroduced only as visible, user-operable controls.

## PR #167 Studio live validation

The Silver, Gold, and Master preview endpoints are wired to real
Refinement datasets, but the local lakehouse seed used during browser
validation has no materialized parquet data under `lakehouse/silver`,
`lakehouse/gold`, or `lakehouse/master`. In that state the endpoints
correctly return `rows: []` and `total: 0`; this is a data seed gap,
not a UI stub. A production-like E2E pass needs a Bronze→Silver→Gold→Master
seed before asserting non-empty preview rows.

---

## Pre-existing context

- v1.44.3.2 shipped the initial 71-test baseline.
- v1.44.3.2.1 expanded to 200+ tests with:
  - Fixed login flow (CSRF) per Codex's diagnostic
  - Global-setup-mounted storageState (login once per run)
  - Deep specs for login (20+), dashboard (20+), cartridges (28),
    studio (50+), APIs (40+), UX/mobile/a11y (20+), MCP (36),
    copilot (22)
  - Categorised report digest via `scripts/e2e-report-summary.sh`
- Real credentials: `emmanuel@local.ai` / `Admin123!` (verified
  by Codex's diagnostic — the user EXISTS in the bootstrap DB).
- Login endpoint: `POST /auth/login` (NOT `/api/auth/login`).
  CSRF flow: GET `/login` → echo `csrf_token` cookie value as
  both `X-CSRF-Token` header and `Cookie:` header on the POST.

---

## Próximos pasos (v1.44.3.3)

1. Run `make e2e` on the Mac with a booted stack.
2. Run `bash scripts/e2e-report-summary.sh >> docs/E2E_FINDINGS.md`.
3. Triage each failure into the right severity bucket.
4. Open v1.44.3.3 scoped to the 🔴 + 🟡 buckets first.
5. 🟢 + 🔵 follow in v1.45 (AWS hardening) or get folded into
   the relevant capability sprint.
