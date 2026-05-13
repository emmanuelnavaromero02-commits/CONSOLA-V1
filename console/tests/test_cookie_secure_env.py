"""Sprint v1.7 FIX 2 — cookie_secure() respects APP_ENV when COOKIE_SECURE is unset.

Local dev (http://localhost) cannot accept Secure cookies. Production
defaults must NOT require an extra env var to opt in to security. The
helper now keys off APP_ENV when COOKIE_SECURE is absent.
"""
from unittest.mock import patch

from app.services import auth


def _with_env(**env):
    return patch.dict("os.environ", env, clear=True)


def test_explicit_true_wins_in_dev():
    with _with_env(APP_ENV="development", COOKIE_SECURE="true"):
        assert auth.cookie_secure() is True


def test_explicit_false_wins_in_prod():
    with _with_env(APP_ENV="production", COOKIE_SECURE="false"):
        assert auth.cookie_secure() is False


def test_development_defaults_false():
    with _with_env(APP_ENV="development"):
        assert auth.cookie_secure() is False


def test_production_defaults_true():
    with _with_env(APP_ENV="production"):
        assert auth.cookie_secure() is True


def test_no_app_env_defaults_to_dev_false():
    # APP_ENV absent → development semantics → False (preserves local dev).
    with _with_env():
        assert auth.cookie_secure() is False


def test_staging_treated_as_secure():
    # Any env that is not literally "development" gets secure cookies.
    with _with_env(APP_ENV="staging"):
        assert auth.cookie_secure() is True
