# Baseline Canonico

Este documento fija el punto de partida despues de la reconciliacion de
`#552` sobre `#555`/`#557`. Sirve para dos cosas: reproducir el checkout
sin adivinar, y saber que deuda queda abierta antes de construir encima.

## Punto de partida

| Dato | Valor |
|---|---|
| Base | `main@12ec0c565ffd62cfa1ebeccfd275b3542d5bafbf` |
| Origen | `#557` — reconcile(operational-truth): port #552 onto staged publication main |
| Version | ver `VERSION` en la raiz (fuente unica) |
| CI verde en la base | Lint, Security Scan, Control Room PostgreSQL RLS, MCP Infra PDF Security |

`#552` quedo CLOSED sin merge: su contenido material entro por `#557`.
`#554` se cerro por absorcion. `#559` (cryptography 50.0.0) esta en main.

## Version: una sola fuente

El fichero `VERSION` de la raiz es la unica fuente. Todo lo que reporta
version resuelve a traves de `console/app/version.py`:

- `/healthz` y `/api/system/info`
- el `meta` del Control Room y `/api/control-room/ops/summary`
- `scripts/beta_smoke.py`, que ademas contrasta `VERSION` contra el
  `/healthz` del stack vivo y contra el tag de git

Ningun documento debe reescribir el numero. `docs/release-checklist-v1.md`
lo hizo y quedo 131 releases atras sin que la suite lo notara, porque el
test congelaba el literal en vez de comprobar coherencia. Hoy
`tests/test_v1_release_checklist.py` falla si un documento canonico
vuelve a incrustar una version.

## Comandos canonicos

Tres niveles. No se sustituyen entre si.

### Baseline — sin Docker, menos de un minuto

```bash
make baseline-smoke
```

Comprueba que el checkout es coherente: `VERSION` bien formado y resuelto
por `app.version`, docs sin version incrustada, targets documentados
presentes, workflows y compose parseando, `bash -n`, `compileall`, `ruff`
y los dos tests de contrato de version. **No es una puerta de producto.**

### Rapido — con el stack arriba

```bash
make preflight
make up
bash scripts/wait_for_health.sh
make smoke
```

### Gate completo

```bash
make verify-release
```

Reproduce los controles locales **equivalentes** de lint, Bandit, auditorias
de dependencias y stack real, mas `make test`, `make smoke` y `make e2e`.

No es identico a CI y no debe describirse como tal. CI selecciona jobs de
forma condicional segun el area cambiada (`scripts/ci_changed_areas.py`),
corre en su propio entorno y fija versiones de herramienta (`bandit==1.7.10`).
Ante cualquier diferencia de entorno, de seleccion condicional o de
ejecucion, **CI es la autoridad**.

Paridad exacta y verificada en dos puntos concretos:

- `pip-audit` corre fail-closed y sin supresiones en ambos lados.
- El alcance de Bandit local incluye `scripts/ci_changed_areas.py` y
  `scripts/ci_control_room_paths.py`, igual que el job `bandit` de
  `security.yml`. Mismos umbrales: `--severity-level medium
  --confidence-level high`.

### Cierre limpio

```bash
make down
# reset destructivo, solo desarrollo local:
make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev
```

`make nuke` borra volumenes. Nunca contra AWS, staging, produccion ni
ningun host con datos de cliente.

## Riesgos y deuda abiertos

Registrados, no corregidos en esta fase. Ninguno bloquea el baseline.

| # | Sev | Asunto | Detalle |
|---|---|---|---|
| B-1 | P2 | Aislamiento de tests | `tests/test_talent_benchmark_approval_authority.py` inserta `console/` y `refinement/` en `sys.path` y importa `app.*` sin purgar `sys.modules`. Deja `app` ligado al primer paquete importado. Hoy no se manifiesta (CI verde, repro dirigido en verde), pero es dependiente del orden de coleccion: si otro `test_*.py` de `tests/` empieza a importar `app.*`, aparecen errores tipo `module 'app.main' has no attribute ...`. |
| B-2 | P3 | `cryptography` sin pin | `cartridges/hubspot` y `cartridges/replicon` declaran `cryptography` sin restriccion de version, a diferencia de los otros cuatro cartuchos que la fijan en `50.0.0`. Hoy resuelven limpio; quedan a merced de lo que publique PyPI. |
| B-3 | P3 | Rama cerrada citada | `docs/audits/operational-truth-data-integrity.md` cita `fix/operational-truth-data-integrity` como rama de trabajo. Esa rama es `#552`, cerrada sin merge; el contenido vive en main desde `#557`. |
| B-4 | P3 | Cifras sin verificar | El README afirma `47/47 checks passed` en smoke y `~357 passed, 3 skipped` en E2E. Son observaciones de una corrida pasada, no contratos: nada las verifica y drift silenciosamente. |
| B-5 | P3 | Nota documental cp39/cp311 | Observacion heredada de `#559` sobre el tag de wheel abi3 descrito en la evidencia. Sin efecto funcional: las cuatro imagenes instalan desde wheel precompilado y el smoke criptografico real pasa. |
| B-6 | P2 | `baseline-smoke` no es estrictamente no-mutante | El smoke genera caches locales al ejecutar `compileall`, `ruff` y `pytest`: `__pycache__/`, `.pyc`, `.pytest_cache/`, `.ruff_cache/`. Todas estan en `.gitignore`, asi que **no altera ningun fichero versionado** y `git status` queda limpio. Aun asi escribe en el arbol de trabajo, de modo que describirlo como no-mutante seria inexacto. Registrado, no corregido en esta ronda. |

## Lo que esta fase no toco

Sin cambios funcionales en Intelligence, Control Room, Refinement,
cartuchos, SQL de datasets, migraciones ni frontend. Los unicos ficheros
tocados son de version documental, comandos y documentacion del baseline.
