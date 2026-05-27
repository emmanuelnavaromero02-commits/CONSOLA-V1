# OMEGA E2E Test Suite (Playwright)

Browser-driven end-to-end tests for the OMEGA console. Validates the
FastAPI-served static console (port 8000), the backend APIs, and external
service surfaces (Airflow 8082, Superset 8088, MinIO 9001, Mailhog 8025,
MCP cartridges 8201-8204).

## Architecture (static export)

The React console is exported as static assets and served by FastAPI on
`:8000`. Browser routes, legacy HTML pages, APIs, and the Control Room all
share the same origin; the suite asserts that the runtime no longer calls
or depends on `:3000`.

Sprint v1.44.3.3+ — active regression suite. The tests fail loudly when
the corresponding surface is broken and are wired into CI through
`.github/workflows/e2e.yml`.

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
| `TEST_PASSWORD` | `Admin123!` | Plaintext — bcrypt'd by the backend for local-dev seed only |
| `BASE_URL` | `http://localhost:8000` | FastAPI-served console |
| `LEGACY_URL` | `http://localhost:8000` | FastAPI console |
| `BACKEND_URL` | `http://localhost:8000` | Alias used by `fixtures/auth.ts` |
| `CONTROL_ROOM_URL` | `http://localhost:8000/control-room` | Control Room on the backend (:8000) |
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
- FastAPI must respond at `${BASE_URL}/healthz`
- Legacy console paths must respond at `${LEGACY_URL}`
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
| `01-login.spec.ts` | Static console `/login` flow + auth redirect |
| `02-dashboard.spec.ts` | Static console `/dashboard` KPIs + freshness table |
| `03-cartridges.spec.ts` | Static console `/cartridges` grid + activation flow |
| `04-copilot.spec.ts` | Static console `/copilot` chat shell, input, and conversation sidebar |
| `05-studio.spec.ts` | Legacy `/studio` — pins the user-reported broken buttons |
| `06-html-pages.spec.ts` | Legacy `/audit`, `/iam`, `/operations`, `/monitor`, etc. |
| `07-api-endpoints.spec.ts` | FastAPI contracts (auth gate + shape validation) |
| `08-external-services.spec.ts` | Airflow/Superset/MinIO/Mailhog/MCP |
| `12-control-room.spec.ts` | Control Room on FastAPI `:8000` — hydrated cockpit, decision/approval/audit flow; asserts **no `:3000` calls** |

## Interpreting failures

Any non-skipped failure should be treated as a regression unless the
spec explicitly marks it with `test.fail()`. Read the HTML report and
triage:

1. **Reproducible UI failures** (`05-studio*.spec.ts`) → attach the
   Playwright screenshot/trace path and file the failing route or
   button in `docs/E2E_FINDINGS.md`.

2. **API auth-gate regressions** (`07-api-endpoints.spec.ts`) → P0
   security regression if any protected endpoint returns 200 without
   a session cookie. File immediately.

3. **External service unreachable** (`08-external-services.spec.ts`) →
   likely a compose port drift. Check `infra/docker-compose.yml`
   `ports:` mappings for the service in question.

4. **Explicit expected failures** (`test.fail(true, …)`) → tracked
   deferrals. Once the missing surface lands, remove the marker so the
   suite fails again on regressions.

## CI

The suite is wired into `.github/workflows/e2e.yml`. The workflow
bootstraps `infra/.env`, starts the compose stack with the SAP profile,
waits for healthcheck convergence, runs `make smoke`, then runs
`make e2e` and uploads `tests-e2e/playwright-report` as an artifact.
