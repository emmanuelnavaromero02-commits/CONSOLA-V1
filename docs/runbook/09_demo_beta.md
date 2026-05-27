# 09 — Demo / beta controlada

> Audiencia: cualquier ingeniero que tenga que **levantar la demo y decir
> si está lista para mostrarse**. Si seguís este runbook de arriba a abajo
> y todos los checks pasan, la demo está lista. Si algo falla, cada sección
> "Si falla" dice exactamente qué hacer.

## Qué es esta demo (pitch técnico honesto)

OMEGA **detecta** anomalías operativas, ayuda a **investigar**, propone
**opciones**, registra una **decisión**, deja **aprobar**, corre **dry-run**
seguro y **audita** todo el recorrido. El Control Room es el cockpit de ese
ciclo.

**Lo que la demo NO hace (no lo prometas):**

- ❌ **No hay write-back productivo.** "Ejecutar" en el Control Room está
  bloqueado en V1: el backend rechaza la escritura externa y registra el
  intento. Solo preview y dry-run son operativos.
- ❌ **No hay ejecución SAP real.** No se escribe a SAP/Replicon ni a
  ningún sistema externo.
- ❌ **No es "todo en 8000".** La arquitectura de esta fase es **split**:
  el frontend Next.js vive en `:3000` (oficial *temporal*) y el backend
  FastAPI + APIs + Control Room en `:8000`. No fuerces la suite a 8000-only.

## Arquitectura (esta fase)

| Pieza | URL | Qué es |
|---|---|---|
| Frontend | http://localhost:3000 | Next.js, consola oficial temporal |
| Control Room | http://localhost:8000/control-room | servido por FastAPI (`:8000`) |
| Backend health | http://localhost:8000/healthz | liveness, sin auth (incluye `version` + `app_env`) |
| Backend readiness | http://localhost:8000/readyz | dependencias (Postgres + sibling services) |
| Frontend health | http://localhost:3000/api/health | liveness del Next |

El link "Control Room" en la consola de `:3000` apunta al backend `:8000`
(directo, o vía el redirector same-origin `/legacy` que reenvía a `:8000`).

## Prerrequisitos

| Herramienta | Mínimo |
|---|---|
| Docker Engine | 24+ con **daemon corriendo** |
| Docker Compose | v2.20+ |
| GNU make | 4.0 |
| Python 3 (host) | con `cryptography` funcional (bootstrap genera la Fernet key) |
| RAM / disco | 16 GB libres / 30 GB libres |

## Credenciales / usuario seed

El compose local siembra `emmanuel@local.ai` / `Admin123!` **solo para
levantar y validar**. En un entorno real usá el flujo de bootstrap admin
([02 Primer tenant](02_primer_tenant.md)) y rotá la contraseña antes de
cargar datos sensibles.

## `.env`

- Stack: `infra/.env` lo **genera `make up`** (vía `infra/bootstrap.sh` +
  `infra/bootstrap-keys.sh`). Todas las claves están documentadas en
  `infra/.env.example`.
- E2E: `tests-e2e/.env` se copia de `tests-e2e/.env.example` (lo hace
  `make e2e` la primera vez). Define `BASE_URL=:3000`, `BACKEND_URL`/
  `LEGACY_URL=:8000` y `CONTROL_ROOM_URL=:8000/control-room`.

## Orden de validación (de arriba a abajo)

### 0. Preflight (antes de todo)

```bash
bash scripts/preflight.sh
```

Chequea daemon de Docker, `docker compose`, validez del compose, `infra/.env`,
`cryptography` del Python host (para bootstrap), puertos ocupados, e imprime
las URLs finales. Sale `0` si podés correr `make up` (con o sin warnings) y
`!=0` si hay un bloqueador. Encadenable: `bash scripts/preflight.sh && make up`.

### 1. Compose config

```bash
docker compose -f infra/docker-compose.yml config -q && echo "compose OK"
```

### 2. Arrancar el stack

Para arranque limpio en máquina lenta seguí el orden de dos fases
(Postgres primero) de [01 Arrancar desde cero](01_arrancar_desde_cero.md).
Atajo en máquina sana:

```bash
make up
```

### 3. Healthchecks

```bash
curl -fsS http://localhost:8000/healthz   # {"ok":true,"service":"console","version":...,"app_env":...}
curl -fsS http://localhost:8000/readyz    # 200 si Postgres + deps están up; 503 si no
curl -fsS http://localhost:3000/api/health
curl -fsS http://localhost:8000/control-room -o /dev/null -w "control-room %{http_code}\n"
```

### 4. Smoke

```bash
make smoke      # esperado: 34/34 OK
```

### 5. Tests unitarios

```bash
make test       # tests + console + refinement + vault + workspace
```

### 6. Playwright Control Room

```bash
cd tests-e2e && npm install      # primera vez
npx playwright install chromium  # primera vez
npx playwright test specs/12-control-room.spec.ts --project=desktop-chromium
```

Valida: carga en `:8000`, **no llama a `:3000`**, navegación dominio/módulo,
abrir item, los 7 pasos OMEGA, lecciones/control/ejecución, y que no haya
`console.error`.

## Si falla

### Docker daemon no existe / no responde
`preflight` lo marca como bloqueador. Arrancá Docker Desktop o el servicio
`docker`. En un sandbox/CI sin daemon **no podés** correr `make up`/`make smoke`/
Playwright; validá con `make test` (unit suites, no necesitan stack).

### `make up` falla con `ModuleNotFoundError: No module named '_cffi_backend'`
Es el Python del **host** ejecutando `infra/bootstrap.sh` para generar la
Fernet key; su `cryptography` está roto. Fix:

```bash
python3 -m pip install --upgrade cffi cryptography
```

Luego reintentá `make up`. (Los contenedores traen su propio Python; este
error es solo del paso de bootstrap en el host.)

### Playwright falla
- `Cannot find module '@playwright/test'` → `cd tests-e2e && npm install`.
- Navegador ausente → `npx playwright install chromium`.
- Timeouts / login → el stack tiene que estar **arriba** (`:3000` y `:8000`).
  El `global-setup` hace login una vez contra `:3000`; si `:3000` no
  responde, todos los specs fallan en cascada. Revisá `make up` y
  `bash scripts/wait_for_health.sh`.
- Para solo verificar que el spec **compila/colecta** sin stack:
  `cd tests-e2e && npx playwright test specs/12-control-room.spec.ts --list`.

### Servicios unhealthy tras 3 min
Ver [07 Debug fallos](07_debug_fallos.md) y `docker compose -f infra/docker-compose.yml ps`.

## Checklist "lista para demo"

- [ ] `preflight` sin bloqueadores.
- [ ] `docker compose config -q` OK.
- [ ] `make up` y `docker compose ps` sin contenedores en `restarting`.
- [ ] `/healthz` (8000) y `/api/health` (3000) responden 200.
- [ ] `make smoke` = 34/34.
- [ ] `make test` verde.
- [ ] Playwright `12-control-room` verde.
- [ ] Login con el usuario seed entra a `:3000` y el link "Control Room"
      abre `:8000/control-room`.
- [ ] En el Control Room: navegás dominio/módulo, abrís un item, ves los 7
      pasos OMEGA, y "Ejecutar" muestra el bloqueo de write-back V1.
