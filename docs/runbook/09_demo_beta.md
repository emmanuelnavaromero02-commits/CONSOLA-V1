# 09 — Demo / beta controlada

> Audiencia: cualquier ingeniero que tenga que **levantar la demo y decir
> si está lista para mostrarse**. Si seguís este runbook de arriba a abajo
> y todos los checks pasan, la demo está lista. Si algo falla, cada sección
> "Si falla" dice exactamente qué hacer.

## Qué es esta demo (pitch técnico honesto)

OMEGA **detecta** anomalías operativas, ayuda a **investigar**, propone
**opciones**, registra una **decisión**, deja **aprobar**, corre **dry-run**
seguro, ejecuta el primer **write-back interno auditado** y **audita** todo el
recorrido. El Control Room es el cockpit de ese ciclo.

**Lo que la demo NO hace (no lo prometas):**

- ❌ **No hay write-back externo universal.** V1 solo soporta el flujo
  `create_followup_task`: crea un seguimiento operativo real en
  `decision_actions` después de decisión, dry-run y confirmación explícita.
  Cualquier otro template queda bloqueado y auditado.
- ❌ **No hay write-back SAP/Replicon.** Los cartuchos extraen/consultan;
  no escriben a sistemas externos en esta versión.

## Arquitectura (esta fase)

| Pieza | URL | Qué es |
|---|---|---|
| Consola | http://localhost:8000 | FastAPI sirve el static export de Next + APIs |
| Control Room | http://localhost:8000/control-room | servido por FastAPI (`:8000`) |
| Backend health | http://localhost:8000/healthz | liveness, sin auth (incluye `version` + `app_env`) |
| Backend readiness | http://localhost:8000/readyz | dependencias (Postgres + sibling services) |
| Superset | http://localhost:8088 | analytics |
| Airflow | http://localhost:8082 | orquestación |

No hay runtime oficial en `:3000`; cualquier feature solo disponible ahí no
cuenta para beta/v1.0.

## Prerrequisitos

| Herramienta | Mínimo |
|---|---|
| Docker Engine | 24+ con **daemon corriendo** |
| Docker Compose | v2.20+ |
| GNU make | 4.0 |
| Python 3 (host) | stdlib funcional (`base64`/`os`) para generar Fernet keys |
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
  `make e2e` la primera vez). Define `BASE_URL`, `BACKEND_URL` y
  `LEGACY_URL` apuntando a `:8000`.

## Orden de validación (de arriba a abajo)

### 0. Preflight (antes de todo)

```bash
bash scripts/preflight.sh
bash infra/bootstrap.sh
bash infra/bootstrap-keys.sh infra/.env
docker compose -f infra/docker-compose.yml config -q
make up
```

Chequea daemon de Docker, `docker compose`, validez del compose, `infra/.env`,
generación de Fernet keys con Python stdlib, puertos ocupados, e imprime las
URLs finales. Sale `0` solo si podés correr `make up`; sale `!=0` si hay un
bloqueador. Encadenable: `make preflight && make up`.

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
curl -fsS http://localhost:8000/control-room -o /dev/null -w "control-room %{http_code}\n"
curl -fsS http://localhost:8088/health
curl -fsS http://localhost:8202/health
curl -fsS http://localhost:8203/health
curl -fsS http://localhost:8204/health
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

### `make up` falla generando Fernet keys
`infra/bootstrap.sh` usa solo Python stdlib (`base64`/`os`) para generar claves
Fernet. Si falla aquí, el Python del host está roto o no existe; reinstalá
Python 3 y reintentá `make up`.

### Playwright falla
- `Cannot find module '@playwright/test'` → `cd tests-e2e && npm install`.
- Navegador ausente → `npx playwright install chromium`.
- Timeouts / login → el stack tiene que estar **arriba** en `:8000`.
  El `global-setup` hace login una vez contra FastAPI; si `:8000` no
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
- [ ] `/healthz`, `/readyz`, Superset y SAP health responden 200.
- [ ] `make smoke` = 34/34.
- [ ] `make test` verde.
- [ ] Playwright `12-control-room` verde.
- [ ] Login con el usuario seed entra a `:8000` y el link "Control Room"
      abre `/control-room`.
- [ ] En el Control Room: navegás dominio/módulo, abrís un item, ves los 7
      pasos OMEGA, y "Ejecutar interno" solo queda habilitado para
      `create_followup_task` con flag, decisión, dry-run y confirmación.
