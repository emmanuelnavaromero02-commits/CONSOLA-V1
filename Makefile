PYTEST ?= $(shell if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)

.PHONY: help up down nuke logs ps test smoke migrate rotate-keys

help:
	@echo "MODecissionsPaaS — targets:"
	@echo "  make up           bootstrap secrets and start the stack"
	@echo "  make down         stop the stack"
	@echo "  make nuke         wipe stack, volumes, dangling containers"
	@echo "  make logs         follow service logs"
	@echo "  make ps           list running services"
	@echo "  make test         run the python test suites"
	@echo "  make smoke        run end-to-end smoke checks against a running stack"
	@echo "  make migrate      apply pending infra/init SQL migrations to running Postgres"
	@echo "  make rotate-keys  back up infra/.env, generate fresh secrets"

up:
	bash infra/bootstrap.sh && bash infra/bootstrap-keys.sh infra/.env && mkdir -p data/lakehouse && docker compose -f infra/docker-compose.yml up --build -d

down:
	docker compose -f infra/docker-compose.yml down

nuke:
	docker compose -f infra/docker-compose.yml down -v --remove-orphans && docker ps -aq | xargs -r docker rm -f

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
