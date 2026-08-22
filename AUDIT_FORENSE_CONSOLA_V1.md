# AUDITORÍA FORENSE TOTAL — CONSOLA-V1 / ΩMEGA (main)

**Objeto:** repositorio CONSOLA-V1, checkout `main` (idéntico a la rama de trabajo, divergencia 0/0), `VERSION = 1.45.222-beta`.
**Naturaleza:** auditoría **estática, hostil, evidence-first**. Sin runtime cloud, sin ejecutar servicios, sin llamadas a sistemas externos, sin modificar el código auditado.
**Método:** 21 subauditores especializados en paralelo + verificación independiente del árbitro. ~3.714 archivos versionados; ~2.035 Python, 468 SQL, 148 tsx + 96 ts, 118 js + 95 html, 31 Terraform.
**Regla de honestidad aplicada:** «existe implementación en código» ≠ «funciona». Casi todo lo que depende de sistemas externos queda como **LIVE-VERIFICATION-REQUIRED**.

---

## 0. ADVERTENCIA DE ALCANCE

Esta auditoría puede afirmar con evidencia **qué existe y cómo está cableado el código de main**. NO puede afirmar que ninguna integración externa (SAP, Salesforce, HubSpot, Replicon, Banxico, INEGI, SEC), ningún despliegue AWS/GCP, ni ningún flujo Bronze→Gold **funcione en producción**, porque ninguna prueba de la suite cruza un límite externo real y no hay runtime disponible. Donde el veredicto es "real", significa "implementación real en código, no demostrada en ejecución".

---

## ENTREGABLE 1 — VEREDICTO EJECUTIVO

**¿Qué es realmente CONSOLA-V1 hoy?**
Es una **plataforma de software genuinamente construida, no una maqueta** — y esta es la conclusión más importante y la más contraintuitiva dado el tono de la petición. El equipo red-team anti-maqueta, buscando activamente software falso en código de producción (excluyendo tests), **no encontró ni una sola fabricación de negocio de cara al usuario**: ni KPIs hardcodeados servidos a la UI, ni fallback silencioso que invente datos, ni botones muertos que aparenten actuar, ni endpoints que siempre devuelvan lo mismo. Al contrario: el código está **agresivamente diseñado contra maquetas** (estado de readiness "stub" que se niega a contar datos no operativos, tests DOM de "honestidad" que prohíben mostrar "Todos en línea" durante la carga, seeds de demo vallados tras `APP_ENV=production` que es el valor por defecto, y separación limpia entre metadatos de configuración y datos ficticios).

Dicho esto, **CONSOLA-V1 NO es un producto operativo demostrado**. Es una **beta real, madura en código, pero no verificada en vivo**, con un patrón sistémico de riesgo: **"write-back que no escribe"**. Las superficies de acción de cara al operador (Control Room, Supervised Actions, Copilot Workflows) llegan limpiamente al backend pero **terminan en preview, simulación sandbox, estado local o un `410 Gone`**; el motor de escritura externa real existe y está bien construido, pero está **doblemente bloqueado** (flag `CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK` en `false` por defecto **y** sin ningún llamador desde la UI). El producto **observa** con datos reales; desde la UI **no actúa** sobre sistemas externos.

**¿Maqueta / prototipo / beta / parcialmente operacional?**
Parcialmente operacional en el plano de **lectura/observabilidad** (dashboards, KPIs, freshness, catálogo, lineage, copiloto que consulta datos reales vía RLS). **Beta no verificada** en el plano de **integración** (9 cartuchos con clientes HTTP reales pero cero evidencia live). **Neutralizado por diseño** en el plano de **acción/escritura externa**.

**¿Qué porcentaje parece implementación real?** (estimaciones de juicio, ponderadas por superficie, no por LOC)
- **~68-72%** implementación real en código (IMPLEMENTED-IN-CODE), la mayoría además **LIVE-VERIFICATION-REQUIRED**.
- **~18-22%** infraestructura/configuración (Terraform AWS+GCP, compose, IaC, seeds de metadatos, migraciones).
- **~8-12%** legacy/muerto/duplicado (routers/v1 completo, HTML estático legacy, página workspace Next inalcanzable).
- **~8-10%** parcial (datasets SAP con columnas NULL, talento SF bloqueado, P&L Replicon file-fed).
- **~2-3%** stub/placeholder/fake-data honestos (comp SAP `WHERE FALSE`, launcher Studio Next, external "predictive signals").

**¿Qué NO está demostrado?** Absolutamente ninguna conectividad externa real; ninguna materialización Bronze→Silver→Gold ejecutada; ningún despliegue AWS/GCP vivo; ninguna acción de escritura externa; que las ~130 migraciones RLS tardías estén realmente aplicadas en un clúster existente.

**Componentes más maduros:** Vault (cifrado Fernet real, aislamiento por 3 capas), stack MCP/Copilot/Agentes (bucle de tool-use real con Anthropic, límites read/write con aprobación), plataforma de datos/lakehouse (Gold es derivado real, no sembrado), backend console (DB-real, sin fabricación), auth de console (HS256 fail-closed, sin P0/P1).

**Componentes más débiles:** Control Room como "controlador" (no controla nada externo), Intelligence Engine (matemática real pero la capa "IA/predicción" son heurísticas deterministas disfrazadas), suite de tests como evidencia de integración (todos los límites externos mockeados), cartuchos Replicon (bypass `seeded_gold` + P&L alimentado por email) y las tres fuentes públicas (sin MCP ni UI).

**Principal riesgo para poner un cliente real mañana:**
1. **Aplicación de RLS no garantizada** (P1): las migraciones corren solo vía `docker-entrypoint-initdb.d`, que se ejecuta **una vez sobre volumen vacío**; no hay migration runner. Un clúster ya provisionado puede estar corriendo con RLS **silenciosamente ausente** mientras el código asume que está presente — riesgo de fuga multi-tenant real dependiente del estado del clúster.
2. **Ninguna integración probada en vivo** — no se puede prometer a un cliente que SAP/Salesforce/HubSpot/Replicon extraen datos reales.
3. **Las acciones de la UI no producen efectos externos** — si al cliente se le vende "remediar/ejecutar/sincronizar", hoy la UI no lo hace.

---

## ENTREGABLE 2 — PUNTUACIÓN (0-10, con justificación)

| Dimensión | Nota | Justificación (evidencia) |
|---|---:|---|
| Backend (console) | **8.0** | asyncpg real + RLS por transacción; ~17 endpoints muestreados todos DB-reales; sin fabricación; seeds solo metadatos (db_scope.py:38-108). Resta operacional (scheduler pull-driven externo). |
| Frontend (console-next) | **7.5** | ~30 páginas todas cableadas a `/api` reales; anti-faking explícito ("Sin datos" no 0). Resta Studio/Workspace legacy y página workspace Next muerta. |
| Wiring FE↔BE | **7.0** | Sin path-drift material (~150 rutas verificadas). Penaliza el patrón "write-back que no escribe" y el árbol `routers/v1` muerto. |
| Data platform / Lakehouse | **8.0** | Gold es derivado real vía DuckDB + CAS con checksums; grep `INSERT INTO gold_`=0; lineage y watermarks reales (duckdb_engine.py:1789-2017). Resta puente legacy_unverified latente. |
| Airflow / scheduler | **7.0** | DAGs importan limpios; locks distribuidos reales (fencing+lease+advisory). Resta: DAGs pausados por defecto, cartuchos thin-trigger, gate E2E no cubre cartuchos. |
| Vault | **8.5** | Fernet AEAD real, fail-closed sin key por defecto, aislamiento 3 capas (crypto.py:71-76, 99g/99e RLS). Resta `/destinations` sin scope + único SECURITY_CONTEXT_SIGNING_KEY. |
| MCP | **8.0** | Malla de servicios viva: GET /mcp/tools + POST /mcp/invoke reales con egress-guard SSRF; tools ejecutan SQL/Airflow/MinIO reales. |
| Copilot | **8.0** | Bucle tool-use Anthropic real; sin API key **no fabrica** (error 502); citations grounded. |
| Agents | **7.5** | Runtime + scheduler reales, read-only por defecto en programado, aprobación en manual. LIVE-REQ activación. |
| Intelligence Engine | **5.0** | Matemática real (Theil-Sen, Beta-Bernoulli, Monte-Carlo sembrado) pero "probability/prediction/confidence" son **heurísticas deterministas** con nombre de IA; calibración Bayesiana casi siempre inerte (<10 outcomes). Auto-disclosed, por eso no baja más. |
| Control Room | **4.5** | Plano de lectura honesto y real, pero **no controla nada externo**: execute_item muerto (410), writeback flag-off + sin llamador. Aprobación custodia un vacío. |
| Replicon | **5.0** | Cliente real + retries + breaker, pero bypass `seeded_gold` (extracción→[] reportando healthy) y P&L alimentado por email/file-ingest, no por API. |
| HubSpot | **7.0** | Cliente CRM v3 real prod-grade + MCP + app. Penaliza fake-upstream de acceptance (no prueba API real) y multipágina/429 nunca ejercitados. |
| Salesforce | **6.5** | OAuth2+SOQL real, cursor pagination, watermark. Sin Bulk API; sin prueba live; tests mockean upstream. |
| SAP HCM | **6.0** | OData v2 real, pero 3/7 datasets Gold NULL-stub (fuentes no extraídas) + gemelo duplicado de S/4. |
| SAP S/4HANA | **6.0** | OData real 25 entidades, 3 datasets Gold NULL-stub; gemelo textual de HCM (bug en uno no se arregla en otro). |
| SAP SuccessFactors | **6.5** | El más maduro (OAuth2+SAML, 106/108 datasets reales) pero comp `WHERE FALSE` y capa Talent estructuralmente bloqueada; live SAML test skipped. |
| Banxico | **6.5** | Cliente real con rate-limiters, sin MCP ni UI; tests con FakeSession. |
| INEGI | **6.5** | Idem Banxico; token en path; sin MCP ni UI. |
| SEC EDGAR | **6.5** | Cliente real declared-identity UA, 8 req/s; sin MCP ni UI. |
| Seguridad (app) | **7.5** | Console fuerte, sin P0/P1: JWT HS256 fail-closed, CSRF, CORS fail-closed, CSP, RBAC centralizado, SSRF guard. Resta P2/P3 (CSRF en /monitoring/mcp/invoke, lockout fail-open). |
| Multi-tenancy | **6.5** | Diseño sólido (66 tablas RLS FORCE, rol NOBYPASSRLS, GUC fail-closed) pero **P1: aplicación de RLS no garantizada** + tablas identidad `USING(true)` + un check de conversación workspace-ciego. |
| AWS/Infra | **6.5** | IaC madura dual-cloud (GCP canónico, AWS DR), backup/rollback reales, secretos inyectados no horneados. Resta Postgres single-node SPOF, TLS/WAF condicionales, compose GCP generado ausente. |
| CI/CD | **6.0** | 5 gates fail-closed reales, pero un único detector de paths es SPOF, docker-image/e2e sin gate agregador, backlog bandit tolerado (165), pasos release deshabilitados `if:${{false}}`. |
| Observabilidad | **7.0** | Logs JSON con redacción, request IDs, /healthz + /readyz reales con checks de dependencia, audit events. Sin tracing distribuido. |
| Resiliencia | **6.0** | Circuit breakers, retries backoff, fencing locks, idempotencia. Penaliza DB single-node y dependencia de scheduler externo. |
| Tests | **5.5** | Espina real fuerte (RLS/Control Room sobre Postgres real, motores numéricos), pero ~220/336 son asserts de string-en-fuente y **todos los límites externos mockeados**; joyas RLS saltan en silencio sin Docker. |
| Production readiness | **4.0** | Fuerte en estático, no probado en vivo, write-back neutralizado desde UI, riesgo de aplicación RLS, integraciones sin evidencia. |

**Media aritmética ≈ 6.6** — pero la media engaña: el producto es **alto en construcción, bajo en demostración**. No usar la media como indicador de "listo".

---

## ENTREGABLE 3 — MAPA DE ARQUITECTURA REAL

```
Navegador (usuario)  ──HTTPS mismo-origen──►  console :8000
                                                (FastAPI; SIRVE el frontend:
                                                 console-next static export + HTML legacy)
   console orquesta (HTTP interno con x-api-key por-par + x-security-context HMAC):
     ├─► vault      :8300   secretos/conexiones (Fernet en Postgres)
     ├─► refinement :8500   materialize / refresh-by-source → escribe Gold (DuckDB)
     ├─► mcp-infra  :8010   POST /mcp/invoke (SQL/Airflow/MinIO/RAG tools reales)
     └─► workspace  :8001   apps/preview

   mcp-infra ──► Airflow REST /dags/{id}/dagRuns   (trigger)
   Airflow scheduler :8080 (LocalExecutor, 1 solo scheduler, catchup=False)
     ├─ DAGs plataforma: entity_scheduler(*/5), agent_runner(*/5), dataset_refresh_chain,
     │                    file_ingest, replicon_ses_inbox_import
     └─ DAGs cartucho (cartridges/*/dags): 
           · hubspot/replicon/sap_sf_extract_all → disparan dataset_refresh_chain (B→S→G)
           · salesforce/sap_hcm/sap_s4/banxico/inegi/sec → thin HTTP-trigger, SOLO Bronze
                         │  httpx directo ──►  cartridge REST :820x/821x
                         ▼
        cartridge extrae API EXTERNA (SAP/Salesforce/HubSpot/Replicon/Banxico/INEGI/SEC)
        con credenciales reveladas desde vault
                         │  parquet
                         ▼
              MinIO (LOCAL 9000) / S3 (AWS)  ==►  BRONZE (raw/…)
                         │
     refinement:8500 + DuckDB lee Bronze → SILVER → materializa GOLD (checksum/CAS)
                         │
              postgres_gold :5433 (modecissions_gold, RLS FORCE tenant+workspace)
                    │                          │
             Superset :8088 (BI)     console Intelligence/Copilot (lee Gold vía RLS)

   Stores: postgres pgvector :5432 (app/auth/vault_entries) · postgres_gold :5433 · redis · minio · DuckDB
```

**Correcciones al esquema propuesto en el brief:**
1. El frontend NO es una capa separada: lo **sirve console**.
2. DAG→cartucho es **REST directo**, no siempre vía MCP (los DAGs generados por console sí van por MCP).
3. **refinement** (no Airflow) posee la materialización Silver→Gold.
4. 6 de 9 cartuchos (salesforce, sap_hcm, sap_s4hana, banxico, inegi, sec_edgar) se quedan en **Bronze** por DAG; no propagan a Gold.
5. banxico/inegi/sec_edgar están **ausentes del registry MCP y de la tabla de health-probes** de console (huérfanos a nivel console).

---

## ENTREGABLE 4 — MATRIZ MAESTRA DE FUNCIONALIDADES

| Feature | UI | API | Service | Persistencia | Efecto externo | Auth | Scope | Datos reales | Estado | Evidencia |
|---|---|---|---|---|---|---|---|---|---|---|
| Dashboard KPIs | ✔ Next | ✔ | ✔ SQL | PG | — | ✔ | tenant/ws | ✔ agregados reales | IMPLEMENTED | dashboard.py:130-520 |
| Freshness | ✔ | ✔ | ✔ | PG | — | ✔ | ws | ✔ | IMPLEMENTED | freshness.py:87-153 |
| Catálogo / Lineage / Bronze query | ✔ | ✔ | ✔ | PG/DuckDB | — | ✔ | ws | ✔ | IMPLEMENTED | data/client.ts; main.py:6738 |
| Copilot chat + tools | ✔ | ✔ | ✔ | PG | vía tools MCP | ✔ | ws | ✔ | IMPLEMENTED (LIVE-REQ LLM) | copilot_service.py:1289-1298 |
| Copilot acción write/destructive | ✔ | ✔ | ✔ | PG | sí, tras aprobación server-side | ✔ | ws | ✔ | IMPLEMENTED | copilot_service.py:1745-1771 |
| Agentes programados | ✔ | ✔ | ✔ | PG | read-only (advisory writes firmados) | ✔ | ws | ✔ | IMPLEMENTED (LIVE-REQ activación) | agent_runner.py; agent_scheduler.py:24-153 |
| RAG / knowledge | ✔ | ✔ | ✔ | pgvector | Bedrock embeddings | ✔ | ws | ✔ | IMPLEMENTED + FALLBACK local | store.py:190-255; embeddings.py:43-45 |
| Intelligence (signals/MC/backtest) | ✔ | ✔ | ✔ | PG/Gold | — | ✔ | ws | ✔ inputs reales | IMPLEMENTED (heurística, no ML) | intelligence/*; decision_intelligence.py:651-656 |
| Control Room dashboards/alerts | ✔ | ✔ | ✔ | Gold/PG | — | ✔ | ws | ✔ | IMPLEMENTED | api.py:4552-4611 |
| **Control Room acción/execute** | preview only | 410/preview | dead | PG local | **NO** | ✔ | ws | — | **DEAD / FALLBACK-gated** | control_room.py:1190-1199; execution.py:3419 |
| **Supervised Actions execute** | sin botón | ✔ | sandbox | PG local | **NO (simulado)** | ✔ | ws | — | **MOCKED/PLACEHOLDER** | external_actions.py:455-459 |
| Sync-Now (extract) | ✔ | ✔ | ✔ | Airflow | dispara DAG | ✔ | ws | ✔ | IMPLEMENTED (LIVE-REQ Airflow) | main.py:4717-4760 |
| Marketplace request/activate | ✔ | ✔ | ✔ | PG | — | ✔ | ws/tenant | ✔ | IMPLEMENTED | marketplace_service.py:404 |
| Vault conexiones/secretos | ✔ | ✔ | ✔ | PG cifrado | — | ✔ | tenant/ws | ✔ | IMPLEMENTED | v1/vault.py; vault/app |
| Studio (deploy DAG/materialize/superset) | legacy :8000 | ✔ real | ✔ | Airflow/Gold/Superset | sí, env-gated | ✔ | ws | ✔ | IMPLEMENTED (Next=launcher) | studio.py:2168,2443 |
| Cartuchos extract (los 9) | ✔ (config) | ✔ | ✔ cliente HTTP | Bronze/PG | API externa | ✔ | tenant/ws | LIVE-REQ | IMPLEMENTED-IN-CODE / LIVE-REQ | por cartucho abajo |
| Operations Workflows | plan/cancel | ✔ | ✔ executor | PG | vía agent-runner | ✔ | ws | ✔ | PARTIAL (UI no ejecuta) | operations/client.ts:225 |

---

## ENTREGABLE 5 — REAL vs FAKE (nombres concretos)

**IMPLEMENTACIÓN REAL EN CÓDIGO (y LIVE-VERIFICATION-REQUIRED para efecto externo):**
Backend console (routers/services/domains), persistencia asyncpg+RLS, Vault (Fernet), stack MCP/Copilot/Agentes, lakehouse refinement (Bronze→Silver→Gold vía DuckDB/CAS), watermarks/lineage/freshness, clientes de los 9 cartuchos (OData/REST/OAuth), Studio backend (`/api/studio/*`), Superset client, dashboards/KPIs/catálogo, Intelligence (motores numéricos: Theil-Sen, Beta-Bernoulli, Monte-Carlo, permutación), IaC AWS+GCP, scripts backup/rollback/DR.

**REAL PERO NO VERIFICABLE SIN CLOUD:** toda conectividad externa (SAP/Salesforce/HubSpot/Replicon/Banxico/INEGI/SEC), materialización Bronze→Gold end-to-end, despliegue AWS/GCP, disparo real de DAGs, reveal de credenciales Vault contra secretos poblados, aplicación efectiva de las migraciones RLS tardías.

**PARCIAL:**
- SAP SF: `compensation_full` (valor cifrado no agregable), capa Talent (role→skill matrix no extraída → `blocked`/`role_requirements_pending`).
- SAP HCM: `manager_hierarchy`, `org_hierarchy`, `workforce_cost_monthly` (columnas NULL, fuentes PA0008/HRP1001 no en entities.yaml).
- SAP S/4: `cost_center_expense`, `inventory_movement_summary`, `overdue_billing` (NULL).
- Replicon: P&L Gold alimentado por SES/Outlook/file-ingest, no por API.
- Operations Workflows: la UI planifica/cancela pero no ejecuta.

**STUB:**
- `compensation_distribution.sql` = `SELECT … WHERE FALSE` (schema vacío honesto).
- `routers/v1/*` como implementación "modular" nunca montada (contrato sin ruta viva).

**MOCK (que puede rozar producción — pero contenido):**
- `_sandbox_execute` de Supervised Actions (simulación de éxito, `external_write:false`), habilitado por defecto; el writeback real está flag-off.
- `tests/fixtures/fake_hubspot_api.py` (upstream determinista de acceptance) — inyectado solo en `run_full_stack_acceptance.sh`.

**FAKE-DATA:** Ninguno en producción de cara al usuario (confirmado por red-team). Los únicos datos demostrativos (Type B, thresholds/lessons de Control Room dev-seed) están vallados tras `APP_ENV=production`.

**FALLBACK (silencioso, a vigilar):**
- RAG embeddings → hash SHA-256 lexical local si Bedrock cae (`RAG_LOCAL_EMBEDDING_FALLBACK` default true) → espacios de embedding incompatibles sin error.
- `GOLD_DATABASE_URL`→`DATABASE_URL` (si dispara, consultas Gold contra DB principal).
- Puente legacy-adoption Gold (`legacy_unverified`) que serviría cualquier `public.gold_*` preexistente.

**DEAD CODE:**
- Todo `console/app/routers/v1/` (12 módulos) — nunca `include_router`-ado; `ROUTERS` solo referenciado por tests.
- `console/app/static/*.html` + `static/js/*.js` legacy (0 refs, nunca servidos): admin_users, agents, apps_gallery, cartridges, copilot, decisions, explorer, iam, index, login, me, monitor, my_access, security, settings + `viewers/*.html`.
- `console-next/.../workspace/page.tsx` (compilado pero inalcanzable; `/workspace` sirve legacy).
- Control Room `execute_item` y toda la maquinaria de escritura externa bajo él (~136 KB) — sin llamador vivo.
- Tablas huérfanas: `vault_legacy_unscoped_entries`, `omega_rls_platform_owner_allowlist`, `operational_outcome_binding_candidates`.
- Path LLM Gemini (coercionado a Anthropic).

**DUPLICADO/LEGACY:**
- SAP HCM ≡ SAP S/4HANA (gemelos textuales del mismo template; el código lo admite).
- Migraciones `64≡21_drop_master_layer`, `62≡12_vpn_tokens` (byte-idénticas re-aplicadas).
- `/studio` y `/workspace` legacy HTML autoritativos vs rivales en console-next.
- Dos clientes Replicon (core con breaker vs inline en DAG sin breaker).

**NO IMPLEMENTADO (explícito):** forecasting real en Intelligence (`future_reserved_*` devuelve `anomaly_probability=None`); Bulk API en Salesforce; MCP + App/UI para banxico/inegi/sec_edgar.

**DESCONOCIDO (requiere runtime):** estado real de aplicación de RLS por clúster; qué modo usa Replicon en prod (`seeded_gold` vs live); si TLS/WAF están activados en cloud; si las imágenes ghcr/AR existen.

---

## ENTREGABLE 6 — TOP HALLAZGOS

### P0 (crítico)
No se confirma ningún P0 puramente estático con doble evidencia **incondicional**. El candidato más cercano es **P1-DATA-RLS-001**, que se convierte en P0 (fuga multi-tenant) **si y solo si** se cumple su condición de runtime (RLS no aplicado). Se clasifica P1 por honestidad, porque su materialización depende de estado no verificable estáticamente.

### P1 (alto)

**P1-DATA-RLS-001 — La aplicación de RLS no está garantizada; puede estar ausente en clústeres existentes.**
- Estado: CONFIRMADO (mecanismo) / REQUIERE-RUNTIME (materialización).
- Evidencia A: migraciones montadas solo en `docker-entrypoint-initdb.d` (docker-compose.yml:26, aws.yml:38), que corre una vez sobre volumen vacío. Evidencia B: **no existe migration runner** en el arranque de console (grep alembic/run_migrations = vacío); `schema_migrations` es un ledger manual ya drifted; comentario en compose línea 1136 admite "volumes where /docker-entrypoint-initdb.d no longer runs".
- Impacto: en un clúster provisionado antes de la ola RLS `99d–99zzzz`, `FORCE ROW LEVEL SECURITY` puede estar OFF y el rol `omega_console` (owner) vería todos los workspaces → fuga multi-tenant.
- Corrección: migration runner real que consulte `schema_migrations` y aplique pendientes al arranque; verificar `relforcerowsecurity` en boot.
- Prueba de cierre: en clúster de staging pre-existente, `SELECT relrowsecurity, relforcerowsecurity FROM pg_class` sobre tablas tenant → todas TRUE.

**P1-ACTION-WRITEBACK-001 — "Write-back que no escribe": las acciones de la UI no producen efecto externo.**
- Estado: CONFIRMADO.
- Evidencia A: Control Room `POST /items/{id}/execute` → `410 Gone` (control_room.py:1190-1199); `execute_item` (execution.py:3419) sin llamador vivo (verificado por árbitro); flag `CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK` default `false` (execution.py:256). Evidencia B: Supervised Actions `_sandbox_execute` devuelve `{"external_write":false,"Sandbox adapter simulated success"}` (external_actions.py:455-459); la UI no renderiza botón Ejecutar/Aprobar (test propio page.preview-only.dom.test.tsx:88).
- Impacto: cualquier promesa de "remediar/ejecutar/aplicar/sincronizar hacia el sistema externo" es falsa desde la UI actual.
- Corrección: o bien cablear el motor real (que existe) con su flag y un llamador UI + aprobación, o bien **retirar/renombrar** las superficies para que no aparenten actuar.
- Prueba de cierre: acción en Control Room que produzca una escritura observable en el sistema externo destino (o test que confirme el 4xx honesto "no soportado").

**P1-DEAD-V1-001 — Árbol de rutas `routers/v1/*` completo, muerto, que engaña a los tests de seguridad.**
- Estado: CONFIRMADO (verificado por árbitro).
- Evidencia A: `ROUTERS` (v1/__init__.py:18) referenciado en NINGÚN sitio salvo su propia definición; `main.py` no lo incluye. Evidencia B: `test_console_cleanup_static.py` concatena `main.py` + `v1/*` y asserta invariantes de seguridad sobre la unión → un fix aplicado solo en `v1/*` (muerto) pasa el test mientras `main.py` (vivo) sigue vulnerable.
- Impacto: falsa superficie; riesgo de que una corrección de seguridad se aplique a código inerte y el test la dé por buena.
- Corrección: eliminar `routers/v1/*` o montarlo y retirar los duplicados de `main.py`; que el test cubra solo el código vivo.

### P2 (medio)

- **P2-STUDIO-DRIFT-001** — Comentarios/UI dicen que `/api/studio/*` es stub `{stub:true}` (page.tsx:14-19, main.py:8020) cuando el router es real (studio.py, 2530 líneas, grep stub=0). Migración Next congelada sobre premisa falsa; bounce a legacy.
- **P2-VAULT-DEST-001** — `GET /destinations/{name}` sin scope ni máscara (vault/main.py:744-756); solo auth interna.
- **P2-SEC-CSRF-001** — `/monitoring/mcp/invoke` sin `require_csrf` (v1/monitoring.py:45-59); tools read-only, bajo radio.
- **P2-TENANT-CONV-001** — `_load_conversation` sin predicado workspace + `_is_admin_or_owner` workspace-ciego (copilot_service.py:564-568,1031); mitigado por RLS hoy, fuga si RLS ausente.
- **P2-INTEL-NAMING-001** — Intelligence presenta heurísticas deterministas como "probability/prediction/confidence/Bayesian" (decision_intelligence.py:651-656, prediction.py:8-16); mitigado por auto-disclaimers pero engañoso de cara al cliente.
- **P2-REPLICON-SEED-001** — modo `seeded_gold` corta toda extracción a `[]` reportando healthy (replicon_client.py:84-85,273,309); un despliegue podría no integrar nada y parecer sano.
- **P2-CI-GATE-001** — docker-image/e2e sin gate agregador; `build` procede con `test==skipped` (docker-image.yml:252); config de required-checks no en repo.
- **P2-INFRA-SPOF-001** — Postgres in-container single-node en ambas nubes; sin HA (public_https.tf:413).

### P3 (bajo)
- Lockout brute-force fail-open si falta tabla o `RATE_LIMIT_ENABLED=false` (auth.py:479-484) — compensado por rate-limit por IP.
- Cambio de password no revoca JWTs/sesiones vivas (auth.py:411-436).
- TLS/WAF AWS/GCP condicionales a variables (public_https.tf:199-210); fallback HTTP plano.
- Tablas identidad `USING(true)` (users/workspaces/aliases SAP) — app SQL único guardián.
- Migraciones duplicadas byte-idénticas + colisiones de prefijo numérico.
- Fallback RAG embeddings local por defecto (espacios incompatibles silenciosos).
- Puente Gold `legacy_unverified` latente.
- 6 datasets Gold SAP con columnas NULL; comp SF `WHERE FALSE`.
- pasos de re-verificación de release deshabilitados `if:${{false}}`; autorización skip decorativa.

---

## ENTREGABLE 7 — BOTONES Y PANTALLAS QUE MIENTEN

| Pantalla | Acción visible | Qué promete | Qué hace realmente | Estado |
|---|---|---|---|---|
| Supervised Actions | (sin botón Ejecutar/Aprobar renderizado) | ejecutar/aprobar acción | La UI solo expone validar(dry-run)/rechazar/cancelar; execute/approve son funciones cliente **sin llamador**; el backend simula (`sandbox`) | PLACEHOLDER honesto (no hay botón engañoso, pero la capacidad "aparece" en el cliente) |
| Control Room (experience action) | "Preview" | validar acción | Devuelve un **echo constante** del handle; el 200 no distingue de un cálculo real | Preview-only |
| Control Room (items execute / auto-run) | — (no cableado en Next) | ejecutar remediación | `410 Gone` / `409 auto_run_disabled` | DEAD |
| Operations → Workflows | "Planificar"/"Cancelar" | orquestar workflow | Planifica y cancela; **no dispara ejecución** (comentario en código) | PARTIAL |
| Studio (Next) | tarjetas de cartucho + "Deploy/Materialise" | Studio funcional en Next | Array hardcodeado de 5 cartuchos + banner "migrando"; toda acción rebota a legacy `:8000` | Launcher / FALLBACK |
| Copilot Drafts | "Enviar por email" | enviar borrador | Botón `disabled` con badge "Próximamente v1.44.4.1" | Honesto (deshabilitado, no miente) |

**Nota crítica:** No se encontró ningún botón que dispare una petición y muestre éxito falso. El engaño, donde existe, es por **ausencia de efecto externo** (preview/sandbox/410), no por fabricación de datos. Varias "no-acciones" están **honestamente etiquetadas** ("Próximamente", "próximamente panel"). El caso más problemático de cara a un comprador es que las superficies de "acción" (Control Room, Supervised Actions) **existen y parecen operativas** pero nunca tocan un sistema externo.

---

## ENTREGABLE 8 — APIs HUÉRFANAS

**Endpoint implementado pero aparentemente legacy/muerto (sin ruta viva):**
- Todo `console/app/routers/v1/*`: auth, system, jobs, data, agents, vault, marketplace_apps, misc, monitoring, pipeline_studio, rag, admin_decisions — definidos, nunca montados; duplican handlers inline vivos de `main.py`.
- Control Room: `/items/{id}/execute` (410), `/items/{id}/action-preview`, `/action-dry-run`, `/sap-successfactors/talent/actions/preview` (todos 410); `execute_item` sin llamador.

**Endpoint sin consumidor conocido en console-next:**
- `/api/studio/*` (real y service-backed) — **cero** fetches desde console-next; solo lo consume el frontend legacy `studio.html` + `action-bridge.js`.
- `/api/copilot/workflow/{id}/execute` — sin llamador UI (solo el agent-runner interno).
- `POST /api/control-room/items/{id}/execute` — sin llamador UI.

**Frontend consumer sin endpoint compatible:** Ninguno material — no se detectó path-drift (funciones cliente `approveSupervisedAction`/`executeSupervisedAction` existen pero simplemente no se invocan; los endpoints destino sí existen).

---

## ENTREGABLE 9 — DATASETS (por familia)

| Fuente | Bronze | Silver | Gold | Freshness/Watermark | Lineage | Campos TODO/NULL | Readiness | Notas |
|---|---|---|---|---|---|---|---|---|
| SAP SuccessFactors (108) | real (parquet→MinIO) | 106/108 real DuckDB | employee_360 real 7-join; Talent parcial | real (entity_watermarks RLS) | real | comp `WHERE FALSE`; Talent `role_requirements_pending` | mixto (stub/partial declarados) | el más maduro |
| SAP HCM (21) | real | real `*_latest` | headcount real; 3 NULL-stub | real | real | manager/org/cost NULL (fuentes no extraídas) | partial | gemelo de S/4 |
| SAP S/4HANA (27) | real | real | gl/revenue/orders real; 3 NULL-stub | real | real | cost_center/inventory/overdue NULL | partial | gemelo de HCM |
| Salesforce (14) | real | real dedup | pipeline_forecast/margen/riesgo real | SystemModstamp real | real | — | ok en código | sin Bulk API |
| Replicon (17 API + 4 file) | real | real | pnl_mensual real pero inputs **file-fed** | client-side + PG | real | ProjectDetail/Billing/Audit/UserSkills no-API | partial | seeded_gold bypass |
| HubSpot (6) | real | 7 latest | pipeline_salud + 4 agregados | client-side + 5min buffer | real | — | ok en código | fake-upstream en acceptance |
| Banxico | real (BronzeWriter+manifest) | 3 | market_context | date-window + PG | real | manifest `status:pending` | ok código; sin MCP/UI | preflight "pending_live_token" |
| INEGI | real | 3 | market_context | date-window + PG | real | — | ok código; sin MCP/UI | token en path |
| SEC EDGAR | real (+retención) | 3 | market_context | PG + retención | real | — | ok código; sin MCP/UI | UA declared-identity |

**Hallazgo central de datasets:** **Gold es derivado real, no sembrado** (grep `INSERT INTO gold_`=0; CAS con checksums). Los seeds solo insertan **definiciones** (`sql_def`). Riesgo latente único: el puente `legacy_unverified` que serviría cualquier `public.gold_*` preexistente poblado fuera de banda (inactivo en este checkout).

---

## ENTREGABLE 10 — INTEGRACIONES (fichas)

**Leyenda de veredicto:** todas comparten "IMPLEMENTED-IN-CODE / LIVE-VERIFICATION-REQUIRED" salvo lo indicado. Ninguna tiene evidencia live en el repo; todas las suites mockean el upstream y pasarían con la integración real rota.

### SAP SuccessFactors
Auth: OAuth2 client-credentials + SAML-bearer 2-leg (sap_client.py:662-829) · Creds: Vault reveal per-tenant · Test-conn: GET /$metadata · Entities: 64 · Extraction: OData v2 real · Pagination: $top/$skip + dedup · Retries: urllib3 3/backoff · Rate-limit: reactivo 429+breaker · Watermark: server $filter + PG RLS · Bronze/Silver/Gold: real (106/108) · Airflow: real (schedule=None) · MCP: 8 tools · Apps: 2 · **Live evidence: NINGUNA** (SAML test skipped) · Missing: prueba live auth, materialización, desbloqueo comp/Talent · Verdict: real scaffold, no probado.

### SAP HCM / SAP S/4HANA
Auth: Basic (S/4 +APIKey) · Creds: Vault · Entities: 10 / 25 · OData v2 real, pagination/retries/breaker/watermark reales · Gold: parcial (3 NULL-stub cada uno) · Airflow: thin-trigger · MCP: 8 tools · **Live: NINGUNA** · **Duplicados textuales entre sí** · Missing: prueba live + extraer fuentes de los datasets NULL.

### Salesforce
Auth: OAuth2 (password/client_credentials/bearer) · Creds: Vault · SOQL REST real, queryMore, retries, SystemModstamp watermark · Bronze/Silver/Gold real · Airflow real · MCP + 7 apps · **Sin Bulk API** · **Live: NINGUNA** · La integración más honesta (rechaza si no configurada).

### Replicon
Auth: bearer/api_key/basic **+ modo seeded_gold (bypass total de red)** · Creds: Vault · Cliente async POST/extracts→poll→CSV, retries+breaker · 17 entidades API · **P&L Gold alimentado por SES/Outlook/file-ingest, no por API** · Airflow real (cliente inline duplicado) · MCP + 5 apps · **Live: NINGUNA** · Verdict: **PARCIAL** — analítica estrella desacoplada del API real.

### HubSpot
Auth: bearer · Creds: Vault · CRM v3 real (api.hubapi.com), cursor pagination, retries · 6 entidades · Bronze/Silver/Gold real · Airflow real · MCP 8 tools + app · **Live: NINGUNA** · **Fake-upstream determinista en acceptance** (fake_hubspot_api.py) — prueba plumbing, no el API real; multipágina/429 nunca ejercitados · Verdict: real prod-grade, no probado live.

### Banxico / INEGI / SEC EDGAR
Clientes reales contra hosts reales (banxico.org.mx / inegi.org.mx / data.sec.gov), rate-limiters, retries, redirect-block, date-window incremental, PG watermarks, Bronze+Silver+Gold · **Sin MCP server, sin App/UI** (a diferencia de HubSpot) · Tests con FakeSession (cero cobertura de contrato live) · SEC auth = User-Agent declared-identity · Verdict: extract real, superficie incompleta (falta MCP/UI).

---

## ENTREGABLE 11 — SECURITY REPORT

**Postura general:** console auth/seguridad **endurecida y evidentemente post-auditada** (notas "Codex P1", "audit B2 P0"). **Sin P0/P1 confirmado en console.**

- **Auth/JWT:** HS256 pinado, fail-closed ante secreto inseguro/corto/default (jwt_auth.py:28-69), sin fallback, sin `none`/alg-confusion. jti blacklist Redis prod fail-closed. Refresh single-use rotación atómica SHA-256. Sesiones server-side, cap 12h. bcrypt(12) sobre sha256.
- **Web:** CSRF double-submit constant-time; CORS sin wildcard+credentials, prod fail-closed; CSP `default-src 'self'`, `script-src 'self'` (sin unsafe-inline — la auditoría vieja que decía lo contrario está **stale**), `frame-ancestors 'none'`, HSTS.
- **RBAC:** registry centralizado `require_permission` por ruta; toda ruta sensible muestreada protegida; escalada employee→admin REFUTADA; `/api/mcp/*` bloqueado `require_admin`.
- **APIs internas:** per-pair keys, prod rechaza legacy; `x-security-context` HMAC TTL 300s.
- **Inyección:** sin raw user-SQL→Postgres en console; el sink real de user-SQL es refinement `preview_transform` con guard sqlglot AST RLS default-deny (`1=0` si falta workspace_id) + blacklist + watchdog 30s. Sin command-injection (subprocess argv), sin deser insegura (solo ast.literal_eval).
- **SSRF:** egress_guard resuelve DNS + pin de IP en connect, bloquea privado/link-local/metadata/CGNAT.
- **RLS/multi-tenant:** 66 tablas workspace_id, 65 con FORCE RLS; rol console NOBYPASSRLS; GUC fail-closed. **Gold DB separado con RLS más estricto (tenant+workspace, sin owner bypass)** — patrón de referencia.

**Amenazas P2/P3:** CSRF en /monitoring/mcp/invoke; lockout fail-open; password-change no revoca; tablas identidad `USING(true)`; check de conversación workspace-ciego; único `SECURITY_CONTEXT_SIGNING_KEY` como punto único de confianza (su fuga = forjar cualquier tenant).

**AWS/supply-chain:** secretos inyectados desde Secrets Manager (no horneados), IMDSv2, WAFv2/Cloud Armor (condicionales), gitleaks (solo working-tree), bandit (solo HIGH-confidence, backlog 165 tolerado), pip/npm-audit fail-closed.

**Riesgo de seguridad dominante:** **operacional, no lógico** — la aplicación efectiva de RLS (P1-DATA-RLS-001). El diseño es sólido; la duda es si está desplegado.

---

## ENTREGABLE 12 — DEUDA TÉCNICA REAL (prioritizada por riesgo)

1. **Ausencia de migration runner** → RLS/hardening no garantizado en clústeres existentes (bloquea cliente real).
2. **Doble árbol de rutas** (`routers/v1` muerto vs `main.py` inline) → superficie falsa + tests de seguridad que cubren código muerto.
3. **Superficies de acción neutralizadas** (Control Room/Supervised/Workflows) → comportamiento engañoso; decidir cablear-o-retirar.
4. **`main.py` monolítico** (~8.000+ líneas, 150 handlers inline) → mantenimiento difícil, dominio dentro de rutas.
5. **Gemelos SAP HCM/S4** (copy-paste textual) → un bug se arregla N veces.
6. **Suite de tests engañosa** (~220 asserts de string-en-fuente + límites externos mockeados + joyas RLS que saltan sin Docker) → falsa sensación de cobertura.
7. **Frontend mid-migration** (legacy HTML vivo, workspace Next muerto, Studio Next launcher) → confusión de autoridad.
8. **Postgres single-node** en ambas nubes → SPOF sin HA.
9. **Docs de auditoría stale** que contradicen el código (Studio "stub", Control Room path, CSP) → decisiones sobre premisas falsas.
10. **Naming "IA/predicción"** para heurísticas deterministas → riesgo de sobre-venta.

---

## ENTREGABLE 13 — QUÉ FALTA DEMOSTRAR EN LA NUBE (plan LIVE, no ejecutar ahora)

| ID | Objetivo | Credenciales | Acción | Evidencia esperada | PASS | FAIL |
|---|---|---|---|---|---|---|
| LIVE-00 | Aplicación de RLS | acceso PG prod | `SELECT relforcerowsecurity FROM pg_class` en tablas tenant | todas TRUE | todas TRUE | alguna FALSE |
| LIVE-01 | SAP SF auth | Vault SF (OAuth2+SAML) | `test_connection()` ambos flujos | HTTP 200 + access_token | token válido | 401/timeout |
| LIVE-02 | SAP SF extract+materialize | idem | DAG extract→extract_all→refresh_chain | parquet Bronze + filas Gold employee_360 | filas>0 | vacío/error |
| LIVE-03 | SAP HCM/S4 OData | Vault Basic | multipágina $skip + $filter | filas + paginación | ok | 400/vacío |
| LIVE-04 | Salesforce OAuth+SOQL | Vault SF | /limits + query + nextRecordsUrl | Bronze no vacío | ok | error |
| LIVE-05 | Replicon modo | Vault Replicon | determinar auth_method + POST /extracts real | ¿seeded_gold o live? CSV real | live+CSV | seeded_gold |
| LIVE-06 | Replicon P&L lineage | mail/file ingest | confirmar SES/Outlook DAGs pueblan ProjectDetail/Billing | fuentes pobladas | pobladas | ausentes |
| LIVE-07 | HubSpot API real | token privado | extract >100 registros | paging.next.after + 429 handling | multipágina | 1 página |
| LIVE-08 | Banxico/INEGI/SEC | tokens/UA | extract + validadores estrictos | schema válido live | ok | drift |
| LIVE-09 | Materialización Gold | S3/DuckDB | refinement materialize | receipts CAS + checksums | verificado | fallo |
| LIVE-10 | Copilot LLM | ANTHROPIC_API_KEY | chat + tool-use | tool invoke real + citations | efecto real | error/echo |
| LIVE-11 | Control Room write-back | flag ON + adapter | (con autorización) acción → sistema externo | escritura observable en destino | escribe | no escribe |
| LIVE-12 | Despliegue cloud | AWS/GCP | pull imágenes ghcr/AR + boot | stack up + /readyz 200 | up | fallo |
| LIVE-13 | TLS/WAF | tfstate | confirmar toggles | HTTPS + WAF activos | activos | HTTP plano |
| LIVE-14 | Secrets versions | Secrets Manager | listar versiones | pobladas | pobladas | vacías |
| LIVE-15 | Puente Gold legacy | PG Gold | `SELECT … WHERE status='legacy_unverified' AND row_count>0` | 0 filas | 0 | >0 |

---

## ENTREGABLE 14 — GO / NO-GO

**1. Demo interna → CONDITIONAL GO.**
Condición: usar entorno dev (`APP_ENV≠production` para poblar Control Room demo) o un tenant con datos Gold reales; entender que las acciones no producen efectos externos. El plano de lectura demuestra bien.

**2. Beta con usuarios internos → CONDITIONAL GO.**
Condiciones: (a) verificar LIVE-00 (RLS aplicado) antes de multi-usuario; (b) desplegar el agent-runner/Airflow para que los agentes programados disparen; (c) comunicar que las superficies de acción son observacionales.

**3. Cliente real controlado → NO-GO (hasta condiciones).**
Bloqueadores: P1-DATA-RLS-001 (aislamiento no garantizado), ninguna integración probada live (LIVE-01..08), write-back inexistente desde UI (P1-ACTION-WRITEBACK-001). Pasa a CONDITIONAL GO tras: RLS verificado + al menos las integraciones del cliente probadas live + decisión explícita sobre las superficies de acción (cablear o retirar) + TLS/WAF confirmados.

**4. Producción enterprise pública → NO-GO.**
Bloqueadores adicionales: Postgres single-node SPOF (sin HA), suite de tests sin cobertura de integración real, deuda de `routers/v1` muerto que engaña a los gates de seguridad, backlog bandit tolerado, CI sin gate en docker/e2e, docs contradictorias. Requiere externalizar el estado (RDS/CloudSQL), pruebas live end-to-end, y limpieza de código muerto/duplicado.

---

## ENTREGABLE 15 — PLAN DE CORRECCIÓN (orden de ejecución)

**BLOQUE A — Falsedad/apariencia (primero, barato, alto impacto de honestidad):**
1. Decidir por superficie de acción (Control Room execute, Supervised Actions execute/approve, Operations Workflows): **cablear el motor real con aprobación** o **retirar/renombrar** el control para que no aparente actuar.
2. Actualizar/eliminar los comentarios y banner "stub" de Studio (page.tsx:14-19, main.py:8020) — el backend es real.
3. Renombrar/etiquetar Intelligence: distinguir "score heurístico" de "probabilidad calibrada" en la UI, no solo en `probability_basis`.
4. Documentar el modo `seeded_gold` de Replicon y exponerlo en la UI (como `has_demo_seed`).

**BLOQUE B — Seguridad:**
5. Migration runner real + verificación de RLS en boot (P1-DATA-RLS-001).
6. Añadir predicado workspace en `_load_conversation` (P2-TENANT-CONV-001).
7. `require_csrf` en /monitoring/mcp/invoke; lockout fail-closed en prod; revocar tokens en cambio de password; scope en `/destinations`.
8. Rotación gestionada del `SECURITY_CONTEXT_SIGNING_KEY` (KMS).

**BLOQUE C — Integraciones:**
9. Ejecutar el plan LIVE (Entregable 13) por cliente/cartucho antes de venderlo.
10. Extraer las fuentes de los 6 datasets Gold SAP NULL-stub; desbloquear comp/Talent SF o marcarlos como no disponibles en UI.
11. Añadir MCP + App/UI a banxico/inegi/sec_edgar si se busca paridad.

**BLOQUE D — Datos:**
12. Verificar/neutralizar el puente Gold `legacy_unverified`.
13. Deshabilitar `RAG_LOCAL_EMBEDDING_FALLBACK` en prod (o gate por identidad de proveedor).

**BLOQUE E — Infra:**
14. Externalizar Postgres (RDS/CloudSQL) para eliminar el SPOF.
15. Forzar TLS/WAF (quitar el fallback HTTP plano); confirmar imágenes y secret-versions.
16. Publicar el `docker-compose.gcp.yml` generado en el repo o documentar su generación.

**BLOQUE F — Tests/evidencia:**
17. Añadir tests de contrato contra grabaciones (VCR) de los upstreams; al menos un test que ejercite un límite externo real por integración en un gate opt-in.
18. Garantizar Docker en CI para que las joyas RLS no salten en silencio; gate agregador para docker-image/e2e.

**BLOQUE G — Deuda arquitectónica:**
19. Eliminar `routers/v1/*` muerto (o montarlo y retirar duplicados de `main.py`); corregir el test que cubre código muerto.
20. Borrar HTML/JS estático legacy sin refs y la página workspace Next inalcanzable; completar o retirar la migración Studio/Workspace.
21. De-duplicar migraciones y consolidar; unificar los gemelos SAP HCM/S4 en una librería compartida.

---

## RESPUESTAS A LAS 20 PREGUNTAS FINALES

1. **% software real:** ~68-72% implementación real en código (mayoría LIVE-REQ).
2. **% infra/config:** ~18-22%.
3. **% legacy:** ~8-12% (routers/v1, HTML estático, workspace Next).
4. **% parcial:** ~8-10%.
5. **% stub/mock/fake:** ~2-3% (honestos; fake-data de negocio en producción ≈ 0%).
6. **Features con datos reales:** dashboards/KPIs, freshness, catálogo/lineage, copilot (consulta Gold), intelligence (inputs reales), Control Room lectura — todas vía RLS sobre Gold/PG real.
7. **Features que escriben realmente:** escrituras a **DB propia** (decisiones, status, audit, catálogo, marketplace, vault). Copilot/Studio pueden escribir Gold/DAG/Superset (gated). **Escritura a sistema EXTERNO desde UI: ninguna** (motor existe, doble-bloqueado).
8. **Dónde termina cada acción:** en Postgres propio / preview / sandbox / 410. El write-back externo real existe pero sin ruta viva.
9. **Riesgos de seguridad principales:** aplicación de RLS no garantizada (P1); punto único de confianza (signing key); P2/P3 varios. Sin P0/P1 de código en console.
10. **¿Aislamiento real entre clientes?** Sólido en diseño (RLS FORCE + NOBYPASSRLS + GUC fail-closed, Gold aún más estricto), pero **condicionado a que las migraciones estén aplicadas** — no verificable estáticamente. Dos tablas identidad y un check de conversación se apoyan en una sola capa.
11. **¿Integraciones reales o solo contratos?** Clientes HTTP **reales** (no stubs) para los 9, pero **cero evidencia de ejecución live**; algunas parciales (Replicon P&L, Talent SF, NULL-stubs SAP).
12. **¿Datasets con business data real o sembrados?** Gold es **derivado real, no sembrado** (CAS + checksums). Seeds = solo definiciones/metadatos. Datos de demo (thresholds) vallados fuera de prod.
13. **¿Copilot/MCP actúan de verdad?** Sí — bucle tool-use real que invoca tools MCP con efectos reales (SQL/Airflow/MinIO), con límites read/write y aprobación humana. Sin API key **no fabrica**.
14. **¿Intelligence usa evidencia real?** Lee Gold real vía RLS; matemática real (robust stats, Bayes, Monte-Carlo). Pero "predicción/probabilidad/confianza" son **heurísticas deterministas** disfrazadas; forecasting real NO implementado; calibración casi siempre inerte.
15. **¿Control Room controla algo?** Controla el estado **interno** de ΩMEGA (status/thresholds/decisiones/audit). **No controla nada externo** (execute_item muerto, writeback flag-off sin llamador).
16. **¿AWS implementado o solo definido?** Definido de forma **madura** (Terraform completo AWS+GCP, GCP canónico/AWS DR) pero es **DR standby**, single-node, con TLS/WAF condicionales; despliegue vivo no verificable.
17. **¿Qué se sabe solo por código?** Toda la arquitectura, wiring, esquemas, guards de seguridad, presencia de clientes de integración, que Gold es derivado, que no hay fabricación de negocio.
18. **¿Qué falta demostrar live?** Todo lo externo: RLS aplicado, auth/extract/materialización por integración, despliegue cloud, TLS/WAF, secret-versions, write-back externo (Entregable 13).
19. **¿Qué bloquearía a un cliente mañana?** (a) RLS quizá no aplicado; (b) ninguna integración probada; (c) la UI no ejecuta efectos externos.
20. **Veredicto real sin marketing:** **Software genuinamente construido, honesto en sus datos, fuerte en seguridad de aplicación y en su plataforma de datos — pero es una beta no verificada en vivo cuyas superficies de "acción" no actúan externamente y cuyo aislamiento multi-tenant depende de un paso de despliegue no garantizado. No es una maqueta; tampoco es un producto operativo demostrado.**

---
*Auditoría estática. Ninguna afirmación de "funciona en producción" puede sostenerse desde este checkout; ver Entregable 13 para el plan de verificación live.*
