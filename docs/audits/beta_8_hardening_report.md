# Beta 8 Hardening Pack — report

> One PR (#230) closing real readiness gaps from the post-#228 audit.
> Audit environment: container **without a Docker daemon** → stack-only
> gates are marked **NO COMPROBADO** with exact cause and whether CI covers
> them. No maquillaje.

## What was fixed (by track)

| Track | Gap before | Change |
|---|---|---|
| **1 — Release identity** | `VERSION=1.0.0` while code is post-v1.44.6; `/healthz` reported a stale version. | `VERSION` → `1.45.0-beta`. New single source `app.version.app_version()`; `/healthz`, `/api/system/info` and the Control Room `meta` all read it. Tests assert VERSION ≠ 1.0.0 and the surfaces share one source. |
| **2 — Demo/dev seed** | Lessons/Thresholds panels could be empty in a fresh demo. | `infra/init_dev/16_demo_control_room_seed.sql` — idempotent, workspace/tenant-scoped, `metadata.demo=true`, wired into the dev-seed one-shot and **skipped when APP_ENV=production**. Seeds the **DB-backed** panels only. |
| **3 — Observability** | No light operational visibility of Control Room data. | New `GET /api/control-room/ops/summary` (auth + workspace-scoped): version, app_env, items by status, open items by severity, action executions by status, lessons, active thresholds, last item seen, `write_back_enabled`. Cheap COUNTs — never runs the heavy dataset path. |
| **4 — Honest smoke** | `make smoke` labelled a DOWN service as a "P0 security regression" (curl 000). | `auth_gate_check` classifies 000 (down) vs 401/403 (gate OK) vs 5xx (broken) vs other (real auth gap). |
| **5 — Preflight in Makefile** | Preflight existed but wasn't a target. | `make preflight` + `make demo-check`; both in `make help`. |
| **6 — Control Room confidence** | No runtime signal of version/env/write-back in the cockpit. | Header pills: `Beta <version>` and `Write-back bloqueado V1` (red), sourced from real dashboard `meta` (not a hardcoded label). |
| **7 — CI gate** | `pytest cartridges -q` (the #224 conftest fix) was ungated. | Added a `Run cartridges/ suite` step to `docker-image.yml`. |
| **8 — Audit** | — | This document. |

## Evidence (commands run in this audit)

| Command | Result |
|---|---|
| `make test` | ✅ tests **1728 passed, 76 skipped** · console **483** · refinement **104** · vault **32** · workspace **25** |
| `pytest cartridges -q` | ✅ 64 passed |
| new `test_ops_summary_and_version.py` | ✅ 8 passed |
| new `test_demo_control_room_seed.py` | ✅ 6 passed |
| `test_smoke_script.py` | ✅ 16 passed (incl. 2 new honesty guards) |
| `console-next` lint / typecheck / test / build | ✅ clean / clean / 8 passed / build OK |
| `control-room-next` lint / typecheck / build / `export:copy` | ✅ clean / clean / OK / OK (real content change — new pills/strings in bundle) |
| `ruff` (all changed Python) | ✅ clean |
| `bash scripts/preflight.sh` / `make preflight` | ✅ runs; reports blocker (no Docker daemon) — correct |
| `playwright … 12-control-room --list` | ✅ compiles + collects 3 tests |

### NO COMPROBADO (local) — exact cause

- **`make up` / `make smoke` / `docker compose config`** → no Docker daemon (`/var/run/docker.sock`); `bootstrap.sh` fails on `_cffi_backend`; `compose config` needs `INTERNAL_API_KEY` from `infra/.env`. **CI covers these** (`e2e.yml` runs `make up` + `make smoke`; `docker-image.yml` runs `compose config`).
- **Demo seed actual execution** → needs a live Postgres with the dev workspace. SQL is validated structurally only here; it runs in the dev-seed one-shot on `make up` (CI/stack). It is **non-blocking** (the seed one-shot has no dependents) so a failure can't break bring-up.
- **`/api/control-room/ops/summary` end-to-end** → unit-tested with a mocked pool; live DB run NO COMPROBADO (covered by the stack).
- **Playwright real run** (incl. the new write-back pill assertion) → needs browsers + `:3000`/`:8000`. Covered by `e2e.yml` in CI.

## Calificación antes/después

| Dimensión | Antes (#229) | Después (estimado) | Por qué |
|---|---|---|---|
| **Global** | 6.5 | **7.5** | Version honesta, observabilidad ligera, smoke honesto, preflight integrado, badge de confianza, seed parcial, gate de cartridges. No llega a 8.0 por la dependencia de datos de items y la observabilidad/coverage aún ligeras. |
| **Demo controlada** | 8.5 | **9.0** | Cockpit con señales de confianza reales + lessons/thresholds sembrados; pitch honesto visible. |
| **Beta privada** | 6.5 | **7.5** | Ops summary + version honesta + smoke honesto la hacen defendible; falta monitoreo real y datos de items deterministas. |
| **Producción** | 3.5 | **4.0** | Mejor diagnóstico/identidad, pero sigue **NO-GO**: sin write-back/operación real, sin observabilidad completa, sin escala probada. |

## Qué subió
- Confianza de identidad de release (no más `1.0.0`).
- Diagnóstico honesto (smoke distingue caído vs gate).
- Visibilidad operacional (ops summary) y de runtime (badge).
- Reproducibilidad de arranque (`make preflight`/`demo-check`).
- Protección del fix de cartridges en CI.
- Demo más determinista en los paneles DB-backed.

## Qué NO subió (sin maquillar)
- **Items/alertas del cockpit siguen siendo dataset-driven**: el seed NO los puebla; dependen de los gold datasets (extracción de cartuchos / stack). Una demo sin esos datos seguirá mostrando "Sin señales".
- **Observabilidad sigue ligera**: ops summary es un snapshot, no hay métricas/tracing/alerting.
- **Cobertura unitaria de frontend sigue fina** (8 tests vitest).
- **`control_room_service.py` sigue monolítico** (~4.9k líneas).

## Qué sigue bloqueando producción
- Write-back/ejecución real auditable y reversible (SAP/Replicon) — fuera de alcance por diseño V1.
- Observabilidad completa (métricas + tracing + alerting + SLOs).
- Datos de demo/seed del **lakehouse** (no solo DB) para items deterministas.
- Escala/carga, multi-tenant a volumen, DR/backup-restore y rotación verificados en caliente.

## Pitch correcto (sí se puede decir)
> ΩMEGA **detecta, investiga, decide, aprueba, corre dry-run y audita**
> anomalías operativas, con trazabilidad por workspace.

## Pitch prohibido (sigue siendo mentira)
> ❌ "Remediación automática / write-back productivo a SAP."
> El ciclo termina en **dry-run auditado**; el write-back está **bloqueado
> (HTTP 501)** y se muestra como tal en el cockpit (`Write-back bloqueado V1`).

## Top 5 riesgos restantes
1. **Datos de items dependientes del lakehouse**: sin extracción de cartuchos, el cockpit luce vacío aunque el sistema esté sano (el seed solo cubre lessons/thresholds).
2. **`control_room_service.py` monolítico**: superficie de regresión alta en el activo más vendible.
3. **Observabilidad insuficiente para una beta con usuarios** (sin alerting).
4. **Verificación de UI dependiente de stack** (Playwright) — no corre en muchos entornos locales/sandbox.
5. **Seed/bootstrap frágil fuera de CI** (cryptography del host) — mitigado por preflight, no eliminado.

## Próximos 5 PRs recomendados
1. **Generador de demo-data del lakehouse** (gold datasets deterministas) para que los items del cockpit nunca dependan de suerte — el gap #1.
2. **Observabilidad**: `/metrics` Prometheus básico (req/latencia/errores) + panel mínimo.
3. **Tests de frontend** (vitest/RTL) para AppChrome + pasos OMEGA + badge de write-back.
4. **Refactor modular de `control_room_service.py`** (sin cambiar comportamiento).
5. **Runbook beta privada**: backup/restore + rotación de secretos verificados en caliente.
