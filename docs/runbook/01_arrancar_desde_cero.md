# 01 — Arrancar OMEGA desde cero

> Audiencia: SRE / operador que recibe una nueva máquina y necesita
> levantar la plataforma completa, sin estado previo.

## Pre-requisitos

| Herramienta | Versión mínima |
|---|---|
| Docker Engine | 24.0 |
| Docker Compose | v2.20 |
| GNU make | 4.0 |
| 16 GB RAM libres | – |
| 30 GB disco libre | – |

Variables obligatorias en `.env` (junto a `infra/docker-compose.yml`):

```
POSTGRES_PASSWORD=...
MINIO_SECRET_KEY=...
INTERNAL_API_KEY=...
FIELD_ENCRYPTION_KEY=...   # Fernet key (44 chars base64)
AIRFLOW_SECRET_KEY=...
AIRFLOW_ADMIN_USER=admin
AIRFLOW_ADMIN_PASSWORD=...
SUPERSET_SECRET_KEY=...
OMEGA_CONSOLE_PASSWORD=...
OMEGA_REFINEMENT_PASSWORD=...
OMEGA_VAULT_PASSWORD=...
OMEGA_WORKSPACE_PASSWORD=...
OMEGA_MCP_INFRA_PASSWORD=...
OMEGA_CARTRIDGE_REPLICON_PASSWORD=...
OMEGA_CARTRIDGE_HUBSPOT_PASSWORD=...
OMEGA_CARTRIDGE_SAP_HCM_PASSWORD=...
OMEGA_CARTRIDGE_SAP_S4_PASSWORD=...
OMEGA_CARTRIDGE_SAP_SF_PASSWORD=...
OMEGA_AIRFLOW_DAG_PASSWORD=...
OMEGA_AIRFLOW_META_PASSWORD=...
OMEGA_SUPERSET_META_PASSWORD=...
```

> No copies este bloque a Slack ni a un repo — son todos secretos.

## Pasos

> **Importante (v1.43.4 — Codex H3):** el arranque tiene que hacerse
> en dos fases. Levantar TODO el stack a la vez con `up -d` puede
> race-condition con la inicialización de Postgres en máquinas
> lentas: los servicios de aplicación intentan conectar antes de
> que las migraciones SQL en `infra/init/` hayan terminado, fallan,
> entran en restart-loop, y el resto del stack arranca contra una
> DB incompleta. v1.43.4 introdujo healthchecks + `service_healthy`
> deps que evitan esta condición; el orden de abajo es la garantía
> redundante.

### Paso 1 — Postgres primero (60-90 s)

```bash
cd /opt/omega                 # raíz del repo

# Opcional, SOLO local/desarrollo y destruye volúmenes Docker locales:
make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev

make preflight
make bootstrap-env
docker compose -f infra/docker-compose.yml up -d postgres postgres_gold

# Polling hasta que Postgres reporte healthy (≈30-90 s en máquina lenta).
# El `pg_isready` corre dentro del contenedor y refleja el healthcheck.
for i in $(seq 1 18); do
    status="$(docker inspect --format='{{.State.Health.Status}}' mode_postgres 2>/dev/null || echo unknown)"
    if [ "$status" = "healthy" ]; then echo "Postgres listo"; break; fi
    sleep 5
done

# Verificar también el gold:
docker inspect --format='{{.State.Health.Status}}' mode_postgres_gold
# → debe imprimir: healthy
```

### Paso 2 — Resto del stack

```bash
make up
sleep 180   # margen para arranque de cartridges + airflow + superset
make ps
```

Verificación:

- Cada servicio long-running aparece como **(healthy)** o **Up**.
- 0 contenedores en estado **restarting**.
- `airflow-init` y `superset-init` aparecen como **Exited (0)** —
  son one-shot, terminar es lo esperado.

Smoke:

```bash
make smoke
```

Salida esperada: `34/34 OK ✅`.

## Login local de desarrollo

En el compose local, el seed de desarrollo crea el usuario
`emmanuel@local.ai` con password `Admin123!`. Ese usuario existe solo
para levantar y validar la consola local; en un entorno real usa el
flujo de bootstrap admin de
[02 Primer tenant](02_primer_tenant.md) y rota la contraseña antes de
cargar datos sensibles.

## Sanity HTTP

```bash
curl -fsS http://localhost:8000/healthz                # console
curl -fsS http://localhost:8201/health                 # replicon
curl -fsS http://localhost:8210/health                 # HubSpot CRM
curl -fsS http://localhost:8202/health                 # SAP HCM
curl -fsS http://localhost:8203/health                 # SAP SuccessFactors
curl -fsS http://localhost:8204/health                 # SAP S/4HANA
curl -fsS http://localhost:8010/healthz                # mcp-infra
curl -fsS http://localhost:8300/healthz                # vault
curl -fsS http://localhost:8500/healthz                # refinement
curl -fsS http://localhost:8001/healthz                # workspace
curl -fsS http://localhost:8088/health                 # superset
curl -fsS http://localhost:8082/health                 # airflow web
```

Todos deben responder 200.

## Si algo falla

| Síntoma | Causa probable | Fix |
|---|---|---|
| `console` en restart loop | `INTERNAL_API_KEY` o `FIELD_ENCRYPTION_KEY` ausente | revisa `.env` y `docker compose logs console` |
| `vault` (unhealthy) | `OMEGA_VAULT_PASSWORD` no coincide con el role en Postgres | `make repair-local-stack` sincroniza roles locales con `infra/.env` |
| `airflow` (unhealthy) | DAG con import error bloquea el scheduler | `docker compose logs airflow-scheduler` |
| `superset` 502 | `superset-init` aún corriendo | espera 30s, reintenta |
| Superset no descifra conexiones tras rotar `SUPERSET_SECRET_KEY` local | metastore local cifrado con la key anterior | `CONFIRM_SUPERSET_METASTORE_REPAIR=LOCAL_SUPERSET_REPAIR make repair-local-stack` |

Si tras 3 minutos hay servicios unhealthy, ver
[07 Debug fallos](07_debug_fallos.md).
