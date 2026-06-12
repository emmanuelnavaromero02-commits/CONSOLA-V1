# Control Room Decision Intelligence v0 - Local Validation

Generated: 2026-06-12T23:34:39Z

Branch: `feat/control-room-decision-intelligence-v0`

Version: `1.45.71-beta`

Scope:
- Adds Decision Intelligence v0 to Control Room signals generated from Gold/Replicon-compatible intelligence artifacts.
- Adds honest `insufficient_history` handling and reserved future-forecast handling with no forecast anomaly probability.
- Exposes validated Decision Intelligence payloads in Control Room API/UI without wiring v0 options into execution actions.

Local checks:
- PASS: `ruff check console workspace vault refinement mcp-infra cartridges airflow`
- PASS: `bandit -r console workspace vault refinement mcp-infra cartridges --severity-level medium --confidence-level high`
- PASS: `pip-audit` across every `requirements.txt` with the existing documented ignores from the security workflow.
- PASS: `npm --prefix console-next audit --audit-level=high`
- PASS: `npm --prefix tests-e2e audit --audit-level=high`
- PASS: `python -m py_compile` for touched Python modules/tests.
- PASS: `pytest -q console/tests/test_intelligence_engine.py console/tests/test_control_room_gold_signal_flow.py tests/test_intelligence_engine_contract.py` (`38 passed`).
- PASS: `pytest -q console/tests/test_control_room_service.py console/tests/test_control_room_persistent_cycle.py console/tests/test_control_room_permissions.py console/tests/test_control_room_router_cache.py` (`83 passed, 1 warning`).
- PASS: `pytest -q console/tests tests/test_beta_smoke.py tests/test_aws_beta_operations.py tests/test_replicon_beta_gold_seed.py tests/test_gold_native_rls_contract.py tests/test_operational_native_rls.py` (`863 passed, 1 warning`).
- PASS: `npm --prefix console-next run lint`
- PASS: `npm --prefix console-next run typecheck`
- PASS: `npm --prefix console-next run test:coverage` (`21 files / 79 tests passed`).
- PASS: `NODE_OPTIONS=--max-old-space-size=3072 npm --prefix console-next run export:copy`
- PASS: AWS compose config with non-secret placeholder values for required interpolation variables.
- PASS: `git diff --check`

Known local note:
- `ruff format --check` over the full repo still reports historical formatting drift and is advisory/`continue-on-error` in `.github/workflows/lint.yml`. Touched files were format-checked earlier in the sprint.

AWS status:
- NOT RUN for `v1.45.71-beta` yet.
- Existing AWS evidence in the repo is for prior releases and is not proof for Decision Intelligence v0.
- AWS must be updated only after PR merge to `main`, release tag/image publication, and SSM deploy from the merged ref.
