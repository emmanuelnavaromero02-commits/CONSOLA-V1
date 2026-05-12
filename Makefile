.PHONY: help up down nuke logs ps test smoke rotate-keys

help:
	@echo "MODecissionsPaaS — targets:"
	@echo "  make up           bootstrap secrets and start the stack"
	@echo "  make down         stop the stack"
	@echo "  make nuke         wipe stack, volumes, dangling containers"
	@echo "  make logs         follow service logs"
	@echo "  make ps           list running services"
	@echo "  make test         run the python test suites"
	@echo "  make smoke        end-to-end smoke (Fase 6)"
	@echo "  make rotate-keys  rotate secrets (Fase 2 via UI)"

up:
	bash infra/bootstrap.sh && mkdir -p data/lakehouse && docker compose -f infra/docker-compose.yml up --build -d

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

smoke:
	@echo "smoke implemented in Fase 6"

rotate-keys:
	@echo "rotate-keys implemented in Fase 2 via UI"
