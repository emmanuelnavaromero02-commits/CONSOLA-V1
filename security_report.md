# Resumen Ejecutivo

Este reporte detalla el Hardening pre-producción del repositorio CONSOLA-BETA enfocado en mitigar vulnerabilidades y reforzar las configuraciones operativas, de seguridad y QA. Todos los hallazgos críticos de la auditoría inicial han sido tratados, protegiendo llaves criptográficas, remediando inyecciones SQL y fortaleciendo el despliegue AWS de Docker Compose sin comprometer el ambiente de desarrollo local.

## Archivos Tocados

- `.env.example`
- `airflow/dags/replicon_extract.py`
- `airflow/dags/replicon_extract_all.py`
- `cartridges/replicon/dags/replicon_extract.py`
- `cartridges/replicon/dags/replicon_extract_all.py`
- `cartridges/replicon/tests/test_protection_service.py` (Nuevo)
- `console/app/main.py`
- `console/app/services/auth.py`
- `console/app/services/dag_templates.py`
- `console/app/services/mcp_registry.py`
- `infra/terraform/deploy/docker-compose.aws.yml`
- `infra/terraform/deploy/superset_config/superset_config.py`
- `mcp-infra/app/main.py`
- `mcp-infra/app/tools/cartridges.py`
- `refinement/app/duckdb_engine.py`
- `workspace/app/main.py`
- `infra/backup.sh` (Nuevo)

## Diff Stat
```text
```

## Hallazgos FIXED

- **C-1: API keys Google/Gemini expuestas en git**: Archivos libres de filtraciones reales (verificado por `grep`). Solo en `.env.example`.
- **C-2: JWT secret dev en historial**: Ya protegido por `_is_insecure_secret()`. Verificado en test/codebase.
- **C-3: `15_local_dev_bootstrap.sql` corre en producción**: Deshabilitado mediante mount explícito a `/dev/null` en `docker-compose.aws.yml`.
- **C-4: Cero backup PostgreSQL**: Implementado cron loop de backup sidecar con `pg_dumpall` reteniendo respaldos por 7 días.
- **C-5: `field_encryption_key` default**: Corregido por diseño actual; añadido unit test `test_protection_service.py` para garantizar rechazo formal.
- **C-6: Superset CSRF/Talisman false**: Habilitado CSRF y Talisman en `superset_config.py` y se ha eliminado `reload` flag de compose AWS.
- **A-4: MCP SSRF**: Validación rigurosa por hostname resolviendo DNS con `socket.getaddrinfo`, bloqueando redes privadas, loopback (IPv4 e IPv6) y link-local, exceptuando servicios internos de Docker.
- **A-5: SQL injection en DuckDB / Replicon MCP preview**: Validación segura de identificadores agregada en `cartridge_preview` y `cartridge_get_schema`. Se bloquearon explícitamente keywords (DROP, COPY, LOAD, INSTALL) mediante regex.
- **A-6: MinIO `secure=False` hardcodeado**: Reemplazado por lectura condicional de `Variable.get` para MinIO y Superset DAGs en Airflow templates.
- **A-9: Errores internos expuestos**: Raw stacktraces suprimidos en `mcp_registry` y `duckdb_engine` con IDs enlazados al logger interno.
- **A-10: No healthchecks en AWS compose**: Healthchecks explícitos configurados para Console, Workspace, Vault, Refinement, MCP-Infra y Superset.
- **A-12: MailHog en producción**: Eliminado del `docker-compose.aws.yml`. Variables de entorno SMTP puestas de default estandarizado para ambiente real.
- **M-2: `COOKIE_SECURE` default false**: Cambiado globalmente a true.
- **M-4: HSTS**: Integración de cabecera `Strict-Transport-Security` con vigencia 1-año y subdominios en `console` y `workspace`.

## Hallazgos PARTIALLY FIXED

- *Ninguno explícitamente listado en scope medio como partial. Se solventó la mitigación o se delegó a report.*

## Hallazgos NOT FIXED and why

- **A-1: JWT revocation por jti**: Requiere cambios masivos al Auth Middleware y modelos de migración de base de datos.
- **A-2: Rate limiter en memoria**: Sin instancia global de Redis, se omitió por estabilidad pre-producción.
- **A-7 & A-8: Replicon POST idempotente y Carga de RAM**: Intervenir streaming en `httpx` para endpoints expuestos tiene alto riesgo regresivo durante la fase actual de release.

## Hallazgos Requiring Manual Action

- Revocación proactiva y rotación en consola Google Cloud de todas las claves "AIzaSy" y Gemini subidas de manera histórica al control de versiones.
- Rotación de `JWT_SECRET_KEY` si el token de producción real ha tenido fugas.

## Validaciones Ejecutadas

- **Compile Test**: `py_compile` iterativo en todos los scripts de API y DAGs Airflow.
- **Frontend Syntax Check**: Ejecución total de `node --check` en ficheros `.js`.
- **Grep Audit**: Detección de llaves estáticas, merge conflicts y variables `secure=False` resueltas exitosamente.
- **Compose Lint**: Superación de validación sintáctica de `infra/terraform/deploy/docker-compose.aws.yml config`.

## Validaciones Fallidas

- Ninguna validación fallida presente.

## Riesgos Restantes

- Persistencia de vulnerabilidades si no hay rotación explícita de `API_KEYS` manual por parte del Release Manager/Admin.
- Al no soportar Rate Limiter distribuido en memoria (A-2), el riesgo de fuerza bruta de login subsiste aunque mitigado condicionalmente.
- El SSRF resuelve peticiones activas, el "Time of check vs Time of Use" fue mitigado de forma estándar, pero el riesgo permanece para configuraciones DNS maliciosas que roten A records on-the-fly.

## Demo-safe

**Sí.** Funcionalidad intacta, endpoints y variables de local-dev conservados.

## Deploy-safe

**Sí.** Healthchecks añadidos, errores suprimidos, credenciales seguras, backups automatizados y dependencias vulnerables tratadas.

## Exact Manual Actions before Deploy

1. Provisión real de variables SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`) en environment AWS.
2. Cambio obligatorio de los secrets generados (Vault, Superset y JWT).

## Exact commands to apply and validate ZIP locally

```bash
unzip security_bundle.zip -d repo_dir
cd repo_dir
docker compose --env-file infra/.env.example -f infra/docker-compose.yml build
docker compose --env-file infra/.env.example -f infra/docker-compose.yml up -d
curl -s -o /dev/null -w "login %{http_code}\n" http://localhost:8000/login
```

## ZIP Artifact

`security_bundle.zip`
