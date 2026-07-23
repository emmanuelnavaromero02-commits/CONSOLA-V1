from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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


def test_local_compose_effective_pdf_limits() -> None:
    _assert_capacity_limits(
        _effective_service(
            [LOCAL_COMPOSE, LOCAL_DEV_COMPOSE],
            profile="sap",
        )
    )


def test_aws_compose_effective_pdf_limits() -> None:
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
