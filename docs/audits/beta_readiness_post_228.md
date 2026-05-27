# Beta readiness audit — post #228

> Auditoría post-merge (#223–#228). Objetiva, sin maquillaje. Fecha de
> corte: rama de auditoría desde `origin/main @ 80b10d7`.
> Entorno de auditoría: contenedor **sin daemon Docker** → los gates que
> requieren stack vivo se marcan **NO COMPROBADO (local)** con causa
> exacta; varios de ellos **sí** corren en CI (ver más abajo).

## Veredicto

| Escenario | Veredicto |
|---|---|
| **Demo controlada** | ✅ **GO** |
| **Beta privada** | 🟡 **GO con condiciones** (datos seed reales, operador presente, sin tráfico productivo) |
| **Producción limitada** | ❌ **NO-GO** |
| **Producción real** | ❌ **NO-GO** |

## Calificaciones (0–10)

| Dimensión | Nota | Una línea |
|---|---|---|
| **Global actual** | **6.5** | Base sólida y honesta; el "act" del loop está deliberadamente capado y la observabilidad/escala no están listas. |
| **Demo controlada** | **8.5** | Control Room real, pulido, auditado, con runbook y preflight. |
| **Beta privada** | **6.5** | Aguanta usuarios reales en read/decision/dry-run; falta endurecer datos, monitoreo y cobertura E2E. |
| **Producción** | **3.5** | Sin write-back/ejecución real, observabilidad mínima, escala/multi-tenant no demostrados. |

## Evidencia de validación (ejecutada en esta auditoría)

| Comando | Resultado |
|---|---|
| `make test` | ✅ 1720 passed, 76 skipped · 475 · 104 · 32 · 25 |
| `pytest cartridges -q` | ✅ 64 passed |
| `npm --prefix console-next run lint` / `typecheck` | ✅ limpio / limpio |
| `npm --prefix console/control-room-next run lint` / `typecheck` | ✅ limpio / limpio |
| `bash scripts/preflight.sh` | ✅ corre; reporta 1 bloqueador (daemon Docker) + 3 warnings — correcto |
| `playwright … 12-control-room --list` | ✅ compila y colecta 3 tests |

### NO COMPROBADO (local) — causa exacta

- **`make smoke`** → falla: todos los `/healthz` (8000/8001/8010/8300/8500/8201-8204) y `/mcp/tools` responden `000000` (curl sin conexión). Causa: **no hay stack** (sin daemon Docker, sin servicios). Los "UNAUTHENTICATED — got 000000" son *connection refused*, no una regresión de seguridad real.
- **`make up` / `docker compose config`** → (1) daemon Docker ausente (`/var/run/docker.sock`); (2) `bootstrap.sh` revienta con `ModuleNotFoundError: _cffi_backend` (cryptography del python host); (3) `compose config` pide `INTERNAL_API_KEY` (interpolado de `infra/.env`, generado por `make up`).
- **Playwright run real** (login + clicks) → requiere navegadores + `:3000`/`:8000` vivos.

**Mitigación:** `.github/workflows/e2e.yml` **sí** ejecuta `make smoke` + `make e2e` (Playwright) contra un stack compose real (perfil SAP). Si CI de main está verde, smoke 34/34 y los 3 specs de Control Room están cubiertos allí — solo no en este sandbox.

## Evaluación por área

| Área | Estado | Notas |
|---|---|---|
| **Control Room** | 🟢 Sólido | APIs reales (`/api/control-room/*`), 7 pasos OMEGA navegables, dry-run, bitácora/auditoría, lecciones (lista + empty útil), alertas, umbrales. Sin `:3000`, sin `console.error` en source. Pulido visual (#227). |
| **Console Next** | 🟢 Funciona / 🟡 tests | lint/typecheck/build verdes; link a Control Room robusto. **Solo 2 archivos de test vitest** → cobertura unitaria de UI muy fina; depende de Playwright. |
| **Backend/API** | 🟢 Maduro | 475 tests console; RLS, health/ready; `control_room_service.py` ≈ 4.8k líneas (riesgo de mantenibilidad). |
| **Auth/CSRF** | 🟢 Bien | Double-submit CSRF en console **y** workspace (#224), CORS cross-origin corregido (#225), exención Bearer, RLS por workspace. |
| **E2E** | 🟢 / 🟡 | Split-aware; Control Room valida `:8000` y no-`:3000`. Mayormente desktop-chromium; amplitud y flakiness no medidas aquí. |
| **Runbook demo/beta** | 🟢 Sólido | `01_arrancar_desde_cero` + `09_demo_beta` + `scripts/preflight.sh` con troubleshooting real (daemon, `_cffi_backend`, Playwright). |
| **Observabilidad** | 🟡 Mínima | Logs JSON con `request_id`, `/healthz` (con `version`+`app_env`), `/readyz` (deps), `/api/system/info`, endpoint operativo de métricas. Sin métricas Prometheus/tracing/dashboards/alertas. |

## Qué quedó sólido

- Arquitectura split explícita y honesta (`:3000` frontend / `:8000` backend+Control Room) en docs, scripts y E2E.
- Control Room como cockpit real del ciclo OMEGA (detect→investigate→decide→approve→dry-run→audit), cableado a APIs, no maqueta.
- CSRF consistente console+workspace; CORS de preflight corregido.
- Write-back productivo **bloqueado por defecto** y honesto (HTTP 501 + UI clara).
- Runbook + preflight reproducibles; un ingeniero nuevo puede decidir si la demo está lista.
- Suite unitaria amplia (≈2.4k) verde y `pytest cartridges` arreglado.

## Qué sigue parcial

- E2E solo verificable con stack (no en sandbox); amplitud limitada.
- Observabilidad mínima (sin monitoreo/alerting reales para una beta con usuarios).
- Cobertura unitaria de frontend muy fina.
- `control_room_service.py` monolítico (mantenibilidad/regresión).
- Datos: el cockpit depende de seeds; con seed vacío la demo se ve vacía.

## Qué sigue NO listo

- **Write-back / ejecución real** (SAP/Replicon): no implementado (501 por diseño V1).
- **Producción**: escala/carga, multi-tenant a volumen, DR/backups verificados en caliente, rotación de secretos operada, hardening de red.
- `VERSION` desalineado (ver gaps).

## Top 10 gaps reales

1. **No hay write-back/ejecución real** — el "act" del loop OMEGA termina en dry-run (por diseño V1, pero es *el* gap vs la visión completa).
2. **`VERSION=1.0.0` desalineado** con el estado real (v1.44.6); `/healthz` y `/api/system/info` exponen `1.0.0` → señal de madurez engañosa.
3. **Observabilidad mínima** — sin métricas/tracing/alerting para operar una beta con usuarios.
4. **Cobertura unitaria de frontend casi nula** (2 archivos vitest); regresiones de UI solo las atrapa Playwright (que necesita stack).
5. **`control_room_service.py` monolítico** (~4.8k líneas) — superficie de regresión alta concentrada en el activo más vendible.
6. **Dependencia de datos seed** — sin anomalías/items sembrados el cockpit queda vacío (el propio spec exige `items.length > 0`).
7. **Smoke marca "UNAUTHENTICATED 000000" ante stack caído** — confunde *connection refused* con regresión de seguridad; ruido en diagnóstico.
8. **E2E estrecho** — sin pruebas de error/empty/no-permission en navegador, ni mobile real, ni medición de flakiness.
9. **Bootstrap frágil en host** (`_cffi_backend`) — la primera experiencia puede romperse antes de levantar contenedores.
10. **Sin verificación en caliente de backup/restore ni rotación de secretos** para beta privada.

## Top 10 PRs recomendados (siguiente bloque)

1. **#229+ Alinear `VERSION`** al estado real (p.ej. `1.44.x`/`0.9.0-beta`) + test que lo fije; evita el "version 1.0.0" engañoso.
2. **Observabilidad ligera**: `/metrics` Prometheus básico (req/latencia/errores) + un panel mínimo; sin sistema grande.
3. **Tests de frontend**: vitest/RTL para `AppChrome` (link Control Room) y el render de pasos OMEGA del Control Room.
4. **Smoke más honesto**: distinguir `000`/connection-refused de `401/403` para no reportar "P0 security" cuando el stack está caído.
5. **E2E negativos**: specs de loading/error/empty/no-permission en Control Room (sin tocar happy-path).
6. **Seed de demo determinista**: comando/fixture que garantice anomalías/items para que el cockpit nunca salga vacío en demo.
7. **Refactor por módulos de `control_room_service.py`** (sin cambiar comportamiento) para bajar riesgo de regresión.
8. **Healthcheck de datos**: extender `/readyz` para señalar "0 items operativos" como degradado (no listo para demo).
9. **Hardening de bootstrap**: detectar `_cffi_backend` y guiar el fix automáticamente (el preflight ya avisa; falta en `make up`).
10. **Runbook beta privada**: backup/restore + rotación de secretos verificados en caliente (extiende 05/06).

## Qué subiría a 8/10 (global)

- Alinear `VERSION` (gap #2) + observabilidad ligera real (#2 PR) + seed de demo determinista (#6 PR).
- Smoke honesto (#4) y un puñado de tests de frontend (#3).

## Qué subiría a 9/10 (global)

- Lo anterior **+** E2E negativos con cobertura de estados (#5), refactor modular de `control_room_service.py` (#7), y métricas/alertas mínimas operando en la beta.
- Flakiness de E2E medida y < umbral acordado.

## Qué falta para producción real

- **Write-back/ejecución real** con conector SAP/Replicon auditado y reversible (gran bloque; fuera de esta fase).
- Pruebas de carga/escala y multi-tenant a volumen.
- DR/backup-restore y rotación de secretos verificados en caliente.
- Observabilidad completa (métricas + tracing + alerting + SLOs).
- Hardening de red/secretos y revisión de seguridad formal del camino de ejecución.

## Riesgo oculto principal

La credibilidad de la demo se concentra en **un único servicio monolítico
(`control_room_service.py`, ~4.8k líneas) + datos seed**, mientras la
verificación de UI **depende de un stack vivo** (Playwright no corre en
muchos entornos). Una regresión en ese servicio o un seed vacío puede
**pasar los tests unitarios y aun así vaciar/quebrar la demo**, y solo se
detectaría en CI con stack o en vivo. Mitigación: seed determinista +
healthcheck de datos + algo de cobertura unitaria de UI.

## Mentira funcional peligrosa (si se vende mal)

> "OMEGA **ejecuta y remedia automáticamente** / **escribe de vuelta a SAP**."

**Es falso.** El ciclo termina en **dry-run auditado**; el write-back
productivo está **bloqueado (HTTP 501)** y no hay ejecución SAP real. El
pitch honesto es: *detecta, investiga, decide, aprueba, dry-run y audita*.
Vender "remediación automática" o "write-back productivo" sería una mentira
funcional. (Secundario: `/healthz` reporta `version 1.0.0`, que no refleja
la madurez real — no lo uses como prueba de versión productiva.)
