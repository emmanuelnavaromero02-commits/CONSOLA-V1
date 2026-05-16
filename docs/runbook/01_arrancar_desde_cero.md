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
OMEGA_CARTRIDGE_SAP_HCM_PASSWORD=...
OMEGA_CARTRIDGE_SAP_S4_PASSWORD=...
OMEGA_CARTRIDGE_SAP_SF_PASSWORD=...
OMEGA_AIRFLOW_DAG_PASSWORD=...
OMEGA_AIRFLOW_META_PASSWORD=...
OMEGA_SUPERSET_META_PASSWORD=...
```

> No copies este bloque a Slack ni a un repo — son todos secretos.

## Pasos

```bash
cd /opt/omega                 # raíz del repo
docker compose -f infra/docker-compose.yml down -v     # estado limpio
docker compose -f infra/docker-compose.yml --profile sap up -d --build
```

Espera 90 segundos. Luego:

```bash
docker compose -f infra/docker-compose.yml ps
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

Salida esperada: `30/30 OK ✅`.

## Sanity HTTP

```bash
curl -fsS http://localhost:8000/healthz                # console
curl -fsS http://localhost:8201/health                 # replicon
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
| `vault` (unhealthy) | `OMEGA_VAULT_PASSWORD` no coincide con el role en Postgres | `make smoke` muestra qué role / DSN espera |
| `airflow` (unhealthy) | DAG con import error bloquea el scheduler | `docker compose logs airflow-scheduler` |
| `superset` 502 | `superset-init` aún corriendo | espera 30s, reintenta |

Si tras 3 minutos hay servicios unhealthy, ver
[07 Debug fallos](07_debug_fallos.md).
