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
    assert "ENABLE_PROXY_FIX" in config
    assert "SESSION_COOKIE_SECURE" in config
    assert "SESSION_COOKIE_HTTPONLY" in config
    assert "SESSION_COOKIE_SAMESITE" in config
    assert "TALISMAN_CONFIG" in config
    assert "SUPERSET_TALISMAN_ENABLED: ${SUPERSET_TALISMAN_ENABLED:-true}" in compose
    assert re.search(r"SUPERSET_CSRF_ENABLED:\s+\$\{SUPERSET_CSRF_ENABLED:-true\}", compose)
    assert "SUPERSET_RATELIMIT_STORAGE_URI: ${SUPERSET_RATELIMIT_STORAGE_URI:-redis://redis:6379/1}" in compose
    assert "SUPERSET_SESSION_COOKIE_SECURE: ${SUPERSET_SESSION_COOKIE_SECURE:-false}" in compose
    # The one-shot bootstrap marker must sit inside the superset-init block, but
    # not necessarily on its first line: #624 legitimately put security_opt there.
    assert re.search(
        r"^  superset-init:\n(?:    .*\n)*?    # One-shot bootstrap", compose, re.M
    )
    assert "superset-init:\n        condition: service_completed_successfully" in compose
    assert "SQLALCHEMY_DATABASE_URI: \"postgresql+psycopg2://omega_superset_meta:" in aws_compose
    assert "SUPERSET_RATELIMIT_STORAGE_URI: ${SUPERSET_RATELIMIT_STORAGE_URI:-redis://redis:6379/1}" in aws_compose
    assert "SUPERSET_SESSION_COOKIE_SECURE: ${SUPERSET_SESSION_COOKIE_SECURE:-true}" in aws_compose
    assert '127.0.0.1:8088:8088' in aws_compose
    assert "redis:\n        condition: service_healthy" in aws_compose
    assert "REDIS_URL:           ${REDIS_URL:-redis://redis:6379/0}" in aws_compose
    assert "redis:" in aws_compose
