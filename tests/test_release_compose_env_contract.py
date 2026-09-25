from pathlib import Path
import re


REPO = Path(__file__).resolve().parents[1]


def test_release_env_example_documents_required_compose_variables() -> None:
    compose = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    env_example = (REPO / "infra/.env.example").read_text(encoding="utf-8")

    required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?[^}]+}", compose))
    documented = set(
        re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example, flags=re.MULTILINE)
    )

    assert required <= documented, (
        "infra/.env.example is missing required Compose variables: "
        f"{sorted(required - documented)}"
    )


def test_release_workflow_uses_resolvable_aws_validation_env_files() -> None:
    workflow = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert (
        "AWS_ENV_FILE: ${{ github.workspace }}/infra/terraform/deploy/.env.example"
        in workflow
    )
    assert "MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE: /dev/null" in workflow


def test_docker_image_compose_validation_loads_the_documented_env() -> None:
    workflow = (REPO / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert "run: docker compose --env-file .env.example config --quiet" in workflow
