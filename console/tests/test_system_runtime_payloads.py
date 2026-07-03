from __future__ import annotations

from app.domains.system.runtime import (
    healthz_payload,
    runtime_config_payload,
    system_info_payload,
)


def test_healthz_payload_is_public_and_secret_free():
    assert healthz_payload(version="1.2.3", app_env="production") == {
        "ok": True,
        "service": "console",
        "version": "1.2.3",
        "app_env": "production",
    }


def test_runtime_config_payload_uses_public_url_provider_and_bucket_defaults():
    calls: list[tuple[tuple, dict]] = []

    def _public_url(*args, **kwargs):
        calls.append((args, kwargs))
        return f"url:{args[0]}"

    payload = runtime_config_payload({}, public_url=_public_url)

    assert payload == {
        "workspace_url": "url:WORKSPACE_URL",
        "console_url": "url:CONSOLE_URL",
        "airflow_url": "url:AIRFLOW_PUBLIC_URL",
        "superset_url": "url:SUPERSET_PUBLIC_URL",
        "s3_bucket": "lakehouse",
    }
    assert calls[0] == (
        ("WORKSPACE_URL",),
        {
            "fallback_env": "WORKSPACE_PUBLIC_URL",
            "development_default": "http://localhost:8001",
        },
    )


def test_runtime_config_payload_prefers_explicit_s3_bucket():
    payload = runtime_config_payload(
        {"S3_BUCKET_NAME": "prod-lake", "MINIO_BUCKET": "local-lake"},
        public_url=lambda *args, **kwargs: "",
    )

    assert payload["s3_bucket"] == "prod-lake"


def test_system_info_payload_defaults_to_production_guardrails():
    payload = system_info_payload({}, version="1.2.3")

    assert payload["version"] == "1.2.3"
    assert payload["service"] == "console"
    assert payload["app_env"] == "production"
    assert payload["env"] == "local"
    assert payload["dev_mode"] is False
    assert payload["rce_tools_enabled"] is False
    assert payload["dag_deploy_enabled"] is False


def test_system_info_payload_enables_dag_deploy_only_when_dev_and_rce_enabled():
    payload = system_info_payload(
        {
            "APP_ENV": "local",
            "MODE": "compose",
            "ALLOW_RCE_TOOLS": "yes",
        },
        version="1.2.3",
    )

    assert payload["app_env"] == "local"
    assert payload["env"] == "compose"
    assert payload["dev_mode"] is True
    assert payload["rce_tools_enabled"] is True
    assert payload["dag_deploy_enabled"] is True

