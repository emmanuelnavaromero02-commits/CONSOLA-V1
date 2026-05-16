# 04 — Configurar los cartuchos SAP (HCM / S4 / SuccessFactors)

> Audiencia: admin que va a conectar OMEGA a un sistema SAP existente.
> Aplica a los 3 cartuchos SAP (`sap_hcm`, `sap_s4hana`,
> `sap_successfactors`); el flujo es idéntico — sólo cambian los
> nombres de variables y endpoints.

## Pre-requisitos

- Tenant creado ([02](02_primer_tenant.md)).
- Credenciales reales del sistema SAP correspondiente.
- Los cartuchos SAP requieren `--profile sap` al `up`. Si los servicios
  `sap-*` no aparecen en `docker compose ps`, relanza:
  ```bash
  docker compose -f infra/docker-compose.yml --profile sap up -d
  ```

## Tabla de variables por cartucho

| Cartucho            | Setting key (en /settings)        | Origen      |
|---------------------|-----------------------------------|-------------|
| **sap_hcm**         | `sap_hcm_base_url`                | SAP basis   |
|                     | `sap_hcm_user`                    | RFC user    |
|                     | `sap_hcm_pass`                    | RFC pass    |
|                     | `sap_hcm_client_mandant` (default `100`) | – |
| **sap_s4hana**      | `sap_s4_base_url`                 | OData v2/v4 |
|                     | `sap_s4_user` + `sap_s4_pass`     | técnico     |
|                     | `sap_s4_api_key`                  | si aplica   |
| **sap_successfactors** | `sf_base_url` + `sf_company_id`   | tenant      |
|                     | `sf_client_id` + `sf_client_secret` + `sf_token_url` | OAuth |

## Pasos (idéntico para los 3)

### 1. Cargar secretos en /settings

http://localhost:8000/settings → categoría `integrations` → set cada
key de la tabla anterior. Marcar `is_secret=true` para passwords /
tokens / client_secret.

Cifrado Fernet en reposo. Cada set queda en `audit_events` con `ip`,
`user_agent` y `action=settings.update`.

### 2. Test connection

`/cartridges` → tarjeta `sap_*` → `Test connection`.

- ✅ `ok` — handshake con OData metadata.
- ⚠️ `degraded` — endpoint responde pero metadata vacía. URL apunta
  al server correcto pero a un service path inválido.
- ❌ `error` — leer el `message`:
  - `401` → user/pass incorrectos
  - `404` → base_url o service path mal puesto
  - `502 Bad Gateway` → el SAP backend está caído (no es OMEGA)

### 3. Listar entidades disponibles

`/cartridges/{c}/entities` (la tabla del wizard). El cartucho consulta
`entity_config` en Postgres + `get_watermarks` del propio cartucho:

```bash
curl -fsS -H "Cookie: session=$SESSION" \
  http://localhost:8000/api/cartridges/sap_hcm/entities
```

### 4. Ejecutar carga

Para HCM: elegir `pa0001` (datos maestros empleado) → `Full`. Confirm.

Para S4: `BusinessPartner` o `MaterialMaster` → `Full`.

Para SF: `PerPerson` → `Full`.

### 5. Verificar

```sql
SELECT cartridge_id, entity_name, status, records_extracted
FROM extraction_runs
WHERE cartridge_id LIKE 'sap_%'
ORDER BY started_at DESC LIMIT 10;
```

## Si algo falla

- **`test_connection` devuelve `degraded` pero credenciales son
  correctas**: el `base_url` apunta a la página HTML del Fiori Launchpad
  en vez de a `/sap/opu/odata`. Corregir.
- **Run de pa0001 con 0 records**: filtro de `mandant` mal puesto.
  Revisa `sap_hcm_client_mandant`.
- **Cargas lentas (>30 min)**: SAP corp tiene rate limits agresivos.
  Bajar `page_size` en `entity_config` y/o re-correr en horario
  off-peak.
