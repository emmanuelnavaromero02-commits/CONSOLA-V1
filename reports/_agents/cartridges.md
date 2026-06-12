# Connectors / Cartridges Audit — OMEGA / CONSOLA-BETA

- Repo: `/home/user/CONSOLA-BETA` · main HEAD `bab2a4f` · VERSION 1.45.68-beta
- Auditor scope: `cartridges/{replicon, sap_hcm, sap_successfactors, sap_s4hana, salesforce, hubspot}` + wiring en `infra/docker-compose.yml`, console seeders y tests.
- Veredicto global: **Los 6 cartuchos tienen clientes API reales (no generadores de datos falsos) y el pipeline full/incremental/historical → watermark(5 min) → Parquet Bronze → FastMCP `/mcp` existe en los 6.** El "contrato común de runtime" NO es código compartido: es **copy-paste textual con drift medible** (admitido en `sap_client.py:34-35`: "no shared lib between cartridges → copied textually into each"). Caveats principales: hubspot con **0 tests in-cartridge**, replicon con 2; el guard monotónico de watermark sólo existe en salesforce; replicon usa un DAG que **reimplementa** la extracción (2 code paths); ningún test valida bytes parquet reales contra MinIO (todo mock-level).

---

## 1. Veredictos por cartucho

### 1.1 replicon (:8201, PSA) — **FUNCTIONAL** (con asterisco: incremental = filtro client-side)

- **Cliente real**: `cartridges/replicon/app/core/replicon_client.py:57-282` — Replicon Analytics BI API asíncrona: `POST /extracts` → poll `GET /extracts/{id}` → descarga CSV pre-firmado S3 → `pd.read_csv` (`:197-282`). Retries exponenciales 5 intentos con `Retry-After` (`:15-17,107-181`), circuit breaker umbral 3 (`:25-54`).
- **Sin modo fake en runtime**: `use_demo_data` existe en config (`app/core/config.py:39`) pero **nadie lo lee** en el código replicon (grep: sólo definición). El mock replicon fue eliminado en v1.40; `tests/test_replicon_mock_under_profile.py:1-12` bloquea su reaparición. Compose lo confirma (`infra/docker-compose.yml:436-442`).
- **Watermarks**: tabla Postgres `entity_watermarks` (`app/services/watermark_service.py:8-50`, UPSERT por (cartridge_id, entity)). Buffer de 5 min: `extraction_service.py:17` (`WATERMARK_BUFFER_MINUTES = 5`) aplicado en `:131-148`. Historical vía `from_date/to_date` → modo `historical` (`:58-61, 91-96`).
- **LIMITACIÓN incremental**: la API de Replicon (download extract) no soporta filtro server-side ⇒ **siempre baja la tabla completa** y filtra en cliente (`extraction_service.py:27-39, 85-89`). "Incremental" no reduce carga sobre la API.
- **Bronce**: `parquet_service.py:63-74` → `s3://lakehouse/raw/replicon/{entity}/{tenant_id=…/workspace_id=…/}load_date=YYYY-MM-DD/batch_id={run_id}/{entity}.parquet`, snappy, columnas `_extracted_at/_run_id/_source_entity/_load_type/_watermark_value` (`:51-59`), PII protection antes de escribir (`:48` → `protection_service`, mask/shadow/Fernet).
- **MCP**: 11 tools (`mcp_server.py:91-355` — list_entities, get_schema, preview, extract, extract_all, get_run_logs, get_job_status, list_jobs, list_kbs, run_kb, query_kb) + custom tools desde DB (`:470-495`). Todos reales (DuckDB sobre parquet, job_runner asyncpg). `query_kb` pasa por `sql_guard.validate_kb_sql` con allowlist de prefijos (`:366-374`). Doc-drift: el docstring dice "8 tools" (`mcp_server.py:4`).
- **DAGs**: `dags/replicon_extract.py` (515 líneas) **reimplementa** todo el pipeline dentro del DAG (cliente Replicon embebido `:275-331`, watermark directo a PG `:71-105`, parquet a `raw/replicon/...` `:241-266`) — Pattern B, segundo code path paralelo al del servicio. `dags/replicon_ses_inbox_import.py` (561 líneas, ingesta SES). `config/seed.sql:27-33` registra 4 DAGs; `file_ingest.py` y `replicon_outlook_audit_report_import.py` viven en `airflow/dags/` global (existen, verificado).
- **Tests**: 1 archivo / 2 tests (`tests/test_connectivity_circuit_breaker.py`). Complemento en `tests/` raíz (test_replicon_watermark_no_silent_fail.py = scan de fuente, no comportamiento).
- **Para producción falta**: tests de extracción/parquet; resolver costo del incremental client-side (la propia fuente sugiere target BigQuery, `extraction_service.py:35-38`); guard monotónico de watermark (ver F-05).

### 1.2 sap_hcm (:8202, HR) — **FUNCTIONAL** (perfil `sap`, requiere gateway on-prem)

- **Cliente real**: `cartridges/sap_hcm/app/core/sap_client.py:107-373` — SAP NetWeaver Gateway OData v2, Basic Auth + `sap-client` mandant header+query (`:182-193, 328-339`), `$top/$skip/$select/$filter` (`:328-337`), parse `d.results`/`value` (`:366-373`). Retry urllib3 3x backoff exp. (`:44-63`), circuit breaker (`:75-104`). "Refuses to fetch… degraded" si faltan credenciales (`:154-176`).
- **Incremental server-side**: `extraction_service.py:122-135` arma `$filter={watermark_field} gt '{watermark}'` + re-filtro client-side defensivo (`:140-144`). `odata_filter` estático para entidades que comparten entity set (HRP1000Set, `:66-69`). Buffer 5 min (`:15, 162-179`). Streaming buffer 10k filas con flush (`:97-160`), escribe parquet vacío con schema esperado si 0 filas (`:157-160`, `parquet_service.py:37-53`).
- **Bronce**: `raw/sap_hcm/{entity}/{scope}load_date=…/batch_id=…` (`parquet_service.py:93`).
- **MCP**: mismas 11 tools (`mcp_server.py:97-357`).
- **DAG**: thin trigger HTTP → `POST {SAP_HCM_URL}/entities/{entity}/extract` (`dags/sap_hcm_extract.py:79-80`), key `INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE` fail-fast (`:24-31` patrón igual a salesforce).
- **Compose**: `profiles: ["sap"]` (`infra/docker-compose.yml:660-687`), rol DB `omega_cartridge_sap_hcm` (`:677`), credenciales `SAP_HCM_BASE_URL/USER/PASS` env-fallback (`:684-687`) + Vault vía console.
- **Tests**: 2 archivos / 11 tests (circuit breaker, sql_guard).
- **Para producción falta**: no hay validación de paginación OData real (mock-only); intelligence.yaml existe (a diferencia de s4hana/sfsf).

### 1.3 sap_successfactors (:8203, HR) — **FUNCTIONAL** (el más maduro)

- **Cliente real**: `app/core/sap_client.py:208-935` — OData v2 SuccessFactors con OAuth2 client_credentials **y** SAML2 Bearer Assertion (`:36-38`, `_get_saml_bearer_token :630`, assertion vía `/oauth/idp` `:703-742`), X.509 key montada read-only (`docker-compose.yml:639-642`), token cache compartido thread-safe (`:49-51, 546-584`), normalización de base_url a `/odata/v2` (`:114-131`).
- **Extracción avanzada**: `extraction_service.py` (416 líneas) — normaliza watermarks `/Date(ms)/` a literal `datetime'…'` OData (`:69-78`), **fallback a full snapshot si el watermark es futuro/imparseable** (`:73, 102`), detecta rechazo de `$filter` por el server (`:113`), ventanas effective-dated `fromDate/toDate` (`:197-215`), plan logging (`:126-148`). Buffer 5 min (`:18, 377`). Multi-conexión `conn_id` (extract_all con `conn_id`, `mcp_server.py:229`). `extraction_status.py:6-30` clasifica fallas (auth-blocked / permission-blocked / failed-open).
- **Bronce**: `raw/sap_successfactors/...` mismo layout. `minio_client.py` único con soporte de credenciales EC2 IMDSv2/IAM (93 líneas vs 30 del resto).
- **MCP**: 11 tools; `query_kb` con sql_guard + prefijos (`mcp_server.py:374-384`) — **el hallazgo baseline P1-SQL-001 ("query_kb directo no usa sql_guard") está RESUELTO** y testeado (`tests/test_query_kb_sql_guard_sap_successfactors.py`).
- **DAG**: thin trigger con **firma HMAC del security_context** (`dags/sap_successfactors_extract.py:55-110, 145-152`) — único DAG que firma scope.
- **Tests**: 10 archivos / 50 tests — auth SAML (11), política incremental OData (7), effective dates, selección de conexión Vault (4), plan extract_all scoped (6), contrato DAG (3), contrato entity_config. Además harness live AWS (`scripts/sap_successfactors_aws_live_max.py`, `tests/test_sap_successfactors_aws_live_harness.py`).
- **Gaps**: sin `intelligence.yaml` (no participa del intelligence engine del console — `console/app/services/intelligence/contracts.py:41` glob `*/app/config/intelligence.yaml` no lo encuentra); sin `conftest.py` raíz de cartucho (los demás SAP lo tienen; usa `tests/conftest.py`).

### 1.4 sap_s4hana (:8204, ERP) — **FUNCTIONAL** (fork textual de sap_hcm)

- **Cliente real**: `app/core/sap_client.py:120-401` — OData v2 `/sap/opu/odata/sap/API_*`, Basic Auth + header `APIKey` opcional para SAP API Hub sandbox (`SAP_S4_API_KEY`, `:147-152`; README.md:179). Diff vs sap_hcm = sólo naming + api_key (verificado por diff textual).
- **Extracción**: igual a sap_hcm (server-side `$filter` + re-filtro client, buffer 5 min `extraction_service.py:15, 162`, batch 10k, empty parquet).
- **Bronce**: `raw/sap_s4hana/...`. **MCP**: 11 tools. **DAG**: thin trigger → `/entities/{entity}/extract` (`dags/sap_s4hana_extract.py:72-73`). Compose perfil sap (`docker-compose.yml:707-717`).
- **Tests**: 2 archivos / 10 tests (circuit breaker 1, sql_guard 9).
- **Gaps**: sin `intelligence.yaml`; cobertura mínima comparada con SFSF; entidades 25 en `entities.yaml` (la mayor superficie ERP) con sólo tests de guard.

### 1.5 salesforce (:8205, CRM) — **FUNCTIONAL** (sin Bulk API)

- **Cliente real**: `app/core/salesforce_client.py:95-404` — REST SOQL Query API con paginación cursor `nextRecordsUrl` (queryMore) mapeada al loop skip/page_size de la plataforma (`:369-404`), OAuth2 password / client_credentials / bearer estático (`:206-263`), usa `instance_url` de la respuesta (`:257-259`), refresh defensivo 1h (`:262`), re-auth en 401 (`:338-341, 351-354`), traducción OData→SOQL con unquote de datetimes y escape anti-inyección (`:67-92`). "If credentials are missing the client REFUSES to fetch… never invents data" (`:18-19`). Retry urllib3 (`:41-60`).
- **NO hay Bulk API**: el claim "Salesforce REST/Bulk" es mitad cierto — sólo `/services/data/<v>/query` (`:333-336`). Volúmenes grandes pagan el costo del REST API.
- **Watermarks**: único cartucho con **guard monotónico**: `watermark_service.py:45` `WHERE entity_watermarks.last_watermark_value < EXCLUDED.last_watermark_value` (los otros 5 pueden retroceder el watermark). Buffer 5 min (`extraction_service.py:18, 166`).
- **Bronce**: `raw/salesforce/...` (parquet_service 95 líneas, con expected_columns). **MCP**: **14 tools** — las 11 comunes + `get_watermarks` (`mcp_server.py:131`), `get_entity_status` (`:147`), `run_all_kb` (`:434`).
- **Clave dedicada**: `INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE` verificada end-to-end: cartucho (`app/core/settings_proxy.py:21`, `vault_client.py:43`), compose (`docker-compose.yml:153, 502` — `:?required`), console acepta el par (`console/app/services/auth.py:69-70`, `console/app/main.py:376-377`) y tests (`console/tests/test_vault_reveal_pair_keys.py:106-114`, `tests/test_salesforce_dedicated_vault_key.py`). **Claim del single key pair: VERIFICADO**.
- **Colisión SF_***: host `SALESFORCE_*` → container `SF_*` para no chocar con SuccessFactors (comentario explícito `docker-compose.yml:511-513`). Frágil si alguien levanta ambos con `.env` plano: SFSF consume `SF_BASE_URL/SF_CLIENT_ID/...` del host directamente (`:627-634`).
- **Tests**: 2 archivos + conftest / 25 tests (SOQL translation, 401-retry, protección PII, KB guard). Copy-paste tell: `app/services/preflight.py:4` dice "the three environments the cartridge depends on (SAP, Postgres, MinIO)" en el cartucho salesforce.
- **Para producción falta**: Bulk API 2.0 para tablas grandes; el `FIELDS(STANDARD) LIMIT 200` cuando no hay select_fields (`:322-331`) silenciosamente trunca a 200 filas.

### 1.6 hubspot (:8210, CRM) — **FUNCTIONAL runtime / FAIL en testing** (0 tests in-cartridge)

- **Cliente real**: `app/core/hubspot_client.py:73-308` — CRM v3 REST (`/crm/v3/objects/*`, `/crm/v3/owners`, `/crm/v3/pipelines/deals`), bearer Private App token, paginación cursor `paging.next.after` (`:208-230`), flatten de `properties` (`:232-247`), pipelines aplanados a (pipeline,stage) con probability para el forecast gold (`:272-294`). Retry urllib3 (`:53-66`). "never invents data" (`:15`).
- **Toggle demo**: `hubspot_client.py:82` `if settings.use_demo_data:` — **no genera datos falsos**: sólo salta el Vault y usa base_url/token de env. `USE_DEMO_DATA` no está seteado en ningún compose (grep infra = 0 hits; default `False` en `app/core/config.py:43`).
- **Fake upstream NO flaggeado**: `tests/fixtures/fake_hubspot_api.py` (servidor HubSpot determinístico, requiere bearer) se inyecta vía `HUBSPOT_BASE_URL=http://host.docker.internal:18030` en `scripts/run_full_stack_acceptance.sh:23-90`. El cartucho no distingue fake de real y el parquet resultante **no lleva ninguna marca de sintético** — en un entorno donde alguien re-apunte `HUBSPOT_BASE_URL`, los datos fake pasan por reales (mismo riesgo señalado por el pipeline auditor para seeds).
- **Watermark**: filtro client-side (HubSpot list endpoints no aceptan filtro `updatedAt` server-side — documentado `hubspot_client.py:18-22`), buffer 5 min (`extraction_service.py:18, 137`), cursor loop con flush 10k (`:79-122`).
- **Bronce**: `raw/hubspot/...` (`parquet_service.py:69`). **MCP**: 11 tools. **DAG**: thin trigger pero a **`/skills/run_{mode}/{entity}`** (`dags/hubspot_extract.py:93-94`) — endpoint síncrono (timeout 600s), no `/entities/.../extract` como SAP/SF.
- **Sin routes_console.py / settings_proxy.py / preflight.py** (igual que replicon): el console dispara extracción vía `/skills/run_*` (`console/app/routers/cartridges.py:204-209`) así que funciona, pero la superficie REST difiere del resto.
- **Tests**: **directorio `tests/` inexistente**. Sólo `tests/test_hubspot_cartridge_runtime_contract.py` (raíz, 9 tests) que valida **YAML de compose/terraform**, no código. Requirements sin pin (`pandas/pyarrow/duckdb` flotantes, `requirements.txt`).
- **Para producción falta**: tests unitarios del cliente/extracción; pin de dependencias; associations API (deals↔contacts) no extraída.

---

## 2. Matriz de integración

| Cartucho | Puerto | Auth inbound | Vault (creds fuente) | MCP tools | DAG | Extracción real | Fake mode | Watermarks (5min) | Bronze parquet | Tests | Prod-ready | Evidencia clave |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| replicon | 8201 | `INTERNAL_API_KEY_{CONSOLE,AIRFLOW}_TO_CARTRIDGE` + legacy dev (`security.py:41-55`) | console reveal proxy con `INTERNAL_API_KEY_REPLICON_TO_CONSOLE`; env primero (`vault_client.py:121-133`) | 11 + custom DB | `replicon_extract` (reimplementa), `replicon_ses_inbox_import`; montado ro x3 (`compose:1013`) | Sí — Analytics BI async extract (`replicon_client.py:197-282`) | No en runtime (mock eliminado v1.40) | Sí PG + buffer (`extraction_service.py:17,131-148`); incremental client-side | `raw/replicon/{e}/{scope}load_date=/batch_id=` (`parquet_service.py:68-71`) | 2 (1 archivo) | CASI — falta cobertura y costo incremental | compose:443-490 |
| sap_hcm | 8202 | ídem (copia idéntica `security.py` md5 08b6144c) | `INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE` (`compose:670`) | 11 | `sap_hcm_extract{,_all}` thin → `/entities/{e}/extract`; montado (`compose:1010`) | Sí — OData v2 Gateway Basic (`sap_client.py:318-373`) | No | Sí + `$filter` server-side (`extraction_service.py:122-144`) | `raw/sap_hcm/...` (`parquet_service.py:93`) | 11 (2 archivos) | Sí con perfil sap + gateway accesible | compose:660-704 |
| sap_successfactors | 8203 | ídem | `INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE` (`compose:609`); multi-conn `conn_id` | 11 | thin + HMAC context (`dags/...extract.py:75-110`); montado (`compose:1009`) | Sí — OData v2 + OAuth2 CC/SAML bearer (`sap_client.py:586-742`) | No | Sí + normalización `/Date(ms)/` + fallback futuro (`extraction_service.py:69-113,377`) | `raw/sap_successfactors/...`; minio IMDSv2 | 50 (10 archivos) | Sí (el más cercano) — sin intelligence.yaml | compose:598-658 |
| sap_s4hana | 8204 | ídem | `INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE` (`compose:717`) | 11 | thin → `/entities/{e}/extract` (`dags:72-73`); montado (`compose:1011`) | Sí — OData v2 API_* + APIKey sandbox (`sap_client.py:147-152`) | No | Sí (`extraction_service.py:15,162`) | `raw/sap_s4hana/...` | 10 (2 archivos) | Sí con perfil sap; cobertura floja | compose:706-... |
| salesforce | 8205 | ídem | **`INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE` dedicada — VERIFICADA** (`compose:153,502`; `console/app/services/auth.py:69`) | **14** | `salesforce_extract{,_all}` thin (`dags:76-83`); montado (`compose:1017`) | Sí — SOQL REST cursor + OAuth2 x3 (`salesforce_client.py:206-263,369-404`); **sin Bulk** | No | Sí + **guard monotónico único** (`watermark_service.py:45`) | `raw/salesforce/...` | 25 (2 archivos+conftest) | CASI — Bulk API y FIELDS(STANDARD) LIMIT 200 | compose:492-541 |
| hubspot | 8210 | ídem | `INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE` (`compose:556`) | 11 | `hubspot_extract{,_all}` thin → **`/skills/run_*`** (`dags:93-94`); montado (`compose:1015`) | Sí — CRM v3 cursor bearer (`hubspot_client.py:195-294`) | `use_demo_data` solo bypass Vault (`:82`); fake upstream por env en acceptance (`run_full_stack_acceptance.sh:83-90`), **sin marca en datos** | Sí client-side + buffer (`extraction_service.py:18,137`) | `raw/hubspot/...` (`parquet_service.py:69`) | **0 in-cartridge** (9 contract tests en raíz) | NO — sin tests, deps sin pin | compose:546-590 |

Notas transversales de la matriz:
- **FastMCP `/mcp`**: real en los 6 — FastMCP streamable HTTP montado en `/mcp/rpc` con guard de startup 503 + `InternalApiKeyASGIGuard` (`replicon/app/main.py:52,118-151`), adaptador REST `/mcp/tools` + `/mcp/invoke` (`:198-263`). El claim dice "/mcp"; el mount JSON-RPC real es `/mcp/rpc`.
- **Health**: `/health` (readiness real: startup_ok + tools registradas, 503 si no — `routes_health.py:12-42`), `/healthz` liveness (`main.py:105-107`), `/health/<cartucho>` autenticado prueba conectividad upstream con estados ok/auth_error/degraded/unhealthy (`routes_health.py:45-65`). Compose healthchecks apuntan a `/health` (`compose:479, 641-647`).
- **Vault :8300**: los cartuchos **no hablan con vault:8300 directo**; piden a console `GET /api/vault/connections/{service}/{conn_id}/reveal` (`vault_client.py:83-106`) y console proxya a vault:8300 (`console/app/main.py:5387`; `console/app/services/vault_utils.py:27`). Orden de resolución: **env primero, Vault después** (`get_secret_for_worker`, `vault_client.py:121-133`) — env plano puede pisar Vault silenciosamente.
- **datasets/**: SQL silver/gold DuckDB con metadata en header (`-- sources:`) sobre Bronze (`datasets/hubspot_deals_latest.sql:1-18`); sembrados al console desde el mount `/registry/cartridges` (`console/app/services/seed_packaged_datasets.py:20-32`; mount ro `compose:229`). Conteo: replicon 31, sfsf 32, s4hana 28, hcm 22, salesforce 20, hubspot 11. Reales, alimentan refinement/intelligence.
- **apps/**: pares HTML+JSON sembrados a `analytic_apps` (`seed_packaged_apps.py:52-100`). **ap_flows/**: flows Activepieces (webhook→log→cleanup→extract→log; `hubspot/ap_flows/ingest_bi_entity.json`) — orquestador alternativo real. **hints/assistant.md**: reglas del asistente sembradas a `cartridges.assistant_hints` (`seed_packaged_hints.py:23-34`) y consumidas por agent_runtime con sandboxing anti-inyección (`agent_runtime.py:514-529`).

---

## 3. Hallazgos

| ID | Sev | Hallazgo | Evidencia | Estado |
|---|---|---|---|---|
| CART-F-01 | **P1** | hubspot sin ningún test in-cartridge (directorio `tests/` no existe); CI corre `pytest cartridges` (`.github/workflows/docker-image.yml:92`) y hubspot aporta 0. Los 9 tests raíz validan YAML, no código | `find cartridges/hubspot` (sin tests/); `tests/test_hubspot_cartridge_runtime_contract.py` | ABIERTO |
| CART-F-02 | **P1** | Contrato común = copy-paste con drift: 6 copias de `vault_client.py` (6 md5 distintos), `sql_guard.py` (6 distintos), `watermark_service.py` (6), `parquet_service.py` (6); sólo `security.py`/`auth_factory.py`/`duckdb_service.py` siguen byte-idénticos. Fixes aplicados a uno no llegan a los demás (ver F-05) | md5sum batch (sección 2); admisión en `sap_hcm/app/core/sap_client.py:34-35` | ABIERTO (deuda estructural) |
| CART-F-03 | **P1** | Replicon DAG reimplementa la extracción completa (cliente, watermark, parquet) en 515 líneas paralelas al servicio — dos fuentes de verdad para el mismo pipeline; los otros 5 usan thin-trigger HTTP | `cartridges/replicon/dags/replicon_extract.py:241-331` vs `salesforce/dags/salesforce_extract.py:76-83` | ABIERTO |
| CART-F-04 | **P2** | Incremental client-side en replicon y hubspot: full fetch de la fuente en cada corrida "incremental" (limitación API documentada, pero sin mitigación implementada) | `replicon/app/services/extraction_service.py:27-39`; `hubspot/app/core/hubspot_client.py:18-22` | ABIERTO (by design, costo) |
| CART-F-05 | **P2** | Watermark puede retroceder en 5 de 6 cartuchos: el guard `last_watermark_value < EXCLUDED…` sólo existe en salesforce; en el resto un run con datos viejos (o historical mal etiquetado) pisa el watermark hacia atrás | `salesforce/app/services/watermark_service.py:45` (presente) vs `replicon/...watermark_service.py:40-44` (ausente) | ABIERTO |
| CART-F-06 | **P2** | Fake HubSpot upstream indistinguible: acceptance inyecta `HUBSPOT_BASE_URL` al fake (`scripts/run_full_stack_acceptance.sh:83-90`); ni el cliente ni el parquet marcan los datos como sintéticos. Único toggle es env | `tests/fixtures/fake_hubspot_api.py:1-7` | ABIERTO (mitigado por gate manual en `run_live_cartridge_checks.sh:4-6`) |
| CART-F-07 | **P2** | sap_s4hana y sap_successfactors sin `intelligence.yaml` → fuera del intelligence engine (el glob del console no los encuentra); replicon/hcm/salesforce/hubspot sí participan | `console/app/services/intelligence/contracts.py:41`; ls `cartridges/sap_s4hana/app/config/` | ABIERTO |
| CART-F-08 | **P2** | Salesforce sin Bulk API y `FIELDS(STANDARD)` impone `LIMIT 200` silencioso cuando la entidad no define select_fields | `salesforce_client.py:322-331` | ABIERTO |
| CART-F-09 | **P3** | Colisión de namespace `SF_*` entre salesforce (mapeado desde `SALESFORCE_*`) y SuccessFactors (usa `SF_*` del host) — correcta en compose, frágil ante `.env` compartido | `docker-compose.yml:511-521` vs `:627-634` | ABIERTO (documentado en compose) |
| CART-F-10 | **P3** | Ningún test (cartucho o raíz) valida parquet real escrito a MinIO ni round-trip de watermark contra Postgres: todo monkeypatch (`write_parquet_and_upload` stubbed) | `sap_successfactors/tests/test_odata_incremental_policy.py:14-22`; gate live es script manual (`scripts/run_live_cartridge_checks.sh`) | ABIERTO |
| CART-F-11 | **P3** | Pins de dependencias inconsistentes: replicon y hubspot con `pandas/pyarrow/duckdb` sin versión; los otros 4 pinneados exactos (2.2.3/23.0.1/1.2.2) | `cartridges/{replicon,hubspot}/requirements.txt` vs `salesforce/requirements.txt` | ABIERTO |
| CART-F-12 | **P3** | Doc-drift menor: replicon `mcp_server.py:4` dice "8 tools" (hay 11); `salesforce/app/services/preflight.py:4` menciona "SAP" | citados | ABIERTO |
| CART-F-13 | — | Baseline P1-SQL-001 (SFSF `query_kb` sin sql_guard) **RESUELTO**: `validate_kb_sql` + allowlist de prefijos + tests | `sap_successfactors/app/mcp_server.py:374-384` | RESUELTO |

---

## 4. Contract drift entre cartuchos (detalle)

1. **Estructura app/**: salesforce + 3 SAP tienen `api/routes_console.py`, `core/settings_proxy.py`, `services/preflight.py`; **replicon y hubspot no** — su única superficie de trigger REST es `/skills/*`. SFSF añade `core/extraction_status.py` (clasificación de fallas) que nadie más tiene.
2. **Endpoints que usan los DAGs**: 3 patrones — replicon (in-DAG), hubspot (`/skills/run_{mode}/{entity}` síncrono), SAP×3 + salesforce (`/entities/{entity}/extract`). Un orquestador genérico no puede tratar a los 6 igual.
3. **MCP tools**: 11 comunes idénticas; salesforce expone 3 extra (`get_watermarks`, `get_entity_status`, `run_all_kb`) que no se backportaron.
4. **parquet_service**: SAP×3 + salesforce escriben parquet vacío con schema (`expected_columns`); replicon/hubspot escriben DataFrame vacío sin columnas (consumidores DuckDB con `union_by_name` lo toleran, pero el artefacto difiere).
5. **watermark_service**: guard monotónico sólo en salesforce (F-05).
6. **vault_client `_SERVICE_KEY_ENVS`**: cada copia conoce un subconjunto distinto de pares (la de salesforce sólo replicon+salesforce — `salesforce/app/core/vault_client.py:41-44`; la de replicon no conoce salesforce — `replicon/...:39-45`); además salesforce introduce fallback `INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE` no-prod (`:61`) que replicon no tiene.
7. **minio_client**: SFSF con cadena de credenciales IMDSv2 AWS; resto sólo keys estáticas (despliegue AWS con IAM roles sólo funciona para SFSF sin env extra).
8. **config.py**: `use_demo_data` declarado sólo en replicon (huérfano) y hubspot (bypass Vault).
9. **requirements**: pins divergentes (F-11); todos convergen en `fastmcp>=3.3.0,<4.0` y `fastapi==0.136.1`.
10. **Extracción incremental**: server-side ($filter OData / SOQL WHERE) en SAP×3 + salesforce; client-side full-fetch en replicon + hubspot (F-04). SFSF único con fallback de watermark futuro y normalización `/Date(ms)/`.

## 5. Clasificación final

| Cartucho | Clase | Falta para producción |
|---|---|---|
| replicon | **FUNCTIONAL** | tests (2), guard monotónico, costo incremental, unificar DAG/servicio |
| sap_hcm | **FUNCTIONAL** (perfil sap) | más tests de paginación/fechas SAP, intelligence ok |
| sap_successfactors | **FUNCTIONAL** (mejor estado) | intelligence.yaml, validación parquet real |
| sap_s4hana | **FUNCTIONAL** (perfil sap) | tests (10), intelligence.yaml, validación de volumen (JournalEntryItem) |
| salesforce | **FUNCTIONAL** | Bulk API, fix LIMIT 200 implícito, backport de sus mejoras al resto |
| hubspot | **FUNCTIONAL runtime / PARTIAL madurez** | suite de tests in-cartridge, pins, associations, marca de datos sintéticos |

Ninguno es MOCK-ONLY ni DISCONNECTED: los 6 están en compose con healthcheck, DAGs montados en airflow (scheduler/webserver/worker — `compose:1009-1017, 1112-1120, 1206-1214`), clientes reales y pipeline Bronze completo. La fragilidad dominante no es "fake data" sino **divergencia de 6 copias del mismo runtime** y **cobertura de tests asimétrica** (50 SFSF vs 0 hubspot).
