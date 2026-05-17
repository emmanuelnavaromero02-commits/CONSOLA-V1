# Frontend Migration Contract

This repository is in a staged recovery from a split UI migration.
`localhost:8000` is the canonical console. Features prototyped in the
Next.js app must be ported back into the FastAPI/static console unless
the product owner explicitly approves a separate Next.js surface.

## Source Of Truth

| Surface | Port | Owner | Status |
| --- | --- | --- | --- |
| FastAPI backend, auth, RBAC, APIs, legacy HTML | `8000` | `console/app` | Canonical |
| Next.js prototypes | `3000` | `console-next` | Reference only until ported |
| Service-to-service APIs | Docker network | `infra/docker-compose.yml` | Canonical |

Do not make users discover features by direct `3000` URLs. If a feature
is accepted, make it visible and usable from `8000`.

The Next.js app must not reimplement backend authority. While it exists,
it can call the FastAPI app through controlled proxy routes:

- `/api/[...path]` -> FastAPI `/api/*`
- `/auth/[...path]` -> FastAPI `/auth/*`
- `/security/[...path]` -> FastAPI `/security/*`
- `/login-proxy` -> FastAPI `/login` to seed CSRF cookies

## Route Ownership

| Route | Current UI | Backend/Data | Contract |
| --- | --- | --- | --- |
| `/` | FastAPI/static | FastAPI `/api/*` | Central visible entrypoint |
| `/dashboard` | FastAPI/static or legacy | FastAPI `/api/dashboard/*` | Keep visible from `8000` if enabled |
| `/cartridges` | FastAPI/static | FastAPI `/api/cartridges/*` | Native `8000` page with editable credentials |
| `/copilot` | FastAPI/static | FastAPI `/api/copilot/*` | Official copilot UI on `8000` |
| `/workspace` | Legacy/optional | FastAPI `/api/copilot/*` | Do not make this the only access path |
| `/studio` | FastAPI/static legacy | FastAPI Studio routes | Preserve classic Studio until migrated end to end |
| `/operations` | Next.js | Mixed FastAPI admin/security/vault APIs | Real Next.js overview |
| `/operations/users` | Next.js | FastAPI `/api/admin/users` | Real Next.js page |
| `/operations/audit` | Next.js | FastAPI `/security/audit` | Real Next.js page |
| `/operations/vault` | Next.js | FastAPI `/api/vault/connections/*` | Read-only Next.js page |

## Rules

1. Mount the global shell exactly once.
   `AppChrome` belongs in `console-next/src/app/layout.tsx`. Provider
   components may provide context and toasts, but must not wrap children
   in another shell.

2. Keep `8000` as the system of record.
   Auth, RBAC, CSRF, audit, vault, cartridge metadata, and copilot data
   stay enforced by FastAPI. Next.js can render and proxy; it cannot
   become a second policy engine.

3. Mark bridges honestly.
   A Next.js route that links into `localhost:8000` is a bridge. Do not
   describe it as migrated until the Next.js page owns the full workflow
   end to end.

4. Prefer `/copilot` on `8000` for the copilot UI.
   Next.js `/workspace` can remain as a reference, but accepted user
   functionality must be reachable from `/copilot` without redirecting
   to `3000`.

5. Every migrated page needs a regression test for ownership drift.
   At minimum, assert one global banner/nav, successful authenticated
   route load, and no accidental duplicate shell.
