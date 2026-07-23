from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "infra/terraform/deploy"
AWS_COMPOSE = DEPLOY / "docker-compose.aws.yml"
CARTRIDGES_COMPOSE = DEPLOY / "docker-compose.cartridges.yml"
EVIDENCE_NAMES = {
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID",
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
    "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS",
}


def _resolved_env_files(service: dict) -> set[Path]:
    return {
        Path(item["path"] if isinstance(item, dict) else item).resolve()
        for item in service.get("env_file", [])
    }


@pytest.mark.parametrize("custom_private", [False, True])
def test_compose_resolves_private_env_only_for_console(
    tmp_path: Path, custom_private: bool
) -> None:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker compose is required for env_file resolution")
    shared = tmp_path / "shared.env"
    private = tmp_path / "custom-evidence.env"
    env = os.environ.copy()
    env.pop("MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE", None)
    env["AWS_ENV_FILE"] = str(shared)
    if custom_private:
        env["MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE"] = str(private)
    expected_private = (
        private if custom_private else Path(f"{shared}.control-room-evidence")
    )
    shared.touch()
    expected_private.touch()
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(DEPLOY / ".env.example"),
            "-f",
            str(AWS_COMPOSE),
            "-f",
            str(CARTRIDGES_COMPOSE),
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        cwd=DEPLOY,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    expected_private = expected_private.resolve()
    consumers = {
        name
        for name, service in services.items()
        if expected_private in _resolved_env_files(service)
    }
    assert consumers == {"console"}
    console_paths = [
        Path(item["path"]).resolve() for item in services["console"]["env_file"]
    ]
    assert console_paths == [shared.resolve(), expected_private]
    assert not EVIDENCE_NAMES & services["console"].get("environment", {}).keys()

    raw = yaml.safe_load(AWS_COMPOSE.read_text(encoding="utf-8"))
    assert [item["required"] for item in raw["services"]["console"]["env_file"]] == [
        True,
        True,
    ]
    assert "MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE" not in (
        CARTRIDGES_COMPOSE.read_text(encoding="utf-8")
    )
