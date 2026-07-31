"""
Superset config — points at the postgres container instead of the default
in-image SQLite. Mounted at /app/pythonpath/superset_config.py via
docker-compose.aws.yml. Reads secrets from environment.
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
PREVIOUS_SECRET_KEY = os.environ.get("SUPERSET_PREVIOUS_SECRET_KEY") or None

SQLALCHEMY_DATABASE_URI = os.environ.get("SQLALCHEMY_DATABASE_URI")
if not SQLALCHEMY_DATABASE_URI:
    raise RuntimeError(
        "SQLALCHEMY_DATABASE_URI is required; refusing superuser fallback"
    )

# Security defaults are production-safe. Local/dev can explicitly opt out with
# SUPERSET_TALISMAN_ENABLED=false / SUPERSET_CSRF_ENABLED=false.
TALISMAN_ENABLED = os.environ.get("SUPERSET_TALISMAN_ENABLED", "true").lower() == "true"
WTF_CSRF_ENABLED = os.environ.get("SUPERSET_CSRF_ENABLED", "true").lower() == "true"
RATELIMIT_ENABLED = (
    os.environ.get("SUPERSET_RATELIMIT_ENABLED", "true").lower() == "true"
)
RATELIMIT_STORAGE_URI = (
    os.environ.get("SUPERSET_RATELIMIT_STORAGE_URI")
    or os.environ.get("REDIS_URL")
    or "redis://redis:6379/1"
)

ENABLE_PROXY_FIX = os.environ.get("SUPERSET_ENABLE_PROXY_FIX", "true").lower() == "true"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.environ.get("SUPERSET_SESSION_COOKIE_SAMESITE", "Lax")
SESSION_COOKIE_SECURE = (
    os.environ.get("SUPERSET_SESSION_COOKIE_SECURE", "true").lower() == "true"
)
PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"

TALISMAN_CONFIG = {
    "force_https": os.environ.get("SUPERSET_FORCE_HTTPS", "true").lower() == "true",
    "content_security_policy": {
        "default-src": ["'self'"],
        "img-src": ["'self'", "data:", "blob:"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "script-src": ["'self'", "'unsafe-inline'", "'unsafe-eval'"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'self'"],
    },
    "session_cookie_secure": SESSION_COOKIE_SECURE,
    "session_cookie_http_only": True,
}

FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
}

# Gold identity is the publication head, so cross-head result reuse is unsafe.
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}
RESULTS_BACKEND = None
