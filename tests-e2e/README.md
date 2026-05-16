# OMEGA E2E Test Suite (Playwright)

Browser-driven end-to-end tests for the OMEGA console. Validates the
Next.js console (port 3000), the legacy HTML console (port 8000), the
FastAPI backend (port 8000), and external service surfaces (Airflow
8082, Superset 8088, MinIO 9001, Mailhog 8025, MCP cartridges
8201-8204).

Sprint v1.44.3.2 — **detection-only**. The tests intentionally fail
loudly when the corresponding surface is broken. Bug fixes ship in
v1.44.3.3 after the developer reviews the HTML report.

## Setup (first time)

```bash
cd tests-e2e
npm install
npx playwright install chromium
cp .env.example .env
# Edit .env with your real local-dev credentials
```

The `.env` is gitignored. Required keys:

| Var | Default | Notes |
|---|---|---|
| `TEST_EMAIL` | `emmanuel@local.ai` | Must exist in the `users` table |
| `TEST_PASSWORD` | `ChangeMeFirstBoot123!` | Plaintext — bcrypt'd by the backend |
| `BASE_URL` | `http://localhost:3000` | Next.js console |
| `LEGACY_URL` | `http://localhost:8000` | FastAPI console |
| `AIRFLOW_URL` | `http://localhost:8082` | **Local compose uses :8082, not :8080** |
| `SUPERSET_URL` | `http://localhost:8088` |  |
| `MINIO_CONSOLE_URL` | `http://localhost:9001` |  |
| `MAILHOG_URL` | `http://localhost:8025` |  |

## Running

```bash
make e2e                  # from repo root, validates preconditions first
# or directly:
cd tests-e2e
npx playwright test
```

The runner enforces preconditions before invoking Playwright:
- `tests-e2e/.env` must exist (copied from `.env.example` automatically with a warning)
- Next.js must respond at `${BASE_URL}/api/health`
- Legacy console must respond at `${LEGACY_URL}/healthz`
- Chromium browser must be installed (auto-installs if missing)

## Reports

After every run:

```
tests-e2e/playwright-report/index.html
```

Open the file (auto-opens in an interactive shell) for:
- Per-test pass/fail summary
- Screenshots of every failure
- Video replay (≤30 s) of every failure
- Full Playwright trace (timeline + DOM + network) for debugging

## Specs

| File | What it covers |
|---|---|
| `01-login.spec.ts` | Next.js `/login` flow + middleware redirect |
| `02-dashboard.spec.ts` | Next.js `/dashboard` KPIs + freshness table |
| `03-cartridges.spec.ts` | Next.js `/cartridges` grid + dynamic form |
| `04-copilot.spec.ts` | Next.js `/copilot` (marked `test.fail()` until v1.44.4) |
| `05-studio.spec.ts` | Legacy `/studio` — pins the user-reported broken buttons |
| `06-html-pages.spec.ts` | Legacy `/audit`, `/iam`, `/operations`, `/monitor`, etc. |
| `07-api-endpoints.spec.ts` | FastAPI contracts (auth gate + shape validation) |
| `08-external-services.spec.ts` | Airflow/Superset/MinIO/Mailhog/MCP |

## Interpreting failures

The detection-only sprint expects some tests to fail. Read the HTML
report and triage:

1. **Reproducible UI failures** (`05-studio.spec.ts` flagged buttons:
   Grafo, Deploy a Airflow, Crear en Superset, Silver subtab, Subir
   spec drop zone) → file in `docs/E2E_FINDINGS.md` under "Tests que
   FALLAN ❌" with the screenshot path. These are v1.44.3.3 fix targets.

2. **API auth-gate regressions** (`07-api-endpoints.spec.ts`) → P0
   security regression if any protected endpoint returns 200 without
   a session cookie. File immediately.

3. **External service unreachable** (`08-external-services.spec.ts`) →
   likely a compose port drift. Check `infra/docker-compose.yml`
   `ports:` mappings for the service in question.

4. **Pending Copilot tests** (`04-copilot.spec.ts`) → expected to
   fail until v1.44.4 ships the chat page. The `test.fail(true, …)`
   line documents the deferral; once the page lands the suite
   automatically flips to passing.

## CI

The suite is NOT yet wired into `.github/workflows/`. A future
sprint will add a job that boots the stack via docker-compose,
waits for healthcheck convergence, then runs the suite. For now
the validation is operator-driven on a Mac with a booted stack.
