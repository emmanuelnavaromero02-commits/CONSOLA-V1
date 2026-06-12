# Data Pipeline / Refinement Audit — OMEGA / CONSOLA-BETA

- Repo: `/home/user/CONSOLA-BETA`  · main HEAD `bab2a4f` · VERSION 1.45.68-beta
- Auditor scope: `refinement/` (port 8500), `airflow/dags/`, `refinement/datasets/`, `infra/init_gold/`, MinIO layout, seed scripts.
- Verdict: **The Bronze→Silver→Gold→RLS pipeline is REAL and largely production-grade, not mocked.** Default-deny SQL guard + AST RLS + native Postgres FORCE RLS are genuinely implemented and well-tested. Main caveats: (a) no `2024-12-18` parquet pin exists (silver reads ALL partitions; one gold YAML hardcodes a date), (b) DAGs ship **unpaused** in the live compose despite the "release-gate pause" plumbing, (c) `silver_lineage` is a best-effort fallback that can report `ready` even when the Gold table is empty, (d) seeded demos are indistinguishable from real extractions at the readiness gate.

---

## 1. End-to-end flow verification map

Claimed: external → cartridge → MinIO `raw/`(Bronze) parquet → Refinement DuckDB+SQL → MinIO `silver/` → Postgres Gold table+RLS → consumption.

| Stage | Code (file:line) | Real / Partial / Missing | Evidence |
|---|---|---|---|
| External → cartridge → Bronze parquet | cartridge job_runners + DAGs (`cartridges/replicon/app/core/job_runner.py:233`, `airflow/dags/file_ingest.py`, `replicon_ses_inbox_import.py`, `replicon_outlook_audit_report_import.py`) | **Real** | SES (boto3 S3), Outlook (imaplib), file_ingest (MinIO uploads→parquet bronze). Writes `s3://<bucket>/raw/<cartridge>/<entity>/load_date=.../data.parquet` (`file_ingest.py:26`). |
| Trigger refinement (Silver) | `cartridges/*/app/core/job_runner.py` → `POST {REFINEMENT_URL}/refresh-by-source` | **Real, fail-fast** | `replicon/...job_runner.py:233`, `salesforce:256`, `sap_hcm:244`, `sap_successfactors:257`, `hubspot:234`; raises RuntimeError on failure. Endpoint: `refinement/app/main.py:2376`. |
| Trigger refinement (downstream Silver/Gold graph) | extract DAGs → `dataset_refresh_chain` DAG → `POST /mcp/invoke {tool:materialize}` | **Real** | `replicon_extract.py:500`, `hubspot_extract.py:133` trigger the meta-DAG; `airflow/dags/dataset_refresh_chain.py:348` posts `materialize` per dataset in topological order. |
| Read Bronze parquet (s3/minio cfg) | `refinement/app/duckdb_engine.py:146-209`, `_bronze_read` `:342-344` | **Real** | DuckDB httpfs + `SET s3_endpoint/url_style='path'`; AWS uses `credential_chain` SECRET (`:197-204`), MinIO uses access/secret keys (`:206-209`). `read_parquet('s3://.../**/*.parquet', hive_partitioning=true, union_by_name=true)`. |
| Materialize Silver → MinIO `silver/` | `duckdb_engine.py:1319-1328` (`materialize`) | **Real** | Writes immutable snapshot `s3://<bucket>/silver/<cart>/<name>/[tenant=/ws=/]_snapshots/<ts>-<uuid>.parquet` via `_copy_to_parquet` (local temp → `_upload_local_parquet`, 8 retries). Row count read back from the parquet. |
| Materialize Gold → Postgres Gold table + RLS | `duckdb_engine.py:1285-1317` | **Real** | `_ensure_scoped_gold_table` (`:1179`) CREATEs `pggold.gold_<name>`, `_replace_scoped_gold_rows` (`:1200`) DELETE+INSERT via **psycopg2** (NOT DuckDB COPY — RLS blocks COPY), sets `app.tenant_id/workspace_id`; `_apply_gold_rls` (`:1169`) calls `omega_apply_gold_rls_for_table`. Requires tenant+workspace or raises (`:1294`). Also writes a Gold parquet snapshot. |
| Gold connection / role | `duckdb_engine.py:156, 232, 1171`; compose `infra/docker-compose.yml:164,334` | **Real** | `GOLD_DATABASE_URL` → `omega_refinement_gold@postgres_gold:5433/modecissions_gold` (NOBYPASSRLS role). Falls back to service DB if unset (`:156`). |
| Lineage tracking | `_write_lineage` `duckdb_engine.py:1413-1444`; read `_latest_materialized_uri:402` | **Real but best-effort** | Inserts row into `silver_lineage` (name, cartridge, source, load_date, batch, sql_def, column_mapping, layer, row_count, storage_uri). **Swallows all exceptions** (`:1443 except: pass`) — lineage loss is silent. Same for `_update_catalog` (`:1410`). |
| Consumption (read Gold, RLS) | `console/app/services/intelligence/gold_fetcher.py:105-114`; AST RLS `duckdb_engine.py:987-1065` | **Real** | gold_fetcher `set_config('app.tenant_id/workspace_id')` before read, 404 if table missing, 403 if not workspace-scoped. Refinement reads route through AST RLS (`get_rls_filters`). GET `/datasets/{name}/data` is hard-410'd to prevent RLS-less reads (`main.py:2347-2356`). |

**Net:** every stage maps to executing code that touches real MinIO/Postgres. No stage writes "to nowhere."

---

## 2. Datasets inventory (`refinement/datasets/`)

YAML files are **seed/migration fixtures** only — `lifespan` runs `_migrate_yaml_datasets()` (`main.py:162`) to load them into the `datasets` Postgres table, which is the runtime source of truth (`dataset_store.py`). 18 YAMLs:

| Dataset | Layer | Source | Gold table | Cartridge | Real vs placeholder |
|---|---|---|---|---|---|
| `empleados_maestro` | gold | `raw/replicon/User` | `gold_empleados_maestro` | replicon | **Real** SQL; row_count 202; **hardcoded `WHERE load_date='2026-04-16'`** (not `{latest_date}`) |
| `replicon_timeentry_latest` | silver | `raw/replicon/TimeEntry` | — | replicon | **Real**; `SELECT *`, all partitions; row_count 304242 |
| `replicon_user_latest` | silver | `raw/replicon/User` | — | replicon | **Real**; row_count 1485 |
| `replicon_project_latest` | silver | `raw/replicon/Project` | — | replicon | **Real**; row_count 2436 |
| `replicon_{client,role,task,department,costitem,billingitem,invoiceitem,profititem,expenseentry,activity,timesheet,resourcerequest,projectteammember}_latest` (14) | silver | `raw/replicon/<Entity>` | — | replicon | **Real but un-refreshed**: `SELECT * ... /**/*.parquet`; no `last_refresh`/`row_count` in YAML (never materialized via YAML, only at runtime) |
| `test_endpoint` | silver | `[]` (none) | — | unknown | **Placeholder**: `sql: SELECT 1`, no sources |

Notes:
- Silver "latest" datasets do **NOT** filter to a single load_date — they read `**/*.parquet` (whole history), contradicting the LLM-prompt rule that Silver must use `WHERE load_date='{latest_date}'` (`llm_sql.py:54,64`). The `_latest`-named datasets are not actually latest-filtered.
- Bucket is hardcoded `s3://lakehouse/...` in YAML but `_inject_bucket` (`duckdb_engine.py:1146`) rewrites `{bucket}` at runtime; these YAMLs predate that and pin `lakehouse` literally.

---

## 3. SQL guard analysis + bypass assessment

Two independent gates, layered:

**Gate A — regex blacklist `_validate_safe_sql` (`duckdb_engine.py:794-821`)** applied to all user/LLM SQL paths (`preview_sql:861`, `query_dataset:1089`, `get_dataset_schema:944`, `materialize:1273`). Strips comments first (`_strip_sql_comments:94`, quote-aware), then denies `read_csv/text/json/blob/parquet_objects`, `ATTACH/DETACH/INSTALL/LOAD/PRAGMA`, `COPY ... FROM`, `SET GLOBAL/SESSION/memory_limit/...`, `CREATE/DROP TABLE/VIEW/...`. `read_parquet` allowed **only** for `s3://` (`:818-821`); local `file://`,`/etc`,`/proc`,`/var` blocked (`:807,816`).

**Gate B — AST RLS `_inject_rls_ast` (sqlglot) (`duckdb_engine.py:987-1065`)**. **Default-deny is REAL**: `ParseError`/`TokenError`/empty-AST → `raise ValueError` and the query never executes (`:1002-1012`); explicitly no regex fallback. Walks every `exp.Table`; rewrites `pggold.<t>` into `SELECT * FROM pggold.<t> WHERE <rls_clause>` across CTEs/UNION/subqueries, preserving aliases. RLS clause (`_rls_filter_clause:964`): tables without `workspace_id` → `1=0`; with both cols require `tenant=? AND workspace=?`; `workspace_id`-only requires workspace. Admin bypass requires server-built `_server_trusted_context` AND empty tenant/workspace (`:1072-1078`) — a raw `role=admin` body cannot bypass.

**Gate C — HTTP-layer `_require_sql_storage_scope` (`main.py:771-849`)**: blocks `pgdb.` reads, requires SELECT/WITH, blocks `;`/comments, requires `pggold.gold_<dataset>` to be a registered allowed dataset, and rejects readers whose path is not a direct string literal (`:811`).

**load_date injection:** `{latest_date}` from parquet metadata is escaped via `_escape_sql_literal_inner` (quote-doubling) before interpolation (`duckdb_engine.py:1132-1144`); tested at `test_rls_hardening.py:38`.

**Internal bypass (by design):** engine startup `ATTACH`/`INSTALL`/`LOAD` and `_copy_to_parquet` raw `con.execute(COPY ...)` skip Gate A — but the SQL they execute was already validated (materialize calls `_validate_safe_sql` at `:1273`) or is engine-constructed, not user-supplied. Comment at `:790-793` documents this intentionally.

**Bypass assessment:** No user-reachable raw-execute path skips both Gate A and Gate B. String-concat risk is bounded: dataset SQL is validated, scope fragments are regex-validated (`_safe_scope_segment:311`), identifiers gated by `SAFE_IDENTIFIER_RE`. Residual: Gate A is a denylist (theoretically incomplete), but Gate B's parse-or-deny + Gate C's allowlist (`pggold.gold_<dataset>` only) compensate. **Coverage is strong** — `test_rls.py` (40+ cases), `test_rls_hardening.py` (CTE/UNION/subquery/alias/parse-fail-deny/no-tenancy-deny), `test_llm_sql_hardening.py`. No P0 bypass found.

---

## 4. DuckDB limits & parquet pin

- **Memory pin 512MB: REAL.** `DUCKDB_MEMORY_LIMIT: ${DUCKDB_MEMORY_LIMIT:-512MB}` + `DUCKDB_THREADS:-2` (`infra/docker-compose.yml:340-341`); applied via `SET memory_limit=...` before `INSTALL httpfs` (`duckdb_engine.py:182-186`). Container `mem_limit: ${REFINEMENT_MEM_LIMIT:-1g}` (`:314`). Validator rejects injection (`_duckdb_memory_limit_from_env:64`, regex `:40`). Tested: `test_duckdb_resource_limits.py:31-65` (accepts `512MB/1GB/1024MiB`, rejects `1; DROP`).
- **Statement timeout:** 30s watchdog via `threading.Timer(con.interrupt)` (`duckdb_engine.py:838,883`) + 10k-row preview cap (`:828`).
- **Parquet pin date `2024-12-18`: NOT FOUND.** `grep` across `*.py/*.yaml/*.sh/*.tf/*.toml` returned nothing. The only date pin is the single hardcoded `WHERE load_date='2026-04-16'` in `empleados_maestro.yaml`. Silver datasets read all partitions. **Claim of a fixed parquet pin date is unsubstantiated.**

---

## 5. Gold readiness — real green or fake green?

**Mostly honest green** (`console/app/services/intelligence/readiness.py`):
- `_gold_counts` (`:172`) resolves a tenant/workspace scope (falls back to oldest workspace `_default_workspace_scope:36`), sets `app.tenant_id/workspace_id` via `set_config` (`:192`), then `COUNT(*)` the scoped Gold table (`:208-217`). Status `ready` **only when `row_count > 0`** else `empty/missing/invalid` (`:229-236`).
- `intelligence_readiness` (`:286`) returns `up` only if no required dataset is missing; with `require_data` and zero contracts → `degraded` (`:300`). No blanket "always green."
- gold_fetcher read path is consistent (404 if table absent, 403 if unscoped).

**Soft spot (P2):** when the pggold table has `row_count<=0` but `silver_lineage` has a gold row with `row_count>0`, readiness reports `ready` from `silver_lineage` (`:219-227`, `:180`). Because lineage writes are best-effort and never deleted on Gold drop by refinement, a dropped/emptied Gold table can still read **green** off a stale lineage row → divergence between "readiness says ready" and "table actually has rows." (Seed scripts mitigate by rewriting lineage, but ad-hoc drops do not.)

---

## 6. Airflow DAG inventory

`_secrets.py`: `get_secret(name, env=...)` prefers **process env** (Docker/K8s secret) over `Variable.get()` (legacy Airflow metadata DB); raises if neither resolves (`airflow/dags/_secrets.py:28-55`). Per-pair keys via `INTERNAL_API_KEY_AIRFLOW_TO_<TARGET>`; legacy `INTERNAL_API_KEY` fallback **disabled in production** (`_internal_key`, e.g. `dataset_refresh_chain.py:100-108`).

| DAG | Purpose | Schedule | Connected to | Real / no-op |
|---|---|---|---|---|
| `entity_scheduler` | Meta-scheduler: reads `entity_config`, fires due DAGs by cron | `*/5 * * * *` | Airflow REST (basic auth), Postgres, mcp-infra | **Real**. Note: actively **un-pauses** the target DAG (`is_paused:False`) before triggering (`entity_scheduler.py:218-223`). |
| `dataset_refresh_chain` | Topo-orders `datasets.sources` graph, calls `materialize` per dataset | `None` (triggered) | Refinement `/mcp/invoke`, mcp-infra | **Real**. Requires tenant+workspace scope (`:182`), validates cartridge boundary (`:185`). |
| `agent_runner` | Invokes scheduled agents via console REST | `*/5 * * * *` | console `/api/agents/<id>/invoke/scheduled` (shared token), mcp-infra | **Real** (needs `AGENT_RUNNER_TOKEN`). |
| `file_ingest` | Generic file → parquet Bronze ingest | `None` (via entity_scheduler) | MinIO (Variable creds), mcp-infra | **Real**; `noop` path only when no files match (`:341`). |
| `replicon_ses_inbox_import` | SES inbound email S3 → extract attachments → uploads/ | `*/15 * * * *` | AWS S3 (boto3), mcp-infra | **Real** external service. |
| `replicon_outlook_audit_report_import` | Outlook IMAP → ZIP/CSV → MinIO → Bronze | `None` (triggered) | Outlook IMAP (imaplib), MinIO | **Real** external service. **No `is_paused_upon_creation`** set (default Airflow behavior). |

**Pause flags (P1 finding):** all scheduled DAGs read `is_paused_upon_creation=_pause_scheduled_dag_on_creation()` from `AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION` / `AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION` (e.g. `entity_scheduler.py:60-78`). **But every live compose sets this to `"false"`**: `infra/docker-compose.yml:1047,1164`, `infra/terraform/deploy/docker-compose.aws.yml:639,672,755`. Only `docker-compose.dev.yml:13,17` allows override (default still false). So the "release-gate pause" plumbing exists but **is inert in production** — DAGs are created **unpaused**. Additionally `entity_scheduler` force-unpauses targets, so even a manually-paused base DAG would be re-enabled. The recent commits ("Pause scheduled Airflow DAGs in release gate", "Read Airflow pause config in scheduled DAGs") added the *capability* but did not flip the compose default.

---

## 7. Freshness router — real or static?

**Real.** `console/app/routers/freshness.py` queries live `entity_config` ⋈ `entity_watermarks` ⋈ `extraction_runs` (LATERAL top-1 by `started_at DESC`), computing `age_seconds = now() - COALESCE(watermark, last_run)` (`:45-81`). Internal variant `freshness_for_cartridge_internal` is auth-free for copilot citation cards (`:38`). No hardcoded/static timestamps.

---

## 8. Seeds — demos seeded as if real?

- `scripts/seed_replicon_beta_gold.py`: creates `public.gold_<dataset>` with `tenant_id/workspace_id UUID NOT NULL` (`:737-744`), DELETE+INSERT scoped rows (`:776-794`), applies `omega_apply_gold_rls_for_table` (`:794`), GRANTs to `omega_gold_reader` (`:767`), **writes `silver_lineage` rows** (`layer='gold'`, storage_uri with `tenant_id=.../workspace_id=...`) (`:873-916`), upserts `datasets` + `data_catalog` (`:799,918`). Vault marker is **honest**: `status:"data_seed_only"`, `write_back_enabled:False`, `auth_method:"seeded_gold"` (`:26-33`).
- `scripts/seed_intelligence_gold_prod_like.py`: 4 Gold tables (`forecast_mensual`, `deals_estancados`, `pipeline_salud`, …), scoped + RLS-applied (`:350-373`). Docstring states "local/prod-like fixture, **not provider data**" (`:2-6`).
- `generate_seed_from_aws.py` exists at repo root (5.4KB).
- `refinement/scripts/materialize_successfactors_foundation.py` + `scripts/materialize_successfactors_foundation.py` present.

**Finding (P2):** seeds insert into the **exact** Gold tables + lineage + catalog that the readiness gate and gold_fetcher consume. So a seeded workspace reports `ready`/green and serves data **identically to a real extraction**. Provenance ("seeded vs extracted") is only discoverable via the vault marker `status:"data_seed_only"`, which readiness does NOT surface. Demos are, by design, indistinguishable from real data at the consumption layer.

---

## 9. `infra/init_gold/` — RLS verification

- `00_schema.sql`: creates **no** Gold tables (only `CREATE SCHEMA replicon`); explicitly "tables created by cartridges/refinement at runtime." So statically **zero** `gold_*` tables exist; they are created on first materialize/seed as `public.gold_<dataset>`.
- `34_postgres_gold_role.sql`: creates least-privilege `omega_refinement_gold` LOGIN role with DML on `public`+`replicon`; refuses empty password (`:11-13`). **Not** superuser.
- `35_gold_native_rls.sql`: **`ALTER ROLE omega_refinement_gold NOBYPASSRLS` (`:7`)** ✓. `omega_apply_gold_rls_for_table` (`:23`) does `ENABLE` + **`FORCE ROW LEVEL SECURITY` (`:58-59`)** ✓ and creates a USING+WITH CHECK policy on `omega_gold_workspace_matches(tenant,ws)`; **legacy/unscoped tables (no tenant/ws cols) get `USING(false) WITH CHECK(false)` deny-without-scope (`:70-78`)** ✓. `omega_gold_workspace_matches` requires non-empty `app.tenant_id` AND `app.workspace_id` and exact match (`:9-21`). Startup loop applies RLS to any existing `gold\_%` table (`:81-93`).

**Verdict:** FORCE RLS ✓, NOBYPASSRLS ✓, deny-without-scope ✓. Database-level backstop is genuinely correct. (Caveat: enforced only on the `omega_refinement_gold` role; a superuser/`postgres` connection would still bypass FORCE RLS — but refinement does not use it for Gold.)

---

## 10. Mocked / skipped / writes-to-nowhere scan

- No `NotImplemented`/TODO/FIXME in the materialization path. `no-op` hits in `main.py:1756,1758,1950` are deprecated table-ensure shims kept for API compatibility (tables provisioned by `infra/init/`), not pipeline gaps. `file_ingest.py:341` noop = legitimate "no files."
- `00_schema.sql:16` "Placeholder" is intentional (runtime-created tables).
- No silver/gold write path discovered that discards output. `_copy_to_parquet` always uploads or raises; `_replace_scoped_gold_rows` commits or rolls back.
- Only silent-failure surfaces: `_write_lineage` (`:1443`), `_update_catalog` (`:1410`), `_prune_snapshots` (`:677`), `setup` ATTACH (`:218-225`) all swallow exceptions — degrade observability, not correctness of the row write.

---

## Findings (P0–P3) + estado

| ID | Sev | Finding | Evidence | Estado |
|---|---|---|---|---|
| F1 | **P1** | Scheduled DAGs ship **unpaused** in prod despite release-gate pause plumbing; `entity_scheduler` also force-unpauses targets. Pause capability is inert. | compose `:1047,1164`; aws `:639,672,755` = `"false"`; `entity_scheduler.py:218-223` | ABIERTO |
| F2 | **P2** | Readiness can report `ready` from a stale `silver_lineage` row even when the pggold Gold table is empty/dropped (lineage best-effort, not cleaned on drop). | `readiness.py:180,219-227`; `duckdb_engine.py:1443` | ABIERTO |
| F3 | **P2** | Seeded demos are indistinguishable from real extractions at readiness/consumption; provenance only in vault marker `data_seed_only`, not surfaced. | `seed_replicon_beta_gold.py:776-916`; `readiness.py`; vault marker `:26-33` | ABIERTO (by design) |
| F4 | **P2** | No `2024-12-18` (or any) parquet pin date exists; `_latest` silver datasets read full history (`**/*.parquet`), not the latest partition — contradicts the documented Silver latest-date rule. | grep miss; `replicon_*_latest.yaml`; `llm_sql.py:54` | ABIERTO |
| F5 | P3 | `empleados_maestro` Gold SQL hardcodes `WHERE load_date='2026-04-16'` instead of `{latest_date}`; will silently go stale as new partitions land. | `empleados_maestro.yaml` sql | ABIERTO |
| F6 | P3 | Lineage/catalog/prune/ATTACH failures are silently swallowed (`except: pass`), reducing observability of partial materializations. | `duckdb_engine.py:1410,1443,677,221` | ABIERTO |
| F7 | P3 | FORCE/NOBYPASSRLS enforced only for `omega_refinement_gold`; a superuser/`postgres` Gold connection would bypass RLS (not used today, but no guard). | `35_gold_native_rls.sql:7,58-59` | INFORMATIVO |
| OK1 | — | SQL guard: regex denylist + sqlglot AST **default-deny** (parse-or-reject) + HTTP allowlist; no user-reachable double-bypass; 40+ RLS tests. | `duckdb_engine.py:809,1002-1012`; `test_rls*.py` | VERIFICADO |
| OK2 | — | DuckDB 512MB memory pin real, applied pre-extension-load, validated, tested. | `docker-compose.yml:340`; `duckdb_engine.py:182`; `test_duckdb_resource_limits.py` | VERIFICADO |
| OK3 | — | Native Gold RLS (FORCE + NOBYPASSRLS + deny-without-scope) and least-priv role correct. | `infra/init_gold/35_gold_native_rls.sql`, `34_*.sql` | VERIFICADO |
| OK4 | — | Freshness router is live SQL over watermarks/extraction_runs, not static. | `freshness.py:45-81` | VERIFICADO |

**Bottom line:** the data plane is real and the security guards (AST default-deny, FORCE RLS, NOBYPASSRLS, scoped Gold writes via psycopg2) are implemented correctly and tested. The credibility gaps are operational/release-gate posture (F1 unpaused DAGs), readiness honesty edge cases (F2 lineage fallback, F3 seed indistinguishability), and the absent/contradicted parquet "latest" pin (F4/F5) — none of which are pipeline-is-fake P0s.
