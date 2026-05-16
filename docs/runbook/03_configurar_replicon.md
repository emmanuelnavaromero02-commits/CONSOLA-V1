# 03 — Configurar el cartucho Replicon

> Audiencia: admin que quiere conectar OMEGA a Replicon (workforce
> management) y ejecutar la primera extracción.

## Pre-requisitos

- Tenant creado ([02](02_primer_tenant.md)).
- Credenciales reales de Replicon:
  - `REPLICON_BASE_URL` — p.ej. `https://acme.replicon.com`
  - `REPLICON_API_KEY` — bearer token con permiso de export
- Los secretos vivirán en **Vault**, no en `.env`.

## Pasos

### 1. Abrir el wizard

http://localhost:8000/cartridges → tarjeta **replicon**.

### 2. Subir credenciales al Vault

`Configurar` → te muestra el `connector.yaml` con qué env vars espera
el cartucho:

```yaml
auth:
  type: bearer_token
  env_var: REPLICON_API_KEY
api:
  base_url_env: REPLICON_BASE_URL
```

Ve a **`/settings`** → categoría `integrations` → set:

| Key | Value |
|---|---|
| `replicon_base_url` | `https://acme.replicon.com` |
| `replicon_token`    | `<bearer real>` (marcado `is_secret=true`) |

El valor queda cifrado en Postgres (`system_settings.value`, Fernet).

> Cada `update` queda registrado en `audit_events` con
> `ip` + `user_agent` del navegador del admin (v1.41.0
> forensic-complete).

### 3. Probar la conexión

En la tarjeta replicon → `Test connection`. Debe responder en <2s:

- ✅ `ok` → el cartucho hizo handshake con Replicon.
- ⚠️ `degraded` → endpoint responde 200 pero el payload no es JSON
  válido (probable error de URL, no de credencial).
- ❌ `error` → leer el mensaje; suele ser 401 (token caducado) o
  ECONNREFUSED (URL inválida).

### 4. Ejecutar la primera carga

`Entidades` → tabla con las entidades del cartucho. Elegir **users** →
`Full`. Confirmar.

Vuelves al home cuando ves el toast `users (full) → queued`.

### 5. Seguir el run

Ir a **`/operations`** → tabla "extracciones recientes". La fila tendrá
`status=running` → `success` cuando termine (1–5 min para users).

Alternativa CLI:

```bash
docker exec mode_postgres psql -U postgres modecissions -c \
  "SELECT run_id, status, records_extracted, started_at, finished_at
     FROM extraction_runs
     WHERE cartridge_id='replicon' AND entity_name='users'
     ORDER BY started_at DESC LIMIT 5;"
```

### 6. Validar el dato aterrizado

```bash
curl -fsS -H "Cookie: session=$SESSION" \
  http://localhost:8000/api/freshness/replicon
```

Devolverá JSON con `users.age_seconds` cerca de 0 si la carga acabó
hace nada.

## Si algo falla

- **`test_connection` → 502**: el cartucho replicon está unhealthy.
  `docker compose ps` y `docker compose logs replicon`.
- **Run queda `running` >10 min**: probable que el scheduler de Airflow
  no haya recogido el DAG. Ver Airflow UI (http://localhost:8082) →
  `replicon_users_full`.
- **`error_message: 401 Unauthorized`**: token Replicon caducado. Re-set
  `replicon_token` en `/settings` y vuelve a correr.
