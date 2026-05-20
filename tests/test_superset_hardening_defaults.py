from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_superset_prod_hardening_defaults_enabled():
    config = (ROOT / "infra/terraform/deploy/superset_config/superset_config.py").read_text(encoding="utf-8")
    compose = (ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    aws_compose = (ROOT / "infra/terraform/deploy/docker-compose.aws.yml").read_text(encoding="utf-8")

    assert 'SUPERSET_TALISMAN_ENABLED", "true"' in config
    assert 'SUPERSET_CSRF_ENABLED", "true"' in config
    assert 'SUPERSET_RATELIMIT_ENABLED", "true"' in config
    assert "RATELIMIT_STORAGE_URI" in config
    assert "redis://redis:6379/1" in config
    assert "SUPERSET_TALISMAN_ENABLED: ${SUPERSET_TALISMAN_ENABLED:-true}" in compose
    assert re.search(r"SUPERSET_CSRF_ENABLED:\s+\$\{SUPERSET_CSRF_ENABLED:-true\}", compose)
    assert "SUPERSET_RATELIMIT_STORAGE_URI: ${SUPERSET_RATELIMIT_STORAGE_URI:-redis://redis:6379/1}" in compose
    assert "superset-init:\n    # One-shot bootstrap" in compose
    assert "superset-init:\n        condition: service_completed_successfully" in compose
    assert "SQLALCHEMY_DATABASE_URI: \"postgresql+psycopg2://omega_superset_meta:" in aws_compose
    assert "SUPERSET_RATELIMIT_STORAGE_URI: ${SUPERSET_RATELIMIT_STORAGE_URI:-redis://redis:6379/1}" in aws_compose
    assert "redis:\n        condition: service_healthy" in aws_compose
    assert "REDIS_URL:           ${REDIS_URL:-redis://redis:6379/0}" in aws_compose
    assert "redis:" in aws_compose
