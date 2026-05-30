PYTEST ?= $(shell if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)
RUFF ?= $(shell if [ -x .venv/bin/ruff ]; then echo .venv/bin/ruff; else echo ruff; fi)
PIP_AUDIT ?= $(shell if [ -x .venv/bin/pip-audit ]; then echo .venv/bin/pip-audit; else echo pip-audit; fi)
COMPOSE_FULL ?= docker compose -f infra/docker-compose.yml --profile sap
TEST_COMPOSE ?= infra/docker-compose.test.yml
TEST_COMPOSE_PROJECT ?= omega-hermetic-test
MOCK_MCP_PORT ?= 18010
MOCK_REPLICON_PORT ?= 18201
MOCK_SAP_HCM_PORT ?= 18202
MOCK_SAP_SUCCESSFACTORS_PORT ?= 18203
MOCK_SAP_S4HANA_PORT ?= 18204

.PHONY: help up up-core down nuke logs ps test smoke migrate rotate-keys e2e preflight demo-check verify-release verify-v1-public
.PHONY: test-hermetic

help:
	@echo "MODecissionsPaaS — targets:"
	@echo "  make preflight    check Docker/compose/.env/ports BEFORE 'make up'"
	@echo "  make demo-check   preflight + the demo validation order (runbook 09)"
	@echo "  make up           bootstrap secrets and start the full v1.0 stack (SAP profile)"
	@echo "  make up-core      bootstrap secrets and start the core stack without SAP"
	@echo "  make down         stop the stack"
	@echo "  make nuke CONFIRM=NUKE"
	@echo "                    wipe this stack and volumes only"
	@echo "  make logs         follow service logs"
	@echo "  make ps           list running services"
	@echo "  make test         run the python test suites"
	@echo "  make test-hermetic"
	@echo "                    run tests/ against isolated mock services"
	@echo "  make smoke        run end-to-end smoke checks against a running stack"
	@echo "  make e2e          run Playwright browser-driven E2E tests (v1.44.3.2)"
	@echo "  make verify-release"
	@echo "                    run the v1.0 release gate against a running full stack"
	@echo "  make verify-v1-public"
	@echo "                    verify public HTTPS staging with Playwright/live probes"
	@echo "  make migrate      apply pending infra/init SQL migrations to running Postgres"
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
	@echo "  4) curl :8000/healthz"
	@echo "  5) make smoke"
	@echo "  6) make test"
	@echo "  7) cd tests-e2e && npx playwright test specs/12-control-room.spec.ts"

up:
	bash infra/bootstrap.sh && bash infra/bootstrap-keys.sh infra/.env && mkdir -p data/lakehouse && $(COMPOSE_FULL) up --build -d

up-core:
	bash infra/bootstrap.sh && bash infra/bootstrap-keys.sh infra/.env && mkdir -p data/lakehouse && docker compose -f infra/docker-compose.yml up --build -d

down:
	$(COMPOSE_FULL) down

nuke:
	@if [ "$(CONFIRM)" != "NUKE" ]; then \
		echo "Refusing to remove volumes. Re-run: make nuke CONFIRM=NUKE"; \
		exit 2; \
	fi
	$(COMPOSE_FULL) down -v --remove-orphans

logs:
	$(COMPOSE_FULL) logs -f --tail=50

ps:
	$(COMPOSE_FULL) ps

test:
	$(PYTEST) -ra tests/
	PYTHONPATH=console $(PYTEST) -ra console/tests/
	PYTHONPATH=. $(PYTEST) -ra refinement/tests/
	PYTHONPATH=vault $(PYTEST) -ra vault/tests/
	PYTHONPATH=workspace $(PYTEST) -ra workspace/tests/
	$(PYTEST) cartridges -q

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

# Sprint v1.23 (audit B3): real end-to-end smoke. Verifies the stack is
# functional — not just "containers running" — by hitting /healthz on
# every app service, probing Postgres + MinIO, checking the auth gate,
# and asserting the v1.19 vault_entries partitioning is intact.
# Assumes `make up` has been run; doesn't try to start the stack.
smoke:
	@bash scripts/smoke_test.sh

# Sprint v1.44.3.2: Playwright browser-driven E2E suite.
# Validates the FastAPI-served static console (port 8000), legacy HTML
# routes, backend API contracts, and external service reachability
# against a running stack.
# Configuration: edit tests-e2e/.env (copied from tests-e2e/.env.example
# on first run). The HTML report lands at tests-e2e/playwright-report/.
e2e:
	@bash scripts/run-e2e.sh

verify-v1-public:
	@bash scripts/verify_v1_public.sh

verify-release:
	$(RUFF) check .
	npm --prefix console-next ci
	npm --prefix console-next run lint
	npm --prefix console-next run typecheck
	npm --prefix console-next run test
	npm --prefix console-next run verify:static
	npm --prefix console-next audit --audit-level=high
	npm --prefix tests-e2e ci
	npm --prefix tests-e2e audit --audit-level=high
	@command -v $(PIP_AUDIT) >/dev/null 2>&1 || { echo "pip-audit not found. Install with: pip install pip-audit==2.7.3"; exit 1; }
	@set -e; for req in $$(find . -name requirements.txt -not -path './.git/*' -not -path './*/vendor/*' -not -path './*/node_modules/*' | sort); do \
		echo "=== Auditing $$req ==="; \
		$(PIP_AUDIT) -r "$$req" --vulnerability-service=pypi --ignore-vuln PYSEC-2025-183 --ignore-vuln PYSEC-2025-185; \
	done
	docker compose -f infra/docker-compose.yml --profile sap config -q
	docker compose -f infra/docker-compose.yml --profile sap build
	bash scripts/wait_for_health.sh
	$(MAKE) test
	$(MAKE) smoke
	$(MAKE) e2e

migrate:
	@bash scripts/apply_db_migrations.sh

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
