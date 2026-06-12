# Database / RLS Audit — OMEGA CONSOLA-BETA (HEAD bab2a4f, v1.45.68-beta)

Auditor: Database/RLS agent. Evidencia = file:line. Read-only salvo este reporte.

## 0. Verificación de claims

| Claim | Veredicto | Evidencia |
|---|---|---|
| Postgres :15432 `modecissions`, ~80 tablas | CONFIRMADO — 78 `CREATE TABLE` distintos en infra/init (82 matches de grep − 4 artefactos de comentarios "if/so/block/uses" en 19_operational_stability_hotfix.sql:4-6, 45_cascade_to_restrict.sql:16, 67_data_catalog_upgrade_shape.sql:3) | infra/init/*.sql |
| 14 service roles | CONFIRMADO — 14 roles LOGIN en main DB + `omega_refinement_gold` (gold) + `omega_cartridge_jobs_owner` NOLOGIN = 16 roles totales | ver matriz §1 |
| pgvector | CONFIRMADO — `CREATE EXTENSION vector` (03_pgvector_rag.sql:6); estado final `vector(1024)` (71_rag_embedding_dim_1024.sql:19-21) | §6 |
| Gold :15433 `modecissions_gold`, native RLS, NOBYPASSRLS, FORCE, deny-without-scope | CONFIRMADO | docker-compose.yml:65; init_gold/35_gold_native_rls.sql:7,58-59,70-78 |
| Mínimo privilegio one-role-per-service | PARCIAL — main DB sí; gold DB comparte `omega_refinement_gold` entre 3 servicios (P2-1) | compose:163-164,193,334 |
| RLS a 3 niveles | CONFIRMADO — (1) guard AST sqlglot en refinement (duckdb_engine.py:988-1004, default-deny en parse error), (2) gold native (init_gold/35), (3) operational native (99d/99e/99f) | §2 |
| Operational deny-sin-scope | PARCIAL — sí para roles tenant-facing; NO para omega_console/omega_refinement que tienen política owner `USING(true)` (P1-2) | 99e:134-146, 99f:106-118 |

## 1. Matriz de roles

Ningún rol tiene `BYPASSRLS` ni `SUPERUSER` (grep exhaustivo: solo aparecen `NOBYPASSRLS`). El superuser `postgres` se usa solo en bootstrap (initdb, airflow-init compose:1005, superset-init compose:850, postgres_dev_seed dev-only compose:40-50).

| Rol | Creación | LOGIN | NOBYPASSRLS explícito | Grants | Usado por (compose) |
|---|---|---|---|---|---|
| omega_console | 25_service_roles.sql:35 | sí | no (default PG) | AMPLIO: DML en ALL TABLES (25:41) + DEFAULT PRIVILEGES futuras (25:222-225); REVOKE vault_entries (25:44); audit_deletes append-only (45:57) | console (163) |
| omega_refinement | 25:57 | sí | no (default) | SELECT 17 tablas (25:67-72), INSERT/UPDATE 7 (25:76-78); REVOKE vault_entries (25:89) | refinement (333) |
| omega_vault | 25:102 | sí | sí (99e:339) | SOLO vault_entries DML (25:108) + vault_access_log SELECT/INSERT (32:21) | vault (379) |
| omega_workspace | 25:122 | sí | sí (99e:336, 99f:255) | SELECT 13 tablas, INSERT/UPDATE 4 (25:135-142) + user_workspace_roles (72:13); REVOKE vault (25:143) | workspace (276) |
| omega_mcp_infra | 25:156 | sí | sí (99e:333, 99f:252) | SELECT 18 tablas, DML 8 (25:192-206); lockdown identity 35_omega_mcp_infra_lockdown.sql:32-45; +columnas users(email,role,is_active,escalation_notify) (63_escalation_notify.sql:11-18) | mcp-infra (790) |
| omega_cartridge_sap_hcm/s4/sf | 36:65,77,89 | sí | no (default) | catálogo operacional 8 tablas (36:169-180); hard-lock REVOKE 14 tablas identity/vault (36:191-234) | sap_* (677/724/621) |
| omega_airflow_dag | 36:101 | sí | sí (99f:258) | SELECT/INSERT/UPDATE 10 tablas (36:246-252); hard-lock (36:200-205) | DAGs (1069-1070) |
| omega_airflow_meta | 36:113 | sí | no | GRANT ALL en DB `airflow` (post-migrate, compose:985) | airflow web/sched (1052,1167) |
| omega_superset_meta | 36:125 | sí | no | GRANT ALL en DB `superset` (compose:916) | superset (935) |
| omega_cartridge_replicon | 37:19 | sí | no | catálogo (37:30-39) + ownership tablas operacionales (37:74+); hard-lock (37:44-72) | replicon (469) |
| omega_cartridge_hubspot | 93_hubspot:16 | sí | no | catálogo + `CREATE ON SCHEMA public` (93_hubspot:22); jobs_owner membership; hard-lock | hubspot (569) |
| omega_cartridge_salesforce | 93_salesforce:25-28 | LOGIN o NOLOGIN si falta password (resiliente) | no | catálogo + CREATE schema + jobs_owner; hard-lock | salesforce (524) |
| omega_cartridge_jobs_owner | 46_sap_jobs_permissions.sql:79 | NO (NOLOGIN, sin password) | n/a | dueño de `jobs` (46:108) — rol grupo | — |
| omega_refinement_gold (gold DB) | init_gold/34:16 | sí | sí (init_gold/35:7) | AMPLIO en gold: CREATE schema public + DML ALL TABLES public+replicon + DEFAULT PRIVILEGES (init_gold/34:21-31) | console GOLD_DATABASE_URL (164), refinement (334), superset gold (193) |

Todos los roles reciben `GRANT USAGE, SELECT ON ALL SEQUENCES` (25:42,79,109,142,207; 36:182,252; 37:39) — ver P3-3. Passwords: todos los CREATE ROLE abortan si el GUC de password está vacío (p.ej. 25:31-33); salesforce degrada a NOLOGIN (93_salesforce:24-27).

## 2. Cobertura RLS (main DB)

Función de scope: `omega_rls_workspace_matches` (99e:8-24, redefinida 99f:20-36) — fail-closed: exige `current_setting('app.workspace_id')` no vacío y match exacto; tenant si la fila lo tiene. `omega_rls_tenant_matches` (99f:9-18).

| Grupo | ENABLE | FORCE | Política scoped (roles tenant-facing) | Política owner USING(true) | Gaps |
|---|---|---|---|---|---|
| Intelligence ×9 (metric_baselines, intelligence_signals, evidence_packs/items, hypotheses, decision_options, prediction_outcomes, external_intelligence_sources, external_evidence_cache) | sí (99d:62) | sí (99d:63) | sin TO ⇒ aplica a TODOS los roles, deny sin GUC (99d:65-83) | NO — console también scoped | ninguno |
| Operacional ×17 (datasets, decisions, control_room_* ×5, token_usage, vault_entries, vault_access_log, agents, agent_runs, conversations, user_cartridge_overrides, marketplace_orders, tenant_entitlements, cartridge_installations) | sí (99e:115) | sí (99e:116) | TO 10 roles servicio (99e:56-67,119-132) | sí: omega_console+omega_refinement (99e:134-146) | owner bypass-by-policy |
| Hijas ×3 (decision_actions, conversation_messages, cartridge_installation_events) | sí (99e:166,207,248) | sí | EXISTS contra padre (99e:170-191,211-232,252-273) | sí (console) | — |
| 99f ×3 (pipeline_runs, copilot_goals, copilot_lessons) | sí (99f:74) | sí (99f:75) | TO workspace/mcp_infra/airflow_dag (99f:51-58) | sí console+refinement (99f:106-118) | — |
| Identity (users, workspaces, user_workspace_roles) | sí (99f:150,179,208) | sí | tenant-scoped TO mcp_infra (99f:127-130) | sí console+refinement+**workspace** (99f:132-139, excepción bootstrap auth documentada 99f:141-147) | workspace ve todos los users/workspaces (mitigado en app) |
| Vault (vault_entries, vault_access_log) | sí (99g:48,71) | sí (99g:49,72) | TO omega_vault: scoped (99g:52-66) + global restringido a allowlist plataforma (99i:45-69); legacy clasificado en vault_legacy_unscoped_entries (99i:15-37) | NO para console (sin política ⇒ FORCE deniega) | — |
| pipeline_runs plataforma | — | — | filas `cartridge_id='platform'` globales para mcp_infra/airflow_dag (99h:20-35) | — | — |
| agents templates | — | — | SELECT global workspace_id IS NULL para workspace/mcp_infra (99e:299-307) | — | — |

**Tablas con datos de tenant SIN RLS** (P1/P2): rag_sources + rag_chunks (03:8-31 — sin tenant/workspace; kind='document' = uploads de usuario, 14_rag_sources_kind.sql:1-8), audit_events (16:4-17 — PII email/ip, sin tenant col; 39/92 solo añaden tool/request_id), analytic_apps (08:6-27 — solo created_by_id+visibility), user_sessions/user_tokens/refresh_tokens, copilot_drafts (52), workflow_runs/steps (53), user_facts/user_preferences/conversation_memory_summary (51), copilot_briefing_dismissed (50), studio_entities (58), studio_goal_runs/steps (95), login_attempts (17), vault_legacy_unscoped_entries (99i:5), data_catalog/semantic_terms/entity_config/jobs/kb_runs (catálogo compartido por diseño).

## 3. Gold RLS (init_gold)

- `ALTER ROLE omega_refinement_gold NOBYPASSRLS` — init_gold/35:7. CONFIRMADO.
- `omega_apply_gold_rls_for_table`: valida nombre `^gold_[A-Za-z0-9_]+$` (35:32), ENABLE+FORCE (35:58-59), política tenant+workspace o `USING(false)` deny total si faltan columnas (35:70-78). Bucle inicial sobre todos los `gold_*` existentes (35:81-93).
- `omega_gold_workspace_matches` exige AMBOS GUCs no vacíos (35:16-21) — deny-without-scope CONFIRMADO.
- Runtime: refinement llama la función al materializar (refinement/app/duckdb_engine.py:1174) y recrea tablas legacy sin scope como vacías scoped (1191-1199); escrituras vía psycopg2 con set_config transaccional (1226-1227) porque COPY+RLS es incompatible (comentario 1213).
- Gold init no crea tablas (init_gold/00_schema.sql — solo `CREATE SCHEMA replicon`); ventana mínima entre CREATE y apply_rls de una tabla vacía (1187-1189).
- BYPASSRLS: NADIE. Superuser solo `postgres` del contenedor.
- Gap: la conexión DuckDB ATTACH pasa GUCs como connection options (duckdb_engine.py:242) — correcto; pero `omega_refinement_gold` con `GRANT EXECUTE omega_apply_gold_rls_for_table` (35:96) puede re-aplicar (no quitar) políticas — aceptable.

## 4. Cómo la app fija el contexto RLS

- workspace: `scoped_pg`/`_set_db_scope` por transacción (workspace/app/main.py:383-407), session.py:62, token_store.py:35. Si falta tenant/workspace NO setea ⇒ las políticas nativas devuelven 0 filas (fail-closed en DB), salvo tablas identity donde workspace es owner (excepción bootstrap 99f:141-147; scoping en código).
- console: owner en operacionales (no necesita GUC); para intelligence (políticas sin TO) sí setea: persistence.py:46, gold_fetcher.py:105, readiness.py:193,255; para gold readiness: main.py:3627 con abort `workspace_scope_missing` (main.py:3613-3614).
- vault: exige contexto firmado o 403 (vault/app/main.py:180-194) y setea por cursor (main.py:199-204); escrituras unscoped solo allowlist global (main.py:206-230).
- mcp-infra: setea al verificar scope de pipeline runs (mcp-infra/app/main.py:718-720), 403 sin tenant/workspace (703-704). Tool `postgres_execute_query` solo SELECT/WITH + anti multi-statement (tools/postgres.py:211-216).
- refinement: AST rewrite sqlglot default-deny (duckdb_engine.py:988-1004; tablas sin workspace_id ⇒ `1=0`, 964-985) + GUCs en ATTACH (242).

## 5. Aislamiento Vault

- Fernet BYTEA: `value_encrypted BYTEA` (24:11-12), legacy `value` nullable (24:15-16); escrituras siempre cifradas (vault/app/main.py:233-236 comentario).
- Un solo rol: GRANT solo a omega_vault (25:108); REVOKE explícito a console/refinement/workspace/mcp_infra (25:44,89,143,215); hard-lock en todos los cartridges (36:191-234, 37:44-72, 93_*). FORCE RLS sin política para otros roles ⇒ deny aunque hubiera grant residual (99g:48-49).
- vault_access_log: hereda DML de console por DEFAULT PRIVILEGES (25:222) pero FORCE RLS sin política console ⇒ 0 filas (99g:71-89). OK.
- 99c añade tenant/workspace + unique parciales global/scoped (99c:25-31); 99i clasifica filas legacy y restringe la política global a allowlist plataforma (99i:45-69).

## 6. pgvector

03:6 extensión; rag_chunks `vector(768)` base (03:29); 60 fuerza 768 (legacy Gemini, guard atttypmod 60:7-13); 71 estado final `vector(1024)` Titan v2 con reset de embeddings + chunk_count=0 (71:17-25). Orden 60→71 garantiza final 1024. CONSISTENTE; requiere reindex RAG post-upgrade (documentado 71:3-5).

## 7. Higiene de migraciones

- **11 grupos de números duplicados**: 12,13,14,16,17,18,19,20,21,93,94 (no solo 93/94). Orden = glob lexicográfico del entrypoint y de scripts/apply_db_migrations.sh:31 (`[0-9][0-9]*_*.sql`). Determinista por locale; el riesgo `99_` vs `99a_` está reconocido y mitigado (99d:7-9 re-crea tablas defensivamente; 99m:5 re-crea schema_migrations).
- 62==12 y 64==21 byte-idénticos (re-emisiones); 63 = 16 + grant de columnas users a mcp_infra (63:11-18).
- 22_schema_migrations.sql crea tracking y backfillea hasta el 22 (22:5-34); 43 backfillea 00-42 **y pre-estampa 59-64** (43:64-79): en volúmenes upgradeados el runner SALTARÁ 59-64. Inocuo para 62/64 (duplicados) y 60 (superseded por 71), pero el grant nuevo de 63 nunca se aplica por la vía runner en DBs viejas (P3-1).
- Runner: envuelve cada archivo en BEGIN/COMMIT (apply_db_migrations.sh:38-45) — seguro: no hay CONCURRENTLY en ningún init. Gold loop `[0-9][0-9]_*.sql` cubre los 3 archivos gold, registra como `gold/<file>` (52-67).
- Columna `checksum` existe (22:10) pero NUNCA se rellena — sin detección de drift (P3-2).
- Archivos ≥72 se auto-registran con INSERT propio (p.ej. 99e:343-345) + el runner re-inserta con ON CONFLICT — consistente.

## 8. Migraciones 44/45/47 — verificadas

- 44: dedup `ROW_NUMBER ... rn>1` conservando id mínimo (44:15-25); `UNIQUE NULLS NOT DISTINCT` con fallback a índice parcial para PG<15 (44:48-60). HACE LO QUE DICE.
- 45: audit_deletes con `REVOKE ALL FROM PUBLIC` y console append-only (REVOKE UPDATE/DELETE/TRUNCATE, 45:43-57); CASCADE→RESTRICT recorriendo catálogo para user_workspace_roles/conversations/conversation_messages/workspaces/decisions → tenants/users/workspaces/roles/conversations (45:66-115); excluye deliberadamente refresh_tokens/user_sessions (45:80-92); trigger BEFORE-DELETE con strip de 12 claves secret-shaped (45:138-155). HACE LO QUE DICE.
- 47: reemplaza función para strip PII (email→`email_hash` md5 sin sal — decisión documentada 47:23-28) por tabla. HACE LO QUE DICE.

## 9. Auditoría de GRANTs

- Sin `GRANT ... TO PUBLIC` en init (único hit es comentario 36:263). Sin GRANT ALL ON SCHEMA salvo metastores airflow/superset en sus DBs propias (compose:916,985 — aceptable).
- omega_console es deliberadamente amplio (plataforma owner): ALL TABLES + DEFAULT PRIVILEGES (25:41,222-225).
- Secuencias: TODOS los roles tienen USAGE (nextval) sobre TODAS las secuencias (P3-3). 69 re-asegura 2 secuencias a refinement (69:3-4); 72 GRANT user_workspace_roles a workspace (72:13).
- hubspot/salesforce reciben `CREATE ON SCHEMA public` (93_hubspot:22; 93_salesforce:60) (P3-5).

## 10. Seeds demo

- 02 (replicon header) y 20 (SAP headers): registro de catálogo de producto, ON CONFLICT, legítimo en prod.
- 10_replicon_gold_seed: "Auto-generated ... from 10.0.2.175. Datasets: 29 · Apps: 5" (10:1-3) — datasets+apps derivados de un entorno AWS sembrados INCONDICIONALMENTE en prod (P3-8); 65/66 y seeds SAP 80-89 análogos (plantillas globales, workspace NULL ⇒ invisibles a roles scoped bajo RLS).
- init_dev correctamente separado: NO montado en initdb.d; contenedor `postgres_dev_seed` lo aplica SOLO si APP_ENV≠production/prod, default `production` ⇒ skip (compose:33-50). 16_demo_control_room_seed.sql solo en init_dev. Usuario dev `emmanuel@local.ai` con hash bcrypt fijo solo por esa vía (init_dev/15:7-21).

## 11. Findings

| ID | Sev | Hallazgo | Evidencia | Estado | Fix |
|---|---|---|---|---|---|
| F-1 | **P1** | rag_sources/rag_chunks globales sin tenant/workspace ni RLS; `kind='document'` = uploads de usuario; legibles/escribibles por omega_workspace, omega_mcp_infra, omega_airflow_dag, console ⇒ fuga RAG cross-tenant en multi-tenant | 03:8-31; 14:1-8; 25:136,192-206; 36:249 | ABIERTO | añadir tenant_id/workspace_id + política nativa estilo 99e; particionar índice HNSW por workspace |
| F-2 | **P1** | Política owner `USING(true)` para omega_console y omega_refinement en TODAS las tablas 99e/99f ⇒ RLS no es backstop para los 2 servicios mayores; una SQLi en console cruza tenants. Documentado como transitorio (99e:3-6) | 99e:134-146; 99f:106-118 | ABIERTO (by-design transitorio) | migrar console a contexto scoped por request y degradar owner policy a roles batch |
| F-3 | P2 | Gold: `omega_refinement_gold` compartido por console+refinement+superset, con DML total + CREATE; superset solo necesita SELECT | compose:164,193,334; init_gold/34:21-29 | ABIERTO | rol gold_reader para console/superset |
| F-4 | P2 | audit_events sin tenant/workspace ni RLS; PII (email, ip, user_agent) mezclada entre tenants; mitigado: solo console tiene grant | 16:4-17; 39; 92 | ABIERTO | añadir workspace_id + política; o views por tenant |
| F-5 | P2 | analytic_apps sin workspace col ni RLS; workspace role tiene SELECT global; visibilidad solo por filtro de app (`visibility`) | 08:6-27; 25:135 | ABIERTO | workspace_id + RLS |
| F-6 | P2 | user_sessions/user_tokens/refresh_tokens sin RLS; omega_workspace SELECT total de user_sessions | 25:137; 05:15 | ABIERTO (bootstrap requiere) | política por hash de sesión o vista limitada |
| F-7 | P3 | 43 pre-estampa 59-64 ⇒ runner salta el grant nuevo de 63 en upgrades; numeración duplicada en 11 grupos; checksum sin uso | 43:64-79; apply_db_migrations.sh:31-45; 22:10 | ABIERTO | re-emitir grant 63 como 99x; adoptar numeración monotónica; poblar checksum |
| F-8 | P3 | `USAGE` (nextval) en TODAS las secuencias para todos los roles ⇒ avance de ids ajeno / disclosure | 25:42,79,109,142,207; 36:182,252 | ABIERTO | restringir a secuencias propias |
| F-9 | P3 | NOBYPASSRLS no asertado explícitamente para console/refinement/cartridges (default seguro, sin pin) | 99e:330-341; 99f:249-260 | ABIERTO | ALTER ROLE ... NOBYPASSRLS para los 16 |
| F-10 | P3 | hubspot/salesforce con `CREATE ON SCHEMA public` | 93_hubspot:22; 93_salesforce:60 | ABIERTO | owner-role pattern como 46 sin CREATE schema |
| F-11 | P3 | 63 otorga a mcp_infra columnas de users — contradice narrativa lockdown 35 (sin password_hash, scoped por 99f tras GUC) | 63:11-18 vs 35:1-27 | ACEPTABLE-documentar | comentario cruzado en 35 |
| F-12 | P3 | Seeds AWS-derivados (10/65/66/80-89/94) incondicionales en init prod | 10:1-3 | ABIERTO | mover a init_dev o gatear por APP_ENV |

Positivos destacables: creación de roles aborta sin password (25:31-33); vault un-solo-rol + FORCE RLS + Fernet verificado; gold deny-by-default para tablas sin scope (35:70-78); 44/45/47 hacen exactamente lo declarado; init_dev gateado por APP_ENV con default production; tool postgres de mcp-infra anti multi-statement (tools/postgres.py:211-216).
