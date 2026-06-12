# Control Room Data Flow Evidence

## Scope

- Branch: `feature/control-room-data-flow-real`
- Base main SHA: `0d7fa445855d7d59dfdd70744ce62a35217fc6eb`
- UTC evidence start: `20260612T200906Z`
- Golden path: Replicon Gold
- Goal: controlled Gold rows -> Intelligence signal -> persisted Control Room item with evidence, freshness, source, tenant and workspace scope.

## Focused Discovery

- Control Room endpoints: `/api/control-room/dashboard`, `/api/control-room/summary`, `/api/control-room/alerts`.
- Intelligence endpoints: `/api/intelligence/run`, `/api/intelligence/signals`, `/api/intelligence/signals/{signal_id}`, `/api/intelligence/readiness`.
- Signal generation: `console/app/services/intelligence/engine.py::run_intelligence`.
- Gold read path: `console/app/services/intelligence/gold_fetcher.py::query_gold_dataset_rows`.
- Artifact/evidence path: `console/app/services/intelligence/baseline.py::build_metric_artifacts` and `console/app/services/intelligence/evidence.py::dataset_evidence_pack`.
- Persistence path: `console/app/services/intelligence/persistence.py::persist_artifacts`.
- Control Room persisted signal read path: `console/app/services/control_room/api.py::_persisted_intelligence_items`.
- Runtime tables: `intelligence_signals`, `evidence_packs`, `evidence_items`, `control_room_items`, `silver_lineage`, and Replicon Gold tables such as `gold_consultor_mensual`.
- Missing dataset behavior: `run_intelligence` records `status=dataset_unavailable` when the fetcher raises instead of inventing a signal.

## Local Results

| Command | Result |
|---|---|
| `/Users/emmanuel/CONSOLA-BETA/.venv/bin/ruff check console/app/services/intelligence/evidence.py console/app/services/intelligence/baseline.py console/app/services/intelligence/persistence.py console/app/services/control_room/api.py console/tests/test_control_room_gold_signal_flow.py console/tests/test_intelligence_employee_owner_runtime.py` | PASS |
| `/Users/emmanuel/CONSOLA-BETA/.venv/bin/python -m py_compile console/app/services/intelligence/evidence.py console/app/services/intelligence/baseline.py console/app/services/intelligence/persistence.py console/app/services/control_room/api.py console/tests/test_control_room_gold_signal_flow.py console/tests/test_intelligence_employee_owner_runtime.py` | PASS |
| `PYTHONPATH=console /Users/emmanuel/CONSOLA-BETA/.venv/bin/pytest -q console/tests/test_control_room_gold_signal_flow.py` | PASS, 4 passed |
| `PYTHONPATH=console /Users/emmanuel/CONSOLA-BETA/.venv/bin/pytest -q console/tests/test_control_room_gold_signal_flow.py console/tests/test_intelligence_engine.py console/tests/test_control_room_service.py` | PASS, 84 passed |
| `PYTHONPATH=console /Users/emmanuel/CONSOLA-BETA/.venv/bin/pytest -q tests/test_beta_smoke.py tests/test_replicon_beta_gold_seed.py tests/test_aws_beta_operations.py tests/test_production_readiness_gate.py` | PASS, 29 passed |
| `PYTHONPATH=console /Users/emmanuel/CONSOLA-BETA/.venv/bin/pytest -q console/tests` | PASS, 820 passed |
| `docker compose -f infra/terraform/deploy/docker-compose.aws.yml config --quiet` with required variables set to dummy values in-process | PASS |
| `docker compose -f infra/docker-compose.yml config --quiet` with required variables set to dummy values in-process | PASS |
| `git diff --check` | PASS |

## Signal Contract Proven Locally

The new focused test uses the real Replicon intelligence contract and controlled scoped rows for `consultor_mensual`. It verifies:

- `source_system=replicon`
- `dataset=consultor_mensual`
- `gold_table=gold_consultor_mensual`
- `freshness_at=2026-06-01`
- evidence item query reads `gold_consultor_mensual`
- evidence contains row count and sample hash
- Control Room item contains `tenant_id`, `workspace_id`, `source_system`, `dataset`, `gold_table`, `freshness_at`, `evidence_pack_id`, and `evidence_pack`
- persisted Control Room query filters by both `workspace_id` and `tenant_id`
- missing Gold raises `dataset_unavailable` and returns no invented signals

## AWS Status

- PR: pending
- main SHA final: pending
- AWS deploy SHA: pending
- instance id: pending
- region: pending
- public URL: pending
- seed/materialization: pending after merge
- beta-smoke-aws: pending after merge
- tenant-ab-aws: pending after merge
- full console regression: pending after merge

