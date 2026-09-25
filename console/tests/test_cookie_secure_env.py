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


def test_no_app_env_defaults_to_production_secure():
    with _with_env():
        assert auth.cookie_secure() is True


def test_staging_treated_as_secure():
    with _with_env(APP_ENV="staging"):
        assert auth.cookie_secure() is True
