# AUDIT BASELINE NO STUDIO

Fecha: 2026-05-28  
Repo: `/Users/emmanuel/CONSOLA-BETA`  
Branch/commit auditado: `codex/restore-console-wiring` @ `557cd2b`  
Regla aplicada: Studio congelado. Nada en Studio se modifica ni se propone como cambio directo.

## A) Resumen ejecutivo en cristiano

Nota global actual estimada: 7.3/10.

Nota para demo controlada: 8.0/10 si se comunica claramente que Control Room ejecuta preview/dry-run y que el write-back productivo no existe en V1.

Nota para beta privada: 6.8/10. Hay mucho cableado real y los tests base estan fuertes, pero todavia hay riesgos de operacion viva: write-back, CSP de Control Room, env contract, health dependencies locales y gaps de E2E vivo.

Nota para produccion: 5.5/10. Produccion queda bloqueada por write-back incompleto, cobertura/coverage insuficiente, hardening pendiente de Control Room, y validacion viva end-to-end del stack.

Que esta bien:

- Los DAGs SAP HCM, SAP S/4HANA y SAP SuccessFactors existen, estan montados en local y AWS compose, y tienen tests especificos.
- El frontend `console-next` compila en exportacion estatica, tiene API client same-origin con cookies, CSRF y `X-Request-ID`.
- Marketplace existe y esta cableado con permisos backend, no desaparecio.
- Admin users/invite/reset/vpn-reissue ya no estan expuestos sin permisos; tienen CSRF, `iam.users.write` y validacion de workspace/target.
- Las rutas principales de Next estan protegidas por FastAPI y no dependen de Node/3000 para servirse.
- Root tests, console tests, vault tests, frontend lint/typecheck/build y Control Room lint/typecheck/build pasaron en este baseline.

Que sigue siendo peligroso:

- Control Room todavia no tiene write-back real. Con el flag apagado responde 409; con el flag prendido responde 501.
- El MCP directo de SAP SuccessFactors permite SQL DuckDB arbitrario en `query_kb` sin el `sql_guard` que ya tienen HCM/S4.
- `infra/.env.example` no documenta varias variables usadas por codigo/compose y tiene al menos un mismatch real de bootstrap admin name.
- Local compose todavia usa `service_started` en dependencias sensibles; AWS esta mejor.
- Control Room sigue permitiendo `script-src 'unsafe-inline'`, a diferencia del shell Next que calcula hashes CSP.
- Hay HTML/JS legacy vivo bajo `console/app/static`. No todo esta activo, pero existe superficie legacy que puede confundirse con la TSX.
- E2E Playwright esta presente y grande, pero en este baseline solo se pudo listar; no se ejecuto suite viva completa contra stack por ser una auditoria sin mutacion.

Que NO se debe tocar por la regla de Studio:

- `console-next/src/app/(shell)/studio/**`
- `console/app/routers/studio.py`
- `console/app/static/studio.html`
- `console/app/static/js/studio/**`
- `console/app/static/js/action-bridge.js`
- `console/app/static/js/wire-handlers.js`
- Cualquier ruta, texto, menu, link o contrato `/studio` o `/api/studio/*`.

Si algo aparece ahi, queda marcado como: FUERA DE ALCANCE POR REGLA DEL USUARIO.

## B) Hallazgos confirmados

### P1-ENV-001 - `.env.example` no cubre variables reales de runtime

- Estado: CONFIRMADO.
- Evidencia exacta:
  - Script local de auditoria: `env_example_keys=127`, `code_env_keys=158`, `missing=82`.
  - `infra/.env.example:176-179` solo documenta `REFINEMENT_URL` dentro de "Internal service URLs".
  - `console/app/bootstrap_admin.py:73-78` documenta `BOOTSTRAP_ADMIN_NAME`.
  - `console/app/bootstrap_admin.py:88` lee `BOOTSTRAP_ADMIN_NAME`.
  - `infra/.env.example:220-222` documenta `BOOTSTRAP_ADMIN_FULL_NAME`, no `BOOTSTRAP_ADMIN_NAME`.
  - Ejemplos usados por codigo y no documentados claramente: `CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK` en `console/app/services/control_room_service.py:1964-1974`, `RATE_LIMIT_ENABLED` en `console/app/main.py:999-1019`, `TRUSTED_PROXY_IPS` en `console/app/main.py:927-941`, URLs internas de SAP en `console/app/services/mcp_registry.py`.
- Impacto real: operadores pueden levantar el stack con variables faltantes o nombres incorrectos y obtener fallos de login inicial, rate-limit, Control Room o servicios internos.
- Riesgo si no se corrige: errores de primer boot, diferencias local/AWS, configuracion tribal y fallos dificiles de diagnosticar.
- Tamano estimado: mediano.
- Prompt recomendado para arreglarlo despues: Prompt 1.

### P1-INFRA-001 - Local compose todavia tiene dependencias `service_started`

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `infra/docker-compose.yml:231-239`: `console` espera `mcp-infra` y `mailhog` con `service_started`.
  - `infra/docker-compose.yml:284-292`: `workspace` espera `refinement` y `mcp-infra` con `service_started`.
  - `infra/docker-compose.yml:683-691`: `mcp-infra` espera `airflow` con `service_started`.
  - AWS esta corregido en `infra/terraform/deploy/docker-compose.aws.yml:167-175` y `208-216` con `service_healthy`.
- Impacto real: el stack local puede verse "levantado" antes de que servicios aguas arriba respondan, causando errores intermitentes en consola/workspace/MCP.
- Riesgo si no se corrige: pruebas flakey, "veo rojo" al abrir la UI, y fallos falsos en demo local.
- Tamano estimado: chico-mediano.
- Prompt recomendado para arreglarlo despues: Prompt 2.

### P1-SQL-001 - SAP SuccessFactors `query_kb` no usa guard SQL

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `cartridges/sap_successfactors/app/mcp_server.py:306-325` acepta `sql: str`, reemplaza `{bucket}`, mete f-string de `LIMIT` y ejecuta `conn.execute(resolved)`.
  - `cartridges/sap_successfactors/app/mcp_server.py:336` devuelve el SQL resuelto en errores.
  - HCM y S/4 si tienen guard: `cartridges/sap_hcm/app/mcp_server.py:317-330`, `cartridges/sap_s4hana/app/mcp_server.py:317-330`.
  - `mcp-infra/app/main.py:949-953` valida `cartridge_query_kb` para llamadas via infra cuando no es admin unscoped, pero eso no cubre igual el MCP directo de SF.
- Impacto real: superficie de lectura DuckDB mas amplia de lo necesario en SF, inconsistente con HCM/S4.
- Riesgo si no se corrige: lectura de paths no autorizados, DoS por SQL pesado, fuga de path/SQL en mensajes de error.
- Tamano estimado: mediano.
- Prompt recomendado para arreglarlo despues: Prompt 3.

### P1-CR-001 - Control Room write-back externo sigue incompleto

- Estado: CONFIRMADO.
- Evidencia exacta:
  - Flag default false: `console/app/services/control_room_service.py:1964-1974`.
  - Si esta apagado: `console/app/services/control_room_service.py:4880-4919` registra bloqueo y lanza 409.
  - Si esta prendido: `console/app/services/control_room_service.py:4920` lanza 501 `live write-back connector is not implemented in V1`.
  - Runbook honesto: `docs/runbook/09_demo_beta.md:17`.
  - E2E lo espera como comportamiento V1: `tests-e2e/specs/12-control-room.spec.ts:98-99`.
- Impacto real: el pilar "Ejecucion Operativa" no escribe en ERP/SAP; recomienda, registra y bloquea antes de la escritura.
- Riesgo si no se corrige: demo puede sobrevivir si se comunica, pero beta/produccion no pueden vender "actua en ERP" como hecho.
- Tamano estimado: grande.
- Prompt recomendado para arreglarlo despues: Prompt 4.

### P1-CR-CSP-001 - Control Room permite `script-src 'unsafe-inline'`

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `console/app/main.py:818-829` define `CONTROL_ROOM_SECURITY_HEADERS` con `script-src 'self' 'unsafe-inline'`.
  - El shell Next usa hashes CSP para inline scripts: `console/app/routers/pages.py:50-75`.
  - Test documenta el estado actual: `console/tests/test_strict_csp_phase3.py:188-190` espera `unsafe-inline` en `/control-room`.
- Impacto real: Control Room es la excepcion principal a la politica CSP estricta de `script-src 'self'`.
- Riesgo si no se corrige: XSS tiene menos contencion en la pantalla mas operativa.
- Tamano estimado: mediano-grande.
- Prompt recomendado para arreglarlo despues: Prompt 5.

### P2-STATIC-001 - Sigue existiendo HTML/JS legacy fuera de Studio

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `console/app/static/cartridges.html`
  - `console/app/static/copilot.html`
  - `console/app/static/monitor.html`
  - `console/app/static/viewers/lineage.html`
  - `console/app/static/viewers/vault.html`
  - `console/app/static/js/cartridges.js`
  - `console/app/static/js/copilot.js`
  - `console/app/static/js/monitor.js`
  - `console/app/static/js/viewers/*`
  - Rutas modernas sirven TSX exportado para varias superficies: `console/app/routers/pages.py:196-208`, `247-308`, `353-456`, y `console/app/main.py:3577-3609`, `3812-3838`, `5047-5051`.
- Impacto real: aunque muchas rutas ya apuntan a Next exportado, queda superficie legacy que puede confundirse con "lo oficial" y hacer drift.
- Riesgo si no se corrige: regresiones visuales, deep-links viejos, CSP legacy y mantenimiento doble.
- Tamano estimado: mediano.
- Prompt recomendado para arreglarlo despues: Prompt 6.

### P2-KB-001 - SAP KBs existen, pero hay gaps de negocio documentados como parciales/TODO

- Estado: CONFIRMADO.
- Evidencia exacta:
  - Conteo real: HCM 10 entidades / 12 KBs; S4 25 entidades / 12 KBs; SF 30 entidades / 14 KBs.
  - SF KB parcial: `cartridges/sap_successfactors/app/config/knowledge_bits.yaml:196-207` dice que recruitment funnel y turnover son parciales.
  - S4 KB parcial: `cartridges/sap_s4hana/app/config/knowledge_bits.yaml:102-111` estima vencimiento a 30 dias porque falta estado de pago/partidas FI.
  - S4 inventario parcial: `cartridges/sap_s4hana/app/config/knowledge_bits.yaml:135-143`.
  - Dataset TODOs detectados en `cartridges/sap_successfactors/datasets/sap_successfactors_recruitment_pipeline.sql:5`, `sap_successfactors_compensation_distribution.sql:5`, `cartridges/sap_hcm/datasets/workforce_cost_monthly.sql:5`, y seed SQL 80/81/82.
- Impacto real: el copiloto enterprise ya tiene material, pero algunas respuestas relevantes seran aproximaciones o parciales.
- Riesgo si no se corrige: expectativa comercial alta frente a datos reales incompletos, sobre todo payroll/compensacion/reclutamiento/inventario/cobranza.
- Tamano estimado: grande.
- Prompt recomendado para arreglarlo despues: Prompt 7.

### P2-FE-UNIT-001 - Tests unitarios frontend existen pero son muy superficiales

- Estado: CONFIRMADO COMO GAP RESIDUAL.
- Evidencia exacta:
  - `console-next/package.json:12-14` tiene `typecheck`, `lint`, `test`.
  - Solo hay dos unit tests TS en `console-next/src/lib/legacy-url.test.ts` y `console-next/src/lib/utils.test.ts`.
  - Ejecucion local: Vitest paso `2 passed`, `8 tests`.
- Impacto real: componentes criticos como AppChrome, Marketplace, Operations, Vault, Cartridges y Copilot dependen sobre todo de E2E/estatico, no de unit/component tests.
- Riesgo si no se corrige: cambios visuales o de permisos pueden romper UI sin feedback rapido.
- Tamano estimado: mediano.
- Prompt recomendado para arreglarlo despues: Prompt 8.

### P2-CI-COV-001 - CI no publica coverage

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `.github/workflows/lint.yml:42-69` corre lint/test/typecheck/export para console-next, sin coverage.
  - `.github/workflows/e2e.yml:72-81` corre Playwright y sube reporte, sin coverage.
  - `.github/workflows/release.yml:65-77` corre lint/test/export, sin coverage.
  - Busqueda `pytest-cov|coverage|codecov|cov-report` no encontro reporte de coverage CI activo.
- Impacto real: hay muchos tests, pero no hay linea base cuantitativa para saber que areas quedan sin tocar.
- Riesgo si no se corrige: falsa sensacion de seguridad y regresiones en modulos con baja cobertura.
- Tamano estimado: chico-mediano.
- Prompt recomendado para arreglarlo despues: Prompt 8.

### P2-MONO-001 - Monolitos grandes siguen vivos

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `console/app/main.py`: 5939 lineas.
  - `console/app/services/control_room_service.py`: 5473 lineas.
  - `console/control-room-next/app/page.tsx`: 3936 lineas.
  - `console/app/static/js/studio/legacy.js`: 4599 lineas, FUERA DE ALCANCE POR REGLA DEL USUARIO.
- Impacto real: cualquier cambio pequeño puede tener blast radius alto y conflictos de merge.
- Riesgo si no se corrige: velocidad cae y sube el riesgo de parches accidentales.
- Tamano estimado: grande.
- Prompt recomendado para arreglarlo despues: Prompt 9.

### P2-CART-UX-001 - Cartridge viewer muestra fallback generico mientras carga schema

- Estado: CONFIRMADO.
- Evidencia exacta:
  - `console-next/src/app/(shell)/cartridges/viewer/page.tsx:11-18` define fallback `base_url` + `token`.
  - `console-next/src/app/(shell)/cartridges/viewer/page.tsx:56-70` usa `schemaQuery.data ?? fallbackSchema(id)` mientras no hay error.
  - Backend si tiene schema dinamico: `console/app/routers/cartridges.py:128-143`.
- Impacto real: si el schema tarda, el usuario puede ver campos genericos que no necesariamente corresponden al cartucho.
- Riesgo si no se corrige: credenciales mal formadas, mensajes tipo "bad token" o Vault write/test confusos.
- Tamano estimado: chico.
- Prompt recomendado para arreglarlo despues: Prompt 6.

### P2-LIVE-E2E-001 - Stack vivo completo no queda probado en esta auditoria local

- Estado: CONFIRMADO COMO BRECHA DE EVIDENCIA.
- Evidencia exacta:
  - `tests` paso con 15 skips por falta de credenciales/stack E2E vivo.
  - `tests-e2e` lista 338 tests, pero solo se ejecuto `playwright test --list`; no se ejecuto la suite viva porque muta estado y requiere stack/credenciales.
  - Workflow CI si intenta stack vivo: `.github/workflows/e2e.yml:50-75`.
- Impacto real: el codigo y contratos pasan muchas pruebas, pero este baseline no demuestra un recorrido vivo de login, cartuchos, vault, copiloto, control room y servicios externos.
- Riesgo si no se corrige: se puede aprobar con pruebas estaticas aunque Docker/Airflow/Superset/Vault fallen integrados.
- Tamano estimado: mediano.
- Prompt recomendado para arreglarlo despues: Prompt 10.

## C) Hallazgos desmentidos

- DESMENTIDO: "Los DAGs SAP faltan". Existen 6 DAGs SAP en `cartridges/sap_hcm/dags`, `cartridges/sap_s4hana/dags`, `cartridges/sap_successfactors/dags`. `tests/test_sap_dags_deployed.py:15-21` los declara como esperados.
- DESMENTIDO: "Los DAGs SAP no estan montados". Local compose monta los DAGs en `infra/docker-compose.yml:831-835`, `921-929`, `1006-1014`; AWS compose en `infra/terraform/deploy/docker-compose.aws.yml:599-607`, `663-671`.
- DESMENTIDO: "Los DAGs SAP requieren secretos en parse-time". `tests/test_sap_dags_config.py:41-57` exige `_internal_key()` runtime y prohibe `_INTERNAL_API_KEY =`; targeted suite paso 32/32.
- DESMENTIDO: "Invite/send-reset/vpn-reissue no tienen permisos". `console/app/main.py:5692-5717` y `5832-5833` tienen CSRF y `require_permission("iam.users.write")`; ademas `5431-5450` valida workspace/target.
- DESMENTIDO PARCIAL: "No hay tests unitarios frontend". Si hay Vitest, pero solo 2 archivos/8 tests. El hallazgo correcto es "cobertura frontend superficial".
- DESMENTIDO: "Marketplace no esta". AppChrome lo lista en `console-next/src/components/AppChrome.tsx:36`; FastAPI sirve paginas en `console/app/main.py:3577-3609`; endpoints existen en `console/app/main.py:3611-3663`.
- DESMENTIDO PARCIAL: "Control Room duplicado entre Next y HTML legacy independiente". La version oficial parece ser `console/control-room-next` exportada a `console/app/static/control-room`, servida por `console/app/routers/pages.py:227-239`. No encontre `control-room.html` legacy top-level; el riesgo real es source/export drift + CSP.
- DESMENTIDO: "console-next depende de proxy Next". `console-next/src/lib/api.ts:53-78` usa fetch relativo, `credentials: include`, CSRF y `X-Request-ID`.

## D) Estado de DAGs SAP

Archivos existentes:

- `cartridges/sap_hcm/dags/sap_hcm_extract.py`
- `cartridges/sap_hcm/dags/sap_hcm_extract_all.py`
- `cartridges/sap_s4hana/dags/sap_s4hana_extract.py`
- `cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py`
- `cartridges/sap_successfactors/dags/sap_successfactors_extract.py`
- `cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py`

DAG IDs esperados:

- El test `tests/test_sap_dags_deployed.py:15-21` fija los seis archivos esperados. El test de imports `tests/test_sap_dag_imports.py:13-20` importa esos seis paths y exige objeto `dag` en `tests/test_sap_dag_imports.py:46-56`.

Montaje en Airflow local:

- `infra/docker-compose.yml:831-835` en `airflow-init`.
- `infra/docker-compose.yml:921-929` en `airflow`.
- `infra/docker-compose.yml:1006-1014` en `airflow-scheduler`.

Montaje en AWS compose:

- `infra/terraform/deploy/docker-compose.aws.yml:599-607` en `airflow`.
- `infra/terraform/deploy/docker-compose.aws.yml:663-671` en `airflow-scheduler`.

Tests existentes:

- `tests/test_sap_dags_deployed.py`
- `tests/test_sap_dag_imports.py`
- `tests/test_sap_dags_config.py`
- `tests/test_sap_hcm_kbs.py`
- `tests/test_sap_s4hana_kbs.py`
- `tests/test_sap_successfactors_kbs.py`
- `cartridges/sap_hcm/tests/test_query_kb_sql_guard_sap_hcm.py`
- `cartridges/sap_s4hana/tests/test_query_kb_sql_guard_sap_s4hana.py`

Import sin secretos en parse-time:

- Confirmado por `tests/test_sap_dags_config.py:41-57` y ejecucion local: 32 passed.

Que falta probar en runtime vivo:

- Que Airflow real detecte los 6 DAGs dentro de contenedor levantado.
- Que cada DAG pueda llamar el cartridge correspondiente con `INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE` real.
- Que SAP SuccessFactors/S4/HCM manejen credenciales Vault reales y errores de API reales.

## E) Estado de Infra/env

- `docker compose -f infra/docker-compose.yml config` ejecuto OK. El output expande secretos locales; no se copia al reporte.
- Local compose tiene healthchecks en servicios grandes, pero sigue usando `service_started` en `console`, `workspace` y `mcp-infra`.
- AWS compose esta mejor en depends_on/healthchecks; `tests/test_aws_compose_consistency.py:248-266` cubre healthchecks AWS.
- `infra/.env.example` esta bien orientado como plantilla, pero no cubre todo el contrato real de runtime y contiene el mismatch `BOOTSTRAP_ADMIN_FULL_NAME` vs `BOOTSTRAP_ADMIN_NAME`.
- Variables criticas faltantes o subdocumentadas: `DATABASE_URL`, `GOLD_DATABASE_URL`, `MCP_INFRA_URL`, `AIRFLOW_URL`, `SAP_HCM_URL`, `SAP_S4HANA_URL`, `SAP_SUCCESSFACTORS_URL`, `CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK`, `CONTROL_ROOM_ENABLE_EXTERNAL_DELIVERY`, `RATE_LIMIT_ENABLED`, `TRUSTED_PROXY_IPS`.
- Bootstrap existe y se documenta en `infra/.env.example:203-222`, pero el nombre de variable del display name no coincide con `console/app/bootstrap_admin.py:73-88`.
- Secretos: el repo local tiene `.env`/`infra/.env` ignorados. No se encontro evidencia de que deban commitearse; cuidado al copiar salidas de `docker compose config`.

## F) Estado de Seguridad

Permisos admin:

- Usuarios admin: `console/app/main.py:5453-5510`, `5692-5717`, `5832-5847`.
- Scope workspace: `console/app/main.py:5431-5450`.
- Roles: `console/app/services/permissions.py:141-155`.
- Estado: razonablemente solido; el hallazgo viejo queda desmentido.

SQL dinamico:

- Riesgo confirmado en SF direct MCP: `cartridges/sap_successfactors/app/mcp_server.py:306-336`.
- HCM/S4 tienen guard: `cartridges/sap_hcm/app/mcp_server.py:317-330`, `cartridges/sap_s4hana/app/mcp_server.py:317-330`.
- `mcp-infra` valida llamadas `cartridge_query_kb` no-admin: `mcp-infra/app/main.py:949-953`.

CORS:

- FastAPI expone CORS outermost y `X-Request-ID`: `console/app/main.py:5908-5939`.
- Production fail-closed de origenes esta en `console/app/main.py:333-387`.

CSRF:

- API client manda `X-CSRF-Token` en mutaciones: `console-next/src/lib/api.ts:66-69`.
- Auth/login hace GET `/login` antes de POST para sembrar cookie: `console-next/src/lib/auth-flow.ts`.
- Backend usa double-submit cookie en `console/app/services/csrf.py`.

JWT/session:

- Sesiones HttpOnly/server-side en `console/app/services/auth.py`.
- Logout/blacklist tienen tests en `console/tests` y root `tests`.

Auditoria/request-id:

- Client mete `X-Request-ID`: `console-next/src/lib/api.ts:53-78`.
- Middleware normaliza y responde `X-Request-ID`: `console/app/middleware/request_id.py:32-96`.
- Audit service usa `request_id_var`: `console/app/services/audit_service.py:75-112`.

CSP:

- Shell general y viewers ya tienen `script-src 'self'`: `console/app/main.py:765-798`.
- Console-next calcula hashes CSP: `console/app/routers/pages.py:50-75`.
- Control Room queda como excepcion con `unsafe-inline`: `console/app/main.py:818-829`.

Rate limit:

- Implementado y default productivo activo: `console/app/main.py:905-1019`.
- Falta documentacion completa en env example para `RATE_LIMIT_ENABLED` y `TRUSTED_PROXY_IPS`.

Endpoints sensibles:

- Vault read/reveal/write/delete tienen permisos y CSRF en mutaciones: `console/app/main.py:4046-4138`.
- Cartridge credentials usan Vault y auditoria: `console/app/routers/cartridges.py:280-380`.

## G) Estado Frontend/Contrato API

Pantallas principales en Next/export:

- `/dashboard`, `/monitor`, `/security`, `/my-access`, `/operations/*`, `/apps-gallery`, `/cartridges`, `/workspace`, `/copilot`, `/viewer`.
- Marketplace/admin/customer: `console/app/main.py:3577-3609`.
- Explorer/Agents/Decisions: `console/app/main.py:3812-3838`, `5047-5051`.
- Studio existe en nav/ruta pero queda FUERA DE ALCANCE POR REGLA DEL USUARIO.

Endpoints llamados:

- API client same-origin: `console-next/src/lib/api.ts:53-78`.
- Cartridges: `console-next/src/lib/cartridges.ts:103-193`.
- Operations users/audit/vault: `console-next/src/lib/operations/client.ts:18-120`.
- Access/nav: `console-next/src/lib/admin-surfaces.ts:388-390`, `console-next/src/components/AppChrome.tsx:72-81`.

Endpoints existentes:

- Cartridges router: `console/app/routers/cartridges.py:121-143`, `210-260`, `280-380`.
- Marketplace: `console/app/main.py:3611-3663`.
- Vault: `console/app/main.py:4046-4138`.
- Users: `console/app/main.py:5453-5510`.
- Access: `console/app/main.py:1750-1887`.

Botones que prometen acciones no implementadas:

- Control Room ejecutar live: bloqueado/incompleto por `control_room_service.py:4880-4920`.
- Cartridge "Activar" si el usuario no tiene `marketplace.admin`, backend rechazara con 403/400; nav intenta ocultarlo por permisos, pero hay que probar con roles reales.
- Cartridge detail puede mostrar fallback schema generico durante loading.

Exclusion Studio:

- Cualquier problema en `/studio` o `/api/studio/*` queda fuera de correccion por orden del usuario.

## H) Estado Control Room

Version que parece oficial:

- Source oficial: `console/control-room-next`.
- Export estatico servido: `console/app/static/control-room`.
- Copy script: `console/control-room-next/package.json:8`.
- FastAPI sirve `/control-room`: `console/app/routers/pages.py:227-239`.
- Docker copia build: `console/Dockerfile:3-16`.

Version legacy:

- No encontre `console/app/static/control-room.html` independiente.
- Si existe HTML legacy global (`console/app/static/index.html`) que enlaza a Control Room y menciona `:3000`; eso no es la app Control Room oficial.

Riesgo de duplicidad:

- Riesgo real: drift entre `console/control-room-next` y `console/app/static/control-room` si no se corre `export:copy`. CI lo revisa en `.github/workflows/lint.yml:90-93` y `.github/workflows/release.yml:72-77`.

Write-back:

- Bloqueado por default y no implementado aun si se prende el flag: `console/app/services/control_room_service.py:4880-4920`.

Que habria que corregir sin tocar Studio:

- Implementar write-back real por conector o cambiar naming/copy comercial de V1.
- Quitar `unsafe-inline` del CSP de Control Room.
- Agregar tests unit/component al cockpit, no solo e2e.
- Mantener export sincronizado con CI.

## I) Estado SAP Knowledge Bits

Conteo real:

- SAP HCM: 10 entidades, 12 KBs.
- SAP S/4HANA: 25 entidades, 12 KBs.
- SAP SuccessFactors: 30 entidades, 14 KBs.

Estado:

- No estan "pobres" como ausencia; tienen KBs reales y tests dedicados.
- Pero siguen existiendo KBs parciales y datasets con TODOs funcionales.

Gaps principales:

- SuccessFactors recruitment: falta `JobApplication` para etapas reales de candidato; `knowledge_bits.yaml:196-203` lo declara parcial.
- SuccessFactors turnover puede venir vacio hasta activar/explotar `EmpEmploymentTermination`; `knowledge_bits.yaml:205-212`.
- SuccessFactors compensacion: dataset TODO sobre `paycompValue`.
- S/4 overdue invoices estima vencimiento por 30 dias; falta estado de pago/partidas FI, `sap_s4hana/app/config/knowledge_bits.yaml:102-111`.
- S/4 inventory usa documentos agregados, falta detalle por material/cantidad/valor, `sap_s4hana/app/config/knowledge_bits.yaml:135-143`.
- HCM workforce cost menciona `PA0008` pendiente.

KBs faltantes para sentirse enterprise:

- SF: pipeline de reclutamiento con etapas reales, compensation bands con pay components, sucesion/talent mobility, compliance de datos de empleado.
- S/4: aging AR con clearing/payment status real, inventory valuation/movement detail, procurement exceptions, cash conversion, margin por producto/cliente.
- HCM: payroll/costo laboral real por periodo, ausentismo con calendario/turnos, manager span con posicion historica.

## J) Estado Tests/CI

Comandos ejecutados:

- `python -m pytest --collect-only -q`: NO EJECUTADO por falta de binario `python`.
- `python3 -m pytest ...`: NO EJECUTADO por falta de `pytest` en Python global.
- `.venv/bin/python -m pytest --collect-only -q`: 1785 tests collected.
- `.venv/bin/python -m pytest tests/test_sap_dags_deployed.py tests/test_sap_dag_imports.py tests/test_sap_dags_config.py -q`: 32 passed.
- `.venv/bin/python -m pytest tests -q`: 1770 passed, 15 skipped.
- `.venv/bin/python -m pytest console/tests -q`: 491 passed.
- `.venv/bin/python -m pytest vault/tests -q`: 32 passed.
- `.venv/bin/python -m pytest cartridges/sap_hcm/tests cartridges/sap_s4hana/tests -q`: 64 passed.
- `npm --prefix console-next run typecheck`: passed.
- `npm --prefix console-next run lint`: passed.
- `npm --prefix console-next run test`: 2 files, 8 tests passed.
- `npm --prefix console-next run build`: passed, 28 static routes.
- `npm --prefix console/control-room-next run lint`: passed.
- `npm --prefix console/control-room-next run typecheck`: passed.
- `npm --prefix console/control-room-next run build`: passed.
- `docker compose -f infra/docker-compose.yml config`: passed; output redacted because expande secretos locales.
- `npm --prefix tests-e2e run test:list`: 338 Playwright tests listed.

Tests que fallan:

- Ninguno de los comandos ejecutados con entorno disponible fallo por codigo.

Tests no ejecutados y por que:

- Suite Playwright viva (`make e2e` / `playwright test`) no se ejecuto porque requiere stack vivo, credenciales y muta estado (usuarios, thresholds, Control Room). Se listo la suite como baseline no destructivo.
- `python -m pytest` exacto no se ejecuto porque no existe `python`; se uso `.venv/bin/python`.
- `python3 -m pytest` global no se ejecuto porque falta `pytest`.

Falta coverage:

- No hay reporte de coverage CI para Python ni frontend.

Gaps E2E:

- Los 15 skips de `tests -q` son por E2E/stack vivo sin `E2E_ADMIN_PASSWORD` y un caso live-stack de MCP.
- El workflow `.github/workflows/e2e.yml:50-75` intenta cubrir stack vivo, pero esta auditoria local no lo probo.

## K) Plan de correccion por prompts

### Prompt 1 - Cerrar contrato env/bootstrap

- Objetivo: alinear variables usadas, compose, bootstrap y `infra/.env.example`.
- Puede tocar: `infra/.env.example`, `infra/bootstrap*.sh`, docs/runbook, tests env contract.
- Prohibido: Studio completo.
- Tests obligatorios: `.venv/bin/python -m pytest tests/test_bootstrap_admin_no_argv.py tests/test_bootstrap_admin_password_guard.py tests/test_app_env_safe_default.py -q`, `docker compose -f infra/docker-compose.yml config`.
- Riesgo: mediano.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 2 - Endurecer health dependencies locales

- Objetivo: cambiar `service_started` a `service_healthy` donde aplique y agregar healthchecks faltantes sin romper boot order.
- Puede tocar: `infra/docker-compose.yml`, `scripts/wait_for_health.sh`, tests compose.
- Prohibido: Studio completo.
- Tests obligatorios: `.venv/bin/python -m pytest tests/test_compose_healthchecks.py tests/test_aws_compose_consistency.py -q`, `docker compose -f infra/docker-compose.yml config`.
- Riesgo: mediano.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 3 - Guard SQL para SAP SuccessFactors MCP directo

- Objetivo: portar `sql_guard` a SF y agregar tests equivalentes a HCM/S4.
- Puede tocar: `cartridges/sap_successfactors/app/core/sql_guard.py`, `cartridges/sap_successfactors/app/mcp_server.py`, `cartridges/sap_successfactors/tests/**`.
- Prohibido: Studio completo.
- Tests obligatorios: new SF sql guard tests, existing HCM/S4 sql guard tests, `.venv/bin/python -m pytest tests/test_postgres_tools_sqli.py -q`.
- Riesgo: mediano.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 4 - Decidir/implementar write-back V1 honesto

- Objetivo: elegir entre conector real para 1 cartucho o renombrar V1 como ejecucion supervisada sin write-back productivo.
- Puede tocar: `console/app/services/control_room_service.py`, `console/app/routers/control_room.py`, `console/control-room-next/**`, docs/runbook, tests Control Room.
- Prohibido: Studio completo.
- Tests obligatorios: `.venv/bin/python -m pytest console/tests/test_control_room_service.py console/tests/test_ops_summary_and_version.py -q`, Control Room lint/typecheck/build.
- Riesgo: grande.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 5 - CSP estricto Control Room

- Objetivo: quitar `unsafe-inline` de `script-src` para `/control-room` usando hash/nonce compatible con static export.
- Puede tocar: `console/app/main.py`, `console/app/routers/pages.py`, `console/control-room-next/**`, tests CSP.
- Prohibido: Studio completo.
- Tests obligatorios: `console/tests/test_strict_csp_phase3.py`, Control Room build, Playwright CSP smoke si stack vivo.
- Riesgo: grande.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 6 - Cerrar legacy/static y cartridge UX sin borrar contratos

- Objetivo: mapear HTML/JS legacy activo, redirigir o congelar explicitamente, y quitar fallback generico peligroso de cartridge viewer.
- Puede tocar: `console/app/routers/pages.py`, `console-next/src/app/(shell)/cartridges/**`, `console-next/src/lib/cartridges.ts`, tests frontend.
- Prohibido: Studio completo, `action-bridge.js`, `wire-handlers.js`.
- Tests obligatorios: console-next lint/typecheck/test/build, `tests/test_v1443_cartridges_next.py`, relevant E2E list/run.
- Riesgo: mediano.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 7 - Completar KB enterprise por cartucho

- Objetivo: convertir KBs parciales en KBs honestos/completos o marcarlos claramente con data availability.
- Puede tocar: `cartridges/*/app/config/knowledge_bits.yaml`, `cartridges/*/datasets/**`, seed SQL 80/81/82, KB tests.
- Prohibido: Studio completo.
- Tests obligatorios: `tests/test_sap_hcm_kbs.py tests/test_sap_s4hana_kbs.py tests/test_sap_successfactors_kbs.py tests/test_yaml_load.py -q`.
- Riesgo: grande.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 8 - Coverage y frontend component tests

- Objetivo: agregar coverage CI y component/unit tests para AppChrome, Marketplace, Operations, Vault, Cartridges, Copilot.
- Puede tocar: `.github/workflows/*.yml`, `console-next/vitest.config.*`, `console-next/src/**/*.test.tsx`.
- Prohibido: Studio completo.
- Tests obligatorios: console-next test with coverage, lint, typecheck.
- Riesgo: mediano.
- Modelo recomendado: 1.5 rapido para setup, 5.5 superinteligente para tests de permisos.

### Prompt 9 - Romper monolitos sin cambiar comportamiento

- Objetivo: extraer modulos de `main.py`, `control_room_service.py` y `control-room-next/app/page.tsx` con pruebas intactas.
- Puede tocar: archivos nuevos bajo `console/app/routers`, `console/app/services/control_room/**`, `console/control-room-next/components/**`.
- Prohibido: Studio completo.
- Tests obligatorios: root tests, console tests, Control Room build/lint/typecheck.
- Riesgo: grande.
- Modelo recomendado: 5.5 superinteligente.

### Prompt 10 - Prueba viva de release

- Objetivo: levantar stack desde cero y correr smoke + Playwright completo sin mutaciones no controladas.
- Puede tocar: docs/runbook, scripts health/e2e harness, tests-e2e fixtures si fallan por harness.
- Prohibido: Studio completo salvo documentar fallos como fuera de alcance; no tocar archivos Studio.
- Tests obligatorios: `bash infra/bootstrap.sh`, `docker compose -f infra/docker-compose.yml --profile sap up -d --build`, `bash scripts/wait_for_health.sh`, `make smoke`, `make e2e`.
- Riesgo: mediano-grande.
- Modelo recomendado: 5.5 superinteligente.

## L) Release verdict

Sale a demo controlada: SI, con condiciones.

- Condiciones: discurso honesto sobre write-back V1, no prometer escritura ERP, usar seed/demo controlado, preparar stack vivo antes, no improvisar credenciales frente al cliente.

Sale a beta privada: TODAVIA NO como esta, salvo beta tecnica muy controlada.

- Bloquea beta privada normal: write-back/claim, env contract, local compose races, CSP Control Room, falta de E2E vivo local en este baseline, SQL guard SF.

Sale a produccion: NO.

- Bloquea produccion: write-back no implementado, CSP Control Room con unsafe-inline, no coverage, monolitos criticos, gaps KB/datasets, y falta de prueba viva completa reproducible desde cero.

Veredicto final:

- La consola no esta rota ni es puro humo: hay una base real, mucho test y el cableado principal existe.
- Tampoco esta lista para vender produccion enterprise sin letra chica.
- Para llegar a 9/10 hacen falta aproximadamente 8 prompts bien secuenciados si se acepta V1 sin write-back, o 10 prompts si se implementa write-back real de al menos un cartucho.
