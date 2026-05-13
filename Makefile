.PHONY: help up down nuke logs ps test smoke rotate-keys

help:
	@echo "MODecissionsPaaS — targets:"
	@echo "  make up           bootstrap secrets and start the stack"
	@echo "  make down         stop the stack"
	@echo "  make nuke         wipe stack, volumes, dangling containers"
	@echo "  make logs         follow service logs"
	@echo "  make ps           list running services"
	@echo "  make test         run the python test suites"
	@echo "  make smoke        (NOT IMPLEMENTED — exits 1)"
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
	pytest -ra tests/ console/tests/ refinement/tests/ vault/tests/

# Honest stubs (sprint v1.7): these used to print a misleading
# "implemented in Fase X" message and exit 0, so an operator running
# them would believe the action succeeded. Both now exit non-zero so CI
# / orchestration can detect their absence.
smoke:
	@echo "ERROR: 'make smoke' is not implemented yet — pending in roadmap" && exit 1

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
