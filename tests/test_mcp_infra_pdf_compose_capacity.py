from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]
MCP_DOCKERFILE = ROOT / "mcp-infra" / "Dockerfile"
LOCAL_COMPOSE = ROOT / "infra" / "docker-compose.yml"
LOCAL_DEV_COMPOSE = ROOT / "infra" / "docker-compose.dev.yml"
AWS_COMPOSE = ROOT / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
AWS_CARTRIDGES_COMPOSE = (
    ROOT / "infra" / "terraform" / "deploy" / "docker-compose.cartridges.yml"
)
GCP_TEMPLATE = (
    ROOT / "infra" / "terraform-gcp" / "templates" / "docker-compose.gcp.yml.tftpl"
)
CAPACITY_ENV = {
    "MCP_INFRA_PDF_MAX_WORKERS": "2",
    "MCP_INFRA_MEM_LIMIT": "1536m",
    "MCP_INFRA_CPUS": "2.0",
    "MCP_INFRA_PIDS_LIMIT": "128",
}
REQUIRED_ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::)?\?[^}]*\}")


def _environment_for(*sources: str) -> dict[str, str]:
    environ = os.environ.copy()
    for name in CAPACITY_ENV:
        environ.pop(name, None)
    for name in REQUIRED_ENV_RE.findall("\n".join(sources)):
        environ[name] = "compose-contract"
    environ.update(
        {
            "COMPOSE_PROJECT_NAME": "mcp-pdf-capacity-contract",
            "SF_PRIVATE_KEY_HOST_PATH": "/dev/null",
        }
    )
    return environ


def _effective_service(
    compose_files: list[Path],
    *,
    extra_source: str = "",
    profile: str | None = None,
) -> dict[str, object]:
    sources = [path.read_text(encoding="utf-8") for path in compose_files]
    command = ["docker", "compose"]
    for path in compose_files:
        command.extend(["-f", str(path)])
    if profile:
        command.extend(["--profile", profile])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment_for(*sources, extra_source),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    return config["services"]["mcp-infra"]


def _assert_capacity_limits(service: dict[str, object]) -> None:
    environment = service["environment"]
    assert isinstance(environment, dict)
    assert environment["MCP_INFRA_PDF_MAX_WORKERS"] == "2"
    assert int(service["mem_limit"]) == 1536 * 1024 * 1024
    assert float(service["cpus"]) == 2.0
    assert int(service["pids_limit"]) == 128
    assert service.get("command") in (None, [])
    assert service.get("entrypoint") in (None, [])


def _dockerfile_command() -> list[str]:
    lines = MCP_DOCKERFILE.read_text(encoding="utf-8").splitlines()
    commands = [line for line in lines if line.startswith("CMD ")]
    assert len(commands) == 1
    assert not any(line.startswith("ENTRYPOINT ") for line in lines)
    command = json.loads(commands[0].removeprefix("CMD "))
    assert isinstance(command, list)
    return command


def test_dockerfile_forces_one_uvicorn_process() -> None:
    command = _dockerfile_command()

    assert command[0] == "uvicorn"
    assert command[1] == "app.main:app"
    workers_index = command.index("--workers")
    assert command[workers_index + 1] == "1"


def test_uvicorn_cli_workers_one_overrides_web_concurrency(monkeypatch) -> None:
    uvicorn_config = importlib.import_module("uvicorn.config")
    uvicorn_main = importlib.import_module("uvicorn.main")
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn_main, "run", fake_run)
    result = CliRunner().invoke(
        uvicorn_main.main,
        _dockerfile_command()[1:],
        env={"WEB_CONCURRENCY": "4"},
    )

    assert result.exit_code == 0, result.output
    assert captured["workers"] == 1
    assert (
        uvicorn_config.Config(
            "app.main:app",
            workers=captured["workers"],
        ).workers
        == 1
    )


def test_local_compose_effective_pdf_limits() -> None:
    _assert_capacity_limits(
        _effective_service(
            [LOCAL_COMPOSE, LOCAL_DEV_COMPOSE],
            profile="sap",
        )
    )


def test_aws_compose_effective_pdf_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_env = tmp_path / "shared.env"
    evidence_env = tmp_path / "evidence.env"
    shared_env.touch()
    evidence_env.touch()
    monkeypatch.setenv("AWS_ENV_FILE", str(shared_env))
    monkeypatch.setenv("MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE", str(evidence_env))

    _assert_capacity_limits(_effective_service([AWS_COMPOSE, AWS_CARTRIDGES_COMPOSE]))


def test_gcp_overlay_effective_pdf_limits(tmp_path: Path) -> None:
    rendered = GCP_TEMPLATE.read_text(encoding="utf-8").replace("$${", "${")
    overlay = tmp_path / "docker-compose.gcp.yml"
    overlay.write_text(rendered, encoding="utf-8")

    service = _effective_service(
        [LOCAL_COMPOSE, overlay],
        extra_source=rendered,
        profile="sap",
    )

    _assert_capacity_limits(service)


def test_capacity_variables_are_documented_in_env_examples() -> None:
    examples = [
        ROOT / "infra" / ".env.example",
        ROOT / "infra" / "terraform" / "deploy" / ".env.example",
    ]

    for example in examples:
        source = example.read_text(encoding="utf-8")
        for name, value in CAPACITY_ENV.items():
            assert f"{name}={value}" in source
