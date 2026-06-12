# OMEGA / CONSOLA-BETA — Auditoría técnica funcional e integración (evidence-first)

- **Repo:** CONSOLA-BETA
- **Rama auditada:** `claude/magical-davinci-no3jmu` (= `main` HEAD en el momento de la auditoría)
- **Commit:** `bab2a4f` — *“[codex] bump beta release to 1.45.68”* — 2026-06-12 17:33 +0200
- **VERSION:** `1.45.68-beta`
- **Fecha auditoría:** 2026-06-12
- **Método:** 8 subagentes de lectura de código (evidencia `archivo:línea`) + levantamiento real de stack en local (Postgres 15432/15433, Redis, moto-S3 como MinIO, DuckDB con extensiones pinneadas, 8 servicios FastAPI in-process, fake HubSpot upstream) + suite de tests por capas. No se ejecutó ni reactivó ningún workflow cloud. No se hizo deploy. No se consumieron recursos externos.

> Los reportes por dominio están en `reports/_agents/*.md` (backend_console, frontend_next, mcp_copilot, data_pipeline, cartridges, infra_devops, database_rls, product_control_room). Este documento los consolida y añade las pruebas dinámicas que **yo ejecuté en vivo**.

---

## 1. Resumen ejecutivo

OMEGA **no es humo**. Es un sistema real, conectado de punta a punta en su columna vertebral de datos, con seguridad de plataforma seria (auth, RBAC, CSRF, firma de contexto, Gold RLS nativo verificado en vivo). Donde falla no es en “mock vendido como real”, sino en **profundidad**: la capa de *acción/decisión* (Control Room) tiene los gates reales pero **una sola acción interna con sustancia** y un dry-run cosmético; el backstop de RLS operacional es hueco para los dos roles principales; y hay drift de versiones/legacy.

La pregunta central — *“¿la consola ejecuta acciones reales e integra datos reales/controlados de forma verificable, o solo tiene UI bonita con mocks?”* — se responde con **evidencia dinámica**: extraje datos de un upstream HubSpot fake → escribió **parquet real en S3 (Bronze)** → materialicé **Silver** con DuckDB → materialicé **Gold** en `postgres_gold` → la **RLS de Gold bloqueó lectura cross-workspace** con el rol `NOBYPASSRLS`. Eso es integración real, no UI bonita.

### Calificaciones (0-10)

| Dimensión | Nota | Justificación (1 línea) |
|---|---:|---|
| **Funcionalidad real** | **7.0** | Backbone Bronze→Silver→Gold→consumo real; Control Room con gates reales pero acciones de poca sustancia. |
| **Integración real** | **7.5** | 6 cartuchos con cliente API real + parquet a lakehouse, probado en vivo extremo a extremo. |
| **Infra local** | **7.5** | 22 servicios compose con healthchecks reales; levanta; algunos pins de extensión/registry dependen de red. |
| **Frontend conectado** | **7.0** | 113/113 rutas frontend existen en backend; pero dos frontends conviven (legacy dueño de Workspace/Studio) + dead code. |
| **Backend / MCP / agentes** | **8.0** | Auth/RBAC/CSRF/gateway sólidos; 60+ tools MCP con implementación real; copilot fail-closed sin LLM. |
| **Datos Bronze/Silver/Gold** | **8.0** | Guard AST default-deny real, Gold RLS nativo verificado en vivo, lineage y freshness honestos. |

### Veredicto

> **BETA PRIVADA SÓLIDA** (con condiciones). Es claramente más que un demo: el flujo de datos integra y persiste con efectos verificables y aislamiento criptográfico/RLS real. No es “Candidato v1.0” todavía porque la capa de *ejecución de decisiones* es delgada, el RLS operacional no respalda a `omega_console`/`omega_refinement`, y hay deuda de legacy/drift.

### La frase brutal

> **Lo más peligroso hoy es** que OMEGA *parece* tener defensa en profundidad de aislamiento multi-tenant (“RLS en 3 niveles”), pero en la base operacional el backstop **no existe para los dos roles que más datos tocan** (`omega_console`, `omega_refinement` tienen política `USING(true)`): el aislamiento de inquilinos depende **100% de que cada query de la app lleve el `WHERE workspace_id` correcto**. Lo probé en vivo: a nivel SQL el rol ve filas de otro workspace; solo la capa de aplicación lo tapa. Un solo query sin filtrar = fuga cross-tenant sin red de seguridad.

---

## 2. Qué SÍ funciona de verdad (verificado)

### Console (:8000) — **REAL**
- Arranca, sirve el export estático Next.js same-origin, `/healthz` → `200 {"ok":true,"version":"1.45.68-beta"}`. **(probado en vivo)**
- Login real: `POST /auth/login` con bcrypt(rounds=12)+pre-hash SHA-256 → JWT HS256 con `jti`, access TTL **15 min**, refresh 7 días single-use rotado. **(probado en vivo: login 200 + access_token)**
- Blacklist JWT en Redis **fail-closed en producción** si Redis cae (`jwt_blacklist.py:94-118`).
- RBAC: registro de 56 permisos, `require_permission` por ruta; 40+ rutas muestreadas, superficie API ampliamente gateada.
- CSRF doble-submit default-deny en rutas mutantes; Bearer exento correctamente; el frontend lo cablea.
- Gateway: pares `INTERNAL_API_KEY_<from>_TO_<to>` (28 pares en `.env`), `x-security-context` **firmado HMAC-SHA256**, TTL 300 s, consistente console/refinement/vault/mcp-infra.
- Errores: handler global devuelve solo `{error,request_id}`, sin stack ni SQL; redacción agresiva de secretos en logs.

### Control Room — **REAL como máquina de estados auditada** (con sustancia delgada)
- Señales calculadas desde tablas **Gold con RLS** vía intelligence engine con baselines de media móvil — **no hardcodeadas** (`gold_fetcher.py:79-130`, `baseline.py:70-117`). **(probado en vivo: `/api/control-room/summary` y `/api/intelligence/signals` responden con datos reales scoped, vacíos sin pipeline corrido)**.
- Decisión: `INSERT decisions + decision_actions + events + audit_events`, fail-closed, RBAC `control_room.write`, CSRF, scoped por workspace. **REAL.**
- Cadena de gates de ejecución real: confirm → decision → dry-run → idempotencia → `control_room.execute`.
- **Write-back externo BLOQUEADO por diseño y honesto**: flag default `false`, se registra `blocked` + audita `…execute.blocked`, HTTP 409, **nunca se muestra como ejecutado** (P1-CR-001 confirmado implementado).

### Copilot / MCP / Agentes — **REAL**
- Pipeline completo verificado paso a paso: `/api/copilot/...messages` → Anthropic SDK propone `tool_use` → `tool_policy.validate_tool_args` (schema/límites/injection) → RBAC por riesgo → approval gate (estado DB atómico, no frontend) → `mcp_registry.invoke` → citas+freshness → `audit_events`. Denegaciones vuelven al LLM como `tool_result`.
- **Sin modo mock de LLM**: sin `ANTHROPIC_API_KEY` el copiloto **falla cerrado con 502** y mensaje claro, **no fabrica respuesta**. **(probado en vivo: `502 "El copiloto no tiene proveedor LLM configurado"`)**.
- 60+ tools MCP-infra, **todas con implementación real** (Airflow REST, postgres psycopg2, MinIO SDK+pyarrow, Superset REST, Vault HTTP, RAG pgvector+Bedrock Titan). RCE-tools **doble-gated** (`APP_ENV=dev` AND `ALLOW_RCE_TOOLS=true`), prod default off.

### Cartuchos (6) — **FUNCIONALES** (cliente API real + pipeline)
- Replicon (Analytics BI API), SAP HCM/S4HANA (OData v2), SAP SuccessFactors (OAuth2+SAML2 — el más maduro, 50 tests), Salesforce (SOQL REST, key pair `SALESFORCE_TO_CONSOLE` verificado end-to-end), HubSpot (CRM v3).
- Watermarks en PG `entity_watermarks` con **buffer de 5 min** confirmado en los 6 (`WATERMARK_BUFFER_MINUTES=5`).
- Bronze a `s3://lakehouse/raw/<cart>/<entity>/.../load_date=/batch_id=` con protección PII pre-escritura. FastMCP real en `/mcp/rpc` + adaptador REST.

### Refinement / Datos Bronze→Silver→Gold — **REAL** (núcleo más fuerte del sistema)
- Guard SQL en **dos capas**: denylist regex + **AST sqlglot default-deny** (raise en parse-fail, sin fallback) + allowlist HTTP que exige dataset Gold registrado. 40+ tests de RLS/CTE/UNION/subquery.
- DuckDB pin 512 MB aplicado antes de cargar extensiones, testeado.
- Gold RLS nativo: `FORCE RLS + NOBYPASSRLS + deny-without-scope`, rol `omega_refinement_gold` least-priv.
- Readiness y freshness **honestos**: `ready` solo si `COUNT(*)>0` en la tabla Gold scoped; freshness es SQL en vivo sobre watermarks.

### Infra / DevOps — **REAL**
- 22 servicios compose (19 runtime + 3 init), **healthchecks reales** (HTTP/`pg_isready`/proc-scan, cero `sleep`-theater), `depends_on` con condiciones `service_healthy`/`completed_successfully`.
- Bootstrap genera ~39 secretos (28 pares INTERNAL_API_KEY + passwords DB + 2 Fernet) con `umask 077`/`chmod 600`. **`infra/.env` triple-gitignored. Cero secretos comprometidos en el repo** (solo fixtures PEM de test).
- Terraform real (no esqueleto): backend S3+DynamoDB, VPC, EC2 IMDSv2, WireGuard, ALB+ACM+WAF, SES, 58 secretos en Secrets Manager, rol OIDC SSM least-priv default-off, budgets.

### Tests — **REALES y pasan**
- **3,699 tests passed** en 6 capas (tests/ 2405, console 815, refinement 158, vault 46, workspace 33, cartridges 242), 67 skipped. `ruff` limpio. Frontend: typecheck + eslint + vitest 79/79 pass, `next build` OK.

---

## 3. Qué es parcial o fake

| Módulo | Qué promete | Qué hace realmente | Evidencia | Riesgo comercial | Riesgo técnico | Fix |
|---|---|---|---|---|---|---|
| **Control Room — dry-run** | “preview → dry-run → executed” | Dry-run **siempre** devuelve `validated:true`, no valida nada | `execution.py:1250-1257` | Alto (se vende validación que no ocurre) | Medio | Implementar validación real de args/permiso/threshold en dry-run |
| **Control Room — acciones** | Ejecuta remediaciones | **Solo `create_followup_task`** inserta 1 fila; 9/12 templates sin adapter; no dispara MCP/DAG | `product_control_room.md` §execución; `state.py` | Alto | Medio | Wire de ≥2 acciones reales vía MCP tools (ya existen) |
| **Control Room — opciones** | “coste · riesgo” calculado | Ítems no-intelligence reciben **scores hardcodeados 92/68/45** | `state.py:522-558` | Medio | Bajo | Derivar scores de datos o etiquetar como heurística |
| **Control Room — investigación** | Evidence pack con citas | Filas reales, pero SQL citado es **template ilustrativo** y root-cause es copy templado | `evidence.py:29-32`, `core.py:930-971` | Medio | Bajo | Generar SQL/cita real ejecutada |
| **RLS operacional (console/refinement)** | “RLS en 3 niveles” | Política `USING(true)` → **sin backstop DB** para los 2 roles mayores | `99e_*.sql`; **probado en vivo** (§7 F-OPS-1) | Alto (claim de aislamiento) | **Alto** | Política scoped real para console/refinement |
| **Intelligence scheduler** | Señales continuas | `/api/intelligence/run` es **manual**; no hay scheduler | `product_control_room.md` §señales | Medio | Bajo | DAG/cron de `intelligence.run` |
| **Frontend Studio (Next)** | Reemplazo de Studio | **Dead code** borrado del export; comentario dice “stub” siendo el backend real | `studio/page.tsx:13-20`, `package.json:9` | Bajo | Bajo | Borrar dead code o terminar migración |
| **MailHog** | “solo desarrollo” | Vive también en compose **AWS prod**; terraform default `email_provider=smtp/mailhog` | `infra_devops.md` §MailHog | **Alto** (correo prod a un catcher) | Alto | Default SES en prod; quitar MailHog de compose AWS |
| **HubSpot tests** | Contrato común testeado | **0 tests in-cartridge** (no existe dir tests/) | `cartridges.md` §hubspot | Medio | Medio | Portar suite de otro cartucho |
| **Replicon doble code path** | 1 pipeline | DAG (515 líneas) **reimplementa** la extracción en paralelo al servicio | `cartridges.md` §replicon | Bajo | Medio | Unificar DAG→llamada REST al servicio |

---

## 4. Matriz arquitectura baseline vs repo

Leyenda Existe/Conectado: ✅ sí · ◻ parcial · ❌ no. Real/Mock: **R** real, **P** parcial, **M** mock-aceptable.

| Componente baseline | Existe | Conectado | Tests | Real/Mock | Evidencia | Riesgo |
|---|:--:|:--:|:--:|:--:|---|---|
| Console :8000 (FastAPI monolito) | ✅ | ✅ | ✅ | **R** | boot+login+health en vivo; `main.py` 7,554 líneas | Bajo |
| Next.js 16 export estático /static | ✅ | ✅ | ✅ | **R** | `next 16.2.6`, `output:export`, build byte-idéntico | Bajo |
| Workspace :8001 | ✅ | ✅ | ✅ | **R** | health 200; LLM+Gold SQL; CORS locked a console | Bajo |
| Superset 3.1.3 :8088 (Gold directo) | ✅ | ◻ | ◻ | **R** | `apache/superset:3.1.3`, apunta a `modecissions_gold` | Medio (no levantado: imagen no traída) |
| Vault :8300 (Fernet BYTEA, 1 rol, kid) | ✅ | ✅ | ✅ | **R** | health 200; `24/32/99c` FORCE RLS + allowlist | Bajo |
| Refinement :8500 (DuckDB, guard AST) | ✅ | ✅ | ✅ | **R** | health 200; Silver+Gold materializado en vivo | Bajo |
| MCP-Infra :8010 (registry, RAG, Bedrock) | ✅ | ✅ | ✅ | **R** | health 200; 60+ tools reales; RCE double-gate | Bajo |
| Replicon :8201 (PSA) | ✅ | ✅ | ◻ | **R** | health 200; cliente Analytics BI real; 2 tests | Medio (pocos tests) |
| SAP HCM :8202 (HR, profile sap) | ✅ | ✅ | ✅ | **R** | OData v2 real; 11 tests | Bajo |
| SAP SuccessFactors :8203 (HR) | ✅ | ✅ | ✅ | **R** | OAuth2+SAML2; 50 tests | Bajo |
| SAP S/4HANA :8204 (ERP) | ✅ | ✅ | ✅ | **R** | OData v2 real; 10 tests | Bajo |
| Salesforce :8205 (CRM) | ✅ | ✅ | ✅ | **R** | SOQL REST; key pair único verificado; 25 tests | Medio (sin Bulk API) |
| HubSpot :8210 (CRM) | ✅ | ✅ | ❌ | **R** | **extracción Bronze probada en vivo**; 0 tests | Medio |
| Postgres :15432 modecissions (~80 tablas/14 roles) | ✅ | ✅ | ✅ | **R** | **78 tablas, 15 roles omega** (todos NOBYPASSRLS), pgvector | Bajo |
| Postgres Gold :15433 (NOBYPASSRLS/FORCE RLS) | ✅ | ✅ | ◻ | **R** | **RLS verificada en vivo** (deny-default + aislamiento) | Bajo |
| MinIO :9000/:9001 (raw/silver/gold parquet) | ✅ | ✅ | ◻ | **R** | parquet real escrito/leído (moto S3 stand-in en audit) | Bajo |
| Redis 7 (rate limit, blacklist JWT) | ✅ | ✅ | ✅ | **R** | `REDIS_URL` mandatorio en prod; fail-closed | Bajo |
| Airflow 2.10.5 (LocalExecutor, init one-shot) | ✅ | ◻ | ✅ | **R** | `2.10.5`, metastore en PG; **DAGs unpaused en prod (P1)** | Medio |
| MailHog 1.0.1 (SMTP 1025/UI 8025) | ✅ | ✅ | — | **P** | **presente en compose AWS prod (P1)** | Alto |
| Control Room (ciclo ΩMEGA 7 pasos) | ✅ | ◻ | ✅ | **P** | gates reales, acciones delgadas | Medio |
| Cartuchos contrato runtime común | ◻ | ✅ | ◻ | **P** | copy-paste con drift (6 md5 distintos de `vault_client`) | Medio |
| Terraform AWS (VPC/EC2/S3/WAF/SES/SM/VPN) | ✅ | ◻ | ✅ | **R** | módulos completos; drift compose.aws vs local | Medio |
| GitHub Actions (7 workflows) | ✅ | — | — | **R** | 7 workflows, ninguno deshabilitado (no ejecutados) | — |

---

## 5. Matriz de integraciones (cartuchos)

| Cartucho | Puerto | Auth/Vault | MCP tools | DAG | Extracción real | Fake mode | Watermarks | Bronze parquet | Silver | Gold | Intelligence | Tests | Prod-ready | Evidencia |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **Replicon** | 8201 | ✅ key+vault | 11 | ✅ ro | ✅ Analytics BI | ◻ env | ✅ 5min | ✅ | ✅ | ✅ | ✅ yaml×17 | 2 | ◻ | `replicon_client.py:197-282` |
| **SAP HCM** | 8202 | ✅ | 11 | ✅ ro | ✅ OData v2 | ◻ | ✅ | ✅ | ✅ | ◻ | ◻ | 11 | ◻ | `sap_client.py` |
| **SAP SF** | 8203 | ✅ OAuth2+SAML | 11 | ✅ ro | ✅ OData | ◻ | ✅ | ✅ | ✅ | ✅ | ✅ | 50 | ✅ | más maduro |
| **SAP S/4HANA** | 8204 | ✅ +APIKey | 11 | ✅ ro | ✅ OData v2 | ◻ | ✅ | ✅ | ✅ | ◻ | ❌ no yaml | 10 | ◻ | fork textual de HCM |
| **Salesforce** | 8205 | ✅ key pair único | 14 | ✅ ro | ✅ SOQL REST | ◻ | ✅ monotónico | ✅ | ✅ | ✅ | ✅ | 25 | ◻ (sin Bulk) | `auth.py:69` verificado |
| **HubSpot** | 8210 | ✅ | 11 | ✅ | ✅ CRM v3 **(vivo)** | ✅ env upstream | ✅ **(vivo)** | ✅ **(vivo)** | ✅ **(vivo)** | ✅ **(vivo)** | ◻ | **0** | ◻ | **probado E2E §7** |

**Probado por mí en vivo (HubSpot):** `run_full_load/contacts` → `record_count:1`, parquet en `s3://lakehouse/raw/hubspot/contacts/load_date=2026-06-12/batch_id=.../contacts.parquet` (10,957 b, schema CRM: `hubspot_id,email,firstname,lastname,company,jobtitle,lifecyclestage,...`). `run_incremental/contacts` → watermark `2026-05-31T08:05:00Z` persistido en `entity_watermarks` (buffer 5 min aplicado). Silver materializado (1 row) → Gold materializado en `gold_hubspot_contacts_latest` (FORCE RLS + policy `tenant_workspace_rls`).

---

## 6. Matriz de acciones reales (Control Room / Copilot)

| Acción | UI existe | Endpoint | Backend ejecuta | MCP/tool real | DB cambia | Audit event | RBAC/approval | Verificable | Veredicto |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| CR: ver señales | ✅ | `GET /control-room/summary` | ✅ Gold query | n/a | — | — | ✅ datasets.read | ✅ **(vivo)** | **REAL** |
| CR: decisión approve/reject | ✅ | `POST /anomalies/{id}/decision` | ✅ INSERT | n/a | ✅ decisions+actions | ✅ | ✅ control_room.write+CSRF | ✅ | **REAL** |
| CR: dry-run | ✅ | `POST /items/{id}/action-dry-run` | ◻ siempre `validated:true` | ❌ | ❌ | ◻ | ✅ | ◻ | **FAKE (cosmético)** |
| CR: execute `create_followup_task` | ✅ | `POST /items/{id}/execute` | ✅ | ◻ interno | ✅ 1 fila decision_actions | ✅ | ✅ control_room.execute | ✅ | **REAL (delgado)** |
| CR: execute otras 9 plantillas | ✅ | `POST /items/{id}/execute` | ❌ sin adapter | ❌ | ❌ | ◻ | ✅ | ❌ | **NO IMPLEMENTADO** |
| CR: write-back externo | ✅ | execute (flag) | ⛔ bloqueado a propósito | adapters listos | registra `blocked` | ✅ `execute.blocked` | ✅ + 409 | ✅ | **BLOQUEADO HONESTO** |
| CR: lecciones aplicar | ✅ | `POST /items/{id}/lessons/{id}/apply` | ✅ boost prioridad | n/a | ✅ | ✅ | ✅ | ◻ | **REAL storage / PARCIAL recalibración** |
| Copilot: chat con tool_use | ✅ | `POST /conversations/{id}/messages` | ✅ LLM+tools | ✅ mcp_registry.invoke | ✅ msgs+audit | ✅ | ✅ tool_policy+approval | ✅ **(vivo: 502 sin key)** | **REAL (fail-closed)** |
| Copilot: approval gate write | ✅ | tool_results claim | ✅ estado DB atómico | ✅ | ✅ tool_calls/results | ✅ | ✅ no-args | ✅ | **REAL** |

---

## 7. Hallazgos P0 / P1 / P2 / P3

> Estado: **confirmado** (evidencia directa) · **sospecha** · **no verificado**.

### P0 — Bloqueantes
**Ninguno confirmado.** No encontré: fake execute vendido como real, secretos comprometidos, RLS de Gold con bypass, ni endpoints que devuelvan success fijo simulando trabajo. El write-back externo está bloqueado y auditado honestamente. *(Esto es notable y a favor del proyecto.)*

### P1 — Necesarios para beta privada seria

**F-OPS-1 · RLS operacional sin backstop para `omega_console`/`omega_refinement`** — **confirmado (probado en vivo)**
- Evidencia: `pg_policies` muestra `decisions_platform_owner_rls … {omega_console,omega_refinement} qual=true` (y 26 políticas `USING(true)` en total). Migración `infra/init/99e_operational_native_rls.sql:3-6` lo documenta como transitorio.
- **Prueba dinámica:** inserté `wsA-secret` y `wsB-secret` en `decisions` (dos workspaces). Como `omega_console` con `SET app.workspace_id='wsA'`, un `SELECT` devolvió **ambas filas** (incluida la de workspace B). El rol es `NOBYPASSRLS=f` pero la política permisiva `true` siempre gana (las RLS policies se combinan con OR).
- Mitigante verificado: la **API sí filtra** (`GET /api/decisions` devolvió solo `wsA-secret` vía `_dec_visible_clause`, `main.py:6680`). Por eso es P1 y no P0: no hay fuga user-facing probada, pero el aislamiento depende 100% del app-layer.
- Impacto: cualquier query futura que olvide el `WHERE workspace_id` filtra datos cross-tenant sin que la DB lo impida, para los dos roles que más SQL ejecutan.
- Fix: reemplazar `USING(true)` por política scoped real (`omega_rls_workspace_matches`) para console/refinement, o mover esos servicios a un patrón set-scope-per-tx obligatorio con `NULLIF` fail-closed. **Riesgo del fix: ALTO** — puede romper queries platform-wide legítimas (catálogos, admin); requiere auditar cada SELECT de esos roles y tests de no-regresión por tabla.

**F-RAG-1 · `rag_sources`/`rag_chunks` sin scope ni RLS** — **confirmado**
- Documentos subidos por usuarios (`kind='document'`) legibles por `omega_workspace`/`omega_mcp_infra`/`omega_airflow_dag` sin columna tenant/workspace ni política. Fuga potencial de RAG cross-tenant.
- Fix: añadir `workspace_id` + RLS a tablas RAG. **Riesgo: medio** (re-embed/backfill).

**F-INFRA-1 · MailHog en compose de producción AWS** — **confirmado**
- `infra/docker-compose.aws.yml:298-309` incluye MailHog y `console` depende de él; terraform default `email_provider=smtp/mailhog`. Correo de producción podría ir a un catcher en vez de SES.
- Fix: default SES en prod, quitar MailHog de compose AWS, test que lo prohíba. **Riesgo: bajo.**

**F-AIR-1 · DAGs scheduled arrancan unpaused en prod** — **confirmado**
- `AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION="false"` en todos los compose live; `entity_scheduler` incluso fuerza unpause. El plumbing de pausa del release-gate existe pero queda inerte.
- Fix: default paused + activación explícita. **Riesgo: bajo.**

**F-CR-1 · Dry-run cosmético** — **confirmado**
- `execution.py:1250-1257` siempre retorna `validated:true`. Se presenta un gate de validación que no valida.
- Fix: validar args/permisos/thresholds reales antes de marcar `validated`. **Riesgo: medio.**

**F-CR-2 · Una sola acción interna con sustancia** — **confirmado**
- Solo `create_followup_task` ejecuta; 9/12 plantillas sin adapter; no se dispara MCP/DAG. La promesa de “ejecución supervisada” es delgada.
- Fix: cablear ≥2 acciones reales a tools MCP existentes (ya implementadas). **Riesgo: medio.**

**F-CART-1 · HubSpot sin tests + 6 copias divergentes del runtime común** — **confirmado**
- `cartridges/hubspot` no tiene dir `tests/`. `vault_client.py`/`sql_guard.py`/`watermark_service.py` tienen 6 md5 distintos entre cartuchos: el “contrato común” es copy-paste con drift, no librería compartida.
- Fix: extraer paquete compartido `cartridge_runtime/`; portar suite de tests a HubSpot. **Riesgo: medio.**

### P2 — Mejoras antes de cliente

| ID | Hallazgo | Evidencia | Estado |
|---|---|---|---|
| F-SEC-1 | `x-security-context` anti-replay es **solo freshness (300s), sin nonce/replay-cache**: firma reutilizable ≤300s por quien tenga la transport key | `security_context.py:87-90` | confirmado |
| F-RBAC-1 | Rutas RBAC-prefijadas dependen del dep propio de la ruta (middleware no 401ea fallthrough); correcto-por-diseño pero frágil sin test que lo asegure | `main.py` middleware | confirmado |
| F-DB-1 | Rol Gold compartido por console+refinement+superset con DML total | compose:164/193/334 | confirmado |
| F-DB-2 | `audit_events` sin scoping ni RLS (PII mezclada entre workspaces) | `16_audit_events.sql` | confirmado |
| F-DATA-1 | Readiness puede reportar `ready` con fila `silver_lineage` stale aunque la tabla Gold esté vacía | `readiness.py` | confirmado |
| F-DATA-2 | Datasets `_latest` leen historia completa (`**/*.parquet`) en vez de la última partición | datasets SQL | confirmado |
| F-CART-2 | Watermark puede retroceder en 5/6 cartuchos (solo Salesforce tiene guard monotónico) | `watermark_service.py:45` | confirmado |
| F-CR-3 | `auto-run` hardcodea opción inválida `remediate` para ítems intelligence → 400 | `state.py` | confirmado |
| F-CR-4 | Tabla `control_room_action_templates` muerta (runtime usa dict Python) | esquema vs runtime | confirmado |
| F-MCP-1 | Sin quota de costo/rate de LLM en copilot; tools Superset-write gated solo por dev-flag (no el doble-gate RCE) | `mcp_copilot.md` | confirmado |
| F-INFRA-2 | Sin `restart:` en postgres/gold/redis/minio/refinement (compose local); red plana sin segmentación | compose | confirmado |
| F-INFRA-3 | Drift: Salesforce ausente del deploy AWS pese a que airflow prod lo referencia | compose.aws | confirmado |

### P3 — Nice-to-have
- Drift de versión: `VERSION=1.45.68-beta` vs docs/diagrama `v1.45.3-beta` vs evidencia AWS `v1.45.30-beta` (~38 parches atrás).
- ~35 archivos legacy muertos servidos en el export (HTML/JS inalcanzables, middleware los 404ea).
- Dos frontends en prod: Next.js dueño de ~30 rutas, pero **Workspace y Studio siguen en HTML/JS legacy** (que sí llama APIs reales).
- 11 grupos de números de migración duplicados (`93_*` ×3, `94_*` ×2); `43_backfill` pre-estampa 59-64 → grant de 63 se salta en upgrades.
- `next dev --port 8001` choca con Workspace; branding `FEMSA` hardcodeado en un panel; catálogo de cartuchos hardcodeado en ≥5 componentes (hazard de drift).
- `onboarding.py` sin consumidores UI; intelligence router ~8 endpoints pero solo `signals/{id}/outcome` cableado.

---

## 8. Tests ejecutados

| Comando | Resultado | Tiempo aprox | Qué cubre | Qué NO cubre |
|---|---|---:|---|---|
| `.venv/bin/ruff check .` | **PASS** (All checks passed) | <1 s | Lint Python global | — |
| `pytest tests/` | 2405 pass, 6 fail→**0 reales**, 66 skip | 134 s | Contratos, seguridad, RLS, cartridge audit | Las 6 “fallas” eran deps faltantes (pgvector) o docker-gated; pasan al instalar `mcp-infra/requirements.txt` |
| `pytest console/tests/` | **815 pass**, 1 error (docker pull rate-limit) | 46 s | Routers, auth, RBAC, CSRF, copilot, control room | El error es testcontainers (red), no funcional |
| `pytest refinement/tests/` | **158 pass** | 2 s | Guard AST, materialización, scoping | — |
| `pytest vault/tests/` | **46 pass** | 1 s | Fernet, RLS allowlist, auditoría | — |
| `pytest workspace/tests/` | **33 pass** | 1 s | Chat, Gold SQL, CORS | — |
| `pytest cartridges` | **242 pass**, 1 skip | 8 s | 6 cartuchos: extracción, watermark, MCP, guard | Validación de parquet/MinIO real (todo mock en estos tests) |
| **TOTAL** | **3,699 passed**, 67 skip, 0 fallas funcionales | ~4 min | — | — |
| **Pruebas dinámicas mías** (no son del repo) | ver §7/§5 | ~20 min | Boot real, login, Bronze→Silver→Gold, Gold RLS, copilot fail-closed, leak operacional | Superset/Airflow UI (imágenes no traídas) |

**Tests que solo prueban mocks (smell confirmado):** los 242 de cartridges y muchos de console usan `TestClient` in-process con upstreams/MinIO mockeados — **ningún test del repo valida parquet real en MinIO ni materialización Gold real**. Yo lo cubrí manualmente en vivo, pero el repo no tiene esa cobertura automatizada (riesgo de regresión silenciosa en el pipeline de datos).

---

## 9. Riesgos por “NO VERIFICADO”

| Área | Por qué no se verificó | Qué faltó |
|---|---|---|
| **Superset 3.1.3 Gold + RLS nativo end-to-end** | La imagen `apache/superset:3.1.3` no se pudo traer (Docker Hub 403 / rate-limit sin auth en el entorno) | Registry auth o mirror; levantar Superset y validar que lee `modecissions_gold` con RLS aplicada |
| **Airflow DAGs disparando cartuchos por REST en vivo** | Imagen `mode-airflow:2.10.5-local` requiere build con red a PyPI/registry | Build de imagen; ejecutar `entity_scheduler`/`file_ingest` reales |
| **MinIO real (usé moto S3 como stand-in)** | `minio/minio:RELEASE.2024-12-18` no traída; moto cubre la API S3 que usa el código | Levantar MinIO real para validar políticas de bucket/credenciales |
| **`test_gold_native_rls_contract` / `cross_tenant_api_isolation`** | Usan `testcontainers`/`docker run`; el daemon estaba arriba pero sin imágenes Postgres pre-traídas | Imágenes Postgres locales; aun así **verifiqué la RLS de Gold manualmente** (§7) con resultado positivo |
| **Bedrock RAG embeddings reales** | Requiere credenciales AWS (no presentes, correcto en audit) | `AWS_ACCESS_KEY_ID`/Bedrock; el código está cableado a Titan v2 dim 1024 |
| **Copilot con LLM real** | Sin `ANTHROPIC_API_KEY` (correcto); verifiqué **fail-closed** | API key para validar tool_use real end-to-end |
| **Cartuchos contra APIs externas reales** | Sin credenciales SAP/Salesforce/Replicon/HubSpot (correcto) | Probé HubSpot contra el fake upstream del propio repo (válido y controlado) |
| **Terraform plan/apply** | Prohibido por consigna (no consumir cloud) | Solo auditoría estática de `.tf` |
| **GitHub Actions** | Prohibido ejecutarlos/reactivarlos | Solo auditoría de config (7 workflows, ninguno disabled) |

---

## 10. Plan de remediación (ordenado por impacto)

### P0 bloqueantes
- *(ninguno)* — el sistema no tiene bloqueantes confirmados para beta privada.

### P1 — necesarios para beta privada seria
1. **F-OPS-1** — Cerrar el backstop RLS operacional para `omega_console`/`omega_refinement` (o documentar explícitamente que el aislamiento es app-layer y blindar con tests por tabla). *Mayor riesgo/impacto del lote.*
2. **F-RAG-1** — Añadir `workspace_id` + RLS a `rag_sources`/`rag_chunks`.
3. **F-INFRA-1** — Sacar MailHog de prod AWS; default SES.
4. **F-CR-1 + F-CR-2** — Dry-run real + cablear ≥2 acciones de ejecución con sustancia (MCP).
5. **F-AIR-1** — DAGs paused-by-default en prod.
6. **F-CART-1** — Tests para HubSpot + extraer runtime compartido de cartuchos.

### P2 — antes de cliente
7. Anti-replay con nonce/cache para `x-security-context` (F-SEC-1).
8. RLS/scoping en `audit_events`; rol Gold dedicado por servicio (F-DB-1/2).
9. Watermark monotónico en los 6 cartuchos; readiness que no quede `ready` con Gold vacío (F-CART-2, F-DATA-1).
10. Quota de costo LLM en copilot; arreglar `auto-run remediate` 400 (F-MCP-1, F-CR-3).

### P3 — nice-to-have
11. Resolver drift de versión (VERSION/docs/diagrama/AWS).
12. Borrar dead code legacy del export; decidir frontend canónico (terminar migración Next o asumir legacy).
13. Normalizar numeración de migraciones; arreglar `43_backfill`.

---

## 11. Prompts siguientes para arreglar (Claude Code / Codex)

### Prompt 1 — Fix RLS operacional (F-OPS-1) `[P1, riesgo ALTO]`
```
Objetivo: eliminar la fuga del backstop RLS para omega_console y omega_refinement en la DB operacional (modecissions).
Contexto: las tablas operacionales (decisions, control_room_*, datasets, users, ...) tienen DOS políticas: una scoped por workspace y otra `*_platform_owner_rls` con USING(true) para {omega_console, omega_refinement}. Como las policies se OR-ean, el USING(true) anula el aislamiento. Probado en vivo: omega_console scoped a wsA lee filas de wsB.
Archivos probables: infra/init/99e_operational_native_rls.sql, 99f_native_rls_completion.sql, 99g/99h; console/app/db (capa que hace SET app.workspace_id).
Tarea:
 1. Inventariar TODA query de omega_console/omega_refinement que necesite acceso platform-wide legítimo (catálogos, admin, jobs). Marcarlas.
 2. Reemplazar USING(true) por una política que permita (a) filas del workspace activo vía omega_rls_workspace_matches, y (b) un modo platform explícito sólo cuando app.platform_scope='true' esté seteado por código admin auditado.
 3. Garantizar fail-closed: sin app.workspace_id ni app.platform_scope → 0 filas.
Pruebas esperadas: nuevo test que, como omega_console scoped a wsA, NO vea filas de wsB en decisions/control_room_items/datasets; y que las rutas admin platform-wide sigan funcionando.
Criterio de aceptación: el test de fuga (insertar 2 workspaces, leer scoped) devuelve solo el workspace propio a nivel SQL; suite console/tests verde; sin regresión en /api/decisions, control room, dashboards admin.
```

### Prompt 2 — Fix Control Room: dry-run real + acciones con sustancia (F-CR-1/F-CR-2) `[P1, riesgo medio]`
```
Objetivo: que dry-run valide de verdad y que existan ≥2 acciones de ejecución que llamen MCP tools reales.
Archivos probables: console/app/services/control_room/execution.py (dry-run ~1250-1257), state.py, console/app/routers/control_room.py, servicios MCP en console/app/services/mcp_registry.py.
Tarea:
 1. dry-run: validar args contra schema de la plantilla, permiso del usuario, y thresholds del workspace; devolver validated:false con motivos cuando falle. Nada de validated:true fijo.
 2. Implementar adapters reales para 2 plantillas además de create_followup_task (p.ej. notify/escalation vía MCP, o refresh de dataset vía refinement). Deben: invocar mcp_registry.invoke, persistir en decision_actions/control_room_action_executions, emitir audit_events, respetar approval gate.
Pruebas esperadas: test que un dry-run con args inválidos retorna validated:false; test que execute de la nueva plantilla cambia DB + escribe audit + invoca tool (mock MCP) y NO ejecuta si falta approval.
Criterio: 0 plantillas con validated:true incondicional; ≥3 plantillas con efecto verificable end-to-end.
```

### Prompt 3 — Fix Gold materialization scheduler + readiness honesto (F-DATA-1, intelligence scheduler) `[P1/P2]`
```
Objetivo: cerrar readiness falso-verde y automatizar la materialización/intelligence.
Archivos: refinement/app/duckdb_engine.py (lineage), refinement readiness.py, airflow/dags/dataset_refresh_chain.py, entity_scheduler.py, console intelligence run.
Tarea:
 1. readiness: que un dataset Gold con COUNT(*)=0 nunca sea `ready`, aunque exista silver_lineage stale; limpiar lineage al drop.
 2. Añadir/activar un DAG que dispare intelligence.run periódicamente (hoy es manual).
Pruebas esperadas: test readiness con Gold vacío → not ready; test del DAG schedule presente y parseable.
Criterio: no hay readiness verde sin filas; intelligence corre sin intervención manual.
```

### Prompt 4 — Fix conector HubSpot (tests + paridad) y runtime compartido (F-CART-1) `[P1, riesgo medio]`
```
Objetivo: HubSpot con cobertura de tests y extraer el runtime común de cartuchos.
Archivos: cartridges/hubspot/ (no tiene tests/), cartridges/*/app/core/{vault_client,sql_guard,watermark_service}.py (6 copias divergentes).
Tarea:
 1. Portar la suite de cartridges/salesforce/tests a hubspot (test_connection, extracción full/incremental contra fake upstream tests/fixtures/fake_hubspot_api.py, watermark, MCP tools).
 2. Crear paquete compartido cartridge_runtime/ con vault_client/sql_guard/watermark y hacer que los 6 cartuchos lo importen (eliminar copy-paste; un solo md5).
Pruebas esperadas: pytest cartridges/hubspot verde; test que los 6 cartuchos usan el mismo módulo runtime (sin divergencia).
Criterio: HubSpot ≥15 tests; 0 divergencia de runtime entre cartuchos.
```

### Prompt 5 — Fix frontend: un solo frontend canónico + borrar dead code (P3) `[P3, riesgo bajo]`
```
Objetivo: eliminar ambigüedad legacy-vs-Next y dead code.
Archivos: console-next/src (Studio page dead), console/app/static (HTML/JS legacy), console/app/routers/pages.py, main.py rutas /workspace /studio.
Tarea: decidir canónico. Opción A (rápida): borrar Next Studio page + ~35 assets legacy muertos + corregir comentarios stale (studio/page.tsx:13-20, main.py:7495 que dicen "stub" siendo routers/studio.py real). Opción B (completa): migrar Workspace/Studio a Next.
Pruebas esperadas: verify:static verde; ninguna ruta servida apunta a asset borrado; e2e Playwright de control-room/workspace verde.
Criterio: 0 archivos servidos inalcanzables; comentarios coinciden con el código real.
```

### Prompt 6 — Fix infra drift (MailHog prod, DAGs unpaused, versión) (F-INFRA-1, F-AIR-1, P3) `[P1/P3, riesgo bajo]`
```
Objetivo: quitar humo de infra de producción.
Archivos: infra/docker-compose.aws.yml (MailHog ~298-309), infra/terraform/ses.tf + variables (email_provider default), infra/docker-compose*.yml (AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION), VERSION/docs.
Tarea:
 1. Quitar MailHog de compose AWS; default email_provider=ses en prod; test que falle si APP_ENV=production y SMTP apunta a mailhog.
 2. DAGS_ARE_PAUSED_AT_CREATION=true en prod; activación explícita documentada.
 3. Alinear VERSION con docs/diagrama (o documentar la política de versión).
Pruebas esperadas: test_no_mailhog_in_production; test_dags_paused_by_default; test de coherencia de versión.
Criterio: prod no manda correo a un catcher; DAGs no auto-activan; versión coherente.
```

### Prompt 7 — Fix tests del pipeline de datos (cobertura real de MinIO/parquet/Gold) `[P2]`
```
Objetivo: que el repo tenga el test E2E del pipeline que hoy solo existe manualmente.
Contexto: ningún test del repo valida parquet real en MinIO ni materialización Gold real; todo es mock in-process. En esta auditoría se probó a mano: HubSpot fake → Bronze parquet → Silver DuckDB → Gold postgres_gold → RLS bloquea cross-workspace.
Archivos: tests/e2e/test_full_extraction_flow.py, infra/docker-compose.test.yml, refinement/tests.
Tarea: test E2E que levante (o mockee con moto+pg local) el flujo completo y asserte: objeto parquet en bucket con schema esperado, watermark persistido, fila en gold_*, y que omega_refinement_gold scoped a wsB NO ve filas de wsA.
Pruebas esperadas: nuevo test marcado `live`/`e2e` que pase en CI hermético.
Criterio: regresión en cualquier etapa Bronze→Silver→Gold→RLS rompe el test.
```

---

## Apéndice — Evidencia dinámica reproducible (lo que ejecuté en vivo)

```
# Stack DB local (2 clusters PG16+pgvector), init aplicado:
#   MAIN INIT: ok=123 fail=0  → 78 tablas, 15 roles omega (todos NOBYPASSRLS), 35 FORCE RLS, 65 policies, ext: vector,pgcrypto
#   GOLD INIT: ok=3   fail=0  → rol omega_refinement_gold NOBYPASSRLS, FORCE RLS on gold_*

# Console boot + login:
GET /healthz   → 200 {"ok":true,"service":"console","version":"1.45.68-beta"}
POST /auth/login (emmanuel@local.ai) → 200 + access_token (JWT HS256, exp 15m)
GET /api/control-room/summary → 200 (datos Gold scoped, 0 anomalías sin pipeline)
GET /api/intelligence/signals → 200 {"signals":[]}

# Copilot fail-closed sin LLM:
POST /api/copilot/conversations/{id}/messages → 502 "El copiloto no tiene proveedor LLM configurado. ANTHROPIC_API_KEY is required."

# Pipeline de datos REAL (HubSpot contra fake upstream del repo):
POST :8210/skills/run_full_load/contacts → 200 record_count=1
   s3://lakehouse/raw/hubspot/contacts/load_date=2026-06-12/batch_id=.../contacts.parquet (10957 b)
   parquet schema: hubspot_id,created_at,updated_at,firstname,lastname,email,company,jobtitle,lifecyclestage,...
POST :8210/skills/run_incremental/contacts → 200; entity_watermarks: hubspot|contacts|2026-05-31T08:05:00Z (buffer 5min)
DuckDBEngine.materialize(silver) → row_count=1 → s3://lakehouse/silver/hubspot/hubspot_contacts_latest/tenant_id=acme/workspace_id=.../...parquet
DuckDBEngine.materialize(gold)   → row_count=1 → gold_hubspot_contacts_latest en postgres_gold

# Gold RLS enforcement (rol omega_refinement_gold, NOBYPASSRLS=f):
sin scope                    → 0 filas (deny-by-default)
scope workspace EQUIVOCADO   → 0 filas (aislamiento)
scope workspace CORRECTO     → 1 fila
gold table: relrowsecurity=t, relforcerowsecurity=t, policy=gold_hubspot_contacts_latest_tenant_workspace_rls

# Fuga RLS operacional (F-OPS-1) — omega_console scoped a wsA:
SELECT ... FROM decisions WHERE title LIKE 'ws%secret'
   → DEVUELVE wsA-secret Y wsB-secret  (USING(true) anula el aislamiento a nivel SQL)
GET /api/decisions (mismo usuario)  → solo wsA-secret  (app-layer SÍ filtra → no fuga user-facing)
```

*Fin del reporte.*
