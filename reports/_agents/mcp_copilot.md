# MCP / Agents / Copilot Audit — OMEGA/CONSOLA-BETA

Scope: `mcp-infra/` (:8010), `console/app/routers/copilot*`, `mcp.py`/`mcp_public.py`,
copilot/LLM/tool_policy services, `airflow/dags/agent_runner.py`. main HEAD bab2a4f, VERSION 1.45.68-beta.
Method: read-only source review, file:line evidence. No servers/docker/external calls.

Provider check: only Anthropic + Ollama in scope. `grep openai|langchain|genai|mistralai|cohere`
hits `console/app/services/llm_client.py` (uses `openai` SDK only as the **Ollama** OpenAI-compatible
client, line 20/184-188) and `copilot_service.py`. No Gemini/OpenAI/etc. as real providers.

---

## 1. Copilot pipeline — step-by-step verification

Entry: `POST /api/copilot/conversations/{cid}/messages` (and `/chat/{cid}/stream`) →
`copilot_service.run_turn` → `_run_loop` → `llm_client.chat`.

| # | Step | Status | Evidence |
|---|------|--------|----------|
| 1 | User → `/api/copilot` (auth + CSRF + `copilot.use`) | REAL | `copilot.py:33` router-wide `require_permission("copilot.use")`; `:79,128,189,254` `require_csrf`; `copilot_service.py:1566` re-checks `copilot.use` |
| 2 | Persist user turn, ownership check | REAL | `copilot_service.py:1559-1586` (`conv["user_id"] != user.id` → 403) |
| 3 | LLM proposes tool_use (Anthropic) | REAL | `llm_client.py:362-470` `_anthropic_chat`, streamed, 20-iteration tool loop; tool_use blocks detected by content not stop_reason (`:428-437`) |
| 4 | tool_policy validates args (schema + injection + limits) | REAL | `copilot_service.py:1122-1128` calls `tool_policy.validate_tool_args`; `tool_policy.py:100-209` |
| 5 | RBAC by risk (use/write/execute) | REAL | `copilot_service.py:1115-1163` risk→perm map `_PERMISSION_BY_RISK`; `permissions.has_permission(user, needed)` → `permission_denied` envelope |
| 6 | Approval gate for write/destructive | REAL | `copilot_service.py:1165-1191` `needs_approval` (risk==destructive OR requires_approval OR manifest); returns `approval_required` envelope, stores pending in `conversation_messages.tool_calls` |
| 7 | MCP server executes | REAL | `copilot_service.py:1197` `_invoke_tool_with_retry` → `mcp_registry.invoke` (`mcp_registry.py:541-587`) POSTs `/mcp/invoke` |
| 8 | Citations + freshness | REAL | `copilot_service.py:1211-1219` `_extract_citations` (from `_meta`/`runs`); `:365-419` `_annotate_citation_freshness` via `freshness_for_cartridge_internal` (real SQL, `freshness.py:38`) |
| 9 | audit_events written | REAL | `copilot_service.py:713-760` `_audit` → `audit_service.record_event` with `critical=True` (fail-closed); covers denied/pending/error/success |
| 10 | Rejected/denied returned to LLM as tool_result | REAL | denied→`{"error":"tool_args_rejected"}` (`:1137`), RBAC→`permission_denied` (`:1155`), gate→`approval_required` (`:1182`), tool error→envelope (`:1199-1207`). All fed back as `tool_result` content (`llm_client.py:451-464`) |
| 11 | Approve → server-side execution | REAL | `copilot_service.py:1682-1797` atomic claim `UPDATE ... WHERE tool_results IS NULL` (`:1742`), executes captured calls server-side (`_execute_approved_tool_calls :836-962`), re-audits, then resumes LLM |

Hallucination guard (`:168-190`, hedged-number regex when no citations) and multi-source cap
(`:1438-1469`, max 3 distinct servers) are real, server-enforced. Verdict: **pipeline is real end-to-end**, not staged.

---

## 2. LLM integration

- Provider/SDK: **Anthropic `AsyncAnthropic`** (`llm_client.py:18,175-181`), real `messages.stream`. Ollama via `AsyncOpenAI` OpenAI-compat for local dev only (`:184-188,526-605`).
- Models: default `claude-haiku-4-5-20251001` (`:31`), agents default `claude-sonnet-4-6` (`agent_runtime.py:107`, `17_agents.sql:24`, migration `68`). `gemini`/stale models coerced back to Anthropic (`:56-58,160-171`).
- Mock/fake LLM mode: **none in product code.** Tests monkeypatch `_llm_single_shot`/`chat` (`copilot_drafts.py:242`) — not a runtime stub.
- API key wiring: REAL. Platform-admin uses `ANTHROPIC_API_KEY` env (`:133-136`); tenant/workspace users resolve a per-workspace key from Vault (`:124-132`).
- Without key: raises `LLMConfigurationError` (`:135`) → caught in `_run_loop` (`copilot_service.py:1311`) → persisted warning + HTTP 502. **No silent fake answers.** Token usage recorded (`token_store.record`) but recording-only.

---

## 3. MCP-infra tools inventory (all `@tool` in `mcp-infra/app/tools/`)

Dispatch: `registry.py:36-42` calls the real Python fn. **Every tool is a real implementation** — no echo/stub tools found.

| Tool(s) | Claims | Does | Real? | Evidence |
|---|---|---|---|---|
| airflow_list_dags / get_run_status / get_task_logs / list_task_instances / list_dag_runs | Airflow read | Airflow REST v1 GET | REAL | `airflow.py:115,202,230,542,576` |
| airflow_trigger_dag | fire DAG | REST POST dagRuns, idempotent 409 replay | REAL | `airflow.py:150-187` |
| airflow_create_dag / delete_dag / set_variable | RCE-shaped writes | write .py to DAG dir / DELETE / Variables API | REAL, **double-gated** | `airflow.py:271-527`; both `_is_development()` AND `_rce_tools_explicitly_enabled()` (`:281-291,411-421,501-511`) |
| postgres_list_schemas/tables/get_table_schema/get_sample/execute_query | PG read | psycopg2 information_schema + capped SELECT | REAL | `postgres.py:121-315`; multi-stmt/comment guards (`:210-255`) |
| postgres_execute_ddl | non-destructive DDL | allowlisted CREATE only | REAL | `postgres.py:275-282`, regex allowlist `:68-105` |
| minio_list_objects/get_parquet_schema/get_sample_rows/upload_spec/list_cartridge_specs/read_spec | lakehouse | MinIO SDK + pyarrow, bounded reads | REAL | `minio.py:79-253` |
| superset_list_* | Superset read | REST v1 GET (login token) | REAL | `superset.py:64-75,...` |
| superset_create_database/dataset/chart/dashboard/import_dashboard | Superset write | REST POST | REAL, **dev-gated only** | `superset.py:94,146,207,269,336` `_is_development()` — see P1 |
| search_rag / list_rag_sources / ingest_document | RAG | pgvector ANN + Bedrock embed | REAL | `rag.py:41-89`, `store.py:109-160`, `embeddings.py` |
| vault_list/get/set/delete_connection, list/set_secret | Vault creds | HTTP to vault:8300, masked reads | REAL | `vault.py:55-70,93-...` |
| agent_list/get/create/update/delete | agent CRUD | direct SQL on `agents` | REAL | `agents.py:165-455` |
| cartridge_extract / extract_all | fire entity DAGs | Airflow REST POST per entity | REAL | `cartridges.py:818-913` |
| cartridge_preview / query_kb | DuckDB read | DuckDB read_parquet on s3 | REAL | `cartridges.py:776-815,1341-1390` |
| cartridge_run_kb | KB → Silver | DuckDB exec + MinIO/PG write | REAL | `cartridges.py:1167-...` |
| cartridge_get_semantic/manifest/hints/schema/list_entities/run_logs/job_status/list_jobs/list_kbs/search_term/sync_semantic_to_rag | metadata | SQL/MinIO reads | REAL | `cartridges.py` |
| watermark_get/set, pipeline_run_save, dag_save_source/get_source | pipeline state | SQL upserts | REAL | `pipeline.py:34-...` |
| request_admin_help | email admin | SMTP send | REAL | `admin_request.py:49-125` |

RCE gating confirmed prod-safe: `infra/docker-compose.yml:139,263,767` `ALLOW_RCE_TOOLS: ${ALLOW_RCE_TOOLS:-false}`; `infra/.env.example:261=false`; E2E/release flip it true (`.github/workflows/e2e.yml:29`, `Makefile:9`). Tests assert prod-off (`tests/test_airflow_create_dag_disabled_in_prod.py`).

---

## 4. tool_policy (`console/app/services/tool_policy.py`)

- **Args validation: REAL** (not cosmetic). `validate_tool_args` (`:100`): JSON-object check, recursive depth/size guards (`:119-139`: depth 8, 200 keys, 500 array items, 8 000-char strings, 16 KB total), backend-context key rejection (`:142-152`, blocks `security_context`/`_trusted_admin`), then **schema validation** against the live MCP `input_schema` — required keys (`:171-175`), type checks incl. int/bool disambiguation (`:185-206`), enum membership (`:207-209`).
- **Injection guard: REAL but shallow.** Single regex `_PROMPT_INJECTION_RE` (`:40-45`, ignore/bypass/override + system/approval/policy, exfiltrate/reveal-secret). Only applied when `risk_level != "read"` (`:113-114`). Regex-only → bypassable by paraphrase; defense-in-depth, not a hard wall (P2).
- **Limits/quotas:** per-call arg-size limits real (above). **No rate/cost quota** on tool calls or LLM spend (see P1).

---

## 5. Approval gates + persistence

| Surface | Persisted where | Who approves | Blocks until approved? | Evidence |
|---|---|---|---|---|
| Copilot | `conversation_messages.tool_calls` JSONB (38_copilot_conversations) | conversation owner via `POST .../approve/{mid}` (`copilot.py:253`) | YES — atomic claim `UPDATE...WHERE tool_results IS NULL` then server-side exec | `copilot_service.py:1173-1191,1703-1767` |
| Workflows | `workflow_steps.status='waiting_approval'`, `result.approved` (53/56 migrations) | `copilot.execute` via `/steps/{idx}/approve` | YES — `_is_approved` reads **server state, never args** (`workflow_executor.py:69-74`); `approve_step` writes `approved=true` server-side (`:694-711`) | `workflow_executor.py:467-500,676-727` |
| Agents | run-time only (no approval table) | — | Manual write/destructive → `approval_required` envelope but **no approve endpoint exists** | `agent_runtime.py:430-450`; scheduled writes hard-blocked (`:420-425`) |

Audit trail: every tool call (copilot/agent/workflow) writes `audit_events` with `tool_name`,
`tool_args` (scrubbed+clipped), `risk_level`, `status`, `conversation_id`, `critical=True`
(`audit_service.py:47-117`; fail-closed `:90-91`). Secret scrubbing real (`copilot_service.py:530-542`,
`tool_policy.scrub_args`). Verdict: **approval gates are real DB state, not frontend-only.**

---

## 6. RAG

- pgvector: REAL — `store.py:109-160` cosine distance `c.embedding <=> $1`, parent/child chunking, `register_vector`.
- Embeddings: **AWS Bedrock Titan v2** via boto3 (`embeddings.py:16,48-113`), `EMBED_MODEL=amazon.titan-embed-text-v2:0`, **`EMBED_DIM=1024`** (`rag/config.py:30-32`). Migration `60_rag_embedding_dim_768.sql` (legacy Gemini era) superseded by `71_rag_embedding_dim_1024.sql` which resets vectors to `vector(1024)`. No Gemini code remains.
- Citations: REAL retrieval, not canned — harvested from real tool `_meta`/`runs` (`copilot_service.py:271-330`), scoped/filtered per tenant (`mcp-infra/app/main.py:971-982` `_filter_rag_payload`). No credentials → `EmbeddingProviderError` → 503 (`main.py:1212-1213`), never fabricated results.

---

## 7. agent_runner DAG + agents

- DAG real: every 5 min, queries `agents WHERE extra ? 'schedule'`, cron-matches window (`agent_runner.py:148-194`), POSTs `/api/agents/{id}/invoke/scheduled` with `X-Agent-Runner-Token` + internal key (`:199-232`), records `pipeline_run_save` (`:249-288`).
- Endpoint real: `agents.py:140-162` verifies runner token (`secrets.compare_digest`), checks schedule due, runs `agent_runtime.run` (user=None).
- `agent_runtime.run` (`:657-770`): real LLM loop, live tool discovery from `/mcp/tools` (`:195-246`), per-call RBAC + policy + approval (`_make_invoke :345-495`), persists `agent_runs`, audits. **Agents genuinely run.** Scheduled agents are read-only (writes blocked `:420-425`); manual writes only ever return `pending_approval` (no executor → P2 gap).
- Tables seeded: `17_agents.sql` (agents/agent_runs), `68` (model default), plus per-cartridge seeds (`84/86/88/93/94_*`). Real rows.

---

## 8. Copilot workflows

- Executor is REAL (`workflow_executor.py:343-631`): sequential, DB-claimed steps (`_claim_step` ordered, prior-steps-complete predicate `:242-268`), real `mcp_registry.invoke` with `_invoke_with_retry` (3 attempts, backoff, timeout, schema-validated `:301-340`), fail-fast + `_mark_remaining_skipped`, cancellation re-checked each step. **Not faked progress** — `step_results` reflect real tool output (`:533-562`).
- Planner is REAL LLM call (`copilot_workflows.py:415-444` `_llm_plan` → `llm_client.chat`), grounded on live tool list (`:477-497`), with destructive deny-list both in prompt and parser (`:317-368`) and server-side `_validate_planned_steps` forcing write/destructive → human-review (`:371-412`).

---

## 9. Faked / hardcoded / TODO in scope

- **No fabricated tool results found.** No success-fixing, no canned data, no `NotImplemented` in the live paths.
- **Stale/misleading docstrings (P3):** `copilot_drafts.py:86` ("does NOT call the LLM… next-session"), `copilot_memory.py:11`, `copilot_workflows.py:7`, `53_copilot_workflows.sql:7-11` all claim LLM integration is pending — but it is **implemented** (`/drafts/generate :264`, `/workflow/{id}/plan :447`, `/execute`, memory extraction `copilot_service.py:1507-1535`). Documentation rot, not missing code.
- `airflow_create_dag` DB registration is best-effort `except: pass` (`airflow.py:388-390`) — DAG file is written even if `cartridge_dags` insert fails (P3, consistency).

## 10. mcp_public.py — what's exposed unauthenticated

**Despite the filename, nothing is unauthenticated.** `mcp_public.py:45-49` gates the whole `/api/mcp` router with `Depends(require_admin)` (browser/session + admin RBAC), CSRF on mutations. `mcp.py` (`/internal/mcp`) requires `verify_internal_api_key` + trusted signed `security_context`. The only truly public mcp-infra endpoints are `/health`, `/healthz`, `/readyz` — and they are sanitized (tool count redacted, `main.py:1301-1307,1289-1298`). `/mcp/tools` and `/mcp/invoke` require `x-api-key` + `x-internal-service` (`main.py:134-160,1185,1191`).

---

## Findings P0–P3

| ID | Sev | Finding | Evidence | Estado |
|----|-----|---------|----------|--------|
| F1 | **P1** | Superset write tools (`create_database/dataset/chart/dashboard/import_dashboard`) gated by `_is_development()` only — NOT the `ALLOW_RCE_TOOLS` double-gate that protects airflow. `create_database` accepts arbitrary `sqlalchemy_uri` (SSRF/cred surface). Mitigated by admin `studio.write` ctx at boundary (`main.py:1090-1092`) + prod default `APP_ENV=production`, but inconsistent hardening vs airflow. | `superset.py:94,146,207,269,336` | Abierto |
| F2 | **P1** | No cost/rate quota on copilot or LLM. `token_store.record` only records usage (`token_store.py:67-92`); `rate_limiter` exists but is **not applied** to any `copilot*` router. A `copilot.use` holder can drive unbounded Anthropic spend / tool fan-out (only the 20-iteration and 3-source caps bound a single turn). | `copilot.py`, `copilot_advanced.py` (no limiter dep); `rate_limiter.py` unused there | Abierto |
| F3 | **P2** | Agent manual write/destructive tools return `approval_required` but there is **no agent approval-execution endpoint** (unlike copilot/workflows). Agent-initiated writes can never actually execute → functional dead-end, and approvals are not persisted to a table. | `agent_runtime.py:430-450`; no approve route in `v1/agents.py` | Abierto |
| F4 | **P2** | Prompt-injection guard is a single regex applied only to non-read args; paraphrased injection passes. Real but shallow; relies on backend-owned `security_context` (rejected from args) as the true control. | `tool_policy.py:40-45,113-114,155-165` | Por-diseño / defense-in-depth |
| F5 | **P3** | Misleading "does NOT call the LLM / next-session" docstrings on shipped, LLM-backed endpoints — risks an operator assuming features are stubs. | `copilot_drafts.py:86`, `copilot_memory.py:11`, `copilot_workflows.py:7`, `53_*.sql:7` | Doc rot |
| F6 | **P3** | `airflow_create_dag` writes the DAG file before best-effort DB registration (`except: pass`); a DAG can exist on disk with no `cartridge_dags` row. | `airflow.py:308,388-390` | Abierto |

**Bottom line:** the copilot→LLM→policy→RBAC→approval→MCP→citations→audit pipeline is genuinely
implemented and wired to a real Anthropic SDK and real MCP tools (Airflow/MinIO/PG/Superset/Vault/RAG
all do real work). RCE tools are correctly double-gated and prod-off. Main gaps are cost/rate limiting
(F2), Superset's weaker gating (F1), and the agent-write approval dead-end (F3) — no fabricated results.
