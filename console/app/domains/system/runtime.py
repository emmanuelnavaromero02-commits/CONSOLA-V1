from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

TRUEISH_VALUES = {"1", "true", "yes", "on"}
DEV_APP_ENVS = {"development", "dev", "local", "test"}


def healthz_payload(*, version: str, app_env: str) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "console",
        "version": version,
        "app_env": app_env,
    }


def runtime_config_payload(
    environ: Mapping[str, str],
    *,
    public_url: Callable[..., str],
) -> dict[str, Any]:
    return {
        "workspace_url": public_url(
            "WORKSPACE_URL",
            fallback_env="WORKSPACE_PUBLIC_URL",
            development_default="http://localhost:8001",
        ),
        "console_url": public_url(
            "CONSOLE_URL",
            development_default="http://localhost:8000",
        ),
        "airflow_url": public_url(
            "AIRFLOW_PUBLIC_URL",
            development_default="http://localhost:8082",
        ),
        "superset_url": public_url(
            "SUPERSET_PUBLIC_URL",
            development_default="http://localhost:8088",
        ),
        "s3_bucket": environ.get("S3_BUCKET_NAME")
        or environ.get("MINIO_BUCKET", "lakehouse"),
    }


def system_info_payload(
    environ: Mapping[str, str],
    *,
    version: str,
) -> dict[str, Any]:
    app_env = environ.get("APP_ENV", "production").lower()
    rce_tools_enabled = environ.get("ALLOW_RCE_TOOLS", "").strip().lower() in TRUEISH_VALUES
    dev_mode = app_env in DEV_APP_ENVS
    return {
        "version": version,
        "env": environ.get("MODE", "local"),
        "service": "console",
        "app_env": app_env,
        "dev_mode": dev_mode,
        "rce_tools_enabled": rce_tools_enabled,
        "dag_deploy_enabled": dev_mode and rce_tools_enabled,
    }

