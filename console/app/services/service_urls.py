from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def app_env() -> str:
    return os.environ.get("APP_ENV", "production").strip().lower()


def is_production_env() -> bool:
    return app_env() in {"production", "prod"}


def is_private_public_url(value: str) -> bool:
    try:
        host = urlparse(value).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def public_url(
    env_name: str,
    *,
    fallback_env: str | None = None,
    development_default: str = "",
) -> str:
    raw = os.environ.get(env_name)
    if not raw and fallback_env:
        raw = os.environ.get(fallback_env)
    if raw:
        value = raw.rstrip("/")
        if is_production_env() and is_private_public_url(value):
            logger.warning(
                "%s points at a private/local address in production; omitting public URL",
                env_name,
            )
            return ""
        return value
    if is_production_env():
        logger.warning(
            "%s is not configured in production; omitting localhost fallback", env_name
        )
        return ""
    return development_default.rstrip("/")


def running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def service_url(env_name: str, docker_default: str, local_default: str) -> str:
    raw = os.environ.get(env_name)
    if raw:
        return raw.rstrip("/")
    return docker_default.rstrip("/") if running_in_container() else local_default.rstrip("/")


def vault_url() -> str:
    return service_url("VAULT_URL", "http://vault:8300", "http://127.0.0.1:8300")

