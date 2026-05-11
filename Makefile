# CONSOLA-BETA — convenience targets
#
# Most targets shell out to scripts/ — the Python in those scripts is the
# source of truth.

PY ?= python3
SCRIPT_SEED   := scripts/seed_enterprise_demo.py
ENV_FILE      ?= infra/.env

# Default port mappings come from infra/docker-compose.yml.
# Override by exporting PG_DSN / GOLD_DSN in your shell.

.PHONY: help demo-seed demo-seed-smoke demo-reset demo-counts demo-rebuild-gold demo-only-meta

help:
	@echo "Enterprise demo pack targets:"
	@echo "  make demo-seed         — load full enterprise demo (~400k rows total)"
	@echo "  make demo-seed-smoke   — load a 10% scaled demo for quick validation"
	@echo "  make demo-reset        — drop every demo object (safe, no real data touched)"
	@echo "  make demo-counts       — print final row counts for the demo pack"
	@echo "  make demo-rebuild-gold — recompute gold tables from existing demo bronze"
	@echo "  make demo-only-meta    — re-register cartridges / datasets / semantic / RAG only"

demo-seed:
	$(PY) $(SCRIPT_SEED)

demo-seed-smoke:
	$(PY) $(SCRIPT_SEED) --scale 0.1

demo-reset:
	$(PY) $(SCRIPT_SEED) --reset

demo-counts:
	$(PY) $(SCRIPT_SEED) --skip-data --only gold && \
	$(PY) $(SCRIPT_SEED) --skip-data --only meta

demo-rebuild-gold:
	$(PY) $(SCRIPT_SEED) --skip-data --only gold

demo-only-meta:
	$(PY) $(SCRIPT_SEED) --skip-data --only meta
