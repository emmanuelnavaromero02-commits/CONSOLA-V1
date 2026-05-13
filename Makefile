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
	@echo "  make rotate-keys  (NOT IMPLEMENTED — exits 1)"

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

rotate-keys:
	@echo "ERROR: 'make rotate-keys' is not implemented — rotate manually via infra/bootstrap.sh" && exit 1
