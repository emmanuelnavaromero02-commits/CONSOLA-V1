# OMEGA — PLAN DE CIERRE RECONCILIADO (2026-08-14)

Reconciliación de las 4 auditorías independientes (VS-Claude 82%, ChatGPT 7.4, Claude Code 5.5, Codex 4.8) + verificación directa de los hallazgos disputados contra `main` @ `48d29731`.

## 1. Por qué difieren las 4 notas — y cuál es la verdad

Las cuatro auditorías **coinciden en el núcleo**: cero P0, plataforma real (no maqueta), no reescribir el core, fase efectiva 6/7, SF bloqueado externo, hoyo del release gate (.207 falló → .208/.209 sin gate), backups/DR sin probar, y "el siguiente tramo es cerrar, no construir". Difieren en la nota porque midieron cosas distintas:

- **VS-Claude (82%)**: midió *cuánto está construido*. Optimista: contó como terminado lo construido-pero-no-probado y no ponderó release/operación.
- **ChatGPT (7.4)**: sin acceso runtime; confió más en evidencia versionada; no vio la invalidación del profiler ni el LIMIT 5000.
- **Codex (4.8)**: el de mayor acceso (Mac + clouds vivos + ZIP por SHA). Sus hallazgos de runtime **se verificaron ciertos**. Su nota castiga producto/operación, no el código central.
- **Claude Code (5.5)**: repo+CI exhaustivo, sin runtime. Correcto en CI/release/backups; **falló en marcar F7 como "REAL" sin detectar que el runtime staged descarta las stats** (corregido en este documento).

**Verdad reconciliada: el núcleo (data plane, RLS, motores, seguridad) está a nivel 7–8. La operación y el cierre de producto están a nivel 3–4. Nota honesta global: ~5. Nada está podrido; la lista de defectos es finita y está abajo, verificada.**

**Runtime real (evidencia Codex, resuelve los UNKNOWN):** GCP = canonical writer vivo en 1.45.209 (DNS Squarespace → LB GCP); AWS .205 vivo, alcanzable, scheduler detenido, **sin fence**; AWS viejo en 502.

## 2. Defectos VERIFICADOS contra el código (lista única de trabajo)

| # | Defecto | Evidencia | Sev |
|---|---|---|---|
| V1 | `gold_fetcher` hace `SELECT * … LIMIT 5000` sin ORDER BY ni flag de truncación; los motores tratan el subconjunto como población | `console/app/services/intelligence/gold_fetcher.py:111,128,194` | P1 |
| V2 | `kb_talent_pipeline` = CROSS JOIN requisiciones × candidatos con `'Active'` fijo → N×M hechos falsos | `cartridges/sap_successfactors/app/config/knowledge_bits.yaml:75-96` | P1 |
| V3 | Profiler F7 invalidado en runtime: el path staged (`PublicationFinalizeMixin._update_catalog`) no persiste `null_rate/distinct/min/max`; discovery filtra `distinct_count IS NOT NULL` → vacío | `refinement/app/publication_finalize.py:14-40`, `duckdb_engine.py:1909`, `main.py:2675` | P1 |
| V4 | Watermarks scoped por **env del proceso** (`OMEGA_TENANT_ID` de `os.environ`), no por contexto de la corrida → multi-tenant se pisan watermarks (patrón repetido ~5 cartuchos) | `cartridges/hubspot/app/services/watermark_service.py:_scope_ids()` (+ copias) | P1 |
| V5 | Replicon: cadena pasa `security_context` dict a `_context_cache_key` que hace `.strip()` de string → AttributeError | `cartridges/replicon/app/core/vault_client.py:68-73`, `app/api/routes_skills.py:29` | P1 |
| V6 | `main` está 7 commits delante del tag `v1.45.209-beta` con la MISMA VERSION → dos árboles distintos se identifican igual | `git rev-list --count v1.45.209-beta..main` = 7 | P1 |
| V7 | Release gate: base-ref = tag anterior aunque su gate haya fallado; .208/.209 publicaron 15 imágenes sin acceptance en su SHA | `release.yml:26-37`; runs 31649302923/31652557379/31655249215 | P1 |
| V8 | CI oscuro: docker-image.yml + e2e.yml `disabled_manually`; ~33% del suite sin CI desde 12-jun (~140 releases) | GitHub API workflows state | P1 |
| V9 | Cero backups programados en ambos clouds; restore jamás ensayado con evidencia; RPO ilimitado | workflows/terraform/compose: sin cron de backup | P1 |
| V10 | Migraciones aplicadas editadas in place (incl. lógica FX); applier AWS sin checksums; divergencia fresh-vs-upgraded | git log sobre infra/init/*, `infra/terraform/deploy/apply_db_migrations.sh` | P1 |
| V11 | AWS .205 vivo y sin fence mientras GCP es writer (split-brain posible, no observado) | Codex runtime + ausencia de fencing en código | P1 |
| V12 | Golden path SF bloqueado por grant OData externo (User/EmpEmployment/EmpJob) | commit #593 | P1 externo |

**Reportados por Codex/VS-Claude, pendientes de re-verificar (hacerlo en A6, son minutos c/u):** pérdida de scope de credencial en hubspot/salesforce/hcm/s4; tenant/workspace del body sin bind al contexto firmado en banxico/inegi/sec; 12/15 paquetes GHCR públicos; botón "Preparar plan" de OI → 403; `delete dataset` roto; fallback SF que enmascara error de red como vacío-válido; migración de Vault que puede pisar secreto de otro tenant.

## 3. EL PLAN (≈15 días hábiles a beta defendible; los fixes de código: horas cada uno)

### BLOQUE 0 — HOY (2–3 h, decisiones, no código)
1. **Congelar features.** Nada nuevo hasta terminar este plan. Este documento es el backlog único; no más auditorías.
2. **Entregar HOY el checklist de preflight (#593) al admin SAP** — es el lead time más largo y no depende de ti.
3. Declarar por escrito: **GCP = único writer**; AWS .205 = frío (tras backup final); AWS viejo (502) = terminar.
4. Regla de versión: el próximo commit de fixes bumpea a **1.45.210-beta**. El tag 209 no se mueve jamás.

### BLOQUE A — Verdad numérica y defectos confirmados (días 1–3)
- **A1** `gold_fetcher`: ORDER BY determinístico + paginación o `truncated=true` en la evidencia del run. *(horas)*
- **A2** `kb_talent_pipeline`: JOIN real vía `JobApplication` (candidateId↔jobReqId); si el tenant no la expone → abstain/`partial`, nunca cartesiano. + **test semántico que ejecute la SQL real** con fixture. *(horas)*
- **A3** Profiler F7: persistir `column_stats` en el path staged (llamar `_profile_columns` desde el finalize o pasarlas por el evidence store). + test que use `StagedPublicationEngine`, no el engine base (el test actual es verde mientras producción pierde stats). *(≤1 día)*
- **A4** Watermarks: scope desde el security context de la corrida, no de `os.environ` — **un patrón, aplicado a los 5 cartuchos** + test multi-tenant (tenant B no avanza watermark de A). *(1 día)*
- **A5** Replicon: normalizar `security_context` (dict vs string) en la cadena vault_client/extraction + test de repro. *(horas)*
- **A6** Re-verificar y cerrar los reportados pendientes (lista arriba): cada uno es verificación de 5-10 min + fix chico. *(1 día)*

### BLOQUE B — CI y release honesto (días 3–6)
- **B1** Reactivar `docker-image.yml` y `e2e.yml` (o portar sus suites al workflow por-push). Cero features sobre CI apagado.
- **B2** Gate: base de diff = **último tag gate-verde**; full-stack obligatorio para todo tag que se vaya a desplegar.
- **B3** Tag **v1.45.210-beta** con gate completo en su SHA → deploy GCP por digest con el driver fail-closed (ya existe y funciona).
- **B4** GHCR: privatizar los paquetes públicos; ruleset para TODOS los tags de release (hoy solo protege .207). Firma/SBOM → F17.
- **B5** Congelar migraciones aplicadas (test CI que falla si cambia un archivo ya aplicado) + unificar applier (el guarded con checksums en todos los paths).

### BLOQUE C — Operación mínima creíble (días 6–10)
- **C1** Backup programado **off-host** en GCP + **1 restore rehearsal con evidencia commiteada**; definir RPO/RTO.
- **C2** Monitor de salud reactivado apuntando a GCP (arreglar por qué fallaba en junio) + ruta de alerta.
- **C3** AWS: backup final verificado → apagar stack .205 → terminar la infra en 502. Fence resuelto por apagado, no por prosa.

### BLOQUE D — Golden path y cierre F6/F7 (días 10–15)
- **D1** Golden path live demostrado con lo que NO depende de terceros (**Banxico o Replicon**): fuente→Bronze→Silver→Gold→signal→Control Room con evidence pack commiteado.
- **D2** SF live acceptance en cuanto llegue el grant (el preflight ya te dice qué falta); arreglar `extract_all` MCP para que refresque Gold como REST/Airflow.
- **D3** Con A3 cerrado, discovery/F7 queda vivo de verdad; delta de AST lineage solo si sobra tiempo.
- **D4** Write-back: **declarar oficialmente "preview-only" para la beta** (honesto, rápido, el stack de ejecución queda DEFER, no se borra). Cablear ejecución real es fase posterior, no blocker de beta.

### BLOQUE E — Declarar private beta (día ~15)
- **E1** Corrida completa `clone → configure → bootstrap → migrate → deploy → backup → restore` documentada de una sentada.
- **E2** Runbook único de beta + comunicado. Solo entonces se declara.

### Qué NO hacer (con la misma disciplina que lo anterior)
Más auditorías (las 4 ya convergen — este documento es el cierre) · BigQuery · cartuchos nuevos · Gemini · superficies nuevas · rewrites del monolito · otro framework de release · más tests de existencia (los que faltan son de **verdad empresarial**, como el de A2).

## 4. Respuesta directa a "¿urge sacarla en estas horas o me corren?"

- **En horas** se puede: Bloque 0 completo + A1, A2, A5 y V6 (bump). Eso ya elimina los datos falsos más vergonzosos y la ambigüedad de versión — es lo que enseñaría hoy.
- **En ~1 semana**: Bloques A+B completos → release limpio v1.45.210 con gate real, CI encendido, desplegado en GCP.
- **En ~2–3 semanas**: beta privada **defendible** (con backup/restore probado, monitoreo, golden path live demostrado).
- Nadie de los 4 auditores encontró P0, podredumbre ni motivo de reescritura. El 4.8 de Codex mide operación/cierre, no tu ingeniería central — y su propia recomendación es la misma que esta: cerrar, no reconstruir. **El proyecto no está en riesgo técnico; está en riesgo de seguir construyendo en vez de cerrar.**
