"""
Superset config — points at the postgres container instead of the default
in-image SQLite. Mounted at /app/pythonpath/superset_config.py via
docker-compose.aws.yml. Reads secrets from environment.
"""
import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

SQLALCHEMY_DATABASE_URI = os.environ.get("SQLALCHEMY_DATABASE_URI")
if not SQLALCHEMY_DATABASE_URI:
    raise RuntimeError("SQLALCHEMY_DATABASE_URI is required; refusing superuser fallback")

# Security defaults are production-safe. Local/dev can explicitly opt out with
# SUPERSET_TALISMAN_ENABLED=false / SUPERSET_CSRF_ENABLED=false.
TALISMAN_ENABLED  = os.environ.get("SUPERSET_TALISMAN_ENABLED", "true").lower() == "true"
WTF_CSRF_ENABLED  = os.environ.get("SUPERSET_CSRF_ENABLED", "true").lower() == "true"
RATELIMIT_ENABLED = os.environ.get("SUPERSET_RATELIMIT_ENABLED", "true").lower() == "true"
RATELIMIT_STORAGE_URI = (
    os.environ.get("SUPERSET_RATELIMIT_STORAGE_URI")
    or os.environ.get("REDIS_URL")
    or "redis://redis:6379/1"
)

FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
}
