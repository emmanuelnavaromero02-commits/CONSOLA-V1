"""
Superset config — points at the postgres container instead of the default
in-image SQLite. Mounted at /app/pythonpath/superset_config.py via
docker-compose.aws.yml. Reads secrets from environment.
"""
import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://postgres:"
    f"{os.environ['POSTGRES_PASSWORD']}@postgres:5432/superset"
)

# Internal-only deployment behind VPN — relax CSP/CSRF that block local devs.
TALISMAN_ENABLED  = False
WTF_CSRF_ENABLED  = False

# Avoid the in-memory rate-limiter warning. Redis would be ideal long-term.
RATELIMIT_ENABLED = False

FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
}
