PYTEST ?= $(shell if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
RUFF ?= $(shell if [ -x .venv/bin/ruff ]; then echo .venv/bin/ruff; else echo ruff; fi)
BANDIT ?= .venv/bin/bandit
PIP_AUDIT ?= .venv/bin/pip-audit
COMPOSE_BASE ?= docker compose -f infra/docker-compose.yml
COMPOSE_DEV ?= $(COMPOSE_BASE) -f infra/docker-compose.dev.yml
COMPOSE_FULL ?= $(COMPOSE_DEV) --profile sap
E2E_STACK_ENV ?= APP_ENV=development ALLOW_RCE_TOOLS=true
TEST_COMPOSE ?= infra/docker-compose.test.yml
TEST_COMPOSE_PROJECT ?= omega-hermetic-test
MOCK_MCP_PORT ?= 18010
MOCK_REPLICON_PORT ?= 18201
MOCK_SAP_HCM_PORT ?= 18202
MOCK_SAP_SUCCESSFACTORS_PORT ?= 18203
MOCK_SAP_S4HANA_PORT ?= 18204
TARGET ?= local
WORKLOAD ?= sap_successfactors
PROFILE ?= beta-safe

.PHONY: help bootstrap-env up up-core down nuke repair-local-stack logs ps test baseline-smoke smoke beta-smoke beta-smoke-aws stress stress-smoke stress-beta stress-spike stress-breakpoint stress-soak-24h stress-write-heavy multiuser-simulation tenant-ab-local tenant-ab-aws live-cartridge-tests monitor-check production-readiness v1-live-readiness production-readiness-aws v1-ga-lite-local v1-ga-lite-aws v1-ga-max-aws v1-ga-cleanup v1-ga-report data-integrity-audit copilot-redteam cartridge-resilience chaos-local chaos-aws enterprise-readiness sap-successfactors-aws-live-max dr-rehearsal backup-aws dr-rehearsal-aws rollback-aws rollback-rehearsal rollback-rehearsal-aws deploy-main-aws aws-full-regression aws-observability-report aws-tls-status aws-superset-probe superset-tenant-probe superset-tenant-probe-aws monte-carlo-aws-probe bayesian-calibration-aws-probe bayesian-loop-probe bayesian-loop-probe-aws control-room-gold-engine-aws-probe control-room-mock-volume-aws-probe control-room-mock-volume-aws-cleanup cartridge-kb-scope-aws-probe action-framework-aws-probe decision-orchestrator-aws-probe decision-orchestrator-execution-aws-probe migrate rotate-keys e2e acceptance preflight demo-check security-scan verify-release verify-v1-public seed-intelligence-gold seed-replicon-beta-gold seed-replicon-beta-gold-aws run-intelligence-scheduled-local run-intelligence-scheduled-aws decision-backtest-local decision-backtest-aws
.PHONY: test-hermetic reconcile-db-passwords verify-gcp-edge-tls gcp-release-authority-generate gcp-release-authority-verify gcp-terraform-plan gcp-terraform-apply gcp-terraform-status gcp-terraform-recover gcp-foundation-plan gcp-foundation-apply gcp-foundation-status gcp-foundation-recover gcp-iam-revoke-plan gcp-iam-revoke-apply gcp-iam-revoke-recover

help:
	@echo "MODecissionsPaaS — targets:"
	@echo "  make preflight    check Docker/compose/.env/ports BEFORE 'make up'"
	@echo "  make bootstrap-env"
	@echo "                    generate/ensure infra/.env and pair keys"
	@echo "  make demo-check   preflight + the demo validation order (runbook 09)"
	@echo "  make up           bootstrap secrets and start the full v1.0 stack (SAP profile)"
	@echo "  make up-core      bootstrap secrets and start the core stack without SAP"
	@echo "  make down         stop the stack"
	@echo "  make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev"
	@echo "                    wipe this local stack and volumes only"
	@echo "  make repair-local-stack"
	@echo "                    reconcile stale local DB roles / optional Superset metastore"
	@echo "  make logs         follow service logs"
	@echo "  make ps           list running services"
	@echo "  make baseline-smoke"
	@echo "                    fast hermetic baseline check (no Docker, <1 min)"
	@echo "  make test         run the python test suites"
	@echo "  make test-hermetic"
	@echo "                    run tests/ against isolated mock services"
	@echo "  make smoke        run end-to-end smoke checks against a running stack"
	@echo "  make beta-smoke   run strict beta gate: smoke + Gold/lineage/RLS/readiness"
	@echo "  make beta-smoke-aws"
	@echo "                    run read-only AWS beta gate via SSM with ALB+internal checks"
	@echo "  make stress       run Locust stress profile against the running stack"
	@echo "  make stress-smoke run 25-user/5m smoke load with p95/p99 summary"
	@echo "  make stress-beta  run beta load profile with p95/p99 summary"
	@echo "  make stress-spike run controlled spike profile with p95/p99 summary"
	@echo "  make stress-breakpoint"
	@echo "                    run breakpoint profile until thresholds expose capacity"
	@echo "  make stress-soak-24h"
	@echo "                    run 24h soak profile (guarded by operator env)"
	@echo "  make stress-write-heavy"
	@echo "                    run write-heavy profile and require data audit evidence"
	@echo "  make data-integrity-audit"
	@echo "                    run enterprise post-test data/RLS/Gold/parquet audit"
	@echo "  make copilot-redteam"
	@echo "                    run adversarial Copilot/MCP live gate or BLOCKED evidence"
	@echo "  make cartridge-resilience"
	@echo "                    run cartridge contract/resilience gauntlet"
	@echo "  make enterprise-readiness"
	@echo "                    run OMEGA 20x enterprise gate (TARGET/WORKLOAD/PROFILE)"
	@echo "  make sap-successfactors-aws-live-max"
	@echo "                    run real AWS + SAP SuccessFactors live E2E validation"
	@echo "  make multiuser-simulation"
	@echo "                    run prod-like tenant/workspace/employee isolation simulation"
	@echo "  make live-cartridge-tests"
	@echo "                    run gated live cartridge test_connection + extraction probes"
	@echo "  make monitor-check"
	@echo "                    run one public AWS health/readiness monitor check"
	@echo "  make production-readiness"
	@echo "                    run the local production-readiness gate"
	@echo "  make v1-live-readiness"
	@echo "                    run production-readiness with v1 live gates required"
	@echo "  make production-readiness-aws"
	@echo "                    run the remote AWS/prod-like readiness gate"
	@echo "  make v1-ga-lite-local"
	@echo "                    run unified v1 GA lite harness locally (stress + offensive security)"
	@echo "  make v1-ga-lite-aws"
	@echo "                    run unified v1 GA lite harness against AWS staging/public URL"
	@echo "  make v1-ga-max-aws"
	@echo "                    run unified v1 GA max harness; requires dedicated staging + chaos confirmation"
	@echo "  make v1-ga-cleanup"
	@echo "                    clean STRESS_* data/prefixes for the current unified v1 GA run"
	@echo "  make v1-ga-report"
	@echo "                    regenerate the unified v1 GA report for the current run"
	@echo "  make seed-intelligence-gold"
	@echo "                    seed scoped prod-like Gold rows for intelligence demos"
	@echo "  make seed-replicon-beta-gold"
	@echo "                    seed scoped Replicon Gold rows for private beta apps"
	@echo "  make seed-replicon-beta-gold-aws"
	@echo "                    seed scoped Replicon Gold rows on AWS via SSM, idempotency checked"
	@echo "  make tenant-ab-local / tenant-ab-aws"
	@echo "  make decision-backtest-local / decision-backtest-aws"
	@echo "                    verify tenant A/B positive and forbidden cross-scope probes"
	@echo "  make dr-rehearsal"
	@echo "                    rehearse backup/restore scripts in a guarded mode"
	@echo "  make backup-aws / dr-rehearsal-aws / rollback-aws"
	@echo "                    AWS backup, DR rehearsal, and tag rollback via SSM"
	@echo "  make deploy-main-aws / aws-full-regression"
	@echo "                    artifact deploy from main and full AWS regression via SSM"
	@echo "  make aws-observability-report / aws-tls-status / aws-superset-probe"
	@echo "  make superset-tenant-probe / superset-tenant-probe-aws"
	@echo "  make monte-carlo-aws-probe"
	@echo "  make bayesian-calibration-aws-probe"
	@echo "  make bayesian-loop-probe / bayesian-loop-probe-aws"
	@echo "  make control-room-gold-engine-aws-probe"
	@echo "  make control-room-mock-volume-aws-probe"
	@echo "                    seed 1M mock Gold rows on AWS and validate Control Room"
	@echo "  make control-room-mock-volume-aws-cleanup OMEGA_MOCK_VOLUME_SUFFIX=<suffix>"
	@echo "                    remove one high-volume mock probe from AWS"
	@echo "  make cartridge-kb-scope-aws-probe"
	@echo "  make action-framework-aws-probe"
	@echo "                    low-cost AWS observability, TLS, Superset, Monte Carlo, and Bayesian probes"
	@echo "  make e2e          run Playwright browser-driven E2E tests (v1.44.3.2)"
	@echo "  make acceptance   run heavy full-stack acceptance with fake live HubSpot"
	@echo "  make security-scan"
	@echo "                    run local Bandit, pip-audit, and console-next npm audit"
	@echo "  make verify-release"
	@echo "                    run the v1.0 release gate against a running full stack"
	@echo "  make verify-v1-public"
	@echo "                    verify public HTTPS staging with Playwright/live probes"
	@echo "  make migrate      apply pending infra/init SQL migrations to running Postgres"
	@echo "  make reconcile-db-passwords"
	@echo "                    rotate existing local DB roles to match infra/.env"
	@echo "  make rotate-keys  back up infra/.env, generate fresh secrets"

# Read-only preflight: Docker daemon, compose plugin, infra/.env, host
# cryptography (bootstrap mints the Fernet key), occupied ports, and the
# final demo URLs. Run it BEFORE 'make up' — it chains: make preflight && make up
preflight:
	@bash scripts/preflight.sh

# Demo readiness helper: preflight, then the documented validation order.
demo-check:
	@bash scripts/preflight.sh || true
	@echo ""
	@echo "Demo validation order (see docs/runbook/09_demo_beta.md):"
	@echo "  1) make preflight"
	@echo "  2) docker compose -f infra/docker-compose.yml config -q"
	@echo "  3) make up"
	@echo "  4) curl 'http://localhost:8000/readyz?require_data=1'"
	@echo "  5) make smoke"
	@echo "  6) make test"
	@echo "  7) cd tests-e2e && npx playwright test specs/12-control-room.spec.ts"

bootstrap-env:
	bash infra/bootstrap.sh
	bash infra/bootstrap-keys.sh infra/.env

up:
	$(MAKE) bootstrap-env
	mkdir -p data/lakehouse
	$(COMPOSE_FULL) up --build -d

up-core:
	$(MAKE) bootstrap-env
	mkdir -p data/lakehouse
	$(COMPOSE_DEV) up --build -d

down:
	$(COMPOSE_FULL) down

nuke:
	@if [ "$(CONFIRM)" != "NUKE" ] || [ "$(NUKE_SCOPE)" != "local-dev" ]; then \
		echo "Refusing to remove volumes. Local non-production only."; \
		echo "Re-run: make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev"; \
		exit 2; \
	fi
	@if printf '%s\n' "$(COMPOSE_FULL)" | grep -Eq 'docker-compose\.aws|terraform/deploy'; then \
		echo "Refusing: nuke is only for infra/docker-compose.yml local compose."; \
		exit 2; \
	fi
	$(COMPOSE_FULL) down -v --remove-orphans

repair-local-stack:
	@LOCAL_REPAIR_SCOPE=local-dev bash scripts/local_stack_repair.sh

logs:
	$(COMPOSE_FULL) logs -f --tail=50

ps:
	$(COMPOSE_FULL) ps

seed-intelligence-gold:
	@set -a; \
	if [ -f infra/.env ]; then . infra/.env; fi; \
	set +a; \
	PG_PORT="$$(docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap port postgres 5432 | awk -F: 'END {print $$NF}')"; \
	GOLD_PG_PORT="$$(docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap port postgres_gold 5433 | awk -F: 'END {print $$NF}')"; \
	DATABASE_URL="postgresql://omega_console:$${OMEGA_CONSOLE_PASSWORD}@127.0.0.1:$${PG_PORT}/modecissions" \
	GOLD_DATABASE_URL="postgresql://omega_refinement_gold:$${OMEGA_REFINEMENT_GOLD_PASSWORD}@127.0.0.1:$${GOLD_PG_PORT}/modecissions_gold" \
	PYTHONPATH=console $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/seed_intelligence_gold_prod_like.py

seed-replicon-beta-gold:
	@set -a; \
	if [ -f infra/.env ]; then . infra/.env; fi; \
	set +a; \
	PG_PORT="$$(docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap port postgres 5432 2>/dev/null | awk -F: 'END {print $$NF}')"; \
	GOLD_PG_PORT="$$(docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap port postgres_gold 5433 2>/dev/null | awk -F: 'END {print $$NF}')"; \
	POSTGRES_PORT="$${PG_PORT:-$${POSTGRES_PORT:-15432}}" \
	POSTGRES_GOLD_PORT="$${GOLD_PG_PORT:-$${POSTGRES_GOLD_PORT:-15433}}" \
	$(PYTHON) scripts/seed_replicon_beta_gold.py

seed-replicon-beta-gold-aws:
	@$(PYTHON) scripts/seed_replicon_beta_gold_aws.py

run-intelligence-scheduled-local:
	@$(PYTHON) scripts/run_intelligence_scheduled.py --target local

run-intelligence-scheduled-aws:
	@$(PYTHON) scripts/run_intelligence_scheduled.py --target aws

decision-backtest-local:
	@$(PYTHON) scripts/run_decision_backtest.py --target local

decision-backtest-aws:
	@$(PYTHON) scripts/run_decision_backtest.py --target aws

test:
	$(PYTEST) -ra tests/
	PYTHONPATH=.:console $(PYTEST) -ra console/tests/
	PYTHONPATH=. $(PYTEST) -ra refinement/tests/
	PYTHONPATH=vault $(PYTEST) -ra vault/tests/
	PYTHONPATH=workspace $(PYTEST) -ra workspace/tests/
	$(PYTEST) --import-mode=importlib cartridges -q

test-hermetic:
	@set -e; \
	MOCK_MCP_PORT=$(MOCK_MCP_PORT) \
	MOCK_REPLICON_PORT=$(MOCK_REPLICON_PORT) \
	MOCK_SAP_HCM_PORT=$(MOCK_SAP_HCM_PORT) \
	MOCK_SAP_SUCCESSFACTORS_PORT=$(MOCK_SAP_SUCCESSFACTORS_PORT) \
	MOCK_SAP_S4HANA_PORT=$(MOCK_SAP_S4HANA_PORT) \
		docker compose -p $(TEST_COMPOSE_PROJECT) -f $(TEST_COMPOSE) down --remove-orphans; \
	MOCK_MCP_PORT=$(MOCK_MCP_PORT) \
	MOCK_REPLICON_PORT=$(MOCK_REPLICON_PORT) \
	MOCK_SAP_HCM_PORT=$(MOCK_SAP_HCM_PORT) \
	MOCK_SAP_SUCCESSFACTORS_PORT=$(MOCK_SAP_SUCCESSFACTORS_PORT) \
	MOCK_SAP_S4HANA_PORT=$(MOCK_SAP_S4HANA_PORT) \
		docker compose -p $(TEST_COMPOSE_PROJECT) -f $(TEST_COMPOSE) up -d --wait; \
	trap 'MOCK_MCP_PORT=$(MOCK_MCP_PORT) MOCK_REPLICON_PORT=$(MOCK_REPLICON_PORT) MOCK_SAP_HCM_PORT=$(MOCK_SAP_HCM_PORT) MOCK_SAP_SUCCESSFACTORS_PORT=$(MOCK_SAP_SUCCESSFACTORS_PORT) MOCK_SAP_S4HANA_PORT=$(MOCK_SAP_S4HANA_PORT) docker compose -p $(TEST_COMPOSE_PROJECT) -f $(TEST_COMPOSE) down --remove-orphans' EXIT; \
	OMEGA_ENABLE_LIVE_STACK_TESTS=1 \
	OMEGA_STACK_BASE=http://127.0.0.1:18000 \
	OMEGA_MCP_INFRA_BASE=http://127.0.0.1:$(MOCK_MCP_PORT) \
	OMEGA_REPLICON_BASE=http://127.0.0.1:$(MOCK_REPLICON_PORT) \
	OMEGA_SAP_HCM_BASE=http://127.0.0.1:$(MOCK_SAP_HCM_PORT) \
	OMEGA_SAP_SUCCESSFACTORS_BASE=http://127.0.0.1:$(MOCK_SAP_SUCCESSFACTORS_PORT) \
	OMEGA_SAP_S4HANA_BASE=http://127.0.0.1:$(MOCK_SAP_S4HANA_PORT) \
		$(PYTEST) tests/ -q

# Fast, hermetic baseline coherence check. No Docker, no DB, no network.
# Answers "is this checkout coherent enough to work from?", not "does the
# product work" — that stays with smoke / beta-smoke / test.
baseline-smoke:
	@bash scripts/baseline_smoke.sh

# Sprint v1.23 (audit B3): real end-to-end smoke. Verifies the stack is
# functional — not just "containers running" — by hitting /healthz on
# every app service, probing Postgres + MinIO, checking the auth gate,
# and asserting the v1.19 vault_entries partitioning is intact.
# Assumes `make up` has been run; doesn't try to start the stack.
smoke:
	@bash scripts/smoke_test.sh

beta-smoke:
	@if [ "$${OMEGA_BETA_SMOKE_WARM_ACCEPTANCE:-0}" = "1" ]; then \
		echo "[beta-smoke] warming HubSpot Bronze/Silver/Gold via make acceptance"; \
		$(MAKE) acceptance; \
	fi
	@$(MAKE) smoke
	@$(PYTHON) scripts/beta_smoke.py

beta-smoke-aws:
	@$(PYTHON) scripts/beta_smoke_aws.py

stress:
	@bash scripts/run_stress.sh

stress-smoke:
	@OMEGA_STRESS_PROFILE=smoke $(MAKE) stress; stress_code=$$?; $(MAKE) data-integrity-audit; audit_code=$$?; if [ $$stress_code -ne 0 ]; then exit $$stress_code; fi; exit $$audit_code

stress-beta:
	@OMEGA_STRESS_PROFILE=beta $(MAKE) stress; stress_code=$$?; $(MAKE) data-integrity-audit; audit_code=$$?; if [ $$stress_code -ne 0 ]; then exit $$stress_code; fi; exit $$audit_code

stress-spike:
	@OMEGA_STRESS_PROFILE=spike $(MAKE) stress; stress_code=$$?; $(MAKE) data-integrity-audit; audit_code=$$?; if [ $$stress_code -ne 0 ]; then exit $$stress_code; fi; exit $$audit_code

stress-breakpoint:
	@OMEGA_STRESS_PROFILE=breakpoint $(MAKE) stress; stress_code=$$?; $(MAKE) data-integrity-audit; audit_code=$$?; if [ $$stress_code -ne 0 ]; then exit $$stress_code; fi; exit $$audit_code

stress-soak-24h:
	@OMEGA_STRESS_PROFILE=soak-24h $(MAKE) stress; stress_code=$$?; $(MAKE) data-integrity-audit; audit_code=$$?; if [ $$stress_code -ne 0 ]; then exit $$stress_code; fi; exit $$audit_code

stress-write-heavy:
	@OMEGA_STRESS_PROFILE=write-heavy OMEGA_STRESS_ENABLE_WRITES=1 $(MAKE) stress; stress_code=$$?; $(MAKE) data-integrity-audit; audit_code=$$?; if [ $$stress_code -ne 0 ]; then exit $$stress_code; fi; exit $$audit_code

data-integrity-audit:
	@$(PYTHON) scripts/data_integrity_audit.py

copilot-redteam:
	@$(PYTHON) scripts/copilot_redteam.py

cartridge-resilience:
	@$(PYTHON) scripts/cartridge_resilience.py --workload "$(WORKLOAD)"

chaos-local:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/chaos_gate.py --target local

chaos-aws:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/chaos_gate.py --target aws

enterprise-readiness:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/enterprise_readiness.py --target "$(TARGET)" --workload "$(WORKLOAD)" --profile "$(PROFILE)"

sap-successfactors-aws-live-max:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/sap_successfactors_aws_live_max.py

multiuser-simulation:
	@bash scripts/run_multiuser_isolation_simulation.sh

tenant-ab-local:
	@$(PYTHON) scripts/tenant_ab_e2e.py --target local

tenant-ab-aws:
	@$(PYTHON) scripts/tenant_ab_e2e.py --target aws

live-cartridge-tests:
	@bash scripts/run_live_cartridge_checks.sh

monitor-check:
	@bash scripts/monitor_health_once.sh

production-readiness:
	@bash scripts/production_readiness.sh

v1-live-readiness:
	@OMEGA_PRODUCTION_READINESS_V1=1 bash scripts/production_readiness.sh

production-readiness-aws:
	@OMEGA_PRODUCTION_READINESS_REMOTE=1 bash scripts/production_readiness.sh

v1-ga-lite-local:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/v1_stress/run_v1_ga.py lite-local

v1-ga-lite-aws:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/v1_stress/run_v1_ga.py lite-aws

v1-ga-max-aws:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/v1_stress/run_v1_ga.py max-aws

v1-ga-cleanup:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/v1_stress/run_v1_ga.py cleanup

v1-ga-report:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/v1_stress/run_v1_ga.py report

dr-rehearsal:
	@bash scripts/run_dr_rehearsal.sh

backup-aws:
	@$(PYTHON) scripts/aws_backup.py

dr-rehearsal-aws:
	@$(PYTHON) scripts/aws_dr_rehearsal.py

rollback-aws:
	@$(PYTHON) scripts/aws_rollback.py

rollback-rehearsal-aws:
	@$(PYTHON) scripts/aws_rollback.py

deploy-main-aws:
	@$(PYTHON) scripts/deploy_main_aws.py

aws-full-regression:
	@$(PYTHON) scripts/aws_full_regression.py

aws-observability-report:
	@$(PYTHON) scripts/aws_observability_report.py

aws-tls-status:
	@$(PYTHON) scripts/aws_tls_status.py

aws-superset-probe:
	@$(PYTHON) scripts/aws_superset_probe.py

superset-tenant-probe:
	@$(PYTHON) scripts/superset_tenant_probe.py --target local

superset-tenant-probe-aws:
	@$(PYTHON) scripts/superset_tenant_probe.py --target aws

monte-carlo-aws-probe:
	@$(PYTHON) scripts/aws_monte_carlo_probe.py

bayesian-calibration-aws-probe:
	@$(PYTHON) scripts/aws_bayesian_calibration_probe.py

bayesian-loop-probe:
	@PYTHONPATH=console $(PYTHON) scripts/bayesian_loop_probe.py

bayesian-loop-probe-aws:
	@$(PYTHON) scripts/aws_bayesian_loop_probe.py

control-room-gold-engine-aws-probe:
	@$(PYTHON) scripts/aws_control_room_gold_engine_probe.py

control-room-mock-volume-aws-probe:
	@$(PYTHON) scripts/aws_control_room_mock_volume_probe.py

control-room-mock-volume-aws-cleanup:
	@test -n "$(OMEGA_MOCK_VOLUME_SUFFIX)" || { echo "OMEGA_MOCK_VOLUME_SUFFIX is required"; exit 2; }
	@$(PYTHON) scripts/aws_control_room_mock_volume_probe.py --cleanup-suffix "$(OMEGA_MOCK_VOLUME_SUFFIX)"

cartridge-kb-scope-aws-probe:
	@$(PYTHON) scripts/cartridge_kb_scope_aws_probe.py

action-framework-aws-probe:
	@$(PYTHON) scripts/aws_action_framework_probe.py

decision-orchestrator-aws-probe:
	@$(PYTHON) scripts/aws_decision_orchestrator_probe.py

decision-orchestrator-execution-aws-probe:
	@$(PYTHON) scripts/aws_decision_orchestrator_execution_probe.py

rollback-rehearsal:
	@$(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi) scripts/rollback_rehearsal.py

# Sprint v1.44.3.2: Playwright browser-driven E2E suite.
# Validates the FastAPI-served static console (port 8000), legacy HTML
# routes, backend API contracts, and external service reachability
# against a running stack.
# Configuration: edit tests-e2e/.env (copied from tests-e2e/.env.example
# on first run). The HTML report lands at tests-e2e/playwright-report/.
e2e:
	@bash scripts/run-e2e.sh

acceptance:
	@bash scripts/run_full_stack_acceptance.sh

security-scan:
	@test -x "$(BANDIT)" || { echo "$(BANDIT) not found. Install dev deps into .venv first."; exit 1; }
	@test -x "$(PIP_AUDIT)" || { echo "$(PIP_AUDIT) not found. Install dev deps into .venv first."; exit 1; }
	$(BANDIT) -r console workspace vault refinement mcp-infra cartridges \
		scripts/ci_changed_areas.py scripts/ci_control_room_paths.py \
		--severity-level medium --confidence-level high
	$(PIP_AUDIT)
	npm --prefix console-next audit

verify-v1-public:
	@bash scripts/verify_v1_public.sh

verify-release:
	$(MAKE) bootstrap-env
	$(RUFF) check .
	npm --prefix console-next ci
	npm --prefix console-next run lint
	npm --prefix console-next run typecheck
	npm --prefix console-next run test
	npm --prefix console-next run verify:static
	npm --prefix console-next audit
	npm --prefix tests-e2e ci
	npm --prefix tests-e2e audit --audit-level=high
	$(MAKE) security-scan
	@set -e; for req in $$(find . -name requirements.txt -not -path './.git/*' -not -path './*/vendor/*' -not -path './*/node_modules/*' | sort); do \
		echo "=== Auditing $$req ==="; \
		$(PIP_AUDIT) -r "$$req" --vulnerability-service=pypi; \
	done
	$(COMPOSE_BASE) --profile sap config -q
	$(E2E_STACK_ENV) $(COMPOSE_FULL) config -q
	$(E2E_STACK_ENV) $(COMPOSE_FULL) up -d --build --force-recreate
	bash scripts/wait_for_health.sh
	$(MAKE) test
	$(MAKE) smoke
	$(MAKE) e2e

migrate:
	@bash scripts/apply_db_migrations.sh

reconcile-db-passwords:
	@bash scripts/reconcile_db_passwords.sh

# Sprint v1.14: real implementation. Backs up the current infra/.env to
# infra/.env.save (gitignored), then regenerates ALL secrets via
# bootstrap.sh + bootstrap-keys.sh. Every active user session becomes
# invalid after `make down && make up` because JWT_SECRET_KEY rotates,
# so this is a deliberately operator-driven action.
rotate-keys:
	@echo "[rotate-keys] Backing up current .env to infra/.env.save..."
	@if [ -f infra/.env ]; then cp infra/.env infra/.env.save; fi
	@echo "[rotate-keys] Generating new infra/.env with fresh secrets..."
	@rm -f infra/.env
	@bash infra/bootstrap.sh
	@bash infra/bootstrap-keys.sh infra/.env
	@echo ""
	@echo "[rotate-keys] DONE. New secrets generated."
	@echo "[rotate-keys] OLD .env backed up to infra/.env.save (gitignored)."
	@echo "[rotate-keys] NEXT STEPS:"
	@echo "  1. Run: make down && make up"
	@echo "  2. All sessions will be invalidated — users must re-login."
	@echo "  3. Once verified, delete infra/.env.save"

# Read-only canonical GCP edge gate. The operator identity/config are explicit;
# ambient gcloud/network overrides are rejected by the verifier.
verify-gcp-edge-tls:
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/verify_edge_tls.py \
		--project-id project-dd5ba7fa-374c-4554-ae6 \
		--project-number 894064513501 --environment staging \
		--expected-ip 34.144.248.147 \
		--legacy-console-ip 136.68.67.95 \
		--legacy-workspace-ip 8.233.29.138 \
		--edge-mode "$(or $(GCP_EDGE_MODE),post-transition)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)" \
		--gcloud-config "$(GCP_CONFIG_DIR)"

verify-gcp-release-backup-permissions:
	@test -n "$(GCP_RELEASE_BACKUP_BUCKET)" || { echo "GCP_RELEASE_BACKUP_BUCKET is required"; exit 2; }
	@test -n "$(GCP_APP_SERVICE_ACCOUNT)" || { echo "GCP_APP_SERVICE_ACCOUNT is required"; exit 2; }
	@sudo /usr/local/sbin/omega-safe-io gcs-release-backup-permissions \
		--bucket "$(GCP_RELEASE_BACKUP_BUCKET)" \
		--expected-service-account "$(GCP_APP_SERVICE_ACCOUNT)"

# The transaction owns plan generation, same-FD JSON derivation, validation,
# and application. Standalone JSON/hash verification is deliberately absent.
gcp-release-authority-generate:
	@test -n "$(GCP_RELEASE_AUTHORITY)" || { echo "GCP_RELEASE_AUTHORITY output path is required"; exit 2; }
	@test -n "$(GCP_SOURCE_REF)" || { echo "GCP_SOURCE_REF exact approved main SHA is required"; exit 2; }
	@test -n "$(GCP_CONTROLLER_REF)" || { echo "GCP_CONTROLLER_REF exact clean controller SHA is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/generate_release_authority.py generate \
		--output "$(GCP_RELEASE_AUTHORITY)" --source-ref "$(GCP_SOURCE_REF)" \
		--controller-ref "$(GCP_CONTROLLER_REF)" \
		--gh-config "$(GH_CONFIG_DIR)" --gcloud-config "$(GCP_CONFIG_DIR)"

gcp-release-authority-verify:
	@test -n "$(GCP_RELEASE_AUTHORITY)" || { echo "GCP_RELEASE_AUTHORITY is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/generate_release_authority.py verify \
		--authority "$(GCP_RELEASE_AUTHORITY)" \
		--gh-config "$(GH_CONFIG_DIR)" --gcloud-config "$(GCP_CONFIG_DIR)"

gcp-terraform-plan:
	@test -n "$(GCP_TRANSACTION_DIR)" || { echo "GCP_TRANSACTION_DIR is required"; exit 2; }
	@test -n "$(GCP_TFVARS)" || { echo "GCP_TFVARS is required"; exit 2; }
	@test -n "$(GCP_RELEASE_AUTHORITY)" || { echo "GCP_RELEASE_AUTHORITY is required"; exit 2; }
	@test -n "$(TOFU)" || { echo "TOFU must point to the verified official OpenTofu 1.11.6 binary"; exit 2; }
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@test "$(or $(GCP_TERRAFORM_PROFILE),foundation)" != "iam-revoke" || { echo "use gcp-iam-revoke-plan for reversible IAM removal"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/terraform_transaction.py plan \
		--profile "$(or $(GCP_TERRAFORM_PROFILE),foundation)" \
		--transaction "$(GCP_TRANSACTION_DIR)" --tfvars "$(GCP_TFVARS)" \
		--release-authority "$(GCP_RELEASE_AUTHORITY)" \
		--tofu "$(TOFU)" \
		--gh-config "$(GH_CONFIG_DIR)" \
		--gcloud-config "$(GCP_CONFIG_DIR)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)"

gcp-terraform-apply:
	@test -n "$(GCP_TRANSACTION_DIR)" || { echo "GCP_TRANSACTION_DIR is required"; exit 2; }
	@test -n "$(GCP_TFVARS)" || { echo "GCP_TFVARS is required"; exit 2; }
	@test -n "$(TOFU)" || { echo "TOFU must point to the verified official OpenTofu 1.11.6 binary"; exit 2; }
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@test "$(or $(GCP_TERRAFORM_PROFILE),foundation)" != "iam-revoke" || { echo "use gcp-iam-revoke-apply for reversible IAM removal"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/terraform_transaction.py apply \
		--profile "$(or $(GCP_TERRAFORM_PROFILE),foundation)" \
		--transaction "$(GCP_TRANSACTION_DIR)" --tfvars "$(GCP_TFVARS)" \
		--tofu "$(TOFU)" \
		--gh-config "$(GH_CONFIG_DIR)" \
		--gcloud-config "$(GCP_CONFIG_DIR)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)"

gcp-terraform-status:
	@test -n "$(GCP_TRANSACTION_DIR)" || { echo "GCP_TRANSACTION_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/terraform_transaction.py status \
		--profile "$(or $(GCP_TERRAFORM_PROFILE),foundation)" \
		--transaction "$(GCP_TRANSACTION_DIR)"

gcp-terraform-recover:
	@test -n "$(GCP_TRANSACTION_DIR)" || { echo "GCP_TRANSACTION_DIR is required"; exit 2; }
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@test "$(or $(GCP_TERRAFORM_PROFILE),foundation)" != "iam-revoke" || { echo "use gcp-iam-revoke-recover for reversible IAM recovery"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/terraform_transaction.py recover \
		--profile "$(or $(GCP_TERRAFORM_PROFILE),foundation)" \
		--transaction "$(GCP_TRANSACTION_DIR)" \
		--gh-config "$(GH_CONFIG_DIR)" --gcloud-config "$(GCP_CONFIG_DIR)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)"

# Explicit canonical foundation entry points. The Terraform transaction is
# also the sole startup-metadata/reset/handoff authority; never invoke its
# embedded startup_metadata_transaction.py helper out of band.
gcp-foundation-plan:
	@$(MAKE) --no-print-directory gcp-terraform-plan GCP_TERRAFORM_PROFILE=foundation

gcp-foundation-apply:
	@$(MAKE) --no-print-directory gcp-terraform-apply GCP_TERRAFORM_PROFILE=foundation

gcp-foundation-status:
	@$(MAKE) --no-print-directory gcp-terraform-status GCP_TERRAFORM_PROFILE=foundation

gcp-foundation-recover:
	@$(MAKE) --no-print-directory gcp-terraform-recover GCP_TERRAFORM_PROFILE=foundation

gcp-iam-revoke-plan:
	@test -n "$(GCP_IAM_TRANSACTION_DIR)" || { echo "GCP_IAM_TRANSACTION_DIR is required and must be an empty mode-0700 directory"; exit 2; }
	@test -n "$(GCP_TFVARS)" || { echo "GCP_TFVARS is required"; exit 2; }
	@test -n "$(GCP_RELEASE_AUTHORITY)" || { echo "GCP_RELEASE_AUTHORITY is required"; exit 2; }
	@test -n "$(TOFU)" || { echo "TOFU must point to the verified official OpenTofu 1.11.6 binary"; exit 2; }
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/iam_revoke_transaction.py plan \
		--transaction "$(GCP_IAM_TRANSACTION_DIR)" --tfvars "$(GCP_TFVARS)" \
		--release-authority "$(GCP_RELEASE_AUTHORITY)" --tofu "$(TOFU)" \
		--gh-config "$(GH_CONFIG_DIR)" --gcloud-config "$(GCP_CONFIG_DIR)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)"

gcp-iam-revoke-apply:
	@test -n "$(GCP_IAM_TRANSACTION_DIR)" || { echo "GCP_IAM_TRANSACTION_DIR is required"; exit 2; }
	@test -n "$(GCP_TFVARS)" || { echo "GCP_TFVARS is required"; exit 2; }
	@test -n "$(TOFU)" || { echo "TOFU must point to the verified official OpenTofu 1.11.6 binary"; exit 2; }
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GH_CONFIG_DIR)" || { echo "GH_CONFIG_DIR is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/iam_revoke_transaction.py apply \
		--transaction "$(GCP_IAM_TRANSACTION_DIR)" --tfvars "$(GCP_TFVARS)" \
		--tofu "$(TOFU)" --gh-config "$(GH_CONFIG_DIR)" \
		--gcloud-config "$(GCP_CONFIG_DIR)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)"

gcp-iam-revoke-recover:
	@test -n "$(GCP_IAM_TRANSACTION_DIR)" || { echo "GCP_IAM_TRANSACTION_DIR is required"; exit 2; }
	@test -n "$(GCP_OPERATOR_ACCOUNT)" || { echo "GCP_OPERATOR_ACCOUNT is required"; exit 2; }
	@test -n "$(GCP_CONFIG_DIR)" || { echo "GCP_CONFIG_DIR is required"; exit 2; }
	@/usr/bin/python3 -I scripts/gcp/iam_revoke_transaction.py recover \
		--transaction "$(GCP_IAM_TRANSACTION_DIR)" \
		--gcloud-config "$(GCP_CONFIG_DIR)" \
		--expected-account "$(GCP_OPERATOR_ACCOUNT)"
