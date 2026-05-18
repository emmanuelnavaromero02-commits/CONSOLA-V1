# Claude Code Handoff: Frontend Stabilization

Goal: stabilize the v1.44.4 migration without splitting the product.
`localhost:8000` remains the canonical console. Functionality prototyped
in `localhost:3000` must be ported back into `8000` before it counts as
available to users.

## Do Not Do

- Do not move auth, RBAC, CSRF, audit, vault policy, or cartridge
  authority into Next.js.
- Do not create a second AppChrome or second global shell.
- Do not label a page as fully migrated if it still links back to
  `localhost:8000`.
- Do not make `/workspace` or any `3000` route the only access path for
  accepted product features.
- Do not hide new features behind direct URLs. They must be visible from
  the `8000` home/control panel.

## Already Fixed In This Branch

1. AppChrome duplication:
   - `console-next/src/app/layout.tsx` owns the single global shell.
   - `console-next/src/app/providers.tsx` now only provides React Query
     and Sonner toasts.

2. Native `8000` Copilot recovery:
   - `console/app/static/copilot.html`, `css/copilot.css`, and
     `js/copilot.js` now expose memory, drafts, workflows and slash
     commands directly on `/copilot`.
   - These controls call the real FastAPI `/api/copilot/*` endpoints.

3. Native `8000` Cartridge recovery:
   - `console/app/static/cartridges.html`, `css/cartridges.css`, and
     `js/cartridges.js` now expose editable credential forms, save,
     test connection, delete credentials, entities and run actions.
   - The `8000` home page now links to Copilot and Cartuchos in the
     central panel and top navigation.

4. Architecture contract:
   - `docs/runbook/frontend-migration-contract.md` documents route
     ownership and the `8000` canonical / `3000` prototype boundary.

## Next Tasks For Claude Code

### Task 1: Replace Stale `/workspace` / `3000` Assumptions

Files:
- `tests-e2e/specs/04-copilot.spec.ts`
- `tests-e2e/specs/11-copilot-deep.spec.ts`
- `tests-e2e/specs/01-login-deep.spec.ts`
- `tests-e2e/specs/06-html-pages.spec.ts`

Rules:
- `/copilot` on `8000` is the official copilot UI.
- `/workspace` is a reference/prototype route unless explicitly kept.
- Remove or rewrite `test.fail(true)` markers that are only stale route
  assumptions.
- Keep expected-fails only for genuinely missing backend features such
  as SSE streaming.

Validation:

```bash
cd /Users/emmanuel/CONSOLA-BETA/tests-e2e
npx playwright test 04-copilot.spec.ts 11-copilot-deep.spec.ts --reporter=list
```

### Task 2: Add A Route Visibility Regression Spec

Create a small spec, for example:

```text
tests-e2e/specs/00-route-ownership.spec.ts
```

Minimum assertions:
- `/` on `8000` visibly links to `/copilot`, `/cartridges`, `/studio`,
  `/operations`.
- `/cartridges` on `8000` visibly exposes "Editar credenciales",
  "Test connection", "Entidades", "Guardar credenciales".
- `/copilot` on `8000` visibly exposes Memoria, Redactar, Workflows,
  and `/` commands.
- `/studio` stays classic and functional.

Validation:

```bash
npx playwright test 00-route-ownership.spec.ts --reporter=list
```

### Task 3: Keep Studio Honest

`/studio` is currently a launcher to the legacy console. Do not add
fake Next.js Studio controls until the backend endpoints stop being
stubs. If adding UI, it must either:

- call a real FastAPI endpoint with real data, or
- link clearly to the legacy `:8000/studio` workflow.

### Task 4: Fix Python Test Execution Separately

The Docker image for `mode_console` does not include `pytest`.
Do not confuse that with a product regression. Decide one path:

- add a test-target image/stage that installs test dependencies, or
- run Python tests on the host `.venv` and stop documenting
  `docker exec mode_console pytest ...` as a valid command.

## Required Final Validation

```bash
cd /Users/emmanuel/CONSOLA-BETA
make smoke

Use Playwright or curl to verify `8000` native routes:

```text
/               shows Copiloto + Cartuchos
/copilot        shows Memoria + Redactar + Workflows
/cartridges     shows editable credential form
```
```

Expected:
- Smoke: `34/34`
- `8000` UI: no hidden accepted features behind `3000` direct URLs
