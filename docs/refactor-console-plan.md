# Console Router Refactor Plan

Sprint v1.32 defers the `console/app/main.py` monolith split to v1.33 because
it is a high-blast-radius refactor. The file currently mixes app boot, auth
pages, data APIs, decisions, admin/IAM, monitoring, Studio, viewers, and
internal endpoints in one module, so moving it inside a mega-sprint would risk
breaking routes that are covered by integration smoke rather than narrow unit
tests.

## Target Router Layout

- `app/routers/auth.py`: login, logout, forgot/reset/activate flows.
- `app/routers/data.py`: dataset/app data APIs and query proxy endpoints.
- `app/routers/decisions.py`: decisions CRUD and action log endpoints.
- `app/routers/admin.py`: IAM, users, settings, security/admin pages.
- `app/routers/monitoring.py`: monitoring tools, job/deeplink APIs.
- `app/routers/studio.py`: cartridge, pipeline, DAG, and Studio operations.
- `app/routers/viewers.py`: static viewer page routing.
- `app/routers/internal.py`: internal service endpoints protected by pair keys.

## Execution Plan

1. Add route-level characterization tests that snapshot every current path,
   method, auth dependency, and response class.
2. Move one router at a time without changing behavior.
3. Keep `main.py` as composition root only: middleware, lifespan, security
   headers, static mount, router includes.
4. Run `make smoke`, `make test`, and browser smoke for console pages after
   each router move.
5. Remove duplicated helpers only after all routers are green.

## Beta Blocking Criteria

This refactor is not a beta blocker if the monolith remains tested and the
security fixes from v1.27-v1.32 are present. It is a production maintainability
blocker because future security changes are too easy to land in the wrong
section of the file.
