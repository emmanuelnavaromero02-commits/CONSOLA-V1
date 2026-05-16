# E2E Findings — v1.44.3.2

This document is the **post-run triage form** for the Playwright E2E
suite shipped in v1.44.3.2. Fill it in after running `make e2e` on
your Mac against a booted stack; the contents drive the v1.44.3.3
bug-fix sprint.

The suite under `tests-e2e/specs/` is **detection-only** — every
spec is meant to fail loudly when the corresponding surface is
broken. The findings below capture the post-detection state.

---

## How to populate

1. Boot the stack:
   ```bash
   docker compose -f infra/docker-compose.yml --profile sap up -d --build
   sleep 240
   ```

2. Edit `tests-e2e/.env` with real credentials (see
   `tests-e2e/README.md`).

3. Run the suite:
   ```bash
   make e2e
   ```

4. Open the HTML report:
   ```
   tests-e2e/playwright-report/index.html
   ```

5. For each failing test, copy the description below into the
   "Tests que FALLAN" section and attach the screenshot path
   from the report.

---

## Tests que PASAN ✅

_(Fill in after running the suite. Group by spec file.)_

- [ ] `01-login.spec.ts` — all 4 tests
- [ ] `02-dashboard.spec.ts` — all 8 tests
- [ ] `03-cartridges.spec.ts` — all 7 tests
- [ ] `06-html-pages.spec.ts` — 7 admin pages reachable
- [ ] `07-api-endpoints.spec.ts` — auth gate green on 7 endpoints
- [ ] `08-external-services.spec.ts` — Airflow / Superset / MinIO / Mailhog

---

## Tests que FALLAN ❌

_(Per-failure entry. Triage column maps to expected next-sprint
fix size: `xs` = config/typo, `s` = single function, `m` =
component + tests, `l` = cross-stack.)_

### Example entry (delete once real failures land)

| Test | Page | Screenshot | Diagnosis | Triage |
|---|---|---|---|---|
| `studio › 'Grafo' button responds to click` | `/studio` | `playwright-report/data/<hash>.png` | Click handler not wired or routed | s |

### Real entries (to be filled)

| Test | Page | Screenshot | Diagnosis | Triage |
|---|---|---|---|---|
|  |  |  |  |  |

---

## Pre-flagged user-reported bugs

The following failures are **expected** because the user already
reported them. The corresponding test from `05-studio.spec.ts`
should turn red on the first run; each one becomes a v1.44.3.3
fix card.

| User report | Test name | Status after first run |
|---|---|---|
| "Botón Grafo no responde" | `'Grafo' button responds to click` | TBD |
| "Botón Deploy a Airflow no responde" | `'Deploy a Airflow' button fires …/dag-deploy` | TBD |
| "Plantillas no abre" | _(no dedicated test — needs a `studio.html` audit; add in v1.44.3.3)_ | TBD |
| "Subir spec de entidades no acepta archivos" | `'Subir spec' drop zone accepts files` | TBD |
| "Silver/Gold/Master no interactivos" | `'Silver' subtab renders data, not blank` | TBD |
| "Crear en Superset no funciona" | `'Crear en Superset' triggers …/superset` | TBD |
| "Superset reporta healthy pero no responde" | `Superset /health probe` | TBD |
| "Airflow en :8082 (no :8080)" | `Airflow UI responds at …` | TBD if `.env` sets the wrong port |

---

## Pending Copilot tests

`04-copilot.spec.ts` is intentionally marked `test.fail(true, …)`
because the Next.js `/copilot` chat page is deferred to v1.44.4.
Those failures are expected and NOT blockers — they document the
gap, and the moment v1.44.4 lands the suite automatically flips
those tests to passing.

---

## Próximos pasos (v1.44.3.3)

Once this file has real entries:

1. Group failures by `Triage` column (xs / s / m / l).
2. Open a v1.44.3.3 sprint scoped to the xs + s items first
   (low-risk, high-volume cleanup).
3. m + l items go into v1.44.4 (Copilot chat sprint) or v1.45
   (AWS hardening) as appropriate.
4. Re-run `make e2e` after each fix batch; the HTML report
   should show progressively fewer reds.

---

## Pre-existing context

- v1.44.3 shipped `/cartridges` Next.js + LLM integration backend.
- v1.44.2 R-Mac-3 fixed every healthcheck to use `127.0.0.1` so a
  green `docker inspect Status=healthy` is now load-bearing.
- v1.44.2 R-Mac-2 bumped Next.js to ≥ 14.2.21 — residual Next-15
  CVEs were documented for v1.45 deploy sprint.
- The user reports the local-dev admin is **`emmanuel@local.ai`**
  (not `admin@omega.local` that some docs still reference); the
  `tests-e2e/.env.example` reflects this.
