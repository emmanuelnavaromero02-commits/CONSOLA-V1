PYTEST ?= $(shell if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)

.PHONY: help up down nuke logs ps test smoke migrate rotate-keys e2e preflight demo-check

help:
	@echo "MODecissionsPaaS — targets:"
	@echo "  make preflight    check Docker/compose/.env/ports BEFORE 'make up'"
	@echo "  make demo-check   preflight + the demo validation order (runbook 09)"
	@echo "  make up           bootstrap secrets and start the stack"
	@echo "  make down         stop the stack"
	@echo "  make nuke CONFIRM=NUKE"
	@echo "                    wipe this stack and volumes only"
	@echo "  make logs         follow service logs"
	@echo "  make ps           list running services"
	@echo "  make test         run the python test suites"
	@echo "  make smoke        run end-to-end smoke checks against a running stack"
	@echo "  make e2e          run Playwright browser-driven E2E tests (v1.44.3.2)"
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
	@echo "  4) curl :8000/healthz  &&  curl :3000/api/health"
	@echo "  5) make smoke"
	@echo "  6) make test"
	@echo "  7) cd tests-e2e && npx playwright test specs/12-control-room.spec.ts"

up:
	bash infra/bootstrap.sh && bash infra/bootstrap-keys.sh infra/.env && mkdir -p data/lakehouse && docker compose -f infra/docker-compose.yml up --build -d

down:
	docker compose -f infra/docker-compose.yml down

nuke:
	@if [ "$(CONFIRM)" != "NUKE" ]; then \
		echo "Refusing to remove volumes. Re-run: make nuke CONFIRM=NUKE"; \
		exit 2; \
	fi
	docker compose -f infra/docker-compose.yml down -v --remove-orphans

logs:
	docker compose -f infra/docker-compose.yml logs -f --tail=50

ps:
	docker compose -f infra/docker-compose.yml ps

test:
	$(PYTEST) -ra tests/
	PYTHONPATH=console $(PYTEST) -ra console/tests/
	PYTHONPATH=. $(PYTEST) -ra refinement/tests/
	PYTHONPATH=vault $(PYTEST) -ra vault/tests/
	PYTHONPATH=workspace $(PYTEST) -ra workspace/tests/

# Sprint v1.23 (audit B3): real end-to-end smoke. Verifies the stack is
# functional — not just "containers running" — by hitting /healthz on
# every app service, probing Postgres + MinIO, checking the auth gate,
# and asserting the v1.19 vault_entries partitioning is intact.
# Assumes `make up` has been run; doesn't try to start the stack.
smoke:
	@bash scripts/smoke_test.sh

# Sprint v1.44.3.2: Playwright browser-driven E2E suite.
# Validates Next.js console (port 3000), legacy HTML console (port 8000),
# backend API contracts, and external service reachability against a
# running stack. The runner asserts both consoles are reachable before
# the suite starts so a failed connection produces a clear precondition
# error instead of an obscure test timeout.
# Configuration: edit tests-e2e/.env (copied from tests-e2e/.env.example
# on first run). The HTML report lands at tests-e2e/playwright-report/.
e2e:
	@bash scripts/run-e2e.sh

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
