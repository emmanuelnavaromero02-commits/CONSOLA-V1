from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "infra/terraform-gcp/release/hydrate-bigquery-shadow-config.sh"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"


def _config(*, enabled: bool = True) -> dict[str, object]:
    return {
        "schema_version": 2,
        "deployment_project_id": "omega-deployment-project",
        "environment": "staging",
        "enabled": enabled,
        "project_id": "omega-shadow-pilot" if enabled else "",
        "dataset_id": "omega_staging_talent_shadow" if enabled else "",
        "location": "us-central1",
        "service_account": (
            "omega-shadow@omega-shadow-pilot.iam.gserviceaccount.com"
            if enabled
            else ""
        ),
        "gold_bucket": "omega-gold" if enabled else "",
        "tenant_id": TENANT if enabled else "",
        "workspace_id": WORKSPACE if enabled else "",
        "maximum_bytes_billed": 10 * 1024**3,
        "population_backend": "postgres_gold",
    }


def _run(
    env_file: Path,
    config_file: Path,
    *,
    mode: str | None = None,
    include_env_file: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "OMEGA_BIGQUERY_SHADOW_CONFIG_FILE": str(config_file),
        "OMEGA_GCP_PROJECT_ID": "omega-deployment-project",
        "OMEGA_GCP_ENVIRONMENT": "staging",
    }
    if include_env_file:
        env["OMEGA_ENV_FILE"] = str(env_file)
    else:
        env.pop("OMEGA_ENV_FILE", None)
    if mode is not None:
        env["OMEGA_BIGQUERY_SHADOW_CONFIG_MODE"] = mode
    return subprocess.run(
        ["bash", str(HELPER)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_enabled_runtime_handoff_is_complete_atomic_and_postgres_serving(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "infra.env"
    env_file.write_text(
        "UNCHANGED=value\nTALENT_POPULATION_BACKEND=postgres_gold\n",
        encoding="utf-8",
    )
    config_file = tmp_path / "shadow.json"
    config_file.write_text(json.dumps(_config()), encoding="utf-8")

    result = _run(env_file, config_file)
    assert result.returncode == 0, result.stderr
    values = _values(env_file)
    assert values["UNCHANGED"] == "value"
    assert values["TALENT_POPULATION_BACKEND"] == "postgres_gold"
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_ENABLED"] == "true"
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST"] == TENANT
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST"] == WORKSPACE
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_MAX_BYTES_BILLED"] == str(
        10 * 1024**3
    )
    assert "service_account" not in result.stdout


def test_invalid_or_partial_handoff_never_changes_existing_environment(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "infra.env"
    original = "KEEP=exactly-this\nBIGQUERY_TALENT_9BOX_SHADOW_ENABLED=false\n"
    env_file.write_text(original, encoding="utf-8")
    config = _config()
    del config["workspace_id"]
    config_file = tmp_path / "partial.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    result = _run(env_file, config_file)
    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == original


def test_legacy_schema_is_rejected_even_when_shadow_is_disabled(tmp_path: Path) -> None:
    env_file = tmp_path / "infra.env"
    original = "KEEP=exactly-this\n"
    env_file.write_text(original, encoding="utf-8")
    config = _config(enabled=False)
    config["schema_version"] = 1
    config_file = tmp_path / "legacy.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    result = _run(env_file, config_file)

    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == original


def test_check_mode_validates_handoff_without_touching_environment(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "infra.env"
    original = b"KEEP=byte-for-byte\nBIGQUERY_TALENT_9BOX_SHADOW_ENABLED=false\n"
    env_file.write_bytes(original)
    config_file = tmp_path / "shadow.json"
    config_file.write_text(json.dumps(_config()), encoding="utf-8")

    result = _run(env_file, config_file, mode="check", include_env_file=False)

    assert result.returncode == 0, result.stderr
    assert "environment unchanged" in result.stdout
    assert env_file.read_bytes() == original


def test_disabled_handoff_clears_stale_coordinates(tmp_path: Path) -> None:
    env_file = tmp_path / "infra.env"
    env_file.write_text(
        "BIGQUERY_TALENT_9BOX_SHADOW_ENABLED=true\n"
        "BIGQUERY_TALENT_9BOX_SHADOW_PROJECT_ID=stale-project\n",
        encoding="utf-8",
    )
    config_file = tmp_path / "disabled.json"
    config_file.write_text(json.dumps(_config(enabled=False)), encoding="utf-8")

    result = _run(env_file, config_file)
    assert result.returncode == 0, result.stderr
    values = _values(env_file)
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_ENABLED"] == "false"
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_PROJECT_ID"] == ""
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST"] == ""
    assert values["BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST"] == ""


def test_handoff_is_bound_to_deployment_project_environment_and_dataset(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "infra.env"
    original = "KEEP=unchanged\n"
    env_file.write_text(original, encoding="utf-8")

    for field, value in (
        ("deployment_project_id", "foreign-project"),
        ("environment", "production"),
        ("dataset_id", "omega_production_talent_shadow"),
    ):
        config = _config()
        config[field] = value
        config_file = tmp_path / f"bad-{field}.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        result = _run(env_file, config_file)

        assert result.returncode != 0
        assert env_file.read_text(encoding="utf-8") == original


def test_hydrator_requires_server_owned_deployment_identity(tmp_path: Path) -> None:
    env_file = tmp_path / "infra.env"
    env_file.write_text("KEEP=unchanged\n", encoding="utf-8")
    config_file = tmp_path / "shadow.json"
    config_file.write_text(json.dumps(_config()), encoding="utf-8")
    env = {
        **os.environ,
        "OMEGA_BIGQUERY_SHADOW_CONFIG_FILE": str(config_file),
        "OMEGA_ENV_FILE": str(env_file),
    }
    env.pop("OMEGA_GCP_PROJECT_ID", None)
    env.pop("OMEGA_GCP_ENVIRONMENT", None)

    result = subprocess.run(
        ["bash", str(HELPER)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "KEEP=unchanged\n"
