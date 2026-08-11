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


@pytest.mark.parametrize("custom_private", [False, True])
def test_compose_resolves_private_env_only_for_console(
    tmp_path: Path, custom_private: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker compose is required for env_file resolution")
    shared = tmp_path / "shared.env"
    private = tmp_path / "custom-evidence.env"
    monkeypatch.setenv("S3_BUCKET_NAME", "")
    env = os.environ.copy()
    # Shell values outrank --env-file; ignore the empty local bootstrap value.
    env.pop("S3_BUCKET_NAME", None)
    env.pop("MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE", None)
    env["AWS_ENV_FILE"] = str(shared)
    if custom_private:
        env["MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE"] = str(private)
    expected_private = (
        private if custom_private else Path(f"{shared}.control-room-evidence")
    )
    shared.touch()
    evidence_values = {
        name: f"private-value-{index}"
        for index, name in enumerate(sorted(EVIDENCE_NAMES))
    }
    expected_private.write_text(
        "".join(f"{name}={value}\n" for name, value in evidence_values.items()),
        encoding="utf-8",
    )
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
    consumers = {
        name
        for name, service in services.items()
        if EVIDENCE_NAMES & service.get("environment", {}).keys()
    }
    assert consumers == {"console"}
    assert {
        name: services["console"]["environment"][name] for name in EVIDENCE_NAMES
    } == evidence_values

    raw = yaml.safe_load(AWS_COMPOSE.read_text(encoding="utf-8"))
    assert [item["required"] for item in raw["services"]["console"]["env_file"]] == [
        True,
        True,
    ]
    assert not EVIDENCE_NAMES & raw["services"]["console"].get("environment", {}).keys()
    assert "MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE" not in (
        CARTRIDGES_COMPOSE.read_text(encoding="utf-8")
    )
