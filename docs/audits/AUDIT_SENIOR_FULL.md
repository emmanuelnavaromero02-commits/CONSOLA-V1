# AUDITORÍA SENIOR — CONSOLA-BETA / OMEGA

> Auditoría sin piedad, zero-trust sobre el baseline. Backend, frontend, infra,
> CI/CD, datos/SQL/RLS, MCP/cartridges, seguridad, QA y observabilidad.
> Rama auditada: `claude/beautiful-maxwell-KTDgM` (= `origin/main` @ `22c37f2`).
> Fecha: 2026-06-02. Alcance: TODO excepto modificaciones a Studio (auditado, no tocado).

---

## 0. Veredicto global

| Estado | Veredicto | Motivo en una línea |
|---|---|---|
| **Demo controlada** (single-tenant, operador de confianza) | **APROBADO CON RIESGO** | Los P0 requieren un `workspace_admin` malicioso y multi-tenant; el write-back externo está OFF por default (409). |
| **Beta privada** (multi-tenant real) | **NO APTO** | P0-RLS-001 (escritura cross-tenant) y P0-RLS-002 (sin RLS de BD) muerden exactamente en multi-tenant; + P1 de env/infra que rompen el deploy y discrepancia de write-back. |
| **Producción** | **NO APTO** | Suma de P0 + P1: sin backstop RLS de BD, gate de release débil, contrato de env incompleto, guard MCP duplicado/drift, write-back SAP no endurecido/honesto. |

**Resumen de conteo (revalidado con evidencia, no copiado del baseline):**
2 P0 confirmados · 9 P1 confirmados · ~18 P2 · ~7 P3 · **6 hallazgos del baseline REFUTADOS**.

---

## 1. Resumen ejecutivo brutal

La plataforma está **mucho más endurecida de lo que el baseline sugiere** en autenticación, CSRF, RBAC, CORS, headers, Terraform y supply-chain de CVEs — y **varios de los P1 del baseline ya estaban arreglados** (los confirmé refutados con `file:line`). Pero la narrativa de "RLS estricto default-deny" que vende el `README.md` es **falsa en su mecanismo y peligrosamente incompleta en su capa**: el aislamiento multi-tenant de los datos *gold* depende de **una sola capa de aplicación** (reescritura AST con sqlglot), sin **ningún** Row-Level-Security a nivel Postgres, sobre **tablas físicas compartidas** entre tenants. Eso convierte dos debilidades en **P0 reales**:

1. **Un admin de workspace (no de plataforma) puede escribir filas con el `tenant_id`/`workspace_id` de otra organización** en la tabla gold compartida, porque el materializador *añade* las columnas de scope pero **no las sobrescribe** si el SQL del dataset ya las trae forjadas (P0-RLS-001).
2. **No existe RLS de Postgres en producción** — el único `CREATE POLICY` del repo vive dentro de un **test**. El equipo conoce y probó la técnica, pero **no la desplegó** (P0-RLS-002). Sin ese backstop, cualquier ruta que olvide el guard de aplicación = lectura cross-tenant total.

La buena noticia: el guard SQL **read-path** (casing, comillas, comentarios, CTE, UNION, subquery, stacked statements, default-deny `1=0`) lo probé **en vivo contra sqlglot 25.16.1** y **no encontré bypass de forma**. El problema es **arquitectónico** (capa única + escritura forjada), no de parsing.

En MCP/cartridges, el P1 estrella del baseline (**SuccessFactors `query_kb` sin `sql_guard`**) está **REFUTADO**: los tres cartridges SAP llaman `validate_kb_sql` idéntico y SuccessFactors es el **más estricto**. El riesgo real reframeado es `mcp-infra.cartridge_query_kb`, que ejecuta SQL sin guard *in-tool* y se apoya en un guard **duplicado y con drift** en `main.py` (P1-MCP-001).

En CI/CD, **el coverage SÍ se publica** (baseline refutado), pero `release.yml` **puede publicar 12 imágenes sin ruff/pip-audit/bandit/pytest completo/smoke/e2e** (P1-CI-001). En infra, **no hay `service_started`** (baseline refutado) y **`.env.example` está completo** (baseline refutado), pero AWS **suelta `vault` de `depends_on`** (race de cold-boot) y **`bootstrap.sh` no genera las 18 claves per-pair** que el compose AWS exige (el deploy aborta).

QA: con el **Python correcto del proyecto (3.12)**, las suites pasan limpias (**~3.272 passed, 66 skipped opt-in, 1 error solo por falta de Docker daemon**). En **Python 3.11** (mi sandbox inicial) `control_room/api.py:305` no compila (f-string PEP 701) — irrelevante para prod (Docker/CI son 3.12) pero **ruff no lo detecta** (target `py312`) y deja el repo sin soporte 3.11.

**Bottom line:** producto sólido para una **demo controlada**, pero con **dos P0 de aislamiento de datos** y un puñado de P1 de operación/seguridad que **bloquean beta y producción** hasta cerrarse.

---

## 2. Top 10 hallazgos más graves

| # | ID | Sev | Estado | Una línea |
|---|---|---|---|---|
| 1 | **P0-RLS-001** | P0 | confirmed | `workspace_admin` escribe filas con scope de víctima forjado en la tabla `gold_{name}` compartida (`_ensure_scope_columns` solo añade, no pisa). |
| 2 | **P0-RLS-002** | P0 | confirmed | No hay RLS Postgres en producción; el único `CREATE POLICY` está en un test. Aislamiento solo app-layer, sin backstop de BD. |
| 3 | **P1-CR-001** | P1 | confirmed | El write-back SAP HCM IT0008 **existe** (flag OFF→409) pero el runbook afirma "no hay write-back SAP". Un env var dispara mutaciones reales. |
| 4 | **P1-MCP-001** | P1 | confirmed | `mcp-infra.cartridge_query_kb` corre SQL sin guard in-tool; seguridad depende de un guard duplicado/drift + `_duckdb()` sin `lock_configuration`. |
| 5 | **P1-CI-001** | P1 | confirmed | `release.yml` publica imágenes sin ruff/pip-audit/bandit/pytest completo/smoke/e2e. |
| 6 | **P1-ENV-002** | P1 | confirmed | `bootstrap.sh` no llama a `bootstrap-keys.sh` → 18 claves per-pair vacías → el compose AWS aborta en el primer boot. |
| 7 | **P1-INFRA-002** | P1 | confirmed | AWS suelta `vault` de `depends_on` en console/mcp-infra/refinement → race en cada cold boot. |
| 8 | **P1-RLS-003 / P1-SQL-005** | P1 | confirmed | El engine RLS no es self-default-deny para `pggold.main.x` y su blocklist no cubre INSERT/UPDATE/DELETE sobre conexión read-write; solo el gate de `main.py` salva. |
| 9 | **P1-MCP-002** | P1 | confirmed | `/mcp/rpc` de cartridges corre con `security_context` vacío → lecturas cartridge-wide (tras internal API key). |
| 10 | **P2-WS-XSS-001** | P2 | confirmed | `workspace.js` renderiza `[x](//attacker.com)` desde salida del LLM → phishing por prompt-injection. |

---

## 3. Revalidación del baseline (uno por uno, con evidencia)

| Baseline | Veredicto | Evidencia `file:line` |
|---|---|---|
| **P1-ENV-001** .env.example incompleto + mismatch `BOOTSTRAP_ADMIN_NAME` | ❌ **REFUTADO** → P1-ENV-002 | `.env.example` cubre las vars `:?`; el nombre correcto `BOOTSTRAP_ADMIN_FULL_NAME` está presente. Problema real: `bootstrap.sh` no llama a `bootstrap-keys.sh`. |
| **P1-INFRA-001** compose usa `service_started` | ❌ **REFUTADO** → P1-INFRA-002 | Cero `service_started`; todo `service_healthy`/`service_completed_successfully`. Real: AWS suelta `vault` de `depends_on`. |
| **P1-SQL-001** SuccessFactors `query_kb` sin `sql_guard` | ❌ **REFUTADO** | `sap_successfactors/app/mcp_server.py:368,376` == hcm/s4hana; SF tiene `_TAUTOLOGY_RE` extra (`sql_guard.py:31-34,83-84`). 6/6 cartridges con `sql_guard.py`. |
| **P1-CR-001** Control Room write-back bloqueado/no implementado | ⚠️ **ACTUALIZADO** | Bloqueado por default (`execution.py:2248` → 409) pero **implementado** (`sap_hcm_adapter.py:43` POST a SAP). Discrepancia con runbook (`09_demo_beta.md:21`). |
| **P1-CR-CSP-001** Control Room `script-src unsafe-inline` | ❌ **REFUTADO** | `CONTROL_ROOM_SECURITY_HEADERS` (`main.py:880`) = `script-src 'self'`; hashes SHA-256 en `pages.py:82-92`. Solo `style-src` global → P2-HEADERS-004. |
| **P2-STATIC-001** HTML/JS legacy vivo | ❌ **REFUTADO** → P3-STATIC-001 | `main.py:1207-1230` 404ea todo `/static/*.html` antes de auth. Código muerto, no superficie viva (solo `workspace.html` en `/workspace`, auth-gated). |
| **P2-KB-001** KBs SAP con gaps | ✅ **CONFIRMADO** (mitigado) | `compensation_distribution.sql:10-17` (`WHERE FALSE`), `workforce_cost_monthly.sql:21` (NULL); divulgado en `knowledge_bits.yaml`; datasets huérfanos. |
| **P2-FE-UNIT-001** FE tests superficiales | ✅ **CONFIRMADO** | AppChrome/CredentialsForm/ChatMessages/control-room (2573 líneas)/operations sin unit tests. |
| **P2-CI-COV-001** CI no publica coverage | ❌ **REFUTADO** | `docker-image.yml:93-99` coverage xml/json + artifact; `lint.yml:59-67` vitest --coverage. Residual: sin `--fail-under`. |
| **P2-MONO-001** Monolitos grandes | ✅ **CONFIRMADO** | `console/app/main.py` **5.903 líneas**; `control-room/page.tsx` **2.573**; `control_room/` ya troceado en submódulos (7.276 líneas). Blast radius alto. |
| **P2-CART-UX-001** Cartridge viewer fallback genérico | ✅ **CONFIRMADO** | `cartridges.ts:128-146` inyecta base_url/token genéricos con el shape legacy `connector.api.base_url_env`. |
| **P2-LIVE-E2E-001** Stack vivo no ejecutado | ⚠️ **PARCIAL** | La suite viva existe y está bien gateada (skips opt-in); **no se pudo levantar** (sin Docker daemon en el sandbox). Ver §6. |
| Baseline RESOLVED (DAGs SAP, admin endpoints, Marketplace) | ✅ **CONFIRMADOS resueltos** | DAGs montados + tests; admin endpoints con CSRF+`iam.users.write`+scope (`main.py:5390-5798`); Marketplace en Next+backend. |

---

## 4. Tabla completa de hallazgos por severidad

Ver `AUDIT_SENIOR_FINDINGS.json` para el detalle estructurado (impacto, reproducción, evidencia, fix, tests, blocks_*). Resumen:

### P0 (confirmados — 2)
- **P0-RLS-001** — Escritura cross-tenant en tablas gold vía columnas de scope forjadas. `duckdb_engine.py:537-543,1050,1058,1062` · `console/app/routers/v1/data.py:76,109` · `permissions.py:152-157`.
- **P0-RLS-002** — Sin RLS Postgres en producción; tablas gold compartidas, aislamiento solo app-layer. `infra/init_gold/34_postgres_gold_role.sql:23` · `console/tests/test_cross_tenant_api_isolation.py:215-238` (único `CREATE POLICY`, en test).

### P1 (confirmados — 9)
- **P1-MCP-001** — `cartridge_query_kb` sin guard in-tool; `_duckdb()` sin `disabled_filesystems`/`lock_configuration`; LIMIT no acotado. `mcp-infra/app/tools/cartridges.py:1355-1381,40-51` · `main.py:792-811`.
- **P1-MCP-002** — `/mcp/rpc` sin scoping de tenant (`security_context` vacío). `cartridges/*/app/main.py:143` · `kb_service.py:24-28`.
- **P1-RLS-003** — Inyector RLS evadible por identificador de 3 partes (`pggold.main.x`). `duckdb_engine.py:866`.
- **P1-RLS-004** — Claims de RLS del README falsos (no regex/read_only/_read_conn/apply_rls/DB-RLS). `README.md:4,7,27` vs `duckdb_engine.py:129,825`.
- **P1-SQL-005** — `_validate_safe_sql` omite INSERT/UPDATE/DELETE/TRUNCATE sobre conexión read-write. `duckdb_engine.py:636-647,129`.
- **P1-CR-001** — Write-back SAP implementado pero runbook lo niega. `sap_hcm_adapter.py:43` · `execution.py:2236-2248` · `09_demo_beta.md:21`.
- **P1-CI-001** — `release.yml` sin ruff/pip-audit/bandit/pytest completo/smoke/e2e. `release.yml:42-49`.
- **P1-INFRA-002** — AWS suelta `vault` de `depends_on`. `docker-compose.aws.yml:168-176,252-257,370-377`.
- **P1-ENV-002** — `bootstrap.sh` no genera claves per-pair. `bootstrap.sh` · `bootstrap-keys.sh:17-51` · `docker-compose.aws.yml:537-540`.

### P2 (confirmados — ~18, selección)
P2-WS-XSS-001 (markdown protocol-relative) · P2-MCP-003 (`str(exc)` leakage) · P2-HEADERS-004 (style-src unsafe-inline) · P2-AUTH-002 (JWT blacklist fail-open) · P2-AUTH-003 (legacy key non-prod) · P2-CI-002 (Actions sin SHA pin) · P2-CI-004 (hubspot deps sin pin) · P2-CI-005 (starlette sin cota) · P2-OBS-001 (`_fetch_tools` bare pass) · P2-OBS-003 (cartridges sin RequestID) · P2-DIVERGE-003 (Superset `--reload` en prod) · P2-INFRA-PORTS-001 (Superset/Mailhog 0.0.0.0 local) · P2-ENV-003 (APP_BASE_URL/IMAGE_TAG sin documentar) · P2-CART-UX-001 · P2-FE-UNIT-001 · P2-KB-001 · P2-RLS-006 (comentario engañoso) · P2-PORT-001 (PEP 701 / ruff target).

### P3 (confirmados — ~7, selección)
P3-AUTH-001 (settings `_require_admin` latente, NO explotable) · P3-MCP-004 (6 copias de `sql_guard` con drift) · P3-STATIC-001 (HTML muerto) · P3-OBS-001 (16 broad excepts en copilot) · P3-OBS-002 (proxy timeout=None) · P3-WS-LS-001 (email en localStorage) · P3-CI-FORMAT-001 (ruff format advisory).

### REFUTADOS (6) — importante registrarlos
P1-SQL-001 · P1-CR-CSP-001 · P1-INFRA-001(orig) · P1-ENV-001(orig) · P2-CI-COV-001 · Bandit B613 Trojan-Source · Terraform IAM/S3 (least-privilege / public-access bloqueado).

---

## 5. Comandos ejecutados (resumen; detalle en `AUDIT_SENIOR_TEST_LOG.md`)

| Comando | Resultado |
|---|---|
| `ruff check .` | ✅ **All checks passed** (exit 0) |
| `pytest` × 6 suites en **Python 3.12** | ✅ **~3.272 passed, 66 skipped (opt-in), 1 error (sin Docker daemon)** |
| `pytest` × 6 suites en **Python 3.11** | ⚠️ 8 failed / 70 errors **= artefactos de versión** + `console/tests` no colecta (SyntaxError PEP 701) |
| `bandit -r ... --severity-level medium --confidence-level high` | ✅ **0 hallazgos**; scan completo: 2.359 (1 HIGH refutado, 129 MEDIUM ≈ B608 string-SQL en interpolaciones de identificadores validados) |
| `pip-audit -r <14 requirements>` | ✅ **0 CVEs conocidos** en los 14 archivos |
| `docker compose -f infra/docker-compose.test.yml config -q` | ✅ válido |
| `docker compose config` (base/sap/aws) | ⚠️ falla por env vars ausentes (esperado sin `infra/.env`); estructura revisada estáticamente |
| `python3.11/3.12 ast.parse control_room/api.py` | 3.11 ❌ SyntaxError · 3.12 ✅ OK |
| `npm --prefix console-next ci/lint/typecheck/test/verify:static/audit` | ✅ **todo verde** (0 vuln, typecheck OK, vitest 27✓, verify:static sin drift, 1 warning lint) |
| `npm --prefix tests-e2e ci/audit/test:list` | ✅ 0 vuln · **360 specs en 18 files** (Playwright en vivo no ejecutado — sin stack) |

**Subagentes usados (6 carriles en paralelo):** AppSec/Auth · MCP/Cartridges · Data/SQL/RLS · Infra/DevOps · Frontend/Browser · CI-CD/Observabilidad. Coordinación y verificación de joyas de la corona hechas por el lead (yo), con lectura directa de `file:line`.

---

## 6. Qué NO se pudo ejecutar y por qué

| Comando / gate | Motivo exacto | Alternativa usada |
|---|---|---|
| `terraform fmt/init/validate` | **`terraform` no está instalado** (`which terraform` → vacío) | Revisión estática de los `.tf` (IAM, S3, SG, secretsmanager) |
| `make up` / `make smoke` / `make e2e` / `make test-hermetic` / `make acceptance` / `make verify-release` | **No hay Docker daemon** en el sandbox (`/var/run/docker.sock` ausente) + sin `infra/.env` | Validación estática de compose + suites pytest unitarias/herméticas-sin-stack |
| `console/tests/test_cross_tenant_api_isolation.py` (RLS real) | Levanta un contenedor `pgvector` real → requiere Docker daemon | Verificación estática del esquema (confirmó P0-RLS-002: sin policies en `infra/init`) |
| Suite viva `tests/e2e/*`, `tests/test_e2e_*`, `test_security_negative_v141` | Stack no levantado (sin daemon); tests **opt-in** correctamente gateados → **66 SKIPPED** | N/A — documentado |
| `npm ... playwright test` (e2e navegador) | Requiere stack vivo en `:8000` | `test:list` para inventariar specs |
| Instalación de deps Python en el intérprete del sistema | Conflicto `PyYAML` (instalado por Debian, sin RECORD) | **venvs limpios** `/tmp/auditvenv` (3.11) y `/tmp/venv312` (3.12) |

**Ninguna de estas omisiones se ocultó.** Donde un control crítico (RLS de BD, write-back, env contract) no pudo probarse en vivo, se verificó por trazado estático con `file:line` y se marcó `needs-runtime-validation` donde corresponde.

---

## 7. Riesgos bloqueantes (resumen accionable)

**Bloquean BETA y PRODUCCIÓN (cerrar antes de multi-tenant real):**
1. **P0-RLS-001** — Proyectar a la fuerza el scope server-side en materialize gold + RLS `WITH CHECK`.
2. **P0-RLS-002** — Desplegar RLS Postgres en `gold_*`/`master_*` (la técnica ya existe en el test; llevarla a `infra/init_gold`).
3. **P1-ENV-002** — `bootstrap.sh` debe encadenar `bootstrap-keys.sh` (o documentar el dos-pasos como atómico).
4. **P1-INFRA-002** — Añadir `vault: service_healthy` a `depends_on` AWS de console/mcp-infra/refinement.
5. **P1-CI-001** — `release.yml` debe correr los mismos gates que `make verify-release`.
6. **P1-CR-001** — Alinear runbook ↔ código del write-back SAP y endurecer/aprobar el path antes de venderlo.

**Bloquean PRODUCCIÓN:**
7. **P1-MCP-001/002**, **P1-RLS-003**, **P1-SQL-005** — Consolidar un guard SQL compartido, hardening de `_duckdb()`, allowlist real default-deny, conexión read-only.
8. **P2-CI-002/004/005**, **P2-DIVERGE-003**, **P2-AUTH-002**, **P2-OBS-001** — supply-chain pins, Superset prod, Redis HA, observabilidad.

---

## 8. Claims que NO se pueden vender todavía

- ❌ **"RLS estricto default-deny vía parameterized queries / read_only"** (README §1, §4, tabla): el mecanismo es AST (no regex), la conexión es **read-write**, y **no hay RLS de BD**. *(P1-RLS-004, P0-RLS-002)*
- ❌ **"Aislamiento multi-tenant garantizado"**: hay una **escritura cross-tenant** real para `workspace_admin` y **una sola capa** de aislamiento sin backstop de BD. *(P0-RLS-001/002)*
- ❌ **"No hay write-back SAP/Replicon"** (runbook 09): el adapter SAP HCM IT0008 **existe** y dispara con un env var. *(P1-CR-001)*
- ❌ **"Write-back productivo a ERP/SAP"**: en V1 está **OFF por default (409)** y no endurecido para prod. *(P1-CR-001)* — no se puede vender ni la ausencia ni la presencia como "lista".
- ❌ **"Release gateado por tests/seguridad"**: `release.yml` **no** corre ruff/pip-audit/bandit/pytest completo/smoke/e2e. *(P1-CI-001)*
- ⚠️ **Analítica SAP de compensación / costo laboral / pipeline de reclutamiento**: stubs (`WHERE FALSE`/NULL); divulgados pero no funcionales. *(P2-KB-001)*
- ⚠️ **"Soporta Python 3.11"**: `control_room/api.py:305` no compila en 3.11. El proyecto es **3.12-only** de facto. *(P2-PORT-001)*

**Sí se puede sostener (verificado):** auth/JWT/CSRF/RBAC robustos; CORS fail-closed; 0 CVEs en deps; IAM/S3 least-privilege; Dockerfiles non-root; coverage publicado; guard SQL read-path sin bypass de forma; defensas anti-prompt-injection presentes.

---

## 9. Plan de remediación por sprints

**Sprint 1 — Aislamiento de datos (P0, bloquea beta):**
- [ ] P0-RLS-001: en `materialize` gold, descartar y reproyectar `tenant_id`/`workspace_id` como constantes server-side (`SELECT <const> AS tenant_id, <const> AS workspace_id, _q.* EXCLUDE(tenant_id, workspace_id) FROM (sql) _q`). + test de regresión cross-tenant write.
- [ ] P0-RLS-002: migración `infra/init_gold/*` con `ENABLE/FORCE ROW LEVEL SECURITY` + `CREATE POLICY ... USING/WITH CHECK (current_setting('app.tenant_id'/'app.workspace_id'))`; refinement fija los GUCs por request. + test de esquema que falla si falta RLS.
- [ ] P1-RLS-003/P1-SQL-005: allowlist default-deny en `_inject_rls_ast` (db **o** catalog), añadir verbos DML al blocklist, conexión read-only.

**Sprint 2 — Deploy / release / env (P1, bloquea beta/prod):**
- [ ] P1-ENV-002: `bootstrap.sh` → `bootstrap-keys.sh`. P2-ENV-003: documentar APP_BASE_URL/GHCR_OWNER/IMAGE_TAG.
- [ ] P1-INFRA-002 / P2-DIVERGE-*: `vault` en `depends_on` AWS; `/readyz` simétrico; quitar `--reload` de Superset prod; bind loopback local.
- [ ] P1-CI-001: `release.yml` corre los gates de `make verify-release` (workflow_call). P2-CI-002/004/005: pins SHA de Actions, pin de hubspot deps, cota de starlette.

**Sprint 3 — MCP / write-back / observabilidad (P1/P2, bloquea prod):**
- [ ] P1-MCP-001/002 + P3-MCP-004: un `sql_guard` compartido; hardening `_duckdb()`; scoping fail-closed en `/mcp/rpc`.
- [ ] P1-CR-001: corregir runbook + endurecer/aprobar write-back SAP (o feature-flag documentado con guard de aprobación).
- [ ] P2-AUTH-002 (Redis HA / TTL), P2-OBS-001/003 (logging + RequestID en cartridges), P2-MCP-003 (no `str(exc)`).

**Sprint 4 — Frontend / QA / limpieza (P2/P3):**
- [ ] P2-WS-XSS-001 (regex markdown), P2-FE-UNIT-001 (4 suites mínimas), P2-CART-UX-001 (migrar connector.yaml).
- [ ] P2-PORT-001 (f-string 3.11 + allowlist de `entity_id_field`), P3-STATIC-001 (borrar HTML muerto), P3-OBS-001/002, P3-CI-FORMAT-001.

---

## 10. Advertencias de cobertura

- **Sin ejecución en vivo del stack** (sin Docker daemon): write-back real, RLS de BD end-to-end, smoke, e2e de navegador y `test-hermetic` **no se ejecutaron**; se verificaron por trazado estático (`file:line`) y marcaron `needs-runtime-validation` donde aplica. **Recomendado:** correr `make verify-release` en un entorno con Docker/Node/Python completo antes de beta.
- **Terraform** revisado de forma estática (binario ausente); no se corrió `terraform validate`.
- **Studio** fue auditado pero **no modificado** (regla de alcance); sus riesgos de CSP/SQL comparten patrón con lo reportado, pero cualquier cambio queda fuera de alcance.
- Los **129 B608 (string-SQL)** de bandit son mayormente interpolación de **identificadores validados** (`SAFE_IDENTIFIER_RE`, `validate_safe_identifier`) o nombres de schema/tabla server-side; **no** se confirmó SQLi explotable por input de usuario, pero la clase merece una pasada de parametrización donde sea posible.
- Severidades reconciliadas entre carriles (p.ej. JWT blacklist fail-open: P2, no P1, por ventana de 15 min). Donde dos agentes discreparon, prevalece la verificación directa con `file:line`.
