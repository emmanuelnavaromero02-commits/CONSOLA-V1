# OMEGA INDEPENDENT AUDIT — FINAL

**Fecha:** 2026-08-13 · **Método:** auditoría hostil evidence-first, 8 auditores especializados en paralelo + refutación adversarial independiente (11 agentes, 808 operaciones de verificación, 0 conclusiones heredadas de reportes previos) · **Alcance:** GitHub `emmanuelnavaromero02-commits/CONSOLA-V1` @ `48d29731` (main), GitHub Actions API, working tree local. **Sin acceso cloud:** credenciales GCP/AWS del contenedor inválidas — todo estado runtime se marca UNKNOWN o INFERENCE. **Sin acceso** a `/Users/emmanuel/CONSOLA-BETA` ni al ZIP W01–W13 (no existe en este entorno; SHA no verificable).

---

## A. EXECUTIVE VERDICT

**Veredicto: CONDITIONAL GO hacia private beta · NO-GO para producción hoy.**

**Nota global: 5.5/10** (ponderada por blockers, no promedio).

OMEGA hoy es una plataforma real de ~215k líneas construida en 3.5 meses (991 commits, ~427 PRs, 1 humano + agentes IA). No es humo: Bronze/Silver/Gold son capas funcionales con RLS nativo forzado, la separación IA≠autoridad-numérica está impuesta estructuralmente (no solo declarada), los 9 conectores tienen clientes HTTP reales con auth/retry/watermarks, y el frontend compila, pasa 388 tests y no contiene un solo dato mock. Pero está **operacionalmente ciega y sin gobernanza de release**: el 33% del test suite lleva 2 meses sin ejecutarse en CI (workflows deshabilitados a mano), las imágenes v1.45.208/209 se publicaron **sin pasar el gate full-stack en su SHA** por un hoyo estructural del pipeline, no existe **ningún backup programado en ningún cloud** (RPO ilimitado), el monitoreo de producción está apagado desde junio, nadie puede afirmar con evidencia qué cloud sirve a los usuarios hoy, y el loop signal→action→outcome se rompe en la ejecución (endpoints 410/409, sandbox simulado). El golden path insignia (SuccessFactors EC) está código-completo pero rehén de un permiso OData que debe otorgar un administrador SAP externo. La ingeniería es mejor que la operación: el cuello de botella no es escribir más código, es probar, desplegar, respaldar y observar el que ya existe.

**Notas por dominio (0–10, sustentadas en secciones C–K):**

| Dominio | Nota | Dominio | Nota |
|---|---|---|---|
| Architecture | 6.5 | CI/Testing | 6 |
| Data platform | 7 | Release engineering | 5.5 |
| Integrations | 6.5 | GCP | 5 (runtime UNKNOWN) |
| Intelligence | 7 | AWS/DR | 4 |
| Control Room | 6 | Observability | 5.5 |
| Analytics | 7 | Scalability | 5 |
| Frontend/product | 7.5 | Multi-tenancy | 7.5 |
| Security | 7 | **Production readiness** | **3.5** |

---

## B. GROUND TRUTH

| Ítem | Valor verificado |
|---|---|
| main SHA | `48d29731c0dd488d84dac5d2efc2c7b45ed2b977` (2026-08-13, "feat(phase7): data-validate FK candidates" #597) |
| VERSION | `1.45.209-beta` — coincide con el tag más reciente de 248 |
| Historia | 991 commits, 2026-05-05 → 2026-08-13; primer commit = "Add files via upload" (código pre-existente subido como ZIP) |
| Working tree | limpio, branch de auditoría = main exacto |
| PRs abiertos | **0** |
| Branches remotos | 79 (incl. `agent/gcp-*` experimentales sin mergear; el trabajo GCP real SÍ está mergeado a main vía #579/#586/#588/#590/#591) |
| GitHub Releases | abandonados: solo 3, el último v1.45.67-beta (11-jun). Desde entonces solo tags. |
| Último deploy verificable | **AWS, v1.45.205-beta, 2026-07-16** (run 29528154629: healthz `{"version":"1.45.205-beta","app_env":"production"}` en console.7businesssolutions.com, readyz?require_data=1 verde). 61 commits detrás de main. |
| Cutover GCP | Commit #591 (12-ago) afirma "the successful 1.45.209-beta deploy used these driver versions" → **INFERENCE**: hubo un cutover GCP live el 12–13 ago a la VM `omega-staging-app`. No verificable desde aquí. |
| Autoridad cloud | **CONTRADICTORIA**: docs/runbook declaran GCP canónico y "AWS es DR standby"; `deploy-aws.yml` sigue `active` con `environment: production`; DNS UNKNOWN. |
| Schedulers/writers | Sin fencing cross-cloud (solo comentarios en scripts). Ambos stacks PUEDEN correr writers simultáneos; ocurrencia real UNKNOWN. |
| Bases de datos | `modecissions` (operacional, pgvector) + `modecissions_gold` (5433, RLS nativo forzado, roles NOBYPASSRLS). Estado runtime UNKNOWN. |
| Backups | Código real (`backup.sh`/`restore.sh` con manifiestos sha256) pero **cero schedule en workflows/terraform/compose de ambos clouds**; evidencia de DR-rehearsal inexistente en repo. |
| CI en main HEAD | Verde: Lint, Security Scan, Control Room PostgreSQL RLS (~10k tests + compose E2E 18 min), MCP Infra PDF Security. **Deshabilitados a mano:** docker-image.yml (05-jul), e2e.yml (26-jul), monitor-aws-health.yml (14-jun). |

---

## C. ARCHITECTURE RECONSTRUCTED (real, no aspiracional)

**Un solo docker-compose de 25 contenedores sobre 1 VM** (GCP prod = mismo compose vía terraform template; AWS igual). No hay microservicios distribuidos reales ni colas: **todo es HTTP síncrono**.

```
                    ┌────────────────────────── 1 VM (compose) ──────────────────────────┐
 Usuario ──HTTPS──► console :8000 (FastAPI 7,847-line main.py + 25 routers + domains/)
                    │  sirve console-next (export estático Next.js 16 commiteado en git)
                    │  JWT+sesión, CSRF, rate-limit Redis, CSP hash por archivo
                    ├── workspace :8001 (expuesto público — UI legacy divergente)
                    ├── refinement :8500 (DuckDB engine → Silver/Gold, staged publication CAS)
                    ├── vault :8300 (credenciales Fernet)
                    ├── mcp-infra :8010 (tools MCP, SQL guards)
                    ├── 9 cartuchos :801x (SF, HCM, S/4, Salesforce, HubSpot,
                    │                      Replicon, Banxico, INEGI, SEC EDGAR)
                    ├── Airflow 2.10.5 LocalExecutor (extract DAGs + dataset_refresh_chain)
                    ├── Superset · Redis · MailHog
                    ├── postgres  (modecissions: operacional; RLS ~97 policies, 18 roles)
                    ├── postgres_gold :5433 (gold_*: RLS nativo FORCE, deny-by-default)
                    └── MinIO (local/AWS) / GCS (GCP) — lakehouse bronze/silver parquet
```

Autenticación inter-servicio: matriz de llaves por par `INTERNAL_API_KEY_X_TO_Y` (compare_digest, legacy key deshabilitada en prod) + contextos HMAC-SHA256 purpose-bound con TTL/nonce/body-digest (`airflow/dags/runtime_security_context.py`, verificados en refinement). Es la arquitectura de aislamiento real dado que casi todos comparten el Postgres operacional.

**Vivo vs muerto:** `domains/` (105 archivos) es descomposición en curso del monolito, importada y real. `console/app/static/*.html` legacy (~15 páginas + JS) está MUERTO (middleware 404 en `/static/*.html`; solo studio.html/workspace.html se sirven). Studio y Workspace son islas legacy vanilla-JS **vivas** dentro del producto Next.

## D. PRODUCT REALITY

| Superficie | Estado | Evidencia clave |
|---|---|---|
| Dashboard | **REAL** | `/api/dashboard/kpis` → SQL scoped |
| Control Room (experience v2 + Talent + SF Gold) | **REAL** (lectura/decisión) | `routers/control_room.py` 60+ endpoints; 9-box SQL sobre Gold |
| Control Room acciones (preview/confirm) | **REAL preview-only** | `/actions/preview` registra; "Confirmar preview" nunca ejecuta |
| Control Room write-back externo | **DEAD desde el producto** | `/execute` → 410; auto-run → 409; stack de 4,117 líneas solo llamado por tests |
| Supervised Actions | **DEMO-ONLY** | `adapter_name: Literal["sandbox"]`; ejecución simulada por construcción |
| Operational Intelligence | **REAL** | Monte Carlo/calibración/backtests en `routers/intelligence.py` (1,348 líneas) |
| Copilot | **REAL** | Anthropic streaming real, tools read-only, citas; guardrail numérico = prompt+regex (no estructural) |
| Analytics `/analytics` | **REAL** (la superficie más sólida) | ~16 apps HTML sandboxed (iframe credentialless + capability URLs + grants ledger digest-pinned) leyendo Gold vía RLS |
| Marketplace + Cartridges wizard | **REAL** | SQL sobre marketplace_products; instalación estática (compose), no dinámica |
| Agents / Decisions / Data hub / Admin | **REAL** | endpoints verificados server-side, gates de permisos por página |
| Studio | **REAL legacy** | vanilla JS 6,806 líneas, mantenido (11-ago), congelado por regla de usuario |
| Workspace | **PARTIAL** | 3 implementaciones divergentes; la copia vieja expuesta en :8001 público |
| console-next /studio y /workspace | **DEAD** | export los borra / ruta sirve legacy |
| HTML/JS legacy estático | **DEAD** | inalcanzable, solo bloat de imagen |

Frontend: build + `tsc --noEmit` + 388/388 vitest verdes; export commiteado byte-idéntico al build; **cero datos mock en producción**. Sin P0/P1 en este dominio.

## E. DATA REALITY

**Bronze/Silver/Gold son capas funcionales, no nombres.**
- **Bronze:** parquet hive-partitioned `raw/<cartucho>/<Entidad>/tenant_id=/workspace_id=/load_date=/batch_id=` con columnas de provenance (`_extracted_at/_run_id/_source_entity/_watermark_value`); cartuchos nuevos usan BronzeWriter con publish staged, manifiestos sha256 y detección de batch inmutable.
- **Silver:** DuckDB SQL → snapshots parquet inmutables + `silver_lineage` (scope_status, legacy en cuarentena `omega_quarantine`); defs en tabla `datasets` con RLS.
- **Gold:** tablas `gold_*` en postgres_gold con tenant/workspace obligatorios, `_apply_gold_rls`, reemplazo transaccional scoped, snapshot parquet paralelo. RLS nativo FORCE + rol NOBYPASSRLS + deny-by-default.
- **Staged publication CAS** (11 módulos, ~2,000 líneas): TODA materialización pasa por input_digest+contract_digest con replay de head y separación publisher/verifier. Pesado pero cableado end-to-end.
- **Cadena completa demostrada en código:** Replicon (extract DAG → dataset_refresh_chain → materialize topológico fail-closed). Banxico/INEGI/SEC-EDGAR/SF con materializers dedicados.
- **KnowledgeBits existen con ese nombre literal** (YAML SQL por cartucho → tablas `knowledge_bits.*` con head/history, digests, kb_runs). 100% SQL-computados. `calibration_observations` es una abstracción de hecho verdadera (metric/value/interval/probability/evidence_refs/reproducibility_hash/scope) recomputada por funciones SQL SECURITY DEFINER.
- **Discovery/profiling (F7):** profiler por columna vía DuckDB SUMMARIZE auto-ejecutado en cada materialización + descubrimiento de FK candidatos + validación por contención (#594/#596/#597, los 3 commits más recientes). Real, determinístico, sin UI dedicada aún.
- **Debilidades:** migraciones aplicadas **editadas in place** (ver O-5); nombrado caótico de 203 archivos init (prefijos duplicados, sufijos 99zz*); RAG degrada silenciosamente a pseudo-embeddings hash si faltan credenciales de embeddings; KB tables aterrizan en la DB operacional, no en Gold.

## F. INTELLIGENCE REALITY

- **Motores determinísticos REALES y conectados a Gold:** baselines/desviaciones/z-robusto/estacionalidad (`baseline.py`, `time_series.py`), Monte Carlo sembrado con reproducibility_hash, calibración Bayesiana beta-binomial con observaciones evidence-only (rechaza fuentes no autoritativas), backtesting (replay histórico/outcome-linked), horizontes de predicción por contrato. Auto-disparados desde Airflow tras materializar Gold.
- **Separación LLM/numérica ESTRUCTURAL:** `llm_client` solo lo importan copilot/assistant/agents/studio/rag; **cero** imports LLM bajo `services/intelligence/` o `services/control_room/`. En refinement el LLM solo genera SQL que se valida (SELECT/WITH-only) y ejecuta DuckDB/Postgres. Punto débil: en chat el guardrail es prompt + regex de números con hedging que **antepone advertencia, no bloquea**.
- **WisdomBits:** existe exactamente **uno** hardcodeado (WB-TALENTO); el endpoint rechaza cualquier otro id. Hipótesis por plantillas de reglas (sin LLM), opciones con fórmula explícita de score. Capa conceptual: PARTIAL.
- **El loop se rompe en ACTION:** signal→ControlRoom→opción→aprobación funciona; ejecución externa real es inalcanzable desde el producto (410/409/sandbox). Outcomes se registran manualmente/API; hit/miss lo computa Postgres (`record_prediction_outcome`). Calibración/backtesting: módulos reales, acumulación viva de observaciones no probada.
- **Market decision loop:** prueba gobernada recommendation-only (falla 500 si recomienda acción) — honesta, pero su smoke AWS corrió 1 vez y falló (15-jul), nunca re-ejecutado.
- La reconciliación "operational truth" (ago) **eliminó números fabricados** (opciones 92/68/45), hizo fail-closed el FX faltante y renombró métodos mentirosos a `*_heuristic` — señal de honestidad activa del proyecto.

## G. INTEGRATION REALITY (9 cartuchos — no hay más)

| Cartucho | Clasificación | Nota |
|---|---|---|
| sap_successfactors | **REAL-BUT-PARTIAL** (live-blocked externo) | 67 entidades, 108 datasets, OAuth2 + SAML2 bearer, preflight $metadata, circuit breaker. Golden path cableado completo pero **jamás corrido live desde este repo**: bloqueado por grant OData de SAP (#593). Única evidencia live: validación AWS 10-jun con cadena de evidencia incompleta e inconsistente (employee_360 "1362 rows" con silvers fuente en 0). |
| salesforce | **REAL** (live no verificado) | SOQL REST, 3 flujos auth, paginación cursor, retry 429 |
| hubspot | **REAL-BUT-PARTIAL** | cliente CRM v3 real; **cero tests**; incremental solo client-side |
| replicon | **REAL-BUT-PARTIAL + modo DEMO explícito** | cliente extracts real + `seeded_gold` que bypasea APIs; seed de 3,318 líneas generado desde host AWS real (huella de operación live pasada); ingesta SES inbox real |
| sap_hcm / sap_s4hana | **REAL-BUT-PARTIAL** | clientes OData genéricos con CSRF/retry; solo 2 tests c/u |
| banxico / inegi / sec_edgar | **REAL** (los mejor testeados) | clientes públicos con rate limiters, bronze writer, 5-7 tests |

Time Management SF (EmployeeTimeSheet, TimeValuationResult, TimeType, WorkScheduleDay/Model, Holiday/Calendar): **AUSENTE — cero hits en el repo**. Recruiting: JobRequisition/Candidate/JobApplication implementados; **JobOffer ausente**. Contrato de cartucho uniforme y validado (connector.yaml/entities.yaml/seed.sql/scheduling DB-driven), pero instalación **estática** (cada cartucho es un service compose hardcodeado; el import ZIP no levanta nada). SAP-independencia: arquitectura sí, profundidad de producto no (Control Room solo tiene módulos SAP+Replicon). **Testing 100% mock en CI**; el único test live (SF SAML) está env-gated y siempre se salta.

## H. CLOUD REALITY

- **AWS (cuenta 095713296066, EC2 i-07a82861245b34481):** último estado verificable = production sana en v1.45.205-beta el 16-jul. Desde entonces: 0 deploys, monitoreo apagado (14-jun, tras 3 fallos), host-maintenance sin correr. Rol: **CANONICAL-WRITER según última evidencia / declarado DR por docs → UNKNOWN real**.
- **GCP:** Terraform completo (VM única, IAP-only SSH, LB, Secret Manager, GCS) pero **solo existe env `staging`** y la VM "canónica" se llama `omega-staging-app`. Driver de deploy fail-closed genuinamente bueno (backup sha256 verificado pre-mutación, promote atómico, rollback con restore automático de ambas DBs, dry-run). Cutover live 1.45.209-beta **afirmado por mensaje de commit #591** (INFERENCE), sin workflow de deploy (operator-manual). Rol: **declarado CANONICAL / runtime UNKNOWN**.
- **Split-brain:** ningún fencing cross-cloud en código — solo prosa. Mitigantes reales hoy: write-back default-off y deploys dispatch-only (por eso el refutador lo bajó a P2 como riesgo activo), pero la ambigüedad de autoridad + prod sin monitoreo sigue siendo blocker de release (O-4).
- **DNS/edge:** UNKNOWN (sin acceso). Dominio conocido: console.7businesssolutions.com (apuntaba a AWS el 16-jul).
- **Backups/DR:** código completo, **schedule inexistente en ambos clouds**, restore jamás probado con evidencia (`docs/release-evidence/dr-rehearsal-aws` no existe), backups en el MISMO bucket S3 que el lakehouse vivo. "Backup exists" ≠ "restore tested" ≠ "DR works": OMEGA está en el primer estadio, y solo on-demand.
- `generate_seed_from_aws.py` (repo root): utilidad dev que llama al mcp-infra AWS interno **sin header de auth** (red privada; P3).

## I. SECURITY & MULTI-TENANCY

Genuinamente fuerte y por capas (evaluado por resultados, no nombres): JWT HS256 pinned con jti + blacklist Redis fail-closed en prod; bcrypt(12) con pre-hash SHA-256; llaves por par + `x-internal-service`; contextos HMAC con TTL/nonce; membership de workspace verificado server-side (403 si el cliente pide workspace ajeno); GUCs transaction-local + **~97 CREATE POLICY / ~65 FORCE RLS / roles NOBYPASSRLS**; 99p/99s reemplazaron los escapes `USING(true)` en tablas críticas; Gold físicamente separado con deny-by-default; vault_entries con REVOKE total salvo omega_vault; SQL guards multicapa (mcp-infra token regex + sqlglot AST en refinement + validación LLM-SQL); CSRF double-submit; rate limiting Redis fail-closed; CSP estricto con hashes (el `script-src 'unsafe-inline'` del audit de mayo: **remediado**; queda `style-src`).

- **P0: ninguno encontrado.** No se halló ruta cross-tenant reproducible en revisión estática (verificación runtime de RLS: UNKNOWN, sin DB viva).
- **P1: ninguno vigente tras refutación.**
- **P2:** (1) llave Gemini real en historia git (`git show fdf1dd73:infra/.env.save`; repo privado, expuesta ~13h en mayo, sin uso operacional de Gemini — **rotar de todos modos**); (2) tools sin clasificar en `/mcp/invoke` (p.ej. `request_admin_help`) sin gate de permiso, tras llave interna; (3) el scoping de tablas nuevas depende de disciplina de migraciones (primer filtro = WHERE aplicativo).

## J. RELEASE READINESS

Pipeline de tag REAL y ejercitado 255 veces (validate → gate full-stack condicional → 15 imágenes GHCR, matriz exacta a lo esperado). **Pero hoy un release real está bloqueado por:**
1. **Hoyo del gate (confirmado):** el diff-base es "tag anterior" aunque ese tag haya fallado su gate → v1.45.207 falló acceptance, y v1.45.208/209 se saltaron el gate y publicaron 16 imágenes. **El release vigente nunca pasó full-stack acceptance en su SHA.** El "fix" del 208 tampoco fue gate-verificado.
2. Imágenes pinneadas **solo por tag mutable** (sin digest en el path AWS; el day2 GCP sí tiene digest lock), sin firma ni SBOM.
3. Tags v1.45.206/207 existen **sin imágenes** (runs fallidos) — el ledger tag↔imagen miente por omisión.
4. Migraciones: applier AWS sin checksums/lock (columna checksum siempre NULL) vs applier raíz con drift-guard — **dos contratos de seguridad divergentes para el mismo ledger**.
5. Rollback AWS no restaura DB (forward-only): un mal migration deja schema nuevo bajo código viejo.
6. Stress/production-readiness: **todo tag `beta` lo salta** — es decir, nunca ha corrido en CI.
7. GitHub Releases muertos desde junio; el gate de identidad deploy GCP es manual.

## K. TESTING TRUTH

- **14,858 tests pytest colectables** + ~219 Playwright. Núcleo honesto y auto-protegido: en cada push corren ~9,950 tests focales + 296 live-Postgres/RLS (postgres real, migraciones aplicadas 2×) + compose E2E de 18 min con stack real, con **pisos JUnit anti-skip fail-closed** y meta-tests que protegen a los propios workflows. Verificado verde en HEAD vía API. Los motores numéricos (Monte Carlo, calibración) tienen tests determinísticos en el set de cada push.
- **La otra tercera parte está a oscuras:** docker-image.yml (único ejecutor de refinement 461 / vault 46 / workspace 33 / cartuchos 362 / ~1,100 console no-CR / ~3,000 root) deshabilitado desde ~05-jul; e2e.yml desde 26-jul; última corrida 12-jun (v1.45.68, **~140 releases atrás**). El auditor ejecutó ~2,580 de esos tests localmente con 0 fallos — no están podridos, pero **nada en CI detectaría su regresión**.
- E2E de navegador solo vive dentro del gate de release condicional (última ejecución real: v1.45.207, pasó Playwright pero falló acceptance).
- Vendors externos: **exclusivamente mocks in-repo** (sin VCR/cassettes); un único test live (SF SAML) permanentemente skipped.
- Bandit solo falla en MEDIUM+ con confianza HIGH (165 hallazgos MEDIUM aceptados silenciosamente). Cobertura: nunca publicada.
- Branch protection: UNKNOWN (sin scope admin).

## L. 17-PHASE INDEPENDENT RECONSTRUCTION

| Fase | Status | Evidencia / Faltante |
|---|---|---|
| F1–F4 reconciliación | **CLOSED** | #555/#557 (1–5 ago) con items literalmente etiquetados "F1…"; docs/audits/operational-truth-data-integrity.md |
| F5 baseline canónico | **CLOSED** | #560 (05-ago) "so phase 6 starts from a known point" |
| F6A SF EC golden path | **PARTIAL** | Código completo (anomaly dataset + preflight #593); **bloqueado externo**: grant OData SAP. Falta: una sola corrida live |
| F6B Time Management | **NOT-STARTED** | cero implementación de las 7 entidades |
| F6C Recruiting | **PARTIAL** | JobRequisition/Application sí; JobOffer ausente; sin trabajo phase6 |
| F7 discovery/profiling | **PARTIAL (en ejecución en HEAD)** | #594/#596/#597 (13-ago): profiler+FK+validación reales; falta UI/product wiring |
| F8 contracts/governance | **NOT-STARTED** | `contracts/` = 1 fixture UI; sin inferencia de contratos |
| F9 KnowledgeBits | **PARTIAL** | existen y funcionan desde junio; falta pasada de gobernanza post-F8 |
| F10 engines | **PARTIAL** | motores reales desde julio; su conexión al golden path es "Phase 10/11" según commit b48808fd |
| F11 WisdomBits | **PARTIAL** | 1 solo WB (WB-TALENTO) con validación de ancestría fail-closed |
| F12 productos analíticos | **PARTIAL** | workforce/talent/9-box/P&L existen y se sirven; sin revalidación post-baseline |
| F13 population scoring | **NOT-STARTED** | cero código |
| F14 BigQuery/pushdown | **NOT-STARTED** | cero hits "pushdown"; 1 comentario "bigquery"; solo lakehouse storage-neutral S3/GCS |
| F15 outcomes/calibración | **PARTIAL** | módulos reales (outcome_writer/backtesting/calibración SQL); acumulación live no probada |
| F16 private beta reproducible | **PARTIAL (avanzado)** | Driver GCP fail-closed real con backup/restore-en-fallo; PERO: sin backups programados (P1), sin evidencia de restore rehearsal, gate de release con hoyo, env solo-staging. El auditor lo marcó SUBSTANTIALLY-SATISFIED; los P1 confirmados de B/J lo bajan a PARTIAL |
| F17 production hardening | **NOT-STARTED** | sin artefactos de fase; hardening ad-hoc previo no cuenta |

```
CURRENT EFFECTIVE PHASE: F6/F7 simultáneas
  - F6A código-terminada pero rehén externo (SAP grant)
  - F7 activa en HEAD (los 3 últimos commits)
  - con F16 adelantada fuera de orden (driver GCP) y agujeros F16 abiertos (backups/restore/gate)
```

## M. ZIP W01–W13

**El bundle NO existe en este entorno** (búsqueda exhaustiva de .zip; SHA `6102af1a…` no verificable) ni en el repo. Clasificación basada en qué resuelve ya main (verificado por grep/inspección) — disposición final requiere el bundle:

| W | Contra main | Disposición |
|---|---|---|
| W01 Terraform | infra/terraform (AWS) + terraform-gcp existen | **ALREADY-SATISFIED** (base); deltas a F17 |
| W02 SES IaC | SES en terraform AWS (variables/iam/outputs) + DAG `replicon_ses_inbox_import` | **ALREADY-SATISFIED** |
| W03 SF Time Mgmt | **cero implementación en main** | **ADAPTED** → es EL candidato para F6B |
| W04 Workforce analytics | workforce_overview app + workforce trends SQL existen | **ALREADY-SATISFIED** (comparar deltas) |
| W05 P&L Replicon | pnl_revenue_manager + consultor_horas_costos + 3 apps más | **ALREADY-SATISFIED** |
| W06 Finance fixture | simulation_inputs/market-decision existen parcialmente | **ADAPTED** (solo si aporta fixtures que main no tiene) |
| W07 Manual capture | cero hits en main | **ADAPTED** → F8/F16 |
| W08 SES multicartucho | solo Replicon tiene SES import | **ADAPTED** → generalizar en F16 |
| W09 CFDI/RFC | sin parsing CFDI (solo columnas RFC en seeds) | **ADAPTED** → post-golden-path (F16–17), como el propio mapa dice |
| W10 Governance | F8 NOT-STARTED | **ADAPTED** → insumo principal de F8 |
| W11 Indicator builder | no existe builder | **ADAPTED** → F9–10 |
| W12 Gemini provider | main **deshabilitó Gemini deliberadamente** (`llm_client.py:53-56` coerciona configs stale) y ya es provider-agnostic (Anthropic/ollama) | **REJECTED-WITH-REASON**: decisión ya tomada en main; además historia de llave filtrada |
| W13 Lineage AST | sqlglot ya usado en 5 módulos refinement + FK discovery #596 | **ALREADY-SATISFIED parcial**; deltas AST → F7–8 |

## N. OVERENGINEERING REPORT

- **SIMPLIFY:** export estático Next commiteado en git (462 archivos, 5.2MB, el churn #1 del repo — construir en CI/deploy); dos paths de release GCP paralelos (canonical-deploy vs day2-release — quedarse con uno, el que tiene digest lock); dos appliers de migraciones con contratos distintos (unificar en el guarded); triple Workspace UI (consolidar en ChatLayout, dejar de servir :8001 root); dos stacks de storage (migrar cartuchos legacy a omega_lakehouse); scaffolding duplicado ×9 cartuchos (deuda aceptada por contrato de cartucho — solo alinear); ~1,000 líneas de fallback SQL SF en el core compartido (registry por cartucho); dual router `/api/intelligence` + `/api/v1/...`; detector de cambios `ci_changed_areas.py` (origen del hoyo del gate); 248 tags/130 bumps en 3.5 meses (ruido); monolito main.py 7,847 líneas (continuar extracción, **no** rewrite — es la superficie más testeada).
- **KEEP (feo pero correcto):** staged publication CAS; matriz de llaves por par + HMAC; Studio legacy (única Studio funcional); 150 micro-módulos control_room (cohesivos, testeados, façade estable); meta-tests CI auto-protectores; capability machinery de Analytics embed; logging_config copiado byte-idéntico con test que lo pinnea.
- **DEFER:** stack write-back de 4,117 líneas sin caller (motor pre-construido de la fase de ejecución — cablear o declarar preview-only, no borrar); ~30 scripts `aws_*probe.py` (hasta confirmar cutover GCP); tests/stress locust (o se agenda o se deja de contar como readiness).
- **DELETE:** HTML/JS legacy muerto (~15 páginas); `market-decision-aws-smoke.yml` (1 run, fallido, abandonado — da aseguramiento negativo); AUDIT_BASELINE_NO_STUDIO.md + audit_baseline_findings.json del root (snapshot stale de otra branch, ya remediado — archivar en docs/audits/).

## O. TOP BLOCKERS

**P0: ninguno.** (Ningún caso confirmado de corrupción activa, bypass de tenant/auth, o destrucción irreversible.)

**P1 (7, todos confirmados por refutación adversarial):**
1. **CI oscuro:** docker-image.yml + e2e.yml `disabled_manually` → ~4,800 tests (33%) sin ejecutar en CI desde 12-jun a través de ~140 releases. Ninguna regresión en refinement/vault/workspace/cartuchos/console-no-CR sería detectada.
2. **Hoyo del release gate:** base-ref = tag anterior aunque su gate haya fallado → v1.45.208/209 publicadas sin acceptance full-stack en su SHA; evasión estructural repetible.
3. **RPO ilimitado:** cero backups programados en workflows/terraform/compose de ambos clouds; restore jamás probado con evidencia.
4. **Autoridad cloud irresuelta + producción ciega:** runtime real UNKNOWN en ambos clouds, docs contradicen workflows (`deploy-aws` activo `environment: production` vs "AWS es DR"), monitoreo apagado 2 meses, último deploy verificado hace 1 mes y 61 commits. (El fencing split-brain per se: P2 por mitigantes; la ambigüedad+ceguera: blocker.)
5. **Migraciones aplicadas editadas in place** con cambios de lógica de negocio (ej. FX MXN /20.0 → NULL fail-closed): DBs inicializadas antes/después divergen para el mismo ledger; checksums retrofitteados el 12-ago no cubren lo previo, y el path AWS nunca escribe checksums.
6. **Golden path F6A rehén externo:** grant OData de SAP (User/EmpEmployment/EmpJob) sin otorgar; ninguna fila SF real puede fluir hoy. El preflight (#593) diagnostica, no desbloquea.
7. **Loop decide→act roto en el producto:** ejecución externa inalcanzable (410/409/sandbox-only); OMEGA hoy decide y recomienda pero no actúa, y el roadmap la describe actuando.

## P. DEFERRED DEBT

- **NEXT PHASE (no bloquea hoy):** consolidar E2E harnesses (5 entradas solapadas); dual routers intelligence; triple Workspace; UI para profiling F7.
- **PHASE 16:** unificar appliers de migraciones + normalizar nombrado init; digest pinning + firma de imágenes en todos los paths; restore/DR rehearsal con evidencia; branch protection verificable; cobertura publicada; separar bucket de backups del lakehouse; GitHub Releases o retirar la práctica.
- **PHASE 17:** OTel/traces (hoy cero), Prometheus real, SLOs/alertas con pager, readyz en workspace/vault, escalado más allá de 1 VM, stress real (locust nunca ha corrido), IAM least-privilege cloud, retirar terraform AWS tras cutover probado.
- **NEVER WORTH FIXING:** logging_config triplicado (test lo pinnea); import fallbacks try/except de refinement; micro-modularización de control_room; scripts probe como evidencia histórica.

## Q. RECOMMENDED NEXT EXECUTION SEQUENCE (máx. 10)

1. **Resolver autoridad cloud con evidencia runtime** (health de ambos stacks + DNS): declarar canónico, poner el otro en standby REAL (apagar scheduler/writers o el host), reconciliar `deploy-aws.yml environment: production` con el rol declarado.
2. **Re-encender el CI oscuro:** rehabilitar docker-image.yml y e2e.yml (o portar sus suites al workflow por-push). Nada de features nuevas sobre CI a oscuras.
3. **Cerrar el hoyo del gate:** base de diff = último tag **gate-verde**; re-taggear v1.45.210 con gate full-stack completo en su SHA y usar solo esas imágenes.
4. **Backups programados + un restore probado con evidencia commiteada** en el cloud canónico (define RPO/RTO; bucket separado).
5. **Rehabilitar monitoreo** (monitor-aws-health o equivalente GCP apuntando al canónico, con el fix de por qué fallaba en junio).
6. **Congelar migraciones aplicadas** (política + test que falle si un archivo aplicado cambia) y unificar el applier con checksums en todos los paths; reconciliar el ledger AWS NULL-checksum.
7. **Golden path demostrable sin SAP:** ejecutar Replicon (o Banxico) Source→Bronze→Silver→Gold→signal→ControlRoom **live** end-to-end y commitear la evidencia; en paralelo, entregar el checklist de preflight al admin SAP para destrabar F6A.
8. **Decidir write-back V1:** o se expone `execute` supervisado tras flag+allowlist (el stack de 4,117 líneas ya está testeado), o se declara oficialmente "preview-only" en producto y roadmap. Cerrar la ambigüedad.
9. **Rotar la llave Gemini** de la historia git y cerrar los P2 de seguridad baratos (tools sin clasificar en /mcp/invoke).
10. **Solo entonces** retomar F7→F8 (governance) sobre base verde y monitoreada.

## R. FINAL BRUTAL ASSESSMENT

- **¿OMEGA funciona?** El software sí — en CI levanta 25 contenedores, materializa Bronze→Silver→Gold real, computa señales determinísticas y las muestra en un frontend sin mocks. **Como operación, no se puede afirmar**: nadie sabe con evidencia qué corre hoy en producción.
- **¿Es demo?** Es más que demo: hay RLS real, CAS de publicación, calibración SQL, seguridad por capas. Pero su única evidencia live-SaaS es una validación SF de junio con la cadena de evidencia rota, y su smoke de decisión de mercado falló su única corrida.
- **¿Es private-beta?** Casi en código, no en operación. Le faltan exactamente los ítems de Q1–Q6 (autoridad cloud, CI completo, gate honesto, backups+restore, monitoreo, migraciones congeladas).
- **¿Production-ready?** No. F17 sin empezar; observabilidad a media madurez; 1 VM; stress jamás ejecutado.
- **% conceptual terminado:** ~55–60% del producto final descrito (data plane ~80%, intelligence ~65%, action loop ~30%, governance/discovery ~25%, ops/DR ~35%).
- **¿Ingeniería real vs hardening restante?** ~40% ingeniería real (F6B/6C, F8, F11 real, F13/F14, write-back) / ~60% hardening-operación (F16/F17, CI, DR, observabilidad).
- **Cuello de botella auténtico:** **operación y verificación, no construcción**. El proyecto produce features (F7 aterrizó en un día) mucho más rápido de lo que prueba, despliega, respalda y observa. Segundo cuello: el grant SAP externo, que no depende de código.
- **¿Sobreconstruyendo?** Sí, en los bordes: stack de ejecución de 4,117 líneas sin caller, dos paths de release GCP, tres Workspaces, 248 tags en 14 semanas, staged publication CAS de peso industrial para una beta de un tenant. Nada de eso es el problema principal, pero consume atención.
- **¿Dónde se pierde tiempo?** En re-auditar y re-versionar en lugar de operar: 130 bumps de VERSION, tags sin imágenes, releases sin gate, evidencias que citan logs que no existen. Y en construir F7/F16 encima de un CI apagado.
- **¿Qué cortar sin piedad?** HTML/JS legacy muerto, market-decision-smoke roto, un path de release GCP, dos de los tres Workspaces, el export Next commiteado, los audit files stale del root.
- **¿Qué NO tocar porque ya funciona?** El núcleo refinement/DuckDB + staged publication; el RLS dual-DB; la matriz de auth inter-servicio; los motores determinísticos con sus tests; el pipeline focal por-push con pisos anti-skip; los clientes de cartucho; el frontend console-next; Studio legacy hasta que exista su reemplazo.

---
*Auditoría generada sin mutación: cero commits de código, cero cambios cloud, cero ejecución de migraciones. Este documento es el único artefacto añadido.*
